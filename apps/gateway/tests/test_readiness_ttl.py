"""A06: readiness is re-verified, and a dead model never keeps a green chip or bills.

``ready`` used to be a fact that stayed true forever: the Gateway proved the alias once and then
trusted it, so a llama.cpp that died left the client on «Connected» with a Pod still billing. These
tests pin the TTL re-check, the grace window and the typed stop that closes it. Everything runs
against the fake provider and the controllable clock — no paid resource is created, resumed or
stopped, and the fake raises on any provider route the product does not actually call.
"""

from __future__ import annotations

from datetime import timedelta

from conftest import ALIAS, auth_header, enroll, ensure_body, run

TTL = 30  # GatewaySettings.compute_ready_ttl_seconds
GRACE = 120  # GatewaySettings.compute_ready_grace_seconds


def ensure(client, headers, **body):
    response = client.post("/compute/ensure", json=ensure_body(**body), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def status(client, headers):
    response = client.get("/compute/status", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def ready_compute(gateway, client, *, operation_id="op-ttl-0000001"):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id=operation_id, auto_stop_minutes=30)
    run(gateway.authority.tick())
    assert status(client, headers)["state"] == "ready"
    assert gateway.runpod.pods[0]["status"] == "RUNNING"
    return installation, headers


def test_a_model_that_dies_after_ready_stops_showing_connected(gateway, client):
    """The probe fails: the chip leaves green immediately (inside the grace) and says why."""
    installation, headers = ready_compute(gateway, client)
    # The model gateway stops answering with the configured alias.
    gateway.settings.llm_model = "some-other-model"
    gateway.runpod.time += timedelta(seconds=TTL + 1)
    run(gateway.authority.tick())

    body = status(client, headers)
    assert body["state"] == "loading_model"
    assert body["state"] != "ready"
    assert body["ai"] != "ready"  # the client is not told the AI is usable
    assert body["error_code"] == "model_unavailable"
    assert len(gateway.runpod.creates) == 1  # no second Pod, ever
    assert gateway.runpod.actions == []  # still inside the grace window


def test_a_dead_model_is_stopped_after_the_grace_window(gateway, client):
    """It answered once and is gone: the Pod is stopped with a typed, recoverable reason."""
    installation, headers = ready_compute(gateway, client)
    gateway.settings.llm_model = "some-other-model"
    gateway.runpod.time += timedelta(seconds=TTL + 1)
    run(gateway.authority.tick())
    assert status(client, headers)["state"] == "loading_model"

    gateway.runpod.time += timedelta(seconds=GRACE + 1)
    run(gateway.authority.tick())

    assert gateway.runpod.pods[0]["status"] == "TERMINATED"
    assert gateway.runpod.actions == [{"action": "terminate"}]
    body = status(client, headers)
    assert body["state"] == "stopped"
    assert body["error_code"] == "model_unavailable"
    assert body["state"] != "ready"
    assert len(gateway.runpod.creates) == 1


def test_a_recovered_model_returns_to_ready_without_a_new_pod(gateway, client):
    """A momentary probe failure is not a dead model: recovery needs no new compute."""
    installation, headers = ready_compute(gateway, client)
    gateway.settings.llm_model = "some-other-model"
    gateway.runpod.time += timedelta(seconds=TTL + 1)
    run(gateway.authority.tick())
    assert status(client, headers)["state"] == "loading_model"

    gateway.settings.llm_model = ALIAS  # the model answers again, before the grace expires
    run(gateway.authority.tick())

    body = status(client, headers)
    assert body["state"] == "ready"
    assert body["error_code"] is None
    assert gateway.runpod.actions == []
    assert len(gateway.runpod.creates) == 1


def test_an_active_generation_is_never_probed(gateway, client):
    """A probe must never compete with the single model slot (--parallel 1)."""
    installation, headers = ready_compute(gateway, client)
    gateway.llama.requests.clear()
    gateway.proxy.queue._running = 1  # one admitted generation
    gateway.runpod.time += timedelta(seconds=TTL * 5)
    run(gateway.authority.tick())

    assert gateway.llama.requests == []
    assert status(client, headers)["state"] == "ready"
    gateway.proxy.queue._running = 0


def test_a_stale_startup_is_never_reported_ready_after_an_hour(gateway, client):
    """A startup that never proved readiness is stopped, never rendered as a green chip."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.settings.llm_model = "some-other-model"  # readiness can never be proven
    ensure(client, headers, operation_id="op-ttl-hour-0001")
    run(gateway.authority.tick())
    assert status(client, headers)["state"] == "loading_model"
    assert status(client, headers)["ai"] != "ready"

    gateway.runpod.time += timedelta(hours=1)
    run(gateway.authority.tick())

    assert gateway.runpod.pods[0]["status"] == "TERMINATED"
    body = status(client, headers)
    assert body["state"] == "stopped"
    assert body["state"] != "ready"
    assert body["error_code"] == "startup_timeout"
    assert len(gateway.runpod.creates) == 1


def test_readiness_is_not_probed_more_often_than_the_ttl(gateway, client):
    """The re-check is bounded: a healthy model is not hammered every tick."""
    installation, headers = ready_compute(gateway, client)
    probes = gateway.llama.models
    run(gateway.authority.tick())
    run(gateway.authority.tick())
    assert gateway.llama.models == probes  # inside the TTL: no extra probe
    gateway.runpod.time += timedelta(seconds=TTL + 1)
    run(gateway.authority.tick())
    assert gateway.llama.models == probes + 1  # the TTL elapsed: exactly one re-check
