"""A10 (server-side spending entitlements) and A11 (global Stop permission), deterministically.

Two installations share one RunPod account. The operator's entitlement and role live on the
server, in the ``installations`` row: no client body, header, role or preference can influence
them. Every test here runs against ``FakeRunPod`` and a private SQLite database, so nothing
reaches a real provider and no GPU is spent.

The ten scenarios are the ones the integration brief names; each has one focused test.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from conftest import ALIAS, auth_header, enroll, ensure_body, money, run, wait_for

from gateway.compute import STOP_RETRY_SECONDS, ComputeAuthority
from gateway.models import Installation
from gateway.provider import provider_api
from gateway.security import now as security_now

CHAT = {"model": ALIAS, "messages": [{"role": "user", "content": "привет"}]}


# --------------------------------------------------------------------------------- helpers


def grant(gateway, installation_id, *, role=None, hourly=None, budget=None, period=None, days=None):
    """Operator-side entitlement write, the same effect as ``gateway.cli set-entitlement``."""
    with gateway.sessions() as db:
        row = db.get(Installation, installation_id)
        if role is not None:
            row.role = role
        if hourly is not None:
            row.entitlement_max_hourly_price = money(hourly)
        if budget is not None:
            row.entitlement_session_budget = money(budget)
        if period is not None:
            row.entitlement_period_budget = money(period)
        if days is not None:
            row.entitlement_period_days = days
        db.commit()


def ensure_ok(client, headers, operation_id, **body):
    response = client.post(
        "/compute/ensure", json=ensure_body(operation_id=operation_id, **body), headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def stop(client, headers, operation_id, **body):
    return client.post("/compute/stop", json={"operation_id": operation_id, **body}, headers=headers)


def ready_compute(gateway, client, label="PC A"):
    installation = enroll(gateway, client, label=label)
    headers = auth_header(client, installation)
    client.post("/compute/ensure", json=ensure_body(operation_id="op-ready-0000001"), headers=headers)
    run(gateway.authority.tick())
    assert client.get("/compute/status", headers=headers).json()["state"] == "ready"
    return installation, headers


def restarted_authority(gateway):
    api = provider_api(gateway.settings, httpx.MockTransport(gateway.runpod.handle))
    authority = ComputeAuthority(gateway.settings, api=api, sessions=gateway.sessions)
    authority.transport = httpx.MockTransport(gateway.llama.handle)
    authority.clock = lambda: gateway.runpod.time
    return authority


def commitment(authority, gateway, installation_id):
    with gateway.sessions() as db:
        since = gateway.runpod.time - timedelta(days=90)
        return authority._period_commitment(db, installation_id, since)


# ------------------------------------------------------------- scenario 1: a client cannot widen


def test_a_client_cannot_raise_its_budget_past_the_operator_grant(gateway, client):
    a = enroll(gateway, client, label="PC A")
    b = enroll(gateway, client, label="REGYNA")
    grant(gateway, b["installation_id"], budget="1.00")
    a_headers, b_headers = auth_header(client, a), auth_header(client, b)

    # A has no extra bound: its own $3 budget stands exactly as asked.
    first = ensure_ok(client, a_headers, "op-a10-s1-a0001", session_budget=3.0)
    assert Decimal(first["session"]["budget_usd"]) == money("3.00")
    assert first["caller"]["session_budget_usd"] is None
    stop(client, a_headers, "op-a10-s1-astop")

    # B asks for $50 over HTTP; the operator granted $1. The excess is refused: the session is
    # held to the grant, and the client can see the bound it was actually given.
    second = ensure_ok(client, b_headers, "op-a10-s1-b0001", session_budget=50.0)
    assert Decimal(second["session"]["budget_usd"]) == money("1.00")
    assert Decimal(second["caller"]["session_budget_usd"]) == money("1.00")


# ------------------------------------------------------- scenario 2: the period quota is spent


def test_a_sequence_of_sessions_exhausts_the_period_budget(gateway, client):
    b = enroll(gateway, client, label="REGYNA")
    grant(gateway, b["installation_id"], budget="0.02", period="0.03", days=30)
    headers = auth_header(client, b)

    first = ensure_ok(client, headers, "op-a10-s2-first1", session_budget=0.02)
    assert first["state"] == "starting_pod"

    # The session runs (and bills) for a while, then the user stops it.
    gateway.runpod.time += timedelta(minutes=10)  # 10 min at $0.48/h is $0.08, over the $0.03 quota
    assert stop(client, headers, "op-a10-s2-stop01").json()["state"] == "stopped"

    before = len(gateway.runpod.pods)
    denied = client.post(
        "/compute/ensure",
        json=ensure_body(operation_id="op-a10-s2-second", session_budget=0.02),
        headers=headers,
    )
    assert denied.status_code == 409
    assert denied.json()["code"] == "gateway_budget_denied"
    # A refused reservation creates nothing.
    assert len(gateway.runpod.pods) == before
    assert len(gateway.runpod.creates) == 1


# ---------------------------------------------------- scenario 3: atomic, single reservation


def test_two_requests_reserve_once_and_create_one_pod(gateway, client):
    b = enroll(gateway, client, label="REGYNA")
    grant(gateway, b["installation_id"], period="5.00", days=30)
    headers = auth_header(client, b)

    first = ensure_ok(client, headers, "op-a10-s3-first1")
    second = ensure_ok(client, headers, "op-a10-s3-second")

    assert first["state"] == "starting_pod"
    assert second["state"] in {"starting_pod", "loading_model", "ready"}
    assert len(gateway.runpod.pods) == 1
    assert len(gateway.runpod.creates) == 1
    active = [row for row in gateway.sessions_rows if row.state not in {"stopped"}]
    assert len(active) == 1  # one session committed, not two


# -------------------------------------------------------------- scenario 4: restart durability


def test_a_restart_keeps_the_commitment_and_the_reservation(gateway, client):
    b = enroll(gateway, client, label="REGYNA")
    grant(gateway, b["installation_id"], budget="0.02", period="0.03", days=30)
    headers = auth_header(client, b)
    ensure_ok(client, headers, "op-a10-s4-first1", session_budget=0.02)

    before = commitment(gateway.authority, gateway, b["installation_id"])
    assert before == money("0.02")  # the active session is counted at its reserved ceiling

    restarted = restarted_authority(gateway)
    after = commitment(restarted, gateway, b["installation_id"])
    assert after == before  # the reserve survived the restart

    # Drive the restart forward: bill the run, stop it, then a fresh session still cannot fit.
    gateway.runpod.time += timedelta(minutes=10)
    stop(client, headers, "op-a10-s4-stop01")
    denied = client.post(
        "/compute/ensure",
        json=ensure_body(operation_id="op-a10-s4-second", session_budget=0.02),
        headers=headers,
    )
    assert denied.status_code == 409
    assert denied.json()["code"] == "gateway_budget_denied"


# ------------------------------------------------------------------ scenario 5: stop permission


def test_a_member_cannot_stop_compute_it_does_not_own(gateway, client):
    a = enroll(gateway, client, label="PC A")
    b = enroll(gateway, client, label="REGYNA")
    ensure_ok(client, auth_header(client, a), "op-a11-s5-a0001")

    denied = stop(client, auth_header(client, b), "op-a11-s5-b0001")
    assert denied.status_code == 403
    assert denied.json()["code"] == "gateway_stop_forbidden"
    # The shared Pod is untouched and the owner's run continues.
    assert gateway.runpod.pods[0]["status"] == "RUNNING"
    assert gateway.runpod.actions == []


def test_the_owner_may_still_stop_its_own_compute(gateway, client):
    a = enroll(gateway, client, label="PC A")
    headers = auth_header(client, a)
    ensure_ok(client, headers, "op-a11-s5-own01")
    assert stop(client, headers, "op-a11-s5-own02").json()["state"] == "stopped"
    assert gateway.runpod.pods[0]["status"] == "TERMINATED"


# ------------------------------------------------------- scenario 6: cancel own generation only


def test_cancel_own_reaches_only_the_callers_generation(gateway, client):
    a, _ = ready_compute(gateway, client, label="PC A")
    b = enroll(gateway, client, label="REGYNA")
    b_id = b["installation_id"]

    gate = asyncio.Event()
    gateway.llama.gate = gate
    results: dict[str, str] = {}

    async def generate(name, installation_id):
        response = await gateway.proxy.completions(
            {**CHAT, "stream": True},
            installation_id=installation_id,
            task_id=None,
            request_id=f"req-{name}",
        )
        try:
            async for _ in response.body_iterator:
                pass
            results[name] = "completed"
        except asyncio.CancelledError:
            results[name] = "cancelled"
            raise

    async def scenario():
        # B is admitted and generating; A waits for the single slot.
        b_task = asyncio.create_task(generate("B", b_id))
        await wait_for(lambda: gateway.proxy.queue.active() == 1)
        a_task = asyncio.create_task(generate("A", a["installation_id"]))
        await wait_for(
            lambda: any(owner == a["installation_id"] for owner in gateway.proxy._in_flight.values())
        )
        cancelled = gateway.proxy.cancel_own(b_id)
        with pytest.raises(asyncio.CancelledError):
            await b_task
        gate.set()
        await a_task
        return cancelled

    assert run(scenario()) == 1
    assert results["A"] == "completed"  # the other installation's generation is untouched
    assert results.get("B") == "cancelled"  # B's own generation was cancelled, not the Pod
    # The cancel never reached the shared Pod.
    assert gateway.runpod.pods[0]["status"] == "RUNNING"
    assert gateway.runpod.actions == []


# ------------------------------------------------------------- scenario 7: operator emergency


def test_the_operator_emergency_stop_is_durable_through_a_provider_500(gateway, client):
    member = enroll(gateway, client, label="PC A")
    operator = enroll(gateway, client, label="Operator")
    grant(gateway, operator["installation_id"], role="operator")
    ensure_ok(client, auth_header(client, member), "op-a11-s7-a0001")

    gateway.runpod.action_failure = 500
    gateway.runpod.action_failures = 1
    stopped = client.post(
        "/compute/emergency-stop",
        json={"operation_id": "op-a11-s7-op001"},
        headers=auth_header(client, operator),
    )
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "stopping"  # refused by the provider, intent persisted

    # The durable Stop retries from persisted state and converges on the next tick.
    gateway.runpod.time += timedelta(seconds=STOP_RETRY_SECONDS + 1)
    run(gateway.authority.tick())
    assert gateway.runpod.pods[0]["status"] == "TERMINATED"
    with gateway.sessions() as db:
        from gateway.models import AuditEvent

        results = [row.result for row in db.query(AuditEvent).filter_by(operation="emergency_stop").all()]
    assert "ok" in results


def test_a_member_cannot_issue_an_emergency_stop(gateway, client):
    member = enroll(gateway, client, label="PC A")
    headers = auth_header(client, member)
    ensure_ok(client, headers, "op-a11-s7-a0002")
    denied = client.post("/compute/emergency-stop", json={"operation_id": "op-a11-s7-mem01"}, headers=headers)
    assert denied.status_code == 403
    assert denied.json()["code"] == "gateway_stop_forbidden"
    assert gateway.runpod.pods[0]["status"] == "RUNNING"


# --------------------------------------------------- scenario 9: balance below the session budget


def test_a_small_balance_does_not_reject_a_larger_budget(gateway, client):
    b = enroll(gateway, client, label="REGYNA")
    gateway.runpod.balance = "0.81"

    body = ensure_ok(client, auth_header(client, b), "op-a10-s9-00001", session_budget=3.0)

    assert body["state"] == "starting_pod"
    # The session starts fine; it simply cannot spend more than the balance actually holds.
    assert Decimal(body["session"]["budget_usd"]) == money("0.81")


# --------------------------------------------------------- scenario 10: a revoked installation


def test_a_revoked_installation_loses_every_compute_right(gateway, client):
    a = enroll(gateway, client, label="PC A")
    b = enroll(gateway, client, label="REGYNA")
    ensure_ok(client, auth_header(client, a), "op-a10-s10-a001")
    b_headers = auth_header(client, b)

    with gateway.sessions() as db:
        row = db.get(Installation, b["installation_id"])
        row.revoked_at = security_now()
        db.commit()

    denied = client.post(
        "/compute/ensure", json=ensure_body(operation_id="op-a10-s10-b001"), headers=b_headers
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "installation_revoked"

    denied_stop = stop(client, b_headers, "op-a10-s10-b002")
    assert denied_stop.status_code == 403
    assert gateway.runpod.pods[0]["status"] == "RUNNING"
