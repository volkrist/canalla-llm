import asyncio
import json

import httpx
import pytest

from app.providers import LlamaCppProvider, LLMError
from tests.db_helpers import require_scalar
from tests.settings_factory import make_settings


def provider(handler):
    return LlamaCppProvider(make_settings(_env_file=None), transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "status, payload, expected",
    [
        (503, {}, "loading_model"),
        (401, {}, "connection_auth_failed"),
        (200, [], "malformed_response"),
        (200, {"data": []}, "model_mismatch"),
        (200, {"data": [{"id": "orcarouter-qwen38-27b-q5km"}]}, "ready"),
    ],
)
def test_models_requires_real_alias(status, payload, expected):
    llm = provider(lambda _: httpx.Response(status, json=payload))
    assert asyncio.run(llm.status()) == expected


def test_usage_is_reported_not_estimated():
    def response(request):
        assert json.loads(request.content)["stream_options"] == {"include_usage": True}
        events = [
            {"choices": [{"delta": {"content": "Привет"}}]},
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}},
        ]
        return httpx.Response(
            200, text="".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
        )

    usage = {}

    async def run():
        return "".join([part async for part in provider(response).stream_with_usage([], usage)])

    assert asyncio.run(run()) == "Привет"
    assert usage == {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16}


class SlowStream(httpx.AsyncByteStream):
    closed = False
    # Armed by the test before the stream is iterated.
    waiting: asyncio.Event

    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        self.waiting.set()
        await asyncio.sleep(300)

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("cancel", [False, True])
def test_stop_closes_upstream_immediately(cancel):
    stream = SlowStream()
    llm = provider(lambda _: httpx.Response(200, stream=stream))

    async def run():
        stream.waiting = asyncio.Event()
        iterator = llm.stream_chat([])
        assert await anext(iterator) == "partial"
        if cancel:
            pending = asyncio.create_task(anext(iterator))
            await stream.waiting.wait()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        await iterator.aclose()
        assert stream.closed

    asyncio.run(run())


@pytest.mark.parametrize(
    "body",
    [
        "data: not-json\n\n",
        "data: []\n\n",
        'data: {"choices":{}}\n\n',
        'data: {"choices":[{"delta":{"content":12}}]}\n\n',
    ],
)
def test_malformed_stream_is_sanitized(body):
    llm = provider(lambda _: httpx.Response(200, text=body))
    with pytest.raises(LLMError) as error:
        asyncio.run(llm.chat([]))
    assert error.value.code == "malformed_response"


@pytest.mark.parametrize(
    "failure, code",
    [
        (httpx.ReadTimeout("private URL"), "llm_timeout"),
        (httpx.ReadError("private URL"), "stream_interrupted"),
    ],
)
def test_transport_errors_do_not_leak_target(failure, code):
    def fail(_):
        raise failure

    with pytest.raises(LLMError) as error:
        asyncio.run(provider(fail).chat([]))
    assert error.value.code == code and "private" not in str(error.value)


def test_target_is_resolved_for_each_request():
    target = ["https://pod-one-9000.proxy.runpod.net"]
    hosts = []

    def response(request):
        hosts.append(request.url.host)
        return httpx.Response(503)

    llm = LlamaCppProvider(
        make_settings(_env_file=None), target=lambda: target[0], transport=httpx.MockTransport(response)
    )
    asyncio.run(llm.status())
    target[0] = "https://pod-two-9000.proxy.runpod.net"
    asyncio.run(llm.status())
    assert hosts == ["pod-one-9000.proxy.runpod.net", "pod-two-9000.proxy.runpod.net"]


def test_real_provider_api_persists_usage(client, auth, monkeypatch):
    from sqlalchemy import select

    from app.compute.models import GenerationUsage
    from app.config import get_settings
    from app.database import SessionLocal
    from app.main import app

    headers = auth()

    def response(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "orcarouter-qwen38-27b-q5km"}]})
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"Настоящий протокол"}}]}\n\ndata: {"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3,"total_tokens":11}}\n\ndata: [DONE]\n\n',
        )

    monkeypatch.setattr(app.state, "provider", provider(response))
    monkeypatch.setattr(app.state, "provider_name", "llamacpp")
    monkeypatch.setattr(get_settings(), "llm_provider", "llamacpp")
    monkeypatch.setattr(get_settings(), "llm_connection_mode", "static")
    state = client.get("/llm/status", headers=headers).json()
    assert state["available"] and state["state"] == "ready"
    assert "url" not in str(state).lower() and "key" not in str(state).lower()
    chat = client.post("/chats", headers=headers, json={}).json()["id"]
    result = client.post(f"/chats/{chat}/stream", headers=headers, json={"content": "Привет"})
    assert "event: done" in result.text
    with SessionLocal() as db:
        usage = require_scalar(db, select(GenerationUsage))
        assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (8, 3, 11)
        assert usage.provider == "llamacpp" and usage.message_id
    totals = client.get("/compute/usage/me", headers=headers).json()["periods"]["all"]
    assert totals["total_tokens"] == 11
