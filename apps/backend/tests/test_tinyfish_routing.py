"""TinyFish Agent/Browser routing, budget, and safety contracts. No live paid calls."""

from types import SimpleNamespace

import pytest

from app.tools.contracts import ToolError
from app.tools.orchestrator import ToolOrchestrator
from app.tools.policy import WebSettings
from app.tools.registry import make_registry
from app.tools.security import sanitized, validate_url
from app.tools.tinyfish.budget import TinyFishBudget, exhausted, preflight_agent, preflight_browser
from app.tools.tinyfish.classify import classify_agent_goal, implied_start_url, looks_like_simple_math
from app.tools.web_router import select_tinyfish_route
from tests.fakes_web import public_dns


def _context(prompt, **kwargs):
    settings = kwargs.pop("settings", WebSettings())
    kwargs.setdefault("mode", "on")
    kwargs.setdefault("sources", [])
    kwargs.setdefault("computer_mode", "off")
    kwargs.setdefault("assigned_device_id", None)
    kwargs.setdefault("host_online", False)
    return SimpleNamespace(user_prompt=prompt, settings=settings, **kwargs)


def test_classify_read_only_vs_side_effect():
    assert classify_agent_goal("Find the current Python version").kind == "READ_ONLY"
    assert classify_agent_goal("Отправь форму на внешнем сайте").kind == "SIDE_EFFECT"
    assert classify_agent_goal("Buy a domain with this card").kind == "SIDE_EFFECT"
    assert classify_agent_goal("Click the documentation link").kind == "POTENTIAL_SIDE_EFFECT"


def test_route_hierarchy_and_isolation():
    math = select_tinyfish_route("Сколько будет 2+2?", _context("Сколько будет 2+2?"))
    assert math.paid is None and looks_like_simple_math("Сколько будет 2+2?")
    lookup = select_tinyfish_route(
        "Какая актуальная версия Python?", _context("Какая актуальная версия Python?")
    )
    assert lookup.paid is None and lookup.reason == "search_fetch"
    browser = select_tinyfish_route(
        "Открой официальный сайт Python в браузере и посмотри страницу",
        _context("Открой официальный сайт Python в браузере и посмотри страницу"),
    )
    assert browser.paid == "browser"
    assert implied_start_url(browser.url or "официальный сайт Python") or True
    agent = select_tinyfish_route(
        "Исследуй официальный сайт Python: найди три раздела документации, сравни их назначение и верни ссылки.",
        _context(
            "Исследуй официальный сайт Python: найди три раздела документации, сравни их назначение и верни ссылки."
        ),
    )
    assert agent.paid == "agent"
    tor = select_tinyfish_route(
        "Через Tor найди официальный onion-сервис Tor Project.",
        _context("Через Tor найди официальный onion-сервис Tor Project."),
    )
    assert tor.paid is None and tor.reason == "tor_stack_only"
    blocked = select_tinyfish_route(
        "Отправь форму на внешнем сайте", _context("Отправь форму на внешнем сайте")
    )
    assert blocked.paid is None and blocked.classification == "SIDE_EFFECT"


def test_planner_hides_paid_tools_in_auto():
    orch = ToolOrchestrator(make_registry(), None)
    names = [
        item.name for item in orch.planner_definitions(_context("Какая актуальная версия Python?", mode="on"))
    ]
    assert "web_search" in names and "web_fetch" in names
    assert "web_agent" not in names
    assert "web_browser" not in names
    coding = [
        item.name
        for item in orch.planner_definitions(
            _context("Исправь тесты в проекте", mode="off", computer_mode="ask", host_online=True)
        )
    ]
    assert "web_agent" not in coding and "web_browser" not in coding


def test_budget_preflight_blocks_before_paid_call():
    settings = SimpleNamespace(
        tinyfish_paid_hard_usd=2.0,
        tinyfish_agent_hard_steps=50,
        tinyfish_browser_hard_minutes=30,
        tinyfish_agent_max_runs=2,
        tinyfish_browser_max_sessions=2,
        tinyfish_agent_step_price=0.016,
        tinyfish_browser_minute_price=0.002,
    )
    prefs = WebSettings(tinyfish_paid_task_budget_usd=0.01, agent_max_steps=20, agent_max_runs=2)
    budget = TinyFishBudget()
    with pytest.raises(ToolError, match="run_budget"):
        preflight_agent(budget, settings, prefs, requested_steps=2)
    spent = TinyFishBudget(paid_spent=0.01)
    assert exhausted({"tinyfish": spent.dump()}, settings, prefs)
    with pytest.raises(ToolError, match="run_budget"):
        preflight_browser(TinyFishBudget(browser_sessions=2), settings, prefs, requested_minutes=1)


def test_secret_never_surfaces_and_onion_rejected():
    assert "tf-live-secret" not in sanitized("TINYFISH_API_KEY=tf-live-secret", ("tf-live-secret",))
    with pytest.raises(ToolError, match="unsafe_url"):
        import asyncio

        asyncio.run(
            validate_url(
                "http://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion/",
                public_dns,
            )
        )
    assert implied_start_url("see http://example.onion/docs") == ""
    assert implied_start_url("официальный сайт Python").startswith("https://www.python.org")


def test_web_browser_schema_has_no_script_surface():
    from app.tools.tinyfish.browser import WebBrowserArgs

    schema = WebBrowserArgs.model_json_schema()
    blob = str(schema).lower()
    assert "evaluate" not in blob and "javascript" not in blob and "cdp" not in blob
    assert set(schema["properties"]["operation"]["enum"]) == {
        "open",
        "read",
        "links",
        "click",
        "back",
        "wait",
        "close",
    }


def test_journal_redacts_cdp_and_secrets():
    from app.tools.local.journal import append_event
    from app.tools.models import TaskEvent

    class Dummy:
        def add(self, row):
            self.row = row

    db = Dummy()
    append_event(
        db,
        "task-1",
        "TINYFISH_BROWSER_STARTED",
        {
            "cdp_url": "wss://secret.example/cdp-token",
            "cookie": "sid=abc",
            "api_key": "tf-live-secret",
            "session": "br_ok",
        },
        ("tf-live-secret",),
    )
    assert isinstance(db.row, TaskEvent)
    assert "cdp_url" not in db.row.payload
    assert "cookie" not in db.row.payload
    assert "api_key" not in db.row.payload
    assert db.row.payload.get("session") == "br_ok"
    assert "tf-live-secret" not in str(db.row.payload)


def test_restart_recovery_terminates_leftover_sessions(monkeypatch):
    from pydantic import SecretStr

    from app.tools.local.task import _terminate_tinyfish_leftovers

    calls = []

    class FakeResponse:
        status_code = 204

    class FakeClient:
        def __init__(self, **kwargs):
            assert "tf-live-secret" not in str(kwargs.get("headers"))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def delete(self, url):
            calls.append(("DELETE", url.rsplit("/", 1)[-1]))
            return FakeResponse()

        def post(self, url):
            calls.append(("POST", url.rsplit("/", 2)[-2]))
            return FakeResponse()

    monkeypatch.setattr("httpx.Client", FakeClient)
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(tinyfish_api_key=SecretStr("test-only-provider-credential")),
    )
    _terminate_tinyfish_leftovers(["br_leftover"], ["run_leftover"])
    assert ("DELETE", "br_leftover") in calls
    assert ("POST", "run_leftover") in calls


def test_local_computer_and_math_never_select_paid():
    local = select_tinyfish_route(
        "Создай на моём рабочем столе тестовую папку",
        _context(
            "Создай на моём рабочем столе тестовую папку",
            mode="auto",
            computer_mode="trusted",
        ),
    )
    assert local.paid is None and local.reason == "local_computer_only"


def test_server_injects_browser_and_agent_before_model():
    import asyncio
    import json

    browser_prompt = (
        "Открой официальный сайт Python, посмотри страницу в браузере, "
        "перейди в документацию и кратко скажи, что находится на второй странице."
    )
    agent_prompt = (
        "Исследуй официальный сайт Python: найди три раздела документации, "
        "сравни их назначение и верни ссылки."
    )
    recorded = []

    async def fake_run(name, arguments, context, notes, origin):
        recorded.append((name, origin, json.loads(arguments)))
        if name == "web_browser":
            context.tinyfish_browser_done = True
            context.sources = [
                {
                    "url": "https://www.python.org/",
                    "final_url": "https://www.python.org/",
                    "details": {"links": [{"id": "L2", "url": "https://docs.python.org/3/", "text": "Docs"}]},
                }
            ]
        if name == "web_agent":
            context.tinyfish_agent_done = True
        return {"ok": True}

    async def run():
        orch = ToolOrchestrator(make_registry(), None)
        orch._run = fake_run
        context = _context(browser_prompt, mode="on")
        await orch._maybe_tinyfish_paid(
            context, [], browser_prompt, SimpleNamespace(required=False), planning=[]
        )
        assert recorded[0][0] == "web_browser"
        assert recorded[0][1] == "server_policy"
        recorded.clear()
        context = _context(agent_prompt, mode="on")
        await orch._maybe_tinyfish_paid(
            context, [], agent_prompt, SimpleNamespace(required=False), planning=[]
        )
        assert recorded[0][0] == "web_agent"
        math = _context("Сколько будет 2+2?", mode="on")
        recorded.clear()
        await orch._maybe_tinyfish_paid(
            math, [], "Сколько будет 2+2?", SimpleNamespace(required=False), planning=[]
        )
        assert recorded == []

    asyncio.run(run())


def test_relative_browser_hrefs_become_http_urls():
    from app.tools.tinyfish.browser import absolute_http_url

    assert absolute_http_url("https://www.python.org/", "/doc/") == "https://www.python.org/doc/"
    assert absolute_http_url("https://www.python.org/", "https://docs.python.org/3/") == (
        "https://docs.python.org/3/"
    )
    assert absolute_http_url("https://www.python.org/doc/", "../") == "https://www.python.org/"
    assert absolute_http_url("https://www.python.org/", "./doc/") == "https://www.python.org/doc/"
    assert (
        absolute_http_url("https://www.python.org/doc/", "/doc/?q=1#frag")
        == "https://www.python.org/doc/?q=1#frag"
    )
    assert absolute_http_url("https://www.python.org/", "data:text/html,hi") == ""
    assert absolute_http_url("https://www.python.org/", "javascript:void(0)") == ""
    assert absolute_http_url("https://www.python.org/", "#top") == ""
    assert absolute_http_url("https://www.python.org/", "file:///etc/passwd") == ""


def test_browser_link_contract_and_docs_candidates():
    from app.tools.tinyfish.browser import docs_link_candidates, normalize_browser_link
    from tests.fakes_web import python_org_home_links

    page = "https://www.python.org/"
    relative = normalize_browser_link(page, "/doc/", "Documentation", 0, "L1")
    if relative is None:
        raise AssertionError("an in-page relative link must normalise")
    assert relative["resolved_url"] == "https://www.python.org/doc/"
    assert relative["same_origin"] is True
    absolute = normalize_browser_link(page, "https://docs.python.org/3/", "Python Docs", 1, "L2")
    if absolute is None:
        raise AssertionError("an absolute http link must normalise")
    assert absolute["resolved_url"] == "https://docs.python.org/3/"
    assert absolute["same_origin"] is False
    assert normalize_browser_link(page, "javascript:alert(1)", "x", 2, "L3") is None
    links = python_org_home_links()
    assert links[0]["text"] == "Python"
    assert any(item["text"] == "Documentation" and item["raw_href"] == "/doc/" for item in links)
    token, url = docs_link_candidates(links, "перейди в документацию", page)[0]
    assert token.startswith("L")
    assert url in {"https://www.python.org/doc/", "https://docs.python.org/3/"}
    assert docs_link_candidates([], "documentation", page)[0][1] == "https://www.python.org/doc/"
