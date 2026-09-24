"""One bounded allocation over an ordered *set* of placements — never one pinned slot.

Repeated real-world evidence: Canalla sat in «Ищем GPU» for about an hour, more than once, and
never obtained a usable model. The state machine was already bounded by the previous step (an
expired search collapses to red — see ``test_search_bounds.py``); what could still pin the
product was the *allocator*: one physical placement (one GPU id, one cloud tier, one datacenter),
waited on for as long as the provider kept saying "not now".

Every test here runs against the fake provider and a controllable clock: nothing creates,
resumes or stops a paid resource, and nothing talks to RunPod.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from conftest import auth_header, enroll, ensure_body, run

DEADLINE = 60  # GatewaySettings.compute_search_timeout_seconds (hard-bounded 5..60)
CANDIDATE_LIMIT = 3  # candidates.MAX_CANDIDATE_ATTEMPTS
CANDIDATE_TIMEOUT = 15  # candidates.CANDIDATE_CALL_TIMEOUT_SECONDS


def ensure(client, headers, **body):
    response = client.post("/compute/ensure", json=ensure_body(**body), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def status(client, headers):
    response = client.get("/compute/status", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def audit(gateway, operation):
    from gateway.models import AuditEvent

    with gateway.sessions() as db:
        return [row for row in db.query(AuditEvent).all() if row.operation == operation]


def created_gpus(gateway):
    return [body["gpu"]["id"] for body in gateway.runpod.creates]


# ----------------------------------------------------------------- the ordered candidate walk


def test_matrix_1_the_preferred_candidate_is_selected(gateway, client):
    """The cheapest compatible GPU, in the model's own Volume placement, still wins first."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48

    body = ensure(client, headers, operation_id="op-alloc-0000001", max_hourly_price=0.52)

    assert body["state"] == "starting_pod"
    assert created_gpus(gateway) == ["gpu-48"]
    assert gateway.runpod.creates[0]["cloud"] == "SECURE"
    assert gateway.runpod.creates[0]["dataCenterIds"] == ["US-TX-3"]
    assert gateway.runpod.creates[0]["mounts"] == {
        "network": [{"volumeId": "uwgeaie5b0", "path": "/workspace"}]
    }
    assert len(gateway.runpod.pods) == 1
    allocation = body["allocation"]
    assert allocation["outcome"] == "selected"
    assert allocation["selected"]["gpu"] == "gpu-48"
    assert allocation["selected"]["cloud"] == "SECURE"
    assert allocation["selected"]["datacenter"] == "US-TX-3"
    assert allocation["attempts"][0]["result"] == "selected"


def test_matrix_2_a_refused_preferred_candidate_falls_through_to_the_next(gateway, client):
    """No capacity for the preferred card is not the end of the allocation: the next is tried."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    gateway.runpod.refuse_gpus = {"gpu-48"}

    body = ensure(client, headers, operation_id="op-alloc-0000002", max_hourly_price=1.20)

    assert created_gpus(gateway) == ["gpu-48", "NVIDIA L40S"]
    assert len(gateway.runpod.pods) == 1
    assert body["state"] == "starting_pod"
    assert body["session"]["gpu"] == "NVIDIA L40S"
    attempts = body["allocation"]["attempts"]
    assert [(item["gpu"], item["result"], item["error_code"]) for item in attempts] == [
        ("gpu-48", "refused", "placement_rejected"),
        ("NVIDIA L40S", "selected", None),
    ]


def test_matrix_3_an_unavailable_datacenter_falls_back_to_another_placement(gateway, client):
    """Scheduling is not restricted to one datacenter when the provider permits more."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.settings.runpod_datacenters = "US-KS-2:vol-peers"
    gateway.runpod.price = 0.48
    gateway.runpod.datacenters = [("US-TX-3", "NONE"), ("US-KS-2", "HIGH")]

    body = ensure(client, headers, operation_id="op-alloc-0000003", max_hourly_price=1.20)

    assert body["state"] == "starting_pod"
    assert gateway.runpod.creates[0]["dataCenterIds"] == ["US-KS-2"]
    assert gateway.runpod.creates[0]["mounts"]["network"][0]["volumeId"] == "vol-peers"
    # The cheapest compatible card *in a placement that can book it*: the row that is out of
    # stock in the model's own datacenter is bookable in the added one.
    assert created_gpus(gateway) == ["gpu-cheap-unavailable"]
    assert body["allocation"]["selected"]["datacenter"] == "US-KS-2"
    policy = body["policy"]
    assert [item["datacenter"] for item in policy["datacenters"]] == ["US-TX-3", "US-KS-2"]


def test_matrix_3b_without_the_extra_placement_the_same_catalogue_has_no_candidate(gateway, client):
    """The defect in one assertion: the same catalogue, one placement, nothing bookable."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    gateway.runpod.datacenters = [("US-TX-3", "NONE"), ("US-KS-2", "HIGH")]

    body = ensure(client, headers, operation_id="op-alloc-0000031", max_hourly_price=1.20)

    assert gateway.runpod.creates == []
    assert gateway.runpod.pods == []
    assert body["state"] == "searching"
    assert body["error_code"] == "gpu_unavailable"


def test_matrix_4_all_capacity_unavailable_is_bounded_capacity_unavailable(gateway, client):
    """Nothing is bookable anywhere: a bounded window, then the typed failure — never amber."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.availability = "NONE"

    body = ensure(client, headers, operation_id="op-alloc-0000004", max_hourly_price=1.20)
    assert body["state"] == "searching"
    assert body["error_code"] == "gpu_unavailable"
    assert body["search"]["active"] is True
    assert gateway.runpod.creates == []

    gateway.runpod.time += timedelta(seconds=DEADLINE + 1)
    after = status(client, headers)
    assert after["state"] == "offline"
    assert after["ai"] == "unavailable"
    assert after["ai_label"] != "AI Starting"
    assert after["search"]["active"] is False
    assert after["error_code"] == "gpu_unavailable"
    run(gateway.authority.tick())
    assert gateway.control.state == "offline"
    assert gateway.runpod.pods == []


def test_matrix_4b_every_candidate_refused_closes_the_operation_immediately(gateway, client):
    """A walk that tried real placements and was refused everywhere is over, not amber."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    gateway.runpod.create_refusal = 400

    body = ensure(client, headers, operation_id="op-alloc-0000041", max_hourly_price=1.20)

    assert body["state"] == "offline"
    assert body["ai"] == "unavailable"
    assert body["error_code"] == "gpu_unavailable"
    assert body["search"] is None  # no live search: the operation concluded
    assert body["allocation"]["outcome"] == "capacity_unavailable"
    assert gateway.runpod.pods == []
    assert [row.error_code for row in audit(gateway, "search_closed")] == ["gpu_unavailable"]
    # The window may not be reused as a promise: a new request opens one new bounded cycle.
    again = ensure(client, headers, operation_id="op-alloc-0000042", max_hourly_price=1.20)
    assert again["state"] == "offline"
    assert again["error_code"] == "gpu_unavailable"


def test_matrix_5_one_erroring_candidate_never_stops_the_walk(gateway, client):
    """A conflict on one candidate is followed by the next one, not by a failure."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    gateway.runpod.refuse_gpus = {"gpu-48"}
    gateway.runpod.refuse_status = 409

    body = ensure(client, headers, operation_id="op-alloc-0000005", max_hourly_price=1.20)

    assert len(gateway.runpod.creates) == 2
    assert body["state"] == "starting_pod"
    assert body["session"]["gpu"] == "NVIDIA L40S"


def test_matrix_6_concurrent_ensures_share_one_walk(gateway, client):
    """Three callers, one allocator: request 1 -> Pod A, request 2 -> Pod B is what must not happen."""
    installation = enroll(gateway, client)
    gateway.runpod.price = 0.48
    gateway.runpod.refuse_gpus = {"gpu-48"}
    authority = gateway.authority

    async def race():
        return await asyncio.gather(
            *(
                authority.ensure(
                    installation["installation_id"],
                    operation_id=f"op-alloc-6{i:07d}",
                    task_id=f"t{i}",
                    caps={"max_hourly_price": 1.20},
                )
                for i in range(3)
            ),
            return_exceptions=True,
        )

    results = run(race())

    assert len(gateway.runpod.pods) == 1
    assert len(gateway.runpod.creates) <= CANDIDATE_LIMIT
    assert created_gpus(gateway) == ["gpu-48", "NVIDIA L40S"]
    assert len({row.id for row in gateway.sessions_rows}) == 1
    for result in results:
        if isinstance(result, Exception):
            assert getattr(result, "code", "") == "gateway_busy"


def test_matrix_7_the_deadline_closes_the_search_and_nothing_stays_active(gateway, client):
    """60 s is the whole budget: after it the state is the typed failure, not «Connecting»."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.availability = "NONE"
    first = ensure(client, headers, operation_id="op-alloc-0000007", max_hourly_price=1.20)

    gateway.runpod.time += timedelta(seconds=DEADLINE + 1)
    body = status(client, headers)
    assert body["state"] == "offline"
    assert body["ai"] == "unavailable"
    assert body["search"]["active"] is False

    # A later request starts exactly one new bounded cycle — never a repeating hot loop.
    second = ensure(client, headers, operation_id="op-alloc-0000071", max_hourly_price=1.20)
    assert second["state"] == "searching"
    assert second["search"]["active"] is True
    assert second["search"]["deadline"] > first["search"]["deadline"]
    assert gateway.runpod.creates == []


def test_matrix_8_a_stale_search_with_no_allocator_is_disconnected(gateway, client):
    """`searching` with nothing behind it: red Disconnected, with the typed reason."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    with gateway.sessions() as db:
        control = gateway.authority.control(db)
        control.state = "searching"
        control.error_code = "gpu_unavailable"
        control.last_operation_id = None
        control.updated_at = gateway.runpod.time
        db.commit()

    body = status(client, headers)

    assert body["state"] == "offline"
    assert body["ai"] == "unavailable"
    assert body["search"]["active"] is False
    assert body["error_code"] == "gpu_unavailable"


def test_matrix_9_a_successful_walk_creates_exactly_one_pod(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    gateway.runpod.refuse_gpus = {"gpu-48"}

    body = ensure(client, headers, operation_id="op-alloc-0000009", max_hourly_price=1.20)

    assert body["state"] == "starting_pod"
    assert len(gateway.runpod.pods) == 1
    assert len(gateway.runpod.creates) == 2  # two placements tried, one Pod born
    assert len(gateway.sessions_rows) == 1  # one allocation attempt, not one per candidate
    assert gateway.sessions_rows[0].pod_id == gateway.runpod.pods[0]["id"]
    assert gateway.runpod.actions == []


def test_matrix_10_a_failed_walk_leaks_no_pod(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    gateway.runpod.create_refusal = 400

    body = ensure(client, headers, operation_id="op-alloc-0000100", max_hourly_price=1.20)

    assert body["error_code"] == "gpu_unavailable"
    assert gateway.runpod.pods == []
    assert gateway.runpod.actions == []  # nothing was created, so nothing is terminated
    assert [row.pod_id for row in gateway.sessions_rows] == [None]
    assert gateway.control.active_session_id is None
    assert gateway.control.state == "offline"
    # The Volume is never part of an allocation failure.
    assert all(body["mounts"]["network"][0]["volumeId"] == "uwgeaie5b0" for body in gateway.runpod.creates)


def test_the_walk_never_exceeds_the_candidate_limit(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    gateway.runpod.create_refusal = 400

    body = ensure(client, headers, operation_id="op-alloc-0000101", max_hourly_price=2.00)

    # gpu-48, NVIDIA L40S and gpu-80 are compatible and affordable; the walk stops at the limit.
    assert len(gateway.runpod.creates) == CANDIDATE_LIMIT
    assert created_gpus(gateway) == ["gpu-48", "NVIDIA L40S", "gpu-80"]
    assert len(body["allocation"]["plan"]["candidates"]) == CANDIDATE_LIMIT
    assert body["allocation"]["candidate_limit"] == CANDIDATE_LIMIT


def test_a_candidate_cannot_consume_the_whole_allocation_budget(gateway, client):
    """One slow candidate ends the walk: 60 s is a total budget, never 60 s per candidate."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    gateway.runpod.create_refusal = 400
    gateway.runpod.create_delay_seconds = DEADLINE + 1  # the first candidate burns the window

    body = ensure(client, headers, operation_id="op-alloc-0000102", max_hourly_price=1.20)

    assert len(gateway.runpod.creates) == 1  # the second candidate was never started
    assert body["state"] == "offline"
    assert body["error_code"] == "gpu_unavailable"
    assert body["search"] is None
    assert body["allocation"]["budget_seconds"] == DEADLINE
    assert body["allocation"]["elapsed_seconds"] >= DEADLINE


def test_the_candidate_call_timeout_never_exceeds_what_is_left(gateway, client):
    """Bounded per call, and bounded by the remaining budget: what makes the total provable."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48

    body = ensure(client, headers, operation_id="op-alloc-0000103", max_hourly_price=0.52)

    assert body["state"] == "starting_pod"
    assert gateway.runpod.create_timeouts == [CANDIDATE_TIMEOUT]
    assert max(gateway.runpod.catalog_timeouts) <= CANDIDATE_TIMEOUT
    now = gateway.runpod.time
    assert gateway.authority.candidate_call_timeout(now + timedelta(seconds=60)) == CANDIDATE_TIMEOUT
    assert gateway.authority.candidate_call_timeout(now + timedelta(seconds=3)) == 3.0
    assert gateway.authority.candidate_call_timeout(now) == 1.0  # never a non-positive timeout


def test_the_allocation_record_explains_the_failure_without_secrets(gateway, client):
    """The record answers \"why did Canalla fail to find a GPU?\" without guessing."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    gateway.runpod.create_refusal = 400

    body = ensure(client, headers, operation_id="op-alloc-0000104", max_hourly_price=1.20)

    record = body["allocation"]
    assert record["outcome"] == "capacity_unavailable"
    assert record["reason"] == "gpu_unavailable"
    assert record["budget_seconds"] == DEADLINE
    assert [item["datacenter"] for item in record["attempts"]] == ["US-TX-3", "US-TX-3"]
    assert [item["cloud"] for item in record["attempts"]] == ["SECURE", "SECURE"]
    assert all(item["error_code"] == "placement_rejected" for item in record["attempts"])
    assert all(item["elapsed_seconds"] >= 0 for item in record["attempts"])
    assert record["policy"]["candidate_limit"] == CANDIDATE_LIMIT
    assert record["policy"]["cloud_tiers"] == ["SECURE"]
    assert record["policy"]["allocation_timeout_seconds"] == DEADLINE

    durable = audit(gateway, "allocation")
    assert len(durable) == 1
    stored = durable[0].cost
    assert stored["outcome"] == "capacity_unavailable"
    assert stored["operation_id"] == "op-alloc-0000104"
    assert len(stored["attempts"]) == 2

    # Status reports the same record for the operation that produced it.
    assert status(client, headers)["allocation"]["outcome"] == "capacity_unavailable"

    # Nothing secret ever enters a client-facing payload or the audit record.
    text = json.dumps(body, ensure_ascii=False) + json.dumps(stored, ensure_ascii=False)
    for name in ("api_key", "bearer", "runpod_api", "pod-gateway-key"):
        assert name not in text.lower()
    assert installation["installation_secret"] not in text


def test_community_is_used_only_when_policy_and_user_both_allow_it(gateway, client):
    """A visible, double-gated choice: never a hidden behaviour change."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 1.00  # Secure Cloud is above the user's own maximum
    gateway.runpod.community = True
    gateway.runpod.community_price = 0.30

    # Deployment policy still forbids the tier: no candidate, and the reason says why.
    body = ensure(client, headers, operation_id="op-alloc-0000105", max_hourly_price=0.52)
    assert gateway.runpod.creates == []
    assert body["error_code"] == "price_limit"
    assert body["policy"]["community_allowed"] is False
    assert body["policy"]["community_blocked_by"] == "network_volume"

    # The operator permits it and declares the placement community-capable, but the user did not
    # opt in: still nothing is created silently.
    gateway.settings.runpod_allow_community_cloud = True
    gateway.settings.runpod_datacenters = "US-TX-3:uwgeaie5b0:community"
    body = ensure(client, headers, operation_id="op-alloc-0000106", max_hourly_price=0.52)
    assert gateway.runpod.creates == []
    assert body["error_code"] == "price_limit"
    assert body["policy"]["community_allowed"] is True
    assert body["policy"]["cloud_tiers"] == ["SECURE", "COMMUNITY"]

    # With the user's own opt-in the compatible Community candidate is booked.
    body = ensure(
        client, headers, operation_id="op-alloc-0000107", max_hourly_price=0.52, allow_community=True
    )
    assert body["state"] == "starting_pod"
    assert gateway.runpod.creates[0]["cloud"] == "COMMUNITY"
    assert body["allocation"]["selected"]["cloud"] == "COMMUNITY"


def test_secure_cloud_is_always_preferred_over_a_cheaper_community_candidate(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    gateway.runpod.community = True
    gateway.runpod.community_price = 0.20  # cheaper, and still not the first choice
    gateway.settings.runpod_allow_community_cloud = True
    gateway.settings.runpod_datacenters = "US-TX-3:uwgeaie5b0:community"

    body = ensure(
        client, headers, operation_id="op-alloc-0000108", max_hourly_price=0.52, allow_community=True
    )

    assert body["state"] == "starting_pod"
    plan = [item["cloud"] for item in body["allocation"]["plan"]["candidates"]]
    assert plan[0] == "SECURE"
    assert "COMMUNITY" in plan
    assert plan.index("SECURE") < plan.index("COMMUNITY")
    assert gateway.runpod.creates[0]["cloud"] == "SECURE"


def test_a_stopped_pod_on_the_volume_is_never_treated_as_capacity(gateway, client):
    """A stopped Pod is not bookable capacity: a fresh candidate is created instead of waiting."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    gateway.runpod.attached_pod(name="alex-gw-stopped", status="EXITED", pod_id="pod-old")
    gateway.runpod.refuse_gpus = {"gpu-48"}

    body = ensure(client, headers, operation_id="op-alloc-0000109", max_hourly_price=1.20)

    assert body["state"] == "starting_pod"
    assert [pod["status"] for pod in gateway.runpod.pods] == ["EXITED", "RUNNING"]
    assert body["session"]["gpu"] == "NVIDIA L40S"
    assert gateway.runpod.actions == []  # the stopped Pod is left alone, not terminated
    assert gateway.runpod.pods[1]["mounts"]["network"][0]["volumeId"] == "uwgeaie5b0"


def test_a_vanished_gpu_ends_with_the_catalogue_conclusion(gateway, client):
    """`not_found` on every candidate is a catalogue conclusion, not a capacity promise."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    gateway.runpod.create_refusal = 404

    body = ensure(client, headers, operation_id="op-alloc-0000110", max_hourly_price=1.20)

    assert body["state"] == "offline"
    assert body["error_code"] == "no_compatible_gpu"
    assert body["ai"] == "unavailable"
    assert gateway.runpod.pods == []


def test_a_manual_choice_is_a_pin_never_a_substitution(gateway, client):
    """`selection: manual` keeps the named card — a different one is never a silent fallback."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    gateway.runpod.create_refusal = 400

    body = ensure(
        client,
        headers,
        operation_id="op-alloc-0000111",
        max_hourly_price=1.20,
        selection="manual",
        gpu_id="NVIDIA L40S",
    )

    assert created_gpus(gateway) == ["NVIDIA L40S"]
    assert body["allocation"]["outcome"] == "capacity_unavailable"
    assert body["error_code"] == "gpu_unavailable"
