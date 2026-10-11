"""Inference proxy: readiness gate, streaming, cancel, queue bound, replay safety."""

from __future__ import annotations

import asyncio
import json

from conftest import POD_KEY, auth_header, enroll, ensure_body, run, wait_for

from gateway.errors import GatewayError

CHAT = {"model": "orcarouter-qwen38-27b-q5km", "messages": [{"role": "user", "content": "привет"}]}
FIRST_DELTA = "Привет".encode("utf-8")
DONE = b"[DONE]"


def ready_compute(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    response = client.post(
        "/compute/ensure", json=ensure_body(operation_id="op-infer-0000001"), headers=headers
    )
    assert response.status_code == 200
    run(gateway.authority.tick())
    assert client.get("/compute/status", headers=headers).json()["state"] == "ready"
    return installation, headers


def test_inference_is_refused_without_ready_compute(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    response = client.post("/v1/chat/completions", json=CHAT, headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == "compute_offline"
    assert gateway.llama.requests == []

    # A compute that exists but is not ready yet is still not usable.
    client.post("/compute/ensure", json=ensure_body(operation_id="op-infer-0000002"), headers=headers)
    response = client.post("/v1/chat/completions", json=CHAT, headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == "compute_offline"


def test_models_probe_reports_the_model_alias_only(gateway, client):
    installation, headers = ready_compute(gateway, client)
    response = client.get("/v1/models", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["data"]] == ["orcarouter-qwen38-27b-q5km"]
    serialized = response.text + json.dumps(dict(response.headers))
    assert POD_KEY not in serialized
    assert "runpod" not in serialized.lower()
    assert "pod-1" not in serialized


def test_non_stream_completion_is_proxied_without_provider_details(gateway, client):
    installation, headers = ready_compute(gateway, client)
    response = client.post("/v1/chat/completions", json=CHAT, headers={**headers, "X-Alex-Task-Id": "task-9"})
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "Привет, мир"
    assert len(gateway.llama.requests) == 1
    assert response.headers["X-Alex-Request-Id"]
    serialized = response.text + json.dumps(dict(response.headers))
    assert POD_KEY not in serialized
    assert "proxy.runpod.net" not in serialized
    assert gateway.llama.requests[0]["messages"][0]["content"] == "привет"


def test_streaming_completion_is_forwarded_as_sse(gateway, client):
    installation, headers = ready_compute(gateway, client)
    response = client.post("/v1/chat/completions", json={**CHAT, "stream": True}, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"Привет"' in response.text
    assert "[DONE]" in response.text
    assert gateway.llama.stream_started is True


def test_streaming_is_incremental_and_not_buffered(gateway, client):
    """The proxy must forward a chunk before the upstream answer is complete."""
    installation, headers = ready_compute(gateway, client)
    gate = asyncio.Event()
    gateway.llama.gate = gate

    async def scenario():
        response = await gateway.proxy.completions(
            {**CHAT, "stream": True},
            installation_id=installation["installation_id"],
            task_id="task-incremental",
            request_id="req-incremental-1",
        )
        iterator = response.body_iterator
        first = await iterator.__anext__()
        # The upstream is still blocked on its second delta (its stream is open and the
        # gate is closed): the first delta was already forwarded to the client.
        assert not gate.is_set()
        assert gateway.llama.stream_closed is False
        assert gateway.llama.pending_second_chunk is False
        gate.set()
        rest = []
        async for chunk in iterator:
            rest.append(chunk)
        return first, b"".join(rest)

    first, rest = run(scenario())
    assert FIRST_DELTA in first
    assert DONE in rest
    assert gateway.proxy.queue.active() == 0
    assert gateway.proxy.queue._slot.locked() is False


def test_client_disconnect_cancels_the_upstream(gateway, client):
    installation, headers = ready_compute(gateway, client)
    gate = asyncio.Event()
    gateway.llama.gate = gate

    async def scenario():
        response = await gateway.proxy.completions(
            {**CHAT, "stream": True},
            installation_id=installation["installation_id"],
            task_id=None,
            request_id="req-disconnect-1",
        )
        iterator = response.body_iterator
        first = await iterator.__anext__()
        # Closing the body is exactly what a dropped client connection does.
        await iterator.aclose()
        released = await wait_for(lambda: gateway.proxy.queue.active() == 0)
        return first, released

    first, released = run(scenario())
    assert FIRST_DELTA in first
    assert released is True
    assert gateway.proxy.queue._slot.locked() is False
    assert gateway.llama.stream_closed is True
    assert (
        gateway.proxy._lookup(
            gateway.proxy._key(installation["installation_id"], "req-disconnect-1"),
            gateway.proxy._digest({**CHAT, "stream": True}),
        )["state"]
        == "cancelled"
    )


def test_queue_full_and_busy_are_reported_with_stable_codes(gateway, client):
    installation, headers = ready_compute(gateway, client)
    gateway.proxy.queue.size = 0

    async def scenario():
        from httpx import ASGITransport, AsyncClient

        await gateway.proxy.queue.acquire()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=gateway.app), base_url="http://gateway.test", timeout=30
            ) as async_client:
                return await async_client.post("/v1/chat/completions", json=CHAT, headers=headers)
        finally:
            gateway.proxy.queue.release()

    response = run(scenario())
    assert response.status_code == 429
    assert response.json()["code"] == "gateway_queue_full"
    assert gateway.llama.requests == []


def test_queue_timeout_reports_busy(gateway, client):
    installation, headers = ready_compute(gateway, client)
    gateway.proxy.queue.size = 4
    gateway.proxy.queue.timeout = 0.05

    async def scenario():
        from httpx import ASGITransport, AsyncClient

        await gateway.proxy.queue.acquire()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=gateway.app), base_url="http://gateway.test", timeout=30
            ) as async_client:
                return await async_client.post("/v1/chat/completions", json=CHAT, headers=headers)
        finally:
            gateway.proxy.queue.release()

    response = run(scenario())
    assert response.status_code == 503
    assert response.json()["code"] == "gateway_busy"
    gateway.proxy.queue.timeout = 120


def test_replaying_a_request_id_never_generates_twice(gateway, client):
    installation, headers = ready_compute(gateway, client)
    request_id = "req-replay-000001"
    first = client.post(
        "/v1/chat/completions", json=CHAT, headers={**headers, "X-Alex-Request-Id": request_id}
    )
    assert first.status_code == 200
    gateway.llama.requests.clear()
    replay = client.post(
        "/v1/chat/completions", json=CHAT, headers={**headers, "X-Alex-Request-Id": request_id}
    )
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert gateway.llama.requests == []

    gateway.proxy._remember(
        gateway.proxy._key(installation["installation_id"], "req-inflight-0001"),
        "in_flight",
        gateway.proxy._digest(CHAT),
    )
    inflight = client.post(
        "/v1/chat/completions", json=CHAT, headers={**headers, "X-Alex-Request-Id": "req-inflight-0001"}
    )
    assert inflight.status_code == 409
    assert inflight.json()["code"] == "gateway_request_in_flight"
    assert gateway.llama.requests == []


def test_a_streaming_request_id_conflicts_on_a_different_body(gateway, client):
    """The streaming and non-stream paths share one identity rule (A03)."""
    installation, headers = ready_compute(gateway, client)
    request_id = "req-conflict-stream"
    first = client.post(
        "/v1/chat/completions",
        json={**CHAT, "stream": True},
        headers={**headers, "X-Alex-Request-Id": request_id},
    )
    assert first.status_code == 200

    other = {**CHAT, "stream": True, "messages": [{"role": "user", "content": "другой"}]}
    conflict = client.post(
        "/v1/chat/completions", json=other, headers={**headers, "X-Alex-Request-Id": request_id}
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "gateway_request_conflict"
    assert gateway.llama.requests and len(gateway.llama.requests) == 1


def test_two_installations_with_the_same_request_id_are_isolated(gateway, client):
    """A03: the replay identity includes the installation, so a shared id is not a shared answer."""
    first_installation, first_headers = ready_compute(gateway, client)
    second = enroll(gateway, client, label="PC B")
    second_headers = auth_header(client, second)
    request_id = "req-shared-000001"

    a = client.post(
        "/v1/chat/completions", json=CHAT, headers={**first_headers, "X-Alex-Request-Id": request_id}
    )
    assert a.status_code == 200
    gateway.llama.requests.clear()

    # The same id and body from another installation is a separate request: it generates on its
    # own and is never served the first installation's cached answer.
    b = client.post(
        "/v1/chat/completions", json=CHAT, headers={**second_headers, "X-Alex-Request-Id": request_id}
    )
    assert b.status_code == 200
    assert len(gateway.llama.requests) == 1


def test_upstream_failure_is_reported_without_leaking_the_response(gateway, client):
    installation, headers = ready_compute(gateway, client)
    gateway.llama.failure = 500
    response = client.post("/v1/chat/completions", json=CHAT, headers=headers)
    assert response.status_code == 503
    assert response.json()["code"] == "gateway_unavailable"
    assert "internal upstream" not in response.text
    assert "500" in response.json()["detail"]
    gateway.llama.failure = None


def test_rejected_pod_credential_is_reported_not_leaked(gateway, client):
    installation, headers = ready_compute(gateway, client)
    gateway.llama.expected_key = "another-key-" + "z" * 32
    response = client.post("/v1/chat/completions", json=CHAT, headers=headers)
    assert response.status_code == 503
    assert response.json()["code"] == "gateway_unavailable"
    assert POD_KEY not in response.text
    gateway.llama.expected_key = POD_KEY


def test_invalid_json_body_is_rejected_as_a_request_error(gateway, client):
    installation, headers = ready_compute(gateway, client)
    response = client.post(
        "/v1/chat/completions", content=b"not json", headers={**headers, "Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "gateway_invalid_request"


def test_generation_marks_shared_activity(gateway, client):
    installation, headers = ready_compute(gateway, client)
    client.post("/v1/chat/completions", json=CHAT, headers=headers)
    with gateway.sessions() as db:
        from gateway.models import GatewayCompute

        control = db.get(GatewayCompute, 1)
        assert control.last_activity_at is not None
        assert control.last_activity_at.replace(tzinfo=None) == gateway.runpod.time.replace(tzinfo=None)


def test_generation_is_not_stopping_compute(gateway, client):
    installation, headers = ready_compute(gateway, client)
    client.post("/v1/chat/completions", json=CHAT, headers=headers)
    assert gateway.runpod.actions == []
    assert client.get("/compute/status", headers=headers).json()["state"] in {"ready", "generating"}


def test_stop_waits_for_a_generation_in_flight(gateway, client):
    installation, headers = ready_compute(gateway, client)
    gateway.proxy.queue._running = 1  # simulate one admitted generation
    response = client.post("/compute/stop", json={"operation_id": "op-stop-0000001"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["state"] == "stopping"
    assert gateway.runpod.actions == []

    gateway.proxy.queue.release()
    run(gateway.authority.tick())
    assert gateway.runpod.actions == [{"action": "terminate"}]


def test_passthrough_endpoints_do_not_exist(gateway, client):
    installation, headers = ready_compute(gateway, client)
    for path in ("/runpod/graphql", "/runpod/request", "/provider/raw", "/pods"):
        assert client.get(path, headers=headers).status_code == 404
        assert client.post(path, json={}, headers=headers).status_code == 404
    assert gateway.runpod.creates and len(gateway.runpod.creates) == 1


# --------------------------------------------- A03/A13: one installation, one generation


def test_another_installation_never_receives_a_cached_answer(gateway, client):
    """A03: replay identity is the installation *plus* the request id.

    A request id is chosen by the client, so a shared id space would let installation B read
    installation A's answer out of the cache. B must generate instead.
    """
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    client.post("/compute/ensure", json=ensure_body(operation_id="op-iso-00000001"), headers=headers)
    run(gateway.authority.tick())
    assert client.get("/compute/status", headers=headers).json()["state"] == "ready"
    shared_id = "req-shared-000001"

    first = client.post(
        "/v1/chat/completions", json=CHAT, headers={**headers, "X-Alex-Request-Id": shared_id}
    )
    assert first.status_code == 200

    gateway.llama.requests.clear()
    second_installation = enroll(gateway, client, label="PC B")
    second_headers = auth_header(client, second_installation)
    second = client.post(
        "/v1/chat/completions", json=CHAT, headers={**second_headers, "X-Alex-Request-Id": shared_id}
    )
    assert second.status_code == 200, second.text
    # The second installation had to generate: it could not be served from A's cache.
    assert len(gateway.llama.requests) == 1


def test_the_same_id_with_a_different_request_is_a_conflict(gateway, client):
    """A03: the request body is part of the identity — an id names exactly one request.

    A different body under the same id is refused with a typed conflict: never another
    question's answer, and never a silent second generation under the same id.
    """
    installation, headers = ready_compute(gateway, client)
    request_id = "req-digest-000001"
    first = client.post(
        "/v1/chat/completions", json=CHAT, headers={**headers, "X-Alex-Request-Id": request_id}
    )
    assert first.status_code == 200

    gateway.llama.requests.clear()
    other = {**CHAT, "messages": [{"role": "user", "content": "другой вопрос"}]}
    second = client.post(
        "/v1/chat/completions", json=other, headers={**headers, "X-Alex-Request-Id": request_id}
    )
    assert second.status_code == 409
    assert second.json()["code"] == "gateway_request_conflict"
    # No second generation, and the first request's answer is not leaked.
    assert gateway.llama.requests == []
    assert "Привет, мир" not in second.text


def test_a_concurrent_duplicate_never_starts_a_second_generation(gateway, client):
    """A13: the id is claimed before any await, so a simultaneous copy of the same request is
    refused immediately instead of queueing up behind the first and generating a second time."""
    installation, headers = ready_compute(gateway, client)
    installation_id = installation["installation_id"]

    async def race():
        first = await gateway.proxy.completions(
            {**CHAT, "stream": True}, installation_id=installation_id, task_id=None, request_id=request_id
        )
        # The first generation is in flight: the slot is taken and the id is claimed.
        error = None
        try:
            await gateway.proxy.completions(
                {**CHAT, "stream": True},
                installation_id=installation_id,
                task_id=None,
                request_id=request_id,
            )
        except GatewayError as failure:
            error = failure
        return first, error

    request_id = "req-race-00000001"
    first, error = run(race())
    assert error is not None and error.code == "gateway_request_in_flight"
    assert len(gateway.llama.requests) == 1
    assert gateway.proxy.queue.active() == 1
    # The claim is released when the generation really ends.
    run(first.body_iterator.aclose())
    assert gateway.proxy.queue.active() == 0
    assert gateway.proxy._in_flight == {}


def test_a_corrupted_upstream_body_releases_the_generation_slot(gateway, client):
    """A04: a 200 with a non-JSON body must not wedge the single generation slot.

    The error is typed (``malformed_response``) and the slot *and* the claim are released, so the
    next request can generate as soon as it wants to — never a permanently busy model.
    """
    installation, headers = ready_compute(gateway, client)
    gateway.llama.malformed = True
    response = client.post("/v1/chat/completions", json=CHAT, headers=headers)
    assert response.status_code == 502
    assert response.json()["code"] == "malformed_response"
    assert gateway.proxy.queue.active() == 0
    assert gateway.proxy.queue._slot.locked() is False
    assert gateway.proxy._in_flight == {}

    # The slot really is reusable: a healthy request succeeds right after.
    gateway.llama.malformed = False
    ok = client.post("/v1/chat/completions", json=CHAT, headers=headers)
    assert ok.status_code == 200
    assert gateway.proxy.queue._slot.locked() is False
