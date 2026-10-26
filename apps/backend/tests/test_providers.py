import asyncio
import json

import httpx
import pytest

from app import providers
from tests.settings_factory import make_settings


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
        make_settings(
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
        make_settings(
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


def test_llamacpp_reports_a_silent_stream_as_a_stall(monkeypatch):
    async def body():
        yield b'data: {"choices":[{"delta":{"content":"\xd0\x9f"}}]}\n\n'
        await asyncio.sleep(5)
        yield b"data: [DONE]\n\n"

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body()))
    original = httpx.AsyncClient
    monkeypatch.setattr(
        providers.httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs)
    )
    provider = providers.LlamaCppProvider(make_settings(_env_file=None, llm_stream_stall_seconds=0.05))
    with pytest.raises(providers.LLMError) as error:
        asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))
    assert error.value.code == "llm_stalled"


def test_iter_stream_lines_logs_the_gap_and_the_stall_point(caplog):
    """A silent stream must leave evidence of *where* it stopped, not only that it did."""
    import logging

    async def lines():
        yield "first"
        await asyncio.sleep(0.05)
        yield "second"
        await asyncio.sleep(0.3)

    async def collect():
        return [
            line
            async for line in providers.iter_stream_lines(
                lines(), stall_seconds=0.2, what="probe", slow_gap_seconds=0.01
            )
        ]

    with caplog.at_level(logging.WARNING, logger="app.providers"):
        with pytest.raises(providers.LLMError) as error:
            asyncio.run(collect())

    assert error.value.code == "llm_stalled"
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "llm_stream_gap what=probe after_line=1" in logged
    assert "llm_stream_stalled what=probe lines=2" in logged
    assert "first_line_ms=" in logged


def test_a_silent_first_line_is_a_retryable_timeout_not_a_stall(caplog):
    """Headers arrived and then nothing: no token exists, so the turn may safely retry.

    The 25 Oct lost-response class looked exactly like this. Raising `llm_stalled` would
    have told the user to "send the message again" for a server-side failure, and waiting
    the whole transport budget burned most of the scenario's window.
    """
    import logging

    async def lines():
        await asyncio.sleep(5)
        yield "data: {}"

    async def collect():
        return [
            line
            async for line in providers.iter_stream_lines(
                lines(), stall_seconds=0.2, what="probe", first_line_seconds=0.05
            )
        ]

    with caplog.at_level(logging.WARNING, logger="app.providers"):
        with pytest.raises(providers.LLMError) as error:
            asyncio.run(collect())

    assert error.value.code == "llm_timeout"
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "llm_stream_first_line_timeout what=probe" in logged
    assert "stage=extended" in logged, "the cold-prefill allowance is what finally gives up"


def test_a_cold_prefill_gets_one_bounded_extension(caplog):
    """The first request after `ready` may still be prefilling and produce no bytes for a while.

    The initial budget still detects a dead Gateway quickly, and because nothing was produced the
    same request may wait once more, bounded by the stall budget - no replay, no duplicate work.
    """
    import logging

    async def lines():
        await asyncio.sleep(0.12)
        yield "data: {}"
        yield "data: [DONE]"

    async def collect():
        return [
            line
            async for line in providers.iter_stream_lines(
                lines(), stall_seconds=0.5, what="probe", first_line_seconds=0.05
            )
        ]

    with caplog.at_level(logging.WARNING, logger="app.providers"):
        assert asyncio.run(collect()) == ["data: {}", "data: [DONE]"]

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "stage=initial" in logged, "the extension is recorded, not silent"
    assert "stage=extended" not in logged


def test_a_slow_first_line_keeps_the_transport_budget_when_unset():
    """Direct llama.cpp mode passes no first-line budget; prefill must not be cut short."""

    async def lines():
        await asyncio.sleep(0.2)
        yield "data: {}"
        yield "data: [DONE]"

    async def collect():
        return [line async for line in providers.iter_stream_lines(lines(), stall_seconds=0.05, what="probe")]

    assert asyncio.run(collect()) == ["data: {}", "data: [DONE]"]


def test_llamacpp_rejects_truncated_stream(monkeypatch):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text='data: {"choices":[]}\n\n'))
    monkeypatch.setattr(
        providers.httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs)
    )
    provider = providers.LlamaCppProvider(make_settings(_env_file=None))
    with pytest.raises(RuntimeError, match="completion marker"):
        asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))
