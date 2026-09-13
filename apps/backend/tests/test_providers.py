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
            return httpx.Response(200, json={"data": []})
        assert request.url.path == "/v1/chat/completions"
        assert json.loads(request.content)["model"] == "configured-model"
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


def test_llamacpp_rejects_truncated_stream(monkeypatch):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text='data: {"choices":[]}\n\n'))
    monkeypatch.setattr(
        providers.httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs)
    )
    provider = providers.LlamaCppProvider(Settings(_env_file=None))
    with pytest.raises(RuntimeError, match="completion marker"):
        asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))
