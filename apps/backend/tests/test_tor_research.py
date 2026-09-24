import asyncio
import json

import pytest
from fakes_tor import FakeSocks5hServer, FakeTorService, closed_port, dns_tripwire

from app.tools.contracts import ToolProvider, ToolResult
from app.tools.registry import make_registry
from app.tools.tor.browser import TorBrowserProvider, browser_status
from app.tools.tor.provider import TorFetchProvider

ONION = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion"
ROOT = "http://" + ONION + "/"
CHILD = "http://" + ONION + "/about"
CONTACT = "http://" + ONION + "/contact"


def next_link(url: str) -> str:
    path = (url or "").rstrip("/")
    if path.endswith("/about"):
        return CONTACT
    if path.endswith("/contact"):
        return "http://" + ONION + "/docs"
    return CHILD


class FakeTor(ToolProvider):
    def __init__(self, capability):
        self.capability = capability
        self.calls = []

    async def execute(self, args, context):
        self.calls.append(args.model_dump())
        if self.capability == "tor_search":
            if getattr(args, "query", "").startswith("empty-"):
                from app.tools.contracts import ToolError

                raise ToolError("tor_search_failed")
            return ToolResult(
                sources=[
                    {
                        "url": ROOT,
                        "final_url": ROOT,
                        "title": "Tor Project",
                        "excerpt": "Official onion search hit for Tor Project.",
                        "authority": "OFFICIAL_AND_REACHABLE",
                    }
                ]
            )
        if self.capability == "tor_browser":
            operation = getattr(args, "operation", "open")
            url = getattr(args, "url", None) or "https://check.torproject.org/"
            title = "Congratulations. This browser is configured to use Tor."
            excerpt = "Congratulations. This browser is configured to use Tor."
            links = [
                {
                    "id": "L1",
                    "url": "https://www.torproject.org/",
                    "text": "Tor Project",
                    "source_page": url,
                    "is_onion": False,
                    "is_clearnet": True,
                    "same_host": False,
                }
            ]
            if operation == "click":
                url = "https://www.torproject.org/"
                title = "Tor Project | Anonymity Online"
                excerpt = "Protect yourself against tracking and surveillance."
                links = [
                    {
                        "id": "L1",
                        "url": "https://www.torproject.org/download/",
                        "text": "Download",
                        "source_page": url,
                        "is_onion": False,
                        "is_clearnet": True,
                        "same_host": True,
                    }
                ]
            return ToolResult(
                text=excerpt,
                sources=[
                    {
                        "url": url,
                        "final_url": url,
                        "title": title,
                        "excerpt": excerpt,
                        "authority": "REACHABLE_UNVERIFIED",
                        "links": links,
                        "reachable": True,
                        "transport": "tor",
                        "retrieval": "browser",
                        "rendered": True,
                    }
                ],
                metadata={"started_by_alex": True, "retrieval": "browser", "rendered": True},
            )
        url = args.urls[0]
        nxt = next_link(url)
        return ToolResult(
            sources=[
                {
                    "url": url,
                    "final_url": url,
                    "title": "Tor Project page",
                    "excerpt": "Fetched through Tor. About page documents the official service.",
                    "authority": "OFFICIAL_AND_REACHABLE",
                    "links": [
                        {
                            "url": nxt,
                            "text": "Next",
                            "source_page": url,
                            "is_onion": True,
                            "is_clearnet": False,
                            "same_host": True,
                        }
                    ],
                    "reachable": True,
                    "transport": "tor",
                }
            ]
        )


def tor_registry():
    from dataclasses import replace

    from app.tools.contracts import ToolRegistry

    original, registry = make_registry(), ToolRegistry()
    for definition in original.definitions(auto_only=False):
        if definition.provider == "local_device":
            continue
        if definition.provider == "tor":
            registry.register(replace(definition), FakeTor(definition.capability))
        elif definition.capability in {"search", "fetch"}:
            from fakes_web import FakeWebProvider

            registry.register(replace(definition), FakeWebProvider(definition.capability))
    return registry


@pytest.fixture
def tor_tools(client, monkeypatch):
    registry = tor_registry()
    monkeypatch.setattr(client.app.state, "tools", registry)
    return registry


def test_model_driven_tor_search_fetch_and_follow(client, auth, tor_tools):
    headers = auth()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    response = client.post(
        f"/chats/{chat}/stream",
        headers=headers,
        json={
            "content": "Через Tor найди официальный onion-сервис Tor Project. Проверь его по официальному источнику.",
            "tor_mode": "auto",
            "web_mode": "off",
        },
    )
    assert "event: done" in response.text
    runs = client.get("/tools/runs", headers=headers).json()
    names = [row["tool_name"] for row in runs]
    origins = {(row["tool_name"], row.get("origin")) for row in runs}
    assert "tor_search" in names
    assert "tor_fetch" in names
    assert ("tor_search", "model") in origins
    assert names.count("tor_fetch") >= 2
    messages = client.get(f"/chats/{chat}/messages", headers=headers).json()
    key = messages[-1]["id"]
    sources = client.get(f"/messages/{key}/web-sources", headers=headers).json()
    assert any(row["label"].startswith("T") for row in sources)
    assert all(row["channel"] == "tor" for row in sources)
    assert any((row.get("details") or {}).get("transport") == "tor" for row in sources)


def test_server_policy_tor_search_when_planner_silent(client, auth, tor_tools, monkeypatch):
    class Silent:
        supports_tools = True

        async def plan_tools(self, messages, tools, usage):
            names = [item["function"]["name"] for item in tools]
            assert "tor_search" in names
            return {"tool_calls": []}

        async def stream_with_usage(self, messages, usage):
            yield "Local Tor fallback answer"

    monkeypatch.setattr(client.app.state, "provider", Silent())
    headers = auth()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    response = client.post(
        f"/chats/{chat}/stream",
        headers=headers,
        json={"content": "Через Tor найди официальный onion-сервис Tor Project.", "tor_mode": "auto"},
    )
    assert "event: done" in response.text
    runs = client.get("/tools/runs", headers=headers).json()
    origins = {(row["tool_name"], row.get("origin")) for row in runs}
    assert ("tor_search", "server_policy") in origins
    assert any(name == "tor_fetch" for name, _origin in origins)


def test_tor_off_never_calls_tools(client, auth, tor_tools):
    headers = auth()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    response = client.post(
        f"/chats/{chat}/stream",
        headers=headers,
        json={"content": "Через Tor найди onion", "tor_mode": "off", "web_mode": "off"},
    )
    assert "event: done" in response.text
    assert client.get("/tools/runs", headers=headers).json() == []


def test_continue_research_uses_previous_candidates(client, auth, tor_tools):
    headers = auth()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    first = client.post(
        f"/chats/{chat}/stream",
        headers=headers,
        json={"content": "Через Tor найди официальный onion-сервис Tor Project.", "tor_mode": "auto"},
    )
    assert "event: done" in first.text
    first_ids = {row["id"] for row in client.get("/tools/runs", headers=headers).json()}
    second = client.post(
        f"/chats/{chat}/stream",
        headers=headers,
        json={
            "content": "Продолжи поиск по найденным onion-ссылкам и проверь ещё два источника.",
            "tor_mode": "auto",
        },
    )
    assert "event: done" in second.text
    after = client.get("/tools/runs", headers=headers).json()
    new_runs = [row for row in after if row["id"] not in first_ids]
    assert any(row["tool_name"] == "tor_fetch" for row in new_runs)
    payloads = " ".join(str(row.get("input_summary")) for row in new_runs)
    assert "/contact" in payloads or "/docs" in payloads or "/about" in payloads


def test_query_refinement_stops_at_three_searches(client, auth, tor_tools, monkeypatch):
    class Refiner:
        supports_tools = True

        async def plan_tools(self, messages, tools, usage):
            previous = [m for m in messages if m.get("role") == "tool"]
            if len(previous) >= 3:
                return {"tool_calls": []}
            return {
                "tool_calls": [
                    {
                        "id": f"search-{len(previous)}",
                        "type": "function",
                        "function": {
                            "name": "tor_search",
                            "arguments": json.dumps({"query": f"tor project {len(previous)}"}),
                        },
                    }
                ]
            }

        async def stream_with_usage(self, messages, usage):
            yield "Refined Tor search"

    monkeypatch.setattr(client.app.state, "provider", Refiner())
    headers = auth()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    client.post(
        f"/chats/{chat}/stream",
        headers=headers,
        json={"content": "Через Tor найди onion-ресурсы Tor Project.", "tor_mode": "auto", "web_mode": "off"},
    )
    searches = [
        row for row in client.get("/tools/runs", headers=headers).json() if row["tool_name"] == "tor_search"
    ]
    assert 1 <= len(searches) <= 3


def test_loop_protection_skips_visited_fetch(client, auth, tor_tools, monkeypatch):
    class Looper:
        supports_tools = True

        async def plan_tools(self, messages, tools, usage):
            previous = [m for m in messages if m.get("role") == "tool"]
            if not previous:
                return {
                    "tool_calls": [
                        {
                            "id": "one",
                            "type": "function",
                            "function": {
                                "name": "tor_fetch",
                                "arguments": json.dumps({"urls": [ROOT]}),
                            },
                        }
                    ]
                }
            if len(previous) == 1:
                return {
                    "tool_calls": [
                        {
                            "id": "two",
                            "type": "function",
                            "function": {
                                "name": "tor_fetch",
                                "arguments": json.dumps({"urls": [ROOT]}),
                            },
                        }
                    ]
                }
            return {"tool_calls": []}

        async def stream_with_usage(self, messages, usage):
            yield "loop"

    monkeypatch.setattr(client.app.state, "provider", Looper())
    headers = auth()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    client.post(
        f"/chats/{chat}/stream",
        headers=headers,
        json={"content": "Через Tor открой onion сайт.", "tor_mode": "auto", "web_mode": "off"},
    )
    fetches = [
        row for row in client.get("/tools/runs", headers=headers).json() if row["tool_name"] == "tor_fetch"
    ]
    completed_root = [
        row
        for row in fetches
        if row["status"] == "completed"
        and ROOT.rstrip("/") in str(row.get("input_summary"))
        and "/about" not in str(row.get("input_summary"))
    ]
    assert len(completed_root) == 1
    assert any(row["status"] == "failed" for row in fetches)


def test_tor_browser_fallback_is_disabled_by_default():

    from app.tools.tor.browser import TorBrowserArgs

    status = browser_status()
    assert status["automatic"] is False
    with pytest.raises(Exception) as error:
        asyncio.run(
            TorBrowserProvider().execute(TorBrowserArgs(operation="open", url="https://example.org"), None)
        )
    assert error.value.args[0] == "tor_browser_disabled"


def _enable_browser(monkeypatch):
    monkeypatch.setattr("app.tools.tor.browser.automation_ready", lambda settings=None, prefs=None: True)


def test_explicit_tor_browser_is_model_driven(client, auth, tor_tools, monkeypatch):
    _enable_browser(monkeypatch)
    headers = auth()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    response = client.post(
        f"/chats/{chat}/stream",
        headers=headers,
        json={
            "content": "Через Tor Browser открой официальный сайт проверки Tor и перейди по одной ссылке.",
            "tor_mode": "auto",
            "web_mode": "off",
        },
    )
    assert "event: done" in response.text
    runs = client.get("/tools/runs", headers=headers).json()
    names = [row["tool_name"] for row in runs]
    assert "tor_browser" in names
    assert any(row["tool_name"] == "tor_browser" and row.get("origin") == "model" for row in runs)
    model_names = [row["tool_name"] for row in runs if row.get("origin") == "model"]
    assert model_names and model_names[0] == "tor_browser"
    messages = client.get(f"/chats/{chat}/messages", headers=headers).json()
    sources = client.get(f"/messages/{messages[-1]['id']}/web-sources", headers=headers).json()
    assert any(row["label"].startswith("T") for row in sources)
    assert any((row.get("details") or {}).get("retrieval") == "browser" for row in sources)
    assert all(row["channel"] == "tor" for row in sources)


def test_fetch_remains_primary_when_browser_ready(client, auth, tor_tools, monkeypatch):
    _enable_browser(monkeypatch)
    headers = auth()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    response = client.post(
        f"/chats/{chat}/stream",
        headers=headers,
        json={
            "content": "Через Tor найди официальный onion-сервис Tor Project. Проверь его по официальному источнику.",
            "tor_mode": "auto",
            "web_mode": "off",
        },
    )
    assert "event: done" in response.text
    runs = client.get("/tools/runs", headers=headers).json()
    names = [row["tool_name"] for row in runs]
    assert "tor_search" in names
    assert "tor_fetch" in names
    assert "tor_browser" not in names


def test_js_shell_fetch_triggers_browser_fallback(client, auth, monkeypatch):
    from dataclasses import replace

    from app.tools.contracts import ToolRegistry

    class JsShellTor(FakeTor):
        async def execute(self, args, context):
            if self.capability != "tor_fetch":
                return await super().execute(args, context)
            url = args.urls[0]
            return ToolResult(
                sources=[
                    {
                        "url": url,
                        "final_url": url,
                        "title": "App shell",
                        "excerpt": "Loading...",
                        "authority": "REACHABLE_UNVERIFIED",
                        "links": [],
                        "reachable": True,
                        "transport": "tor",
                        "needs_browser": True,
                        "retrieval": "http",
                        "rendered": False,
                    }
                ]
            )

    original, registry = make_registry(), ToolRegistry()
    for definition in original.definitions(auto_only=False):
        if definition.provider == "local_device":
            continue
        if definition.provider == "tor":
            registry.register(replace(definition), JsShellTor(definition.capability))
        elif definition.capability in {"search", "fetch"}:
            from fakes_web import FakeWebProvider

            registry.register(replace(definition), FakeWebProvider(definition.capability))
    monkeypatch.setattr(client.app.state, "tools", registry)
    _enable_browser(monkeypatch)

    class Silent:
        supports_tools = True

        async def plan_tools(self, messages, tools, usage):
            return {"tool_calls": []}

        async def stream_with_usage(self, messages, usage):
            yield "Fallback browser answer"

    monkeypatch.setattr(client.app.state, "provider", Silent())
    headers = auth()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    response = client.post(
        f"/chats/{chat}/stream",
        headers=headers,
        json={"content": "Через Tor найди onion-сервис Tor Project.", "tor_mode": "auto", "web_mode": "off"},
    )
    assert "event: done" in response.text
    runs = client.get("/tools/runs", headers=headers).json()
    origins = {(row["tool_name"], row.get("origin")) for row in runs}
    assert ("tor_fetch", "server_policy") in origins
    assert ("tor_browser", "server_policy") in origins
    messages = client.get(f"/chats/{chat}/messages", headers=headers).json()
    sources = client.get(f"/messages/{messages[-1]['id']}/web-sources", headers=headers).json()
    assert any((row.get("details") or {}).get("retrieval") == "browser" for row in sources)


def test_prompt_onion_url_is_fetched_without_search(client, auth, monkeypatch):
    from dataclasses import replace

    from app.tools.contracts import ToolRegistry

    class JsShellTor(FakeTor):
        async def execute(self, args, context):
            if self.capability != "tor_fetch":
                return await super().execute(args, context)
            url = args.urls[0]
            return ToolResult(
                sources=[
                    {
                        "url": url,
                        "final_url": url,
                        "title": "App shell",
                        "excerpt": "Loading...",
                        "authority": "REACHABLE_UNVERIFIED",
                        "links": [],
                        "reachable": True,
                        "transport": "tor",
                        "needs_browser": True,
                        "retrieval": "http",
                        "rendered": False,
                    }
                ]
            )

    original, registry = make_registry(), ToolRegistry()
    for definition in original.definitions(auto_only=False):
        if definition.provider == "local_device":
            continue
        if definition.provider == "tor":
            registry.register(replace(definition), JsShellTor(definition.capability))
        elif definition.capability in {"search", "fetch"}:
            from fakes_web import FakeWebProvider

            registry.register(replace(definition), FakeWebProvider(definition.capability))
    monkeypatch.setattr(client.app.state, "tools", registry)
    _enable_browser(monkeypatch)

    class Silent:
        supports_tools = True

        async def plan_tools(self, messages, tools, usage):
            return {"tool_calls": []}

        async def stream_with_usage(self, messages, usage):
            yield "Prompt onion fallback"

    monkeypatch.setattr(client.app.state, "provider", Silent())
    headers = auth()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    response = client.post(
        f"/chats/{chat}/stream",
        headers=headers,
        json={"content": f"Через Tor проверь {ROOT}", "tor_mode": "auto", "web_mode": "off"},
    )
    assert "event: done" in response.text
    runs = client.get("/tools/runs", headers=headers).json()
    names = [row["tool_name"] for row in runs]
    assert "tor_search" not in names
    assert "tor_fetch" in names
    assert "tor_browser" in names
    payloads = " ".join(str(row.get("input_summary")) for row in runs if row["tool_name"] == "tor_fetch")
    assert ONION in payloads
    assert any(row["tool_name"] == "tor_browser" and row.get("origin") == "server_policy" for row in runs)


def test_no_use_tor_browser_disables_browser_tools(client, auth, tor_tools, monkeypatch):
    _enable_browser(monkeypatch)
    headers = auth()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    response = client.post(
        f"/chats/{chat}/stream",
        headers=headers,
        json={
            "content": "Через Tor найди onion, но не используй Tor Browser.",
            "tor_mode": "auto",
            "web_mode": "off",
        },
    )
    assert "event: done" in response.text
    names = [row["tool_name"] for row in client.get("/tools/runs", headers=headers).json()]
    assert "tor_browser" not in names


# ------------------------------------- a named clearnet URL goes through Tor, or not at all


class SilentPlanner:
    """A planner that proposes nothing: every action below is the server's own policy."""

    supports_tools = True

    async def plan_tools(self, messages, tools, usage):
        return {"tool_calls": []}

    async def stream_with_usage(self, messages, usage):
        yield "Tor answer"


def tor_route_registry(web_fakes, *, fetch_provider=None):
    """The Tor tools (the real fetch provider by default) next to recording clearnet fakes."""
    from dataclasses import replace

    from fakes_web import FakeWebProvider

    from app.tools.contracts import ToolRegistry

    original, registry = make_registry(), ToolRegistry()
    for definition in original.definitions(auto_only=False):
        if definition.provider == "local_device":
            continue
        if definition.provider == "tor":
            provider = (
                fetch_provider
                if definition.name == "tor_fetch" and fetch_provider is not None
                else FakeTor(definition.capability)
            )
            registry.register(replace(definition), provider)
        elif definition.capability in {"search", "fetch"}:
            fake = FakeWebProvider(definition.capability)
            web_fakes.append(fake)
            registry.register(replace(definition), fake)
    return registry


def clearnet_runs(runs):
    return [row["tool_name"] for row in runs if row["tool_name"] in {"web_search", "web_fetch"}]


def test_named_clearnet_url_is_fetched_through_tor_over_socks5h(client, auth, monkeypatch, route_service):
    """«Открой … через Tor»: the server fetches the named URL through Canalla's own SOCKS5h route,
    and the run carries the proof — not just the chip."""
    web_fakes = []
    # The whole process may not resolve the destination: the name has to reach the proxy.
    attempts, _install_loop_guard = dns_tripwire(monkeypatch)
    with FakeSocks5hServer() as server:
        service = route_service(FakeTorService(server.endpoint, verified=True))
        monkeypatch.setattr(
            client.app.state,
            "tools",
            tor_route_registry(web_fakes, fetch_provider=TorFetchProvider()),
        )
        monkeypatch.setattr(client.app.state, "provider", SilentPlanner())
        headers = auth()
        chat = client.post("/chats", headers=headers, json={}).json()["id"]
        response = client.post(
            f"/chats/{chat}/stream",
            headers=headers,
            json={
                "content": "Открой http://example.com/ через Tor",
                "tor_mode": "auto",
                "web_mode": "on",
            },
        )
        assert "event: done" in response.text
        runs = client.get("/tools/runs", headers=headers).json()
        requests = list(server.requests)

    fetches = [row for row in runs if row["tool_name"] == "tor_fetch"]
    assert len(fetches) == 1, runs
    assert fetches[0]["status"] == "completed"
    assert fetches[0]["origin"] == "server_policy"
    # Bytes on the wire: the clearnet name reached the proxy as a name (ATYP 3) on port 80.
    assert requests == [("example.com", 80, 3)]
    metadata = fetches[0]["result_metadata"]
    assert metadata["transport"] == "tor-socks5h"
    assert metadata["socks"]["atyp"] == 3
    assert metadata["socks"]["local_dns"] is False
    assert metadata["socks"]["dest_host"] == "example.com"
    assert metadata["route"] == "TOR_ONLY"
    # The circuit proof rides on the run itself.
    assert metadata["verified_chain"] is True
    assert metadata["verified_at"] == "2026-09-24T00:00:00+00:00"
    assert metadata["managed"] is True
    assert service.snapshot_calls >= 1
    # No clearnet tool was invoked, even though web mode was fully on.
    assert clearnet_runs(runs) == []
    assert web_fakes and all(fake.calls == [] for fake in web_fakes)
    assert attempts == []


def test_bare_host_is_normalised_to_https_and_retried_once_inside_tor(
    client, auth, monkeypatch, route_service
):
    """A bare `example.com` becomes `https://example.com`, is sent to the proxy as a name, and one
    bounded recovery may retry it — still inside Tor."""
    web_fakes = []
    attempts, _install_loop_guard = dns_tripwire(monkeypatch)
    with FakeSocks5hServer() as server:
        # The fake origin answers HTTP only, so the https leg cannot complete; that is fine — the
        # assertion is that the destination travelled as a name and that the retry stayed in Tor.
        service = route_service(FakeTorService(server.endpoint, verified=False, recover=True))
        monkeypatch.setattr(
            client.app.state,
            "tools",
            tor_route_registry(web_fakes, fetch_provider=TorFetchProvider()),
        )
        monkeypatch.setattr(client.app.state, "provider", SilentPlanner())
        headers = auth()
        chat = client.post("/chats", headers=headers, json={}).json()["id"]
        response = client.post(
            f"/chats/{chat}/stream",
            headers=headers,
            json={
                "content": "Открой example.com через Tor",
                "tor_mode": "auto",
                "web_mode": "on",
            },
        )
        assert "event: done" in response.text
        runs = client.get("/tools/runs", headers=headers).json()
        requests = list(server.requests)

    fetches = [row for row in runs if row["tool_name"] == "tor_fetch"]
    assert len(fetches) == 1, runs
    assert "https://example.com" in json.dumps(fetches[0]["input_summary"])
    assert requests == [("example.com", 443, 3), ("example.com", 443, 3)]
    assert fetches[0]["error_code"] == "tor_unavailable"
    assert service.recovery_calls == 1
    assert clearnet_runs(runs) == []
    assert web_fakes and all(fake.calls == [] for fake in web_fakes)
    assert attempts == []


def test_tor_unavailable_fails_the_action_without_clearnet_fallback(client, auth, monkeypatch, route_service):
    """No listener at all: the named URL still fails closed with a typed code and zero clearnet
    runs — bounded recovery once, then nothing else."""
    web_fakes = []
    attempts, _install_loop_guard = dns_tripwire(monkeypatch)
    service = route_service(FakeTorService(("127.0.0.1", closed_port()), verified=False))
    monkeypatch.setattr(
        client.app.state,
        "tools",
        tor_route_registry(web_fakes, fetch_provider=TorFetchProvider()),
    )
    monkeypatch.setattr(client.app.state, "provider", SilentPlanner())
    headers = auth()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    response = client.post(
        f"/chats/{chat}/stream",
        headers=headers,
        json={"content": "Открой example.com через Tor", "tor_mode": "auto", "web_mode": "on"},
    )
    assert "event: done" in response.text
    runs = client.get("/tools/runs", headers=headers).json()
    fetches = [row for row in runs if row["tool_name"] == "tor_fetch"]
    assert len(fetches) == 1, runs
    assert fetches[0]["status"] == "failed"
    assert fetches[0]["error_code"] == "tor_unavailable"
    assert fetches[0]["result_metadata"].get("transport") is None
    assert service.recovery_calls == 1
    assert 0 < service.deadlines[0] <= 60
    assert clearnet_runs(runs) == []
    assert all(fake.calls == [] for fake in web_fakes)
    assert attempts == []


def test_explicit_clearnet_tool_under_a_tor_route_is_typed_unsupported(client, auth, monkeypatch):
    """The explicit path cannot bypass the route: a capability that cannot use local Tor answers
    `tor_route_unsupported`, while the same call in a chat without a Tor route still works."""
    web_fakes = []
    monkeypatch.setattr(client.app.state, "tools", tor_route_registry(web_fakes))
    headers = auth()
    tor_chat = client.post("/chats", headers=headers, json={}).json()["id"]
    plain_chat = client.post("/chats", headers=headers, json={}).json()["id"]
    for chat, content in (
        (tor_chat, "Открой example.com через Tor"),
        (plain_chat, "Прочитай страницу примера"),
    ):
        assert (
            "event: done"
            in client.post(
                f"/chats/{chat}/stream",
                headers=headers,
                json={"content": content, "tor_mode": "auto", "web_mode": "off"},
            ).text
        )
    arguments = {"urls": ["https://example.com/"]}

    # The chat asked for Tor, so the explicit call inherits that route and is refused.
    derived = client.post(
        "/tools/execute",
        headers=headers,
        json={"chat_id": tor_chat, "name": "web_fetch", "arguments": arguments},
    )
    assert "event: tool_error" in derived.text
    assert '"code": "tor_route_unsupported"' in derived.text
    # A body may require the Tor route, and cannot claim an unknown one.
    declared = client.post(
        "/tools/execute",
        headers=headers,
        json={
            "chat_id": plain_chat,
            "name": "web_fetch",
            "arguments": arguments,
            "network_route": "TOR_ONLY",
        },
    )
    assert '"code": "tor_route_unsupported"' in declared.text
    unknown = client.post(
        "/tools/execute",
        headers=headers,
        json={
            "chat_id": plain_chat,
            "name": "web_fetch",
            "arguments": arguments,
            "network_route": "DIRECT",
        },
    )
    assert unknown.status_code == 422
    # Same tool, no Tor route: it runs, so the block is about the route and not the capability.
    allowed = client.post(
        "/tools/execute",
        headers=headers,
        json={"chat_id": plain_chat, "name": "web_fetch", "arguments": arguments},
    )
    assert "event: tool_result" in allowed.text
    called = [fake for fake in web_fakes if fake.calls]
    assert len(called) == 1 and called[0].capability == "fetch"
