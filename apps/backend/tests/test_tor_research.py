import asyncio
import json

import pytest

from app.tools.contracts import ToolResult
from app.tools.registry import make_registry
from app.tools.tor.browser import TorBrowserProvider, browser_status

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


class FakeTor:
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
