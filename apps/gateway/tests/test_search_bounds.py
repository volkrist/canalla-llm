"""A capacity search is a real, bounded operation — never an immortal «searching».

The Gateway kept answering `state: searching` (with `ai: starting`) long after the search was
over: the state was re-asserted from a stored error code on every reconciliation, with no
operation identity and no deadline. The client then showed amber «Connecting… Ищем GPU» for
481 s and still ~23 minutes later while no Pod and no session existed at all.

Every test here runs against the fake provider and the controllable clock: no test can create,
resume or stop a paid resource, and none of them waits for real time.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import auth_header, enroll, ensure_body, run

DEADLINE = 60  # GatewaySettings.compute_search_timeout_seconds (hard-bounded)


def ensure(client, headers, **body):
    response = client.post("/compute/ensure", json=ensure_body(**body), headers=headers)
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


def stamp(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)


def seed_control(gateway, **values):
    """Write the singleton control row directly: a legacy row is what the client actually saw."""
    with gateway.sessions() as db:
        control = gateway.authority.control(db)
        for field, value in values.items():
            setattr(control, field, value)
        db.commit()


def audit(gateway, operation):
    from gateway.models import AuditEvent

    with gateway.sessions() as db:
        return [row for row in db.query(AuditEvent).all() if row.operation == operation]


# ------------------------------------------------------- the search has identity and a deadline


def test_a_capacity_search_reports_an_identity_and_a_deadline(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)

    body = ensure(client, headers, operation_id="op-search-id-0001", max_hourly_price=1.20)

    assert body["state"] == "searching"
    assert body["error_code"] == "gpu_unavailable"
    assert body["ai"] == "starting"  # a live, bounded transition may be amber
    search = body["search"]
    assert search["operation_id"] == "op-search-id-0001"
    assert search["active"] is True
    assert search["timeout_seconds"] == DEADLINE
    assert (stamp(search["deadline"]) - stamp(search["started_at"])).total_seconds() == DEADLINE
    assert gateway.runpod.creates == []
    assert gateway.sessions_rows == []  # no fake managed session, ever


def test_a_search_that_expired_is_not_a_transition_anymore(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)
    ensure(client, headers, operation_id="op-search-old-001", max_hourly_price=1.20)

    gateway.runpod.time += timedelta(seconds=DEADLINE + 1)
    body = status(client, headers)

    # The badge must be red Disconnected: idle/off with the typed capacity reason, not amber.
    assert body["state"] == "offline"
    assert body["ai"] == "unavailable"
    assert body["error_code"] == "gpu_unavailable"
    assert body["search"]["active"] is False
    assert body["session"] is None


def test_the_tick_collapses_the_expired_search_and_keeps_the_reason(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)
    ensure(client, headers, operation_id="op-search-tick-01", max_hourly_price=1.20)

    gateway.runpod.time += timedelta(seconds=DEADLINE + 1)
    run(gateway.authority.tick())

    assert gateway.control.state == "offline"
    assert gateway.control.error_code == "gpu_unavailable"
    assert gateway.control.active_session_id is None
    assert [row.error_code for row in audit(gateway, "search_expired")] == ["gpu_unavailable"]
    assert gateway.runpod.actions == []  # nothing to terminate: no Pod ever existed


def test_a_stale_searching_row_without_an_operation_is_never_starting(gateway, client):
    """Exactly the production row: `searching` with nothing behind it."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    seed_control(
        gateway,
        state="searching",
        error_code="gpu_unavailable",
        last_operation_id=None,
        updated_at=gateway.runpod.time,
    )

    body = status(client, headers)

    assert body["state"] == "offline"
    assert body["ai"] != "starting"
    assert body["ai"] == "unavailable"
    assert body["search"]["active"] is False
    assert body["search"]["operation_id"] is None


def test_a_searching_row_with_a_dead_operation_is_reported_as_expired(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    seed_control(
        gateway,
        state="searching",
        error_code="gpu_unavailable",
        last_operation_id="op-legacy-000001",
        updated_at=gateway.runpod.time - timedelta(minutes=23),
    )

    body = status(client, headers)

    assert body["state"] == "offline"
    assert body["ai"] == "unavailable"
    assert body["search"]["active"] is False
    assert stamp(body["search"]["deadline"]) < gateway.runpod.time


def test_reconcile_never_resurrects_a_dead_search(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)
    ensure(client, headers, operation_id="op-search-rec-001", max_hourly_price=1.20)

    gateway.runpod.time += timedelta(seconds=DEADLINE + 5)
    run(gateway.authority.reconcile())

    assert gateway.control.state == "offline"
    assert gateway.control.error_code == "gpu_unavailable"
    assert status(client, headers)["ai"] == "unavailable"


def test_a_search_cannot_renew_its_own_deadline(gateway, client):
    """An attempt inside a live window is one bounded search, not an immortal «searching»."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)

    first = ensure(client, headers, operation_id="op-window-000001", max_hourly_price=1.20)
    gateway.runpod.time += timedelta(seconds=DEADLINE - 10)
    second = ensure(client, headers, operation_id="op-window-000002", max_hourly_price=1.20)

    assert second["state"] == "searching"
    assert second["search"]["deadline"] == first["search"]["deadline"]

    gateway.runpod.time += timedelta(seconds=11)
    assert status(client, headers)["state"] == "offline"


def test_a_later_request_starts_one_new_bounded_search(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)
    first = ensure(client, headers, operation_id="op-again-0000001", max_hourly_price=1.20)

    gateway.runpod.time += timedelta(seconds=DEADLINE + 1)
    run(gateway.authority.tick())
    assert gateway.control.state == "offline"

    second = ensure(client, headers, operation_id="op-again-0000002", max_hourly_price=1.20)

    assert second["state"] == "searching"
    assert second["search"]["operation_id"] == "op-again-0000002"
    assert second["search"]["deadline"] > first["search"]["deadline"]
    assert gateway.runpod.creates == []
    assert gateway.sessions_rows == []


def test_replaying_an_expired_search_operation_does_not_replay_the_promise(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    no_capacity(gateway)
    ensure(client, headers, operation_id="op-replay-000001", max_hourly_price=1.20)

    gateway.runpod.time += timedelta(seconds=DEADLINE + 1)
    replay = ensure(client, headers, operation_id="op-replay-000001", max_hourly_price=1.20)

    # The recorded answer stays idempotent, but it may not claim a transition that is over.
    assert replay["state"] == "offline"
    assert replay["ai"] == "unavailable"
    assert gateway.runpod.creates == []


def test_a_ready_pod_is_never_reported_as_a_search(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-ready-0000001")
    run(gateway.authority.tick())

    body = status(client, headers)

    assert body["state"] == "ready"
    assert body["ai"] == "ready"
    assert body["search"] is None


def test_the_search_window_cannot_be_configured_beyond_sixty_seconds():
    from pydantic import ValidationError

    from gateway.config import GatewaySettings

    base = {"jwt_secret": "search-bounds-secret-" + "s" * 40, "runpod_api_key": "test-key"}
    assert GatewaySettings(**base, compute_search_timeout_seconds=DEADLINE)
    with pytest.raises(ValidationError):
        GatewaySettings(**base, compute_search_timeout_seconds=1200)
