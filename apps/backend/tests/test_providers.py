import asyncio
import json

import httpx
import pytest

from app import providers
from app.config import Settings


def test_llamacpp_transport_without_network(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer backend-only-test-key"
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "configured-model"}]})
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "configured-model"
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        if "/chat/completions" in str(request.url) and body.get("stream"):
            assert body["stream"] is True
            assert "tools" not in body
        return httpx.Response(
            200, text=': heartbeat\n\ndata: {"choices":[{"delta":{"content":"Привет"}}]}\n\ndata: [DONE]\n\n'
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    provider = providers.LlamaCppProvider(
        Settings(
            _env_file=None,
            llm_base_url="http://inference.invalid/v1",
            llm_model="configured-model",
            llm_api_key="backend-only-test-key",
        )
    )
    assert asyncio.run(provider.health()) is True
    assert asyncio.run(provider.chat([{"role": "user", "content": "hello"}])) == "Привет"
    assert len(calls) == 2


def test_llamacpp_plan_tools_disables_thinking_and_keeps_tools(monkeypatch):
    captured = []

    def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "configured-model"}]})
        body = json.loads(request.content)
        captured.append(body)
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert body["stream"] is False
        assert body["tools"]
        assert body["tool_choice"] == "auto"
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
                                    "id": "call-tor",
                                    "type": "function",
                                    "function": {
                                        "name": "tor_search",
                                        "arguments": '{"query":"tor project onion"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    provider = providers.LlamaCppProvider(
        Settings(
            _env_file=None,
            llm_base_url="http://inference.invalid/v1",
            llm_model="configured-model",
            llm_api_key="backend-only-test-key",
        )
    )
    usage = {}
    result = asyncio.run(
        provider.plan_tools(
            [{"role": "user", "content": "Через Tor найди onion"}],
            [{"type": "function", "function": {"name": "tor_search"}}],
            usage,
        )
    )
    assert result["tool_calls"][0]["function"]["name"] == "tor_search"
    assert captured[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "tools" in captured[0]
    assert usage["total_tokens"] == 12


def test_llamacpp_rejects_truncated_stream(monkeypatch):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text='data: {"choices":[]}\n\n'))
    monkeypatch.setattr(
        providers.httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs)
    )
    provider = providers.LlamaCppProvider(Settings(_env_file=None))
    with pytest.raises(RuntimeError, match="completion marker"):
        asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))
