"""Tor Browser provider contracts. Live GUI runs are opt-in via ALEX_TOR_BROWSER_LIVE=1."""

import asyncio
from types import SimpleNamespace

import pytest

from app.tools.contracts import ToolError
from app.tools.tor.browser import (
    TorBrowserArgs,
    TorBrowserProvider,
    TorRoutedBrowserProvider,
    browser_install_info,
    browser_status,
    existing_tor_browser,
    prove_socks5,
    validate_browser_url,
    write_isolated_profile,
)
from app.tools.tor.router import blocked_link
from app.tools.tor.snapshot import (
    assign_link_ids,
    fetch_needs_browser,
    resolve_link_id,
    snapshot_from_html,
    visible_text,
)


def test_detects_tor_browser_executable():
    path = existing_tor_browser()
    info = browser_install_info()
    status = browser_status()
    if path:
        assert path.name.lower() == "firefox.exe"
        assert info["installed"] is True
        assert info["executable"] is True
        assert info["mechanism"] == "marionette"
        assert "Tor" in (info.get("vendor") or "Tor Project")
    assert status["enabled"] is False
    assert status["automatic"] is False
    assert status["routed_provider"] == "not_implemented"


def test_isolated_profile_does_not_import_user_data(tmp_path):
    root = tmp_path / "profile"
    write_isolated_profile(root, socks_host="127.0.0.1", socks_port=9050, marionette_port=2828)
    names = {item.name for item in root.iterdir()}
    assert "user.js" in names
    assert "cookies.sqlite" not in names
    assert "logins.json" not in names
    assert "key4.db" not in names
    prefs = (root / "user.js").read_text(encoding="utf-8")
    assert "socks_remote_dns" in prefs
    assert "extensions.torlauncher.start_tor" in prefs
    assert 'start_tor", false' in prefs


def test_fetch_needs_browser_js_shell_not_mere_script():
    rich = "<html><body><script>ok()</script><p>" + ("text " * 80) + "</p></body></html>"
    assert fetch_needs_browser(rich) is False
    shell = '<html><body><div id="root"></div><script src="app.js"></script>Loading...</body></html>'
    assert fetch_needs_browser(shell) is True
    assert fetch_needs_browser("<p>hello</p>", explicit_browser=True) is True
    fixture = """
    <html><body>
    <div id="root">Loading...</div>
    <script>setTimeout(function(){ document.getElementById("root").innerHTML = "<p>done</p>"; }, 1200);</script>
    </body></html>
    """
    assert fetch_needs_browser(fixture) is True
    assert "done" not in visible_text(fixture)
    rendered = (
        '<html><body><div id="root"><p>Visible article text after render. '
        + ("word " * 40)
        + "</p><a href='/second.html'>Second</a></div></body></html>"
    )
    assert fetch_needs_browser(rendered) is False


def test_link_ids_and_blocked_schemes():
    html = """
    <a href="/docs">Docs</a>
    <a href="mailto:x@y.z">Mail</a>
    <a href="javascript:alert(1)">JS</a>
    <a href="https://example.org/setup.exe">Exe</a>
    <a href="https://example.org/about">About</a>
    """
    snap = snapshot_from_html("https://example.org/", html)
    ids = [item["id"] for item in snap.links]
    assert ids[0] == "L1"
    assert resolve_link_id(snap, "L1")
    assert not any("mailto" in (item["url"] or "") for item in snap.links)
    assert blocked_link("https://example.org/app.exe")
    with pytest.raises(ToolError) as error:
        validate_browser_url("javascript:alert(1)")
    assert error.value.code == "unsafe_url"
    with pytest.raises(ToolError) as error:
        validate_browser_url("file:///C:/Windows/notepad.exe")
    assert error.value.code == "unsafe_url"
    with pytest.raises(ToolError) as error:
        validate_browser_url("https://user:pass@example.org/")
    assert error.value.code == "unsafe_url"
    with pytest.raises(ToolError) as error:
        validate_browser_url("https://example.org/payload.exe")
    assert error.value.code == "download_blocked"
    with pytest.raises(ToolError):
        validate_browser_url("http://127.0.0.1/secret")
    assert validate_browser_url("http://127.0.0.1/ok", allow_loopback=True)


def test_forms_are_detected_not_submitted():
    html = '<form action="/login"><input name="password" type="password"><button type="submit">Go</button></form><a href="/next">Next</a>'
    snap = snapshot_from_html("https://example.org/", html)
    assert snap.forms_present is True
    assert any(item["kind"] == "submit" for item in snap.buttons)
    assert not hasattr(TorBrowserArgs, "script")
    fields = TorBrowserArgs.model_fields
    assert "javascript" not in fields and "script" not in fields


class FakeSession:
    def __init__(self):
        self.session_id = "sess1"
        self.tool_run_id = "run1"
        self.socks = {"host": "127.0.0.1", "port": 9050}
        html = '<html><head><title>Rendered</title></head><body><p>TOR_BROWSER_JS_OK</p><a href="https://example.org/next">Next</a></body></html>'
        self.snapshot = snapshot_from_html(
            "https://example.org/", html, title="Rendered", text="TOR_BROWSER_JS_OK Next"
        )
        self.visited = set()

    def pids(self):
        return [4242]


class FakeController:
    def __init__(self):
        self.session = None
        self.closed = False
        self.opened = []
        self.clicked = []

    async def open(self, url, wait_ms=0, tool_run_id=None):
        self.opened.append(url)
        self.session = FakeSession()
        self.session.tool_run_id = tool_run_id
        return self.session.snapshot

    async def click(self, link_id, wait_ms=0):
        self.clicked.append(link_id)
        url = resolve_link_id(self.session.snapshot, link_id)
        self.session.snapshot = snapshot_from_html(url, "<p>next page</p>", title="Next", text="next page")
        return self.session.snapshot

    async def get_content(self):
        return self.session.snapshot

    async def close(self):
        self.closed = True
        self.session = None
        return {"closed": True, "started_by_alex": True, "process_ids": [4242]}


def test_provider_creates_t_source_and_click_uses_link_id(monkeypatch):
    monkeypatch.setattr("app.tools.tor.browser.automation_enabled", lambda settings=None, prefs=None: True)
    controller = FakeController()
    provider = TorBrowserProvider(controller)
    context = SimpleNamespace(run_id="run1", settings=SimpleNamespace(tor_browser_mode="auto"), secrets=())
    result = asyncio.run(
        provider.execute(TorBrowserArgs(operation="open", url="https://example.org/"), context)
    )
    assert result.sources[0]["retrieval"] == "browser"
    assert result.sources[0]["transport"] == "tor"
    assert result.sources[0]["rendered"] is True
    assert result.metadata["started_by_alex"] is True
    assert "TOR_BROWSER_JS_OK" in result.sources[0]["excerpt"]
    clicked = asyncio.run(provider.execute(TorBrowserArgs(operation="click", link_id="L1"), context))
    assert controller.clicked == ["L1"]
    assert clicked.sources[0]["url"].endswith("/next")
    asyncio.run(provider.execute(TorBrowserArgs(operation="close"), context))
    assert controller.closed is True


def test_routed_provider_is_not_pretending_to_be_tor_browser():
    with pytest.raises(ToolError) as error:
        asyncio.run(TorRoutedBrowserProvider().execute(None, None))
    assert error.value.code == "tor_routed_browser_not_implemented"


def test_fail_closed_invalid_socks(monkeypatch):
    async def run():
        with pytest.raises(ToolError) as error:
            await prove_socks5("127.0.0.1", 19999, timeout=0.3)
        assert error.value.code == "tor_unavailable"

    asyncio.run(run())


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows Job Objects only")
def test_job_process_closes_only_own_tree(tmp_path):
    import subprocess
    import sys
    import time

    from app.tools.tor.winjob import JobProcess

    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x01000000,
    )
    job = JobProcess([sys.executable, "-c", "import time; time.sleep(30)"])
    time.sleep(0.2)
    own = job.pid
    assert job.poll() is None
    job.close()
    deadline = time.time() + 5
    while time.time() < deadline and job.poll() is None:
        time.sleep(0.05)
    try:
        assert job.poll() is not None
        assert unrelated.poll() is None
    finally:
        if unrelated.poll() is None:
            unrelated.terminate()
            unrelated.wait(timeout=5)
    assert own


def test_assign_link_ids_stable():
    labeled = assign_link_ids([{"url": "https://example.org/a"}, {"url": "https://example.org/b"}])
    assert [item["id"] for item in labeled] == ["L1", "L2"]


def test_browser_target_from_prompt_and_opt_out():
    from app.tools.tor.browser import (
        browser_target_from_prompt,
        looks_like_no_tor_browser,
        looks_like_tor_browser,
    )

    assert looks_like_tor_browser("Через Tor Browser открой сайт проверки Tor")
    assert looks_like_no_tor_browser("не используй Tor Browser")
    assert not looks_like_tor_browser("Через Tor найди onion, но не используй Tor Browser")
    assert browser_target_from_prompt("открой https://check.torproject.org/ через Tor Browser") == (
        "https://check.torproject.org/"
    )
    assert browser_target_from_prompt("Через Tor Browser открой официальный сайт проверки Tor") == (
        "https://check.torproject.org/"
    )
    assert not browser_target_from_prompt("file:///etc/passwd")


def test_planner_tor_browser_modes(monkeypatch):
    from types import SimpleNamespace

    from app.tools.orchestrator import ToolOrchestrator
    from app.tools.registry import make_registry

    monkeypatch.setattr("app.tools.tor.browser.automation_ready", lambda settings=None, prefs=None: True)
    orch = ToolOrchestrator(make_registry(), None)

    def context(prompt, browser_mode="auto", tor_mode="auto"):
        return SimpleNamespace(
            mode="off",
            computer_mode="off",
            user_prompt=prompt,
            assigned_device_id=None,
            host_online=False,
            coding_task=False,
            settings=SimpleNamespace(tor_browser_mode=browser_mode, tor_mode=tor_mode),
            tor_mode=tor_mode,
            tor_enabled=tor_mode != "off",
            sources=[],
        )

    explicit = "Через Tor Browser открой https://check.torproject.org/"
    names = {item.name for item in orch.planner_definitions(context(explicit, "auto"))}
    assert "tor_browser" in names
    assert "tor_search" in names
    names = {item.name for item in orch.planner_definitions(context(explicit, "off"))}
    assert "tor_browser" not in names
    names = {item.name for item in orch.planner_definitions(context("Сколько будет 2+2?", "on", "on"))}
    assert "tor_browser" not in names
    research = "Через Tor найди официальный onion-сервис Tor Project."
    names = {item.name for item in orch.planner_definitions(context(research, "auto"))}
    assert "tor_search" in names
    assert "tor_browser" not in names
    names = {item.name for item in orch.planner_definitions(context(research, "on"))}
    assert "tor_browser" in names
    names = {item.name for item in orch.planner_definitions(context("не используй Tor Browser", "on", "on"))}
    assert "tor_browser" not in names
    assert visible_text("<script>secret()</script><p>Hello world</p>") == "Hello world"


def test_fetch_needs_browser_ignores_js_marker_strings():
    import inspect

    from app.tools.tor import snapshot as snapshot_mod

    source = inspect.getsource(fetch_needs_browser) + inspect.getsource(snapshot_mod)
    assert "ALEX_ONION_JS_RENDERED_OK" not in source
    assert "ALEX_ONION_SECOND_PAGE_OK" not in source
    html = """
    <html><body>
    <div id="root">Loading...</div>
    <script>setTimeout(function(){ document.body.innerHTML = "ALEX_ONION_JS_RENDERED_OK"; }, 1200);</script>
    </body></html>
    """
    assert fetch_needs_browser(html) is True
    assert "ALEX_ONION_JS_RENDERED_OK" not in visible_text(html)
    static = "<html><body><p>Plain onion article without a script tag.</p></body></html>"
    assert fetch_needs_browser(static) is False


def test_browser_fallback_cap_and_visited_follow():
    from app.tools.policy import ToolLimits
    from app.tools.registry import make_registry

    definition = make_registry().get("tor_browser")[0]
    limits = ToolLimits(
        max_calls=20,
        hard_max_calls=20,
        max_tor_calls=20,
        max_tor_browser=2,
        hard_tor_browser=2,
    )
    args = TorBrowserArgs(operation="open", url="https://check.torproject.org/")
    limits.consume(definition, args)
    limits.consume(definition, args)
    with pytest.raises(ToolError, match="tool_limit"):
        limits.consume(definition, args)
    import inspect

    from app.tools.tor.browser import TorBrowserController

    source = inspect.getsource(TorBrowserController.navigate)
    assert "if key in session.visited and follow" in source
    assert 'raise ToolError("tool_limit")' in source
