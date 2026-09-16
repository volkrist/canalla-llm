import asyncio
import json

import httpx
import pytest
from fakes_web import fake_registry, public_dns
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import MessageContext
from app.providers import LlamaCppProvider
from app.tools.models import ToolRun


def test_llamacpp_tools_contract_and_usage():
    captured = []

    def handler(request):
        value = json.loads(request.content)
        captured.append(value)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call1",
                                    "type": "function",
                                    "function": {
                                        "name": "web_search",
                                        "arguments": '{"query":"current docs"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
            },
        )

    provider = LlamaCppProvider(get_settings(), transport=httpx.MockTransport(handler))
    usage = {}
    result = asyncio.run(
        provider.plan_tools(
            [{"role": "user", "content": "current docs"}],
            [{"type": "function", "function": {"name": "web_search"}}],
            usage,
        )
    )
    assert provider.supports_tools and result["tool_calls"][0]["function"]["name"] == "web_search"
    assert captured[0]["stream"] is False and captured[0]["tool_choice"] == "auto"
    assert captured[0]["model"] == "orcarouter-qwen38-27b-q5km"
    assert usage["total_tokens"] == 40


@pytest.fixture
def fake_tools(client, monkeypatch):
    registry = fake_registry()
    monkeypatch.setattr(client.app.state, "tools", registry)
    monkeypatch.setattr(client.app.state, "tool_dns_override", public_dns, raising=False)
    return registry


def test_search_fetch_stream_snapshots_and_isolation(client, auth, fake_tools):
    headers = auth()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    response = client.post(
        f"/chats/{chat}/stream", headers=headers, json={"content": "Current Aurora docs", "web_mode": "on"}
    )
    assert response.status_code == 200 and "event: done" in response.text
    assert "event: tool" in response.text
    messages = client.get(f"/chats/{chat}/messages", headers=headers).json()
    key = messages[-1]["id"]
    assert "W1" in messages[-1]["content"]
    sources = client.get(f"/messages/{key}/web-sources", headers=headers).json()
    assert [s["label"] for s in sources] == ["W1", "W2"]
    assert "adversarial" in sources[0]["excerpt"]
    assert len(client.get("/tools/runs", headers=headers).json()) == 2
    other = auth("different@example.com")
    assert client.get(f"/messages/{key}/web-sources", headers=other).status_code == 404
    with SessionLocal() as db:
        context = db.get(MessageContext, key).snapshot
        assert context["web_source_count"] == 2 and context["web_mode"] == "on"
        assert db.scalars(select(ToolRun)).all()[0].status == "completed"


def test_off_never_calls_tools(client, auth, fake_tools):
    headers = auth()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    response = client.post(
        f"/chats/{chat}/stream",
        headers=headers,
        json={"content": "Current news https://example.com", "web_mode": "off"},
    )
    assert "event: done" in response.text
    assert client.get("/tools/runs", headers=headers).json() == []
    assert fake_tools.get("web_search")[1].calls == []


def test_missing_key_chat_still_completes(client, auth):
    headers = auth()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    response = client.post(
        f"/chats/{chat}/stream", headers=headers, json={"content": "Current docs", "web_mode": "on"}
    )
    assert "provider_not_configured" in response.text and "event: done" in response.text
    assert "test-only-provider-credential" not in response.text


def test_planner_never_receives_private_memory_or_documents(client, auth, fake_tools, monkeypatch):
    from app.providers import MockLLMProvider

    original = MockLLMProvider.plan_tools

    async def inspect(self, messages, tools, usage):
        assert "private-context-marker" not in json.dumps(messages)
        return await original(self, messages, tools, usage)

    monkeypatch.setattr(MockLLMProvider, "plan_tools", inspect)
    headers = auth()
    # The final generation gets user instructions; the planner gets only the current question.
    with SessionLocal() as db:
        from app.models import User

        user = db.scalar(select(User))
        user.custom_instructions = "private-context-marker"
        db.commit()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    response = client.post(
        f"/chats/{chat}/stream", headers=headers, json={"content": "Current docs", "web_mode": "on"}
    )
    assert "event: done" in response.text
