"""D-9: the server-side startup deadline.

A managed Pod that never becomes ``ready`` used to bill until the session budget ran out,
because the idle policy only owns ``ready``/``generating``. These tests pin the watchdog that
closes that window. Every case runs against the fake provider and the controllable clock, so no
test here can create, resume or stop a paid resource. The fake answers only the provider routes
the product actually calls and raises on anything else, which is why a volume delete attempted
by this path (it never happens) would fail these tests loudly instead of passing silently.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
from conftest import auth_header, enroll, ensure_body, run

DEADLINE = 300  # GatewaySettings.compute_startup_timeout_seconds


def ensure(client, headers, **body):
    response = client.post("/compute/ensure", json=ensure_body(**body), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def status(client, headers):
    response = client.get("/compute/status", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def startup_state(gateway, client, headers):
    """Create a real session, then keep the alias unavailable so readiness is never proven."""
    ensure(client, headers, operation_id="op-startup-00001")
    gateway.settings.llm_model = "some-other-model"
    run(gateway.authority.tick())
    assert status(client, headers)["state"] == "loading_model"
    return gateway.sessions_rows[0].id


def seed_session(
    gateway, *, state, age_seconds, pod_id: str | None = "pod-1", pod_name="alex-gw-seeded", **extra
):
    """Insert one session at a chosen age: the deadline is derived from persisted timestamps."""
    from gateway.models import GatewaySession

    with gateway.sessions() as db:
        control = gateway.authority.control(db)
        reference = gateway.runpod.time - timedelta(seconds=age_seconds)
        row = GatewaySession(
            id=str(uuid4()),
            pod_id=pod_id,
            pod_name=pod_name,
            state=state,
            gpu_type="NVIDIA L40S",
            gpu_vram_mb=48 * 1024,
            hourly_rate=Decimal("1.09"),
            max_hourly_price=Decimal("1.20"),
            session_budget=Decimal("3.00"),
            auto_stop_minutes=10,
            managed=True,
            adopted=False,
            intent_at=reference,
            created_at=reference,
            started_at=reference,
            last_activity_at=reference,
            **extra,
        )
        db.add(row)
        control.active_session_id = row.id
        control.state = state
        db.commit()
        return row.id


def audit(gateway, operation):
    from gateway.models import AuditEvent

    with gateway.sessions() as db:
        rows = db.query(AuditEvent).order_by(AuditEvent.created_at).all()
    return [row for row in rows if row.operation == operation]


# ------------------------------------------------------------------- below the deadline


def test_a_fresh_create_intent_is_never_stopped_below_the_deadline(gateway, client):
    enroll(gateway, client)
    session_id = seed_session(gateway, state="creating", age_seconds=DEADLINE - 60, pod_id=None)

    run(gateway.authority.tick())

    assert gateway.runpod.actions == []
    assert gateway.control.active_session_id == session_id


def test_a_starting_pod_is_never_stopped_below_the_deadline(gateway, client):
    enroll(gateway, client)
    gateway.runpod.attached_pod("alex-gw-seeded", status="STARTING")
    session_id = seed_session(gateway, state="starting_pod", age_seconds=DEADLINE - 60)

    run(gateway.authority.tick())

    assert gateway.runpod.actions == []
    assert gateway.control.active_session_id == session_id


def test_a_loading_model_is_never_stopped_below_the_deadline(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    startup_state(gateway, client, headers)

    gateway.runpod.time += timedelta(seconds=DEADLINE - 60)
    run(gateway.authority.tick())

    assert gateway.runpod.actions == []
    assert status(client, headers)["state"] == "loading_model"
    gateway.settings.llm_model = "orcarouter-qwen38-27b-q5km"


def test_the_deadline_is_reported_while_starting_and_absent_once_ready(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-deadline-00001")
    assert status(client, headers)["startup_deadline"] is not None

    run(gateway.authority.tick())

    body = status(client, headers)
    assert body["state"] == "ready"
    assert body["startup_deadline"] is None


# ---------------------------------------------------------------- at/after the deadline


def test_loading_model_at_the_deadline_is_stopped_as_startup_timeout(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    session_id = startup_state(gateway, client, headers)

    gateway.runpod.time += timedelta(seconds=DEADLINE + 1)
    run(gateway.authority.tick())

    assert gateway.runpod.actions == [{"action": "terminate"}]
    body = status(client, headers)
    assert body["state"] == "stopped"
    assert body["error_code"] == "startup_timeout"
    assert body["session"] is None
    assert body["last_session"]["id"] == session_id
    assert body["last_session"]["stop_reason"] == "startup_timeout"
    assert body["last_session"]["error_code"] == "startup_timeout"
    assert body["last_session"]["stopped_at"] is not None
    # Evidence: the deadline is in the audit trail, not only in a log line.
    assert [event.error_code for event in audit(gateway, "startup_timeout")] == ["startup_timeout"]
    assert gateway.control.active_session_id is None
    gateway.settings.llm_model = "orcarouter-qwen38-27b-q5km"


def test_creating_at_the_deadline_with_a_visible_pod_is_adopted_and_stopped(gateway, client):
    """An ambiguous create whose Pod does exist is found by reconciliation, then terminated."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    session_id = seed_session(gateway, state="creating", age_seconds=DEADLINE + 120, pod_id=None)
    gateway.runpod.attached_pod("alex-gw-seeded", pod_id="pod-adopted")
    assert gateway.runpod.creates == []  # the Pod exists, but no create was ever recorded

    run(gateway.authority.tick())

    assert gateway.runpod.actions == [{"action": "terminate"}]
    assert gateway.runpod.creates == []  # never a second Pod
    body = status(client, headers)
    assert body["state"] == "stopped"
    assert body["error_code"] == "startup_timeout"
    assert body["last_session"]["id"] == session_id


def test_creating_at_the_deadline_without_provider_evidence_keeps_the_intent(gateway, client):
    """Blind provider: nothing is terminated blindly and the intent is never cleared."""
    installation = enroll(gateway, client)
    session_id = seed_session(gateway, state="creating", age_seconds=DEADLINE + 120, pod_id=None)
    gateway.runpod.list_failure = 503

    run(gateway.authority.tick())

    assert gateway.runpod.actions == []
    assert gateway.runpod.creates == []
    assert gateway.control.active_session_id == session_id  # intent kept: no second Pod
    body = status(client, auth_header(client, installation))
    assert body["state"] == "create_unknown"
    assert body["error_code"] == "create_unknown"
    gateway.runpod.list_failure = None


def test_the_deadline_is_configurable_server_side(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.settings.compute_startup_timeout_seconds = 60
    startup_state(gateway, client, headers)

    gateway.runpod.time += timedelta(seconds=59)
    run(gateway.authority.tick())
    assert gateway.runpod.actions == []

    gateway.runpod.time += timedelta(seconds=2)
    run(gateway.authority.tick())
    assert gateway.runpod.actions == [{"action": "terminate"}]
    assert status(client, headers)["error_code"] == "startup_timeout"
    gateway.settings.llm_model = "orcarouter-qwen38-27b-q5km"


def test_a_new_authority_honours_the_persisted_deadline(gateway, client):
    """A Gateway restart must not reset the startup window: the deadline is persisted."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    startup_state(gateway, client, headers)

    from gateway.compute import ComputeAuthority
    from gateway.provider import provider_api

    settings = gateway.settings
    api = provider_api(settings, httpx.MockTransport(gateway.runpod.handle))
    restarted = ComputeAuthority(settings, api=api, sessions=gateway.sessions)
    restarted.transport = httpx.MockTransport(gateway.llama.handle)
    restarted.clock = lambda: gateway.runpod.time

    gateway.runpod.time += timedelta(seconds=DEADLINE + 5)
    run(restarted.tick())

    assert gateway.runpod.actions == [{"action": "terminate"}]
    assert status(client, headers)["error_code"] == "startup_timeout"
    gateway.settings.llm_model = "orcarouter-qwen38-27b-q5km"


# ----------------------------------------------------------- provider termination refuses


def test_a_transient_termination_failure_is_retried_until_the_pod_stops(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    startup_state(gateway, client, headers)
    gateway.runpod.time += timedelta(seconds=DEADLINE + 1)
    gateway.runpod.action_failure = "timeout"
    gateway.runpod.action_failures = 1

    run(gateway.authority.tick())

    # The refusal is reported honestly: the stop did not happen.
    body = status(client, headers)
    assert body["state"] == "stopping"
    assert body["error_code"] == "runpod_timeout"
    assert body["session"] is not None
    assert gateway.control.active_session_id is not None

    run(gateway.authority.tick())

    assert gateway.runpod.actions == [{"action": "terminate"}, {"action": "terminate"}]
    assert len(gateway.runpod.creates) == 1
    body = status(client, headers)
    assert body["state"] == "stopped"
    assert body["error_code"] == "startup_timeout"
    assert body["last_session"]["stop_reason"] == "startup_timeout"
    assert gateway.control.active_session_id is None
    assert len(audit(gateway, "startup_timeout")) == 1  # retried, not re-announced
    gateway.settings.llm_model = "orcarouter-qwen38-27b-q5km"


def test_a_permanent_termination_failure_stays_loud_and_is_never_reported_stopped(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    startup_state(gateway, client, headers)
    gateway.runpod.time += timedelta(seconds=DEADLINE + 1)
    gateway.runpod.action_failure = 500
    gateway.runpod.action_failures = 99

    for _ in range(3):
        run(gateway.authority.tick())

    body = status(client, headers)
    assert body["state"] == "stopping"
    assert body["session"] is not None
    assert body["error_code"] == "runpod_unavailable"
    assert gateway.control.active_session_id is not None
    assert len(gateway.runpod.actions) >= 2  # bounded retries, all visible
    assert audit(gateway, "stopped") == []
    gateway.runpod.action_failures = 0
    gateway.settings.llm_model = "orcarouter-qwen38-27b-q5km"


def test_a_startup_timeout_never_needs_a_second_pod_or_a_volume_change(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    startup_state(gateway, client, headers)
    gateway.runpod.time += timedelta(seconds=DEADLINE + 1)

    run(gateway.authority.tick())
    run(gateway.authority.reconcile())

    assert len(gateway.runpod.creates) == 1
    assert gateway.runpod.pods[0]["mounts"]["network"][0]["volumeId"] == "uwgeaie5b0"
    gateway.settings.llm_model = "orcarouter-qwen38-27b-q5km"


def test_two_installations_share_the_same_startup_timeout(gateway, client):
    first = enroll(gateway, client, label="PC A")
    second = enroll(gateway, client, label="PC B")
    startup_state(gateway, client, auth_header(client, first))

    gateway.runpod.time += timedelta(seconds=DEADLINE + 1)
    run(gateway.authority.tick())

    assert gateway.runpod.actions == [{"action": "terminate"}]
    assert len(gateway.runpod.creates) == 1
    assert status(client, auth_header(client, second))["state"] == "stopped"
    gateway.settings.llm_model = "orcarouter-qwen38-27b-q5km"


# ---------------------------------------------------------------- the deadline is not idle


def test_a_ready_session_never_fires_the_startup_deadline(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-ready-0000001", auto_stop_minutes=30)
    run(gateway.authority.tick())
    assert status(client, headers)["state"] == "ready"

    gateway.runpod.time += timedelta(seconds=DEADLINE * 2)
    run(gateway.authority.tick())

    assert gateway.runpod.actions == []  # idle owns a ready Pod; the startup phase is over
    assert status(client, headers)["state"] == "ready"


def test_generating_is_never_classified_as_a_startup_timeout(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-gen-00000001", auto_stop_minutes=30)
    run(gateway.authority.tick())
    gateway.authority.mark_activity()
    run(gateway.authority.tick())
    assert status(client, headers)["state"] == "generating"

    gateway.runpod.time += timedelta(seconds=DEADLINE * 2)  # 10 min: inside the idle window
    run(gateway.authority.tick())

    assert gateway.runpod.actions == []
    assert status(client, headers)["state"] == "generating"


def test_adopted_compute_is_never_touched_by_the_startup_deadline(gateway, client):
    """The Gateway only ever stops what it owns, deadline or not."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.attached_pod("someone-elses-pod")
    run(gateway.authority.reconcile())
    assert status(client, headers)["state"] == "external_compute"

    row = gateway.sessions_rows[0]
    assert row.managed is False
    assert gateway.authority.startup_expired(row) is False

    gateway.runpod.time += timedelta(seconds=DEADLINE * 3)
    run(gateway.authority.tick())

    assert gateway.runpod.actions == []
    assert gateway.authority.startup_expired(gateway.sessions_rows[0]) is False
    assert status(client, headers)["state"] != "stopped"


def test_the_session_budget_still_stops_compute_independently(gateway, client):
    """The money ceiling fires on its own, before the startup deadline is anywhere near."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-budget-0000001", session_budget=0.02)
    gateway.settings.llm_model = "some-other-model"

    gateway.runpod.time += timedelta(seconds=200)
    run(gateway.authority.tick())

    body = status(client, headers)
    assert body["state"] == "stopped"
    assert body["error_code"] == "COMPUTE_BUDGET_REACHED"
    assert body["last_session"]["stop_reason"] == "session_budget"
    assert gateway.runpod.actions == [{"action": "terminate"}]
    gateway.settings.llm_model = "orcarouter-qwen38-27b-q5km"


def test_a_stopped_startup_can_be_started_again_by_the_user(gateway, client):
    """Recoverable, not terminal: the same installation may deliberately try again."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    session_id = startup_state(gateway, client, headers)

    gateway.runpod.time += timedelta(seconds=DEADLINE + 1)
    run(gateway.authority.tick())
    assert status(client, headers)["state"] == "stopped"

    # The terminated Pod disappears from the provider list, which is what a real termination
    # looks like to the next reconciliation.
    gateway.runpod.pods.clear()
    gateway.settings.llm_model = "orcarouter-qwen38-27b-q5km"

    body = ensure(client, headers, operation_id="op-retry-00000001")
    assert body["session"]["id"] != session_id
    assert len(gateway.runpod.creates) == 2

    run(gateway.authority.tick())
    assert status(client, headers)["state"] == "ready"
