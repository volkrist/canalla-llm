"""Task-1: a persistent, durable wait for a free GPU — never bounded by 60 s or 300 s.

When RunPod has no suitable free card, the owner must be able to *keep the search open*: the
intent survives a brief network loss, a Gateway restart, an app restart and a transient provider
error, and the Gateway re-attempts on a bounded backoff until the user cancels it or a GPU
appears. Each *attempt* stays bounded; the wait between them does not.

Every test here runs against ``FakeRunPod`` and a private SQLite database with a controllable
clock: none of them creates, resumes or stops a paid resource, and none waits for real time.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
from conftest import auth_header, enroll, ensure_body, money, run

from gateway.compute import ComputeAuthority
from gateway.models import Installation
from gateway.provider import provider_api
from gateway.security import now as security_now

# --------------------------------------------------------------------------------- helpers


def ensure(client, headers, operation_id, **body):
    response = client.post(
        "/compute/ensure", json=ensure_body(operation_id=operation_id, **body), headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def status(client, headers):
    response = client.get("/compute/status", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def no_capacity(gateway):
    """Compatible hardware exists and its price fits, but nothing is bookable."""
    gateway.runpod.availability = "NONE"
    gateway.runpod.price = 0.48


def capacity_returns(gateway):
    gateway.runpod.availability = None
    gateway.runpod.price = 0.48


def grant(gateway, installation_id, *, period=None, days=None):
    with gateway.sessions() as db:
        row = db.get(Installation, installation_id)
        if period is not None:
            row.entitlement_period_budget = money(period)
        if days is not None:
            row.entitlement_period_days = days
        db.commit()


def restarted_authority(gateway):
    api = provider_api(gateway.settings, httpx.MockTransport(gateway.runpod.handle))
    authority = ComputeAuthority(gateway.settings, api=api, sessions=gateway.sessions)
    authority.transport = httpx.MockTransport(gateway.llama.handle)
    authority.clock = lambda: gateway.runpod.time
    return authority


# --------------------------------------------------------- the wait is durable and unbounded


def test_a_wait_stays_active_across_three_virtual_hours(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)

    body = ensure(
        client,
        headers,
        "op-wait-3h-00001",
        wait_for_capacity=True,
        background_start=True,
        max_hourly_price=1.20,
    )

    assert body["state"] == "waiting_capacity"
    assert body["ai"] == "starting"  # a persistent wait is a real, non-terminal transition
    assert body["wait"]["active"] is True
    assert body["wait"]["background_start"] is True
    assert body["wait"]["operation_id"] == "op-wait-3h-00001"
    assert body["search"] is None
    assert gateway.runpod.creates == []

    gateway.runpod.time += timedelta(hours=3)
    run(gateway.authority.tick())

    after = status(client, headers)
    assert after["state"] == "waiting_capacity"
    assert after["wait"]["active"] is True
    assert after["wait"]["elapsed_seconds"] >= 3 * 3600 - 1
    assert after["wait"]["next_attempt_at"] is not None
    assert gateway.runpod.creates == []  # still nothing bookable, still no Pod
    assert gateway.control.active_session_id is None


def test_an_expired_search_is_not_reused_as_a_wait(gateway, client):
    """A bounded search (wait off) still collapses after its window — the wait is opt-in."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)
    ensure(client, headers, "op-wait-off-0001", max_hourly_price=1.20)
    assert gateway.control.wait_active is False

    gateway.runpod.time += timedelta(seconds=61)
    run(gateway.authority.tick())

    body = status(client, headers)
    assert body["state"] == "offline"
    assert body["wait"] is None
    assert gateway.runpod.creates == []


# ----------------------------------------------------------- a GPU that appears is booked once


def test_a_gpu_that_appears_is_booked_exactly_once_and_ends_the_wait(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)
    ensure(
        client,
        headers,
        "op-wait-book-0001",
        wait_for_capacity=True,
        background_start=True,
        max_hourly_price=1.20,
    )

    gateway.runpod.time += timedelta(hours=3)
    capacity_returns(gateway)
    run(gateway.authority.tick())

    assert len(gateway.runpod.creates) == 1  # exactly one managed Pod, no duplicate
    booked = status(client, headers)
    assert booked["state"] == "starting_pod"
    assert booked["wait"] is None
    assert gateway.control.wait_active is False

    # The waiting chat attaches to the same single Pod: a follow-up ensure creates nothing.
    attached = ensure(
        client,
        headers,
        "op-wait-attach-01",
        wait_for_capacity=True,
        background_start=True,
        max_hourly_price=1.20,
    )
    assert attached["state"] in {"starting_pod", "loading_model", "ready"}
    assert len(gateway.runpod.creates) == 1

    run(gateway.authority.tick())
    assert status(client, headers)["state"] == "ready"
    assert len(gateway.runpod.creates) == 1


def test_polling_faster_than_the_backoff_never_hits_the_provider(gateway, client):
    """A not-due ensure answers the current wait instead of firing another attempt."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)
    ensure(
        client,
        headers,
        "op-wait-loop-0001",
        wait_for_capacity=True,
        background_start=True,
        max_hourly_price=1.20,
    )

    reads = len(gateway.runpod.catalog_timeouts)
    for index in range(5):
        polled = ensure(
            client,
            headers,
            f"op-wait-loop-{index + 2:04d}",
            wait_for_capacity=True,
            background_start=True,
            max_hourly_price=1.20,
        )
        assert polled["state"] == "waiting_capacity"
        assert polled["wait"]["active"] is True

    assert len(gateway.runpod.catalog_timeouts) == reads  # the backoff held: no provider read
    assert gateway.runpod.creates == []


# ------------------------------------------------------------------------------ cancellation


def test_cancelling_the_wait_stops_the_search_and_forbids_a_pod(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)
    ensure(
        client,
        headers,
        "op-wait-cancel-0001",
        wait_for_capacity=True,
        background_start=True,
        max_hourly_price=1.20,
    )

    cancelled = client.post(
        "/compute/wait/cancel", json={"operation_id": "op-wait-cancel-0002"}, headers=headers
    )
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["wait"] is None
    assert body["state"] == "offline"
    assert gateway.control.wait_active is False

    # A GPU becomes free, but the user stopped waiting: nothing is created on the old intent.
    gateway.runpod.time += timedelta(hours=1)
    capacity_returns(gateway)
    run(gateway.authority.tick())
    assert gateway.runpod.creates == []
    assert status(client, headers)["state"] == "offline"


def test_a_wait_without_background_permission_is_not_started_by_the_gateway(gateway, client):
    """The difference between a background search and a paid start: only the user's own
    request (or their explicit background permission) may create a Pod while they are away."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)
    body = ensure(
        client,
        headers,
        "op-wait-fg-00001",
        wait_for_capacity=True,
        background_start=False,
        max_hourly_price=1.20,
    )
    assert body["wait"]["background_start"] is False

    gateway.runpod.time += timedelta(hours=2)
    capacity_returns(gateway)
    run(gateway.authority.tick())
    assert gateway.runpod.creates == []  # the Loop must not start paid compute on its own
    assert gateway.control.wait_active is True

    # The waiting client's own request is what drives the next attempt.
    driven = ensure(
        client,
        headers,
        "op-wait-fg-00002",
        wait_for_capacity=True,
        background_start=False,
        max_hourly_price=1.20,
    )
    assert len(gateway.runpod.creates) == 1
    assert driven["wait"] is None


# ---------------------------------------------------------------------- price and entitlement


def test_a_wait_keeps_waiting_when_only_expensive_gpus_are_free(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.availability = None
    gateway.runpod.price = 2.00  # every compatible row is above the user's own maximum

    body = ensure(
        client,
        headers,
        "op-wait-price-0001",
        wait_for_capacity=True,
        background_start=True,
        max_hourly_price=0.52,
    )

    assert body["state"] == "waiting_capacity"
    assert body["error_code"] == "price_limit"
    assert body["wait"]["reason"] == "price_limit"

    gateway.runpod.time += timedelta(hours=3)
    run(gateway.authority.tick())
    assert gateway.runpod.creates == []
    assert status(client, headers)["wait"]["active"] is True


def test_a_parked_wait_creates_nothing_when_the_period_budget_is_exhausted(gateway, client):
    b = enroll(gateway, client, label="REGYNA")
    headers = auth_header(client, b)
    no_capacity(gateway)
    ensure(
        client,
        headers,
        "op-wait-budget-0001",
        wait_for_capacity=True,
        background_start=True,
        session_budget=0.02,
    )
    assert gateway.control.wait_active is True

    grant(gateway, b["installation_id"], period="0.01", days=30)  # operator narrows the quota
    gateway.runpod.time += timedelta(hours=1)
    capacity_returns(gateway)
    run(gateway.authority.tick())

    assert gateway.runpod.creates == []  # a refused reservation creates nothing
    assert gateway.control.wait_active is True  # and the wait is not silently lost either


# ------------------------------------------------------------------- restart / provider error


def test_a_gateway_restart_preserves_the_wait_and_still_books_one_pod(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)
    ensure(
        client,
        headers,
        "op-wait-restart-01",
        wait_for_capacity=True,
        background_start=True,
        max_hourly_price=1.20,
    )

    restarted = restarted_authority(gateway)
    run(restarted.tick())
    after_restart = status(client, headers)
    assert after_restart["state"] == "waiting_capacity"
    assert after_restart["wait"]["active"] is True
    assert gateway.runpod.creates == []

    gateway.runpod.time += timedelta(hours=2)
    capacity_returns(gateway)
    run(restarted.tick())
    assert len(gateway.runpod.creates) == 1


def test_a_transient_provider_error_does_not_cancel_the_wait(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)
    ensure(
        client,
        headers,
        "op-wait-error-0001",
        wait_for_capacity=True,
        background_start=True,
        max_hourly_price=1.20,
    )

    async def boom():
        raise httpx.ReadTimeout("provider detail must never surface")

    gateway.authority.api.gpu_offers = boom
    gateway.runpod.time += timedelta(hours=1)
    body = ensure(
        client,
        headers,
        "op-wait-error-0002",
        wait_for_capacity=True,
        background_start=True,
        max_hourly_price=1.20,
    )

    assert body["state"] == "waiting_capacity"
    assert body["error_code"] == "runpod_unavailable"
    assert body["wait"]["active"] is True
    assert gateway.runpod.creates == []


# ------------------------------------------------------- revocation and multi-installation


def test_a_revoked_installation_cannot_be_started_by_a_parked_wait(gateway, client):
    b = enroll(gateway, client, label="REGYNA")
    headers = auth_header(client, b)
    no_capacity(gateway)
    ensure(
        client,
        headers,
        "op-wait-revoke-001",
        wait_for_capacity=True,
        background_start=True,
        max_hourly_price=1.20,
    )

    with gateway.sessions() as db:
        db.get(Installation, b["installation_id"]).revoked_at = security_now()
        db.commit()

    gateway.runpod.time += timedelta(hours=1)
    capacity_returns(gateway)
    run(gateway.authority.tick())

    assert gateway.runpod.creates == []  # a cut-off client gets no paid resource
    assert gateway.control.wait_active is False  # and the intent cannot resurrect compute


def test_a_pod_that_appears_during_a_wait_is_adopted_not_duplicated(gateway, client):
    """A Pod that becomes visible while a wait is parked is adopted, never followed by a second."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)
    ensure(
        client,
        headers,
        "op-wait-adopt-001",
        wait_for_capacity=True,
        background_start=True,
        max_hourly_price=1.20,
    )
    # A Pod for the model's Volume becomes visible — created by another path or a resolved
    # ambiguous create. The Gateway must adopt it, not create its own on top.
    gateway.runpod.attached_pod(name="alex-llm-other")

    gateway.runpod.time += timedelta(hours=1)
    run(gateway.authority.tick())

    assert gateway.runpod.creates == []  # adopted, never a duplicate Pod
    assert gateway.control.wait_active is False
    assert gateway.control.active_session_id is not None


def test_another_installations_one_shot_request_does_not_downgrade_the_wait(gateway, client):
    a = enroll(gateway, client, label="PC A")
    b = enroll(gateway, client, label="REGYNA")
    no_capacity(gateway)
    ensure(
        client,
        auth_header(client, a),
        "op-wait-multi-a01",
        wait_for_capacity=True,
        background_start=True,
        max_hourly_price=1.20,
    )

    # B asks once, without the wait. The persistent intent is stronger than a 60 s search and is
    # kept — B simply reads the same wait and is served when the GPU appears.
    other = ensure(client, auth_header(client, b), "op-wait-multi-b01", max_hourly_price=1.20)
    assert other["state"] == "waiting_capacity"
    assert other["wait"]["active"] is True

    gateway.runpod.time += timedelta(hours=1)
    capacity_returns(gateway)
    run(gateway.authority.tick())
    assert len(gateway.runpod.creates) == 1  # one shared Pod, still no duplicate
