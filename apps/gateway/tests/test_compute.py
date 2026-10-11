"""Global compute authority: one Pod, no blind retry, server-side money, read-only status."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

from conftest import auth_header, enroll, ensure_body, make_existing_installation, run


def ensure(client, headers, **body):
    response = client.post("/compute/ensure", json=ensure_body(**body), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def status(client, headers):
    response = client.get("/compute/status", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_status_is_read_only_and_never_creates_compute(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    body = status(client, headers)
    assert body["state"] == "offline"
    assert body["ai"] == "off"
    assert body["session"] is None
    assert gateway.runpod.creates == []
    assert gateway.runpod.actions == []


def test_ensure_creates_one_pristine_pod_and_reports_starting(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    body = ensure(client, headers, task_id="task-1")
    assert body["state"] == "starting_pod"
    assert body["ai"] == "starting"
    assert len(gateway.runpod.creates) == 1
    created = gateway.runpod.creates[0]
    assert created["mounts"]["network"][0]["volumeId"] == "uwgeaie5b0"
    assert created["dataCenterIds"] == ["US-TX-3"]
    assert created["gpu"]["count"] == 1
    assert created["env"]["ALEX_LLM_MODEL"] == "orcarouter-qwen38-27b-q5km"
    assert created["name"].startswith("alex-gw-")
    assert body["session"]["gpu"] in {"gpu-48", "NVIDIA L40S"}
    assert body["session"]["managed"] is True
    assert body["session"]["started_by_installation_id"] == installation["installation_id"]


def test_ready_requires_the_model_alias_from_the_pod_gateway(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers)
    run(gateway.authority.tick())
    assert gateway.runpod.creates and gateway.runpod.pods[0]["status"] == "RUNNING"
    assert status(client, headers)["state"] == "ready"
    assert status(client, headers)["ai"] == "ready"


def test_readiness_is_not_claimed_when_the_alias_is_missing(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers)
    gateway.settings.llm_model = "some-other-model"
    run(gateway.authority.tick())
    body = status(client, headers)
    assert body["state"] == "loading_model"
    assert body["ai"] == "starting"
    gateway.settings.llm_model = "orcarouter-qwen38-27b-q5km"


def test_second_ensure_does_not_create_a_second_pod(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    first = ensure(client, headers, operation_id="op-first-000001")
    second = ensure(client, headers, operation_id="op-second-00001", task_id="task-2")
    assert len(gateway.runpod.creates) == 1
    assert first["session"]["id"] == second["session"]["id"]


def test_same_operation_id_returns_the_recorded_result(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    first = ensure(client, headers, operation_id="op-repeat-000001")
    gateway.runpod.creates.clear()
    second = ensure(client, headers, operation_id="op-repeat-000001")
    assert second == first
    assert gateway.runpod.creates == []


def test_operation_id_cannot_be_reused_by_another_installation(gateway, client):
    first = enroll(gateway, client, label="PC A")
    second = make_existing_installation(gateway, "PC B")
    ensure(client, auth_header(client, first), operation_id="op-shared-000001")
    response = client.post(
        "/compute/ensure",
        json=ensure_body(operation_id="op-shared-000001"),
        headers=auth_header(client, second),
    )
    assert response.status_code == 400
    assert response.json()["code"] == "gateway_invalid_request"


def test_concurrent_ensure_creates_exactly_one_pod(gateway, client):
    first = enroll(gateway, client, label="PC A")
    second = enroll(gateway, client, label="PC B")
    authority = gateway.authority

    async def race():
        return await asyncio.gather(
            authority.ensure(first["installation_id"], operation_id="op-race-0000001", task_id="t1"),
            authority.ensure(second["installation_id"], operation_id="op-race-0000002", task_id="t2"),
            return_exceptions=True,
        )

    results = run(race())
    assert len(gateway.runpod.creates) == 1
    session_ids = {row.id for row in gateway.sessions_rows}
    assert len(session_ids) == 1
    for result in results:
        if isinstance(result, Exception):
            # A losing caller is told the authority is busy and never creates anything.
            assert getattr(result, "code", "") == "gateway_busy"
        else:
            assert result["state"] in {"starting_pod", "loading_model"}
            assert result["session"]["id"] in session_ids


def test_two_authorities_still_create_one_pod(gateway, client):
    """Simulates two gateway workers: the database lease is the only coordinator."""
    import httpx

    from gateway.compute import ComputeAuthority
    from gateway.provider import provider_api

    settings = gateway.settings
    api_one = provider_api(settings, httpx.MockTransport(gateway.runpod.handle))
    api_two = provider_api(settings, httpx.MockTransport(gateway.runpod.handle))
    one = ComputeAuthority(settings, api=api_one, sessions=gateway.sessions)
    two = ComputeAuthority(settings, api=api_two, sessions=gateway.sessions)
    one.transport = httpx.MockTransport(gateway.llama.handle)
    two.transport = httpx.MockTransport(gateway.llama.handle)

    async def race():
        return await asyncio.gather(
            one.ensure("installation-a", operation_id="op-worker-00001"),
            two.ensure("installation-b", operation_id="op-worker-00002"),
            return_exceptions=True,
        )

    run(race())
    assert len(gateway.runpod.creates) == 1
    assert len(gateway.sessions_rows) == 1


def ensure_error(client, headers, **body):
    return client.post("/compute/ensure", json=ensure_body(**body), headers=headers)


def test_a_user_policy_is_honoured_instead_of_clamped(gateway, client):
    """Money policy belongs to the user: no hidden $1.20 / $3 product ceiling."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 1.50
    body = ensure(
        client,
        headers,
        operation_id="op-policy-000001",
        max_hourly_price=2.00,
        session_budget=10.00,
    )
    rows = gateway.sessions_rows
    assert len(rows) == 1
    assert float(body["session"]["budget_usd"]) == 10.00
    assert float(rows[0].session_budget) == 10.00
    # Whatever the user allowed, the session never records more than the GPU actually costs.
    assert float(rows[0].max_hourly_price) <= 2.00
    assert float(rows[0].hourly_rate) <= 2.00


def test_a_user_may_lower_their_policy_below_the_default(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.40
    body = ensure(
        client,
        headers,
        operation_id="op-policy-000002",
        max_hourly_price=0.40,
        session_budget=1.00,
    )
    rows = gateway.sessions_rows
    assert float(body["session"]["budget_usd"]) == 1.00
    assert float(rows[0].hourly_rate) == 0.40
    assert float(rows[0].max_hourly_price) <= 0.40


def test_invalid_policy_is_rejected_never_silently_replaced(gateway, client):
    """Structural nonsense is refused by the schema; policy bounds by the money layer."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    for index, caps in enumerate([{"max_hourly_price": 999}, {"session_budget": 5000}]):
        response = ensure_error(client, headers, operation_id=f"op-badpolicy-{index:04d}", **caps)
        assert response.status_code == 422, (caps, response.text)
        assert response.json()["code"] == "compute_policy_invalid"
        assert "больше 0" in response.json()["detail"]
    for index, caps in enumerate([{"max_hourly_price": -1}, {"max_hourly_price": 0}, {"session_budget": -3}]):
        response = ensure_error(client, headers, operation_id=f"op-badshape-{index:04d}", **caps)
        assert response.status_code == 422, (caps, response.text)
    assert gateway.runpod.creates == []
    assert gateway.runpod.actions == []


def test_defaults_apply_when_a_client_sends_no_policy(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    body = ensure(client, headers, operation_id="op-default-000001")
    assert float(body["session"]["budget_usd"]) == 3.00
    assert float(body["session"]["max_hourly_price_usd"]) <= 0.52


def test_cheapest_compatible_gpu_wins_even_when_the_max_allows_more(gateway, client):
    """A higher maximum must not make the Gateway pick a more expensive GPU."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48  # gpu-48 becomes the cheapest selectable option
    body = ensure(
        client,
        headers,
        operation_id="op-cheap-000001",
        max_hourly_price=1.20,
        session_budget=3.00,
        # A stray exact-GPU hint must not pin automatic selection to a pricier card.
        gpu_id="NVIDIA L40S",
    )
    assert gateway.runpod.creates[0]["gpu"]["id"] == "gpu-48"
    # A12: the session records the user's authorized ceiling, not the cheapest candidate's quote.
    assert float(body["session"]["max_hourly_price_usd"]) == 1.20
    assert float(body["session"]["hourly_rate_usd"]) <= 0.48


def test_manual_selection_pins_the_named_gpu(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    body = ensure(
        client,
        headers,
        operation_id="op-manual-000001",
        max_hourly_price=1.20,
        session_budget=3.00,
        selection="manual",
        gpu_id="NVIDIA L40S",
    )
    assert gateway.runpod.creates[0]["gpu"]["id"] == "NVIDIA L40S"
    assert float(body["session"]["max_hourly_price_usd"]) <= 1.20


def test_the_ordering_strategy_reaches_the_allocator(gateway, client):
    """The user's own choice of *order*, honoured by the same walk.

    The fake catalogue offers gpu-48 at $0.48 with LOW stock and an L40S at $1.09 with HIGH stock,
    so the two strategies provably select different cards without changing any filter.
    """
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    body = ensure(
        client,
        headers,
        operation_id="op-strategy-cheap",
        max_hourly_price=2.00,
        session_budget=5.00,
        strategy="cheapest",
    )
    assert gateway.runpod.creates[0]["gpu"]["id"] == "gpu-48"
    # A12: the ceiling is the user's own maximum, never the winning candidate's price.
    assert float(body["session"]["max_hourly_price_usd"]) == 2.00


def test_fastest_prefers_the_best_stocked_compatible_card(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    body = ensure(
        client,
        headers,
        operation_id="op-strategy-fast",
        max_hourly_price=2.00,
        session_budget=5.00,
        strategy="fastest",
    )
    assert gateway.runpod.creates[0]["gpu"]["id"] == "NVIDIA L40S"
    # The strategy orders candidates, it never widens the ceiling the user set (A12): the session
    # records the user's own maximum, and the actual GPU rate is below it.
    assert float(body["session"]["max_hourly_price_usd"]) == 2.00
    assert float(body["session"]["hourly_rate_usd"]) <= 2.00


def test_an_unknown_strategy_is_refused_as_malformed_never_silently_replaced(gateway, client):
    """Same rule as the money policy: a value the server cannot honour is not quietly dropped."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    response = ensure_error(client, headers, operation_id="op-strategy-bad", strategy="turbo")
    assert response.status_code == 422, response.text
    assert gateway.runpod.creates == []
    # And the default is the one the product documents, so an older client that sends nothing
    # keeps the balanced order rather than falling into an unordered walk.
    body = ensure(client, headers, operation_id="op-strategy-none", auto_stop_minutes=30)
    assert body["state"] == "starting_pod"


def test_the_manual_strategy_without_a_card_is_refused(gateway, client):
    """It cannot mean "choose anything": that is what the default strategy is for."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    response = ensure_error(client, headers, operation_id="op-strategy-manual", strategy="manual")
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "compute_policy_invalid"
    assert gateway.runpod.creates == []


def test_the_manual_strategy_is_a_pin_by_the_other_name_too(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    ensure(
        client,
        headers,
        operation_id="op-strategy-pin",
        max_hourly_price=1.20,
        session_budget=3.00,
        strategy="manual",
        gpu_id="NVIDIA L40S",
    )
    assert gateway.runpod.creates[0]["gpu"]["id"] == "NVIDIA L40S"


def test_a_policy_below_every_quote_never_creates_compute(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.79
    body = ensure(
        client,
        headers,
        operation_id="op-cheap-000002",
        max_hourly_price=0.52,
        session_budget=3.00,
    )
    assert body["state"] == "searching"
    assert body["error_code"] == "price_limit"
    assert gateway.runpod.creates == []


def test_stricter_client_limits_are_accepted(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    body = ensure(client, headers, operation_id="op-caps-0000002", max_hourly_price=1.00, session_budget=2.00)
    assert float(body["session"]["budget_usd"]) == 2.00
    assert float(body["session"]["max_hourly_price_usd"]) <= 1.00


def test_limits_that_pick_no_gpu_never_create_a_pod(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.60
    body = ensure(
        client,
        headers,
        operation_id="op-caps-0000003",
        max_hourly_price=0.5,
        session_budget=3.0,
        auto_stop_minutes=15,
    )
    assert gateway.runpod.creates == []
    assert body["state"] == "searching"
    assert body["error_code"] in {"price_limit", "no_compatible_gpu"}


def test_client_auto_stop_outside_the_allowed_set_is_replaced(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    body = ensure(client, headers, operation_id="op-idle-0000001", auto_stop_minutes=7)
    assert body["session"]["auto_stop_minutes"] == gateway.settings.compute_idle_minutes


def test_the_session_keeps_the_clients_own_auto_stop_value(gateway, client):
    """A14: a value inside the allowed set is the session's own policy, not the server default."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    assert gateway.settings.compute_idle_minutes != 5

    body = ensure(client, headers, operation_id="op-idle-0000002", auto_stop_minutes=5)

    assert body["session"]["auto_stop_minutes"] == 5
    # The value the panel reads back is the value the session actually runs with.
    current = client.get("/compute/status", headers=headers).json()
    assert current["session"]["auto_stop_minutes"] == 5


def test_ambiguous_create_becomes_create_unknown_and_is_not_retried(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.create_failure = "timeout"
    body = ensure(client, headers, operation_id="op-unknown-00001")
    assert body["state"] == "create_unknown"
    assert body["ai"] == "waiting"
    assert len(gateway.runpod.creates) == 1

    # A retry inside the resolve window resolves nothing and creates nothing.
    gateway.runpod.time += timedelta(seconds=10)
    again = ensure(client, headers, operation_id="op-unknown-00002")
    assert again["state"] == "create_unknown"
    assert len(gateway.runpod.creates) == 1


def test_create_attempt_budget_stops_the_loop(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.create_failure = "timeout"
    ensure(client, headers, operation_id="op-budget-000001")
    for index in range(6):
        gateway.runpod.time += timedelta(seconds=120)
        ensure(client, headers, operation_id=f"op-budget-{index:06d}")
    # One create plus exactly one evidence-based retry: never an unbounded loop.
    assert len(gateway.runpod.creates) == 2
    assert gateway.control.create_attempts >= 2
    assert status(client, headers)["state"] == "create_unknown"

    for index in range(6, 12):
        gateway.runpod.time += timedelta(seconds=120)
        ensure(client, headers, operation_id=f"op-budget-{index:06d}")
    assert len(gateway.runpod.creates) == 2

    # The operator path clears the budget after a manual provider review.
    gateway.authority.reset_budget()
    gateway.runpod.create_failure = None
    gateway.runpod.time += timedelta(seconds=120)
    body = ensure(client, headers, operation_id="op-budget-final1")
    assert body["state"] == "starting_pod"
    assert len(gateway.runpod.creates) == 3


def test_create_unknown_is_resolved_by_reconciliation_when_no_pod_exists(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.create_failure = "server"
    first = ensure(client, headers, operation_id="op-resolve-00001")
    assert first["state"] == "create_unknown"
    gateway.runpod.create_failure = None
    gateway.runpod.time += timedelta(seconds=120)
    second = ensure(client, headers, operation_id="op-resolve-00002")
    assert second["state"] == "starting_pod"
    assert len(gateway.runpod.pods) == 1


def test_two_pods_on_the_volume_are_reported_and_left_alone(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.attached_pod("alex-gw-other-1", pod_id="pod-a")
    gateway.runpod.attached_pod("alex-gw-other-2", pod_id="pod-b")
    body = ensure(client, headers, operation_id="op-multi-0000001")
    assert body["state"] == "multiple_compute"
    assert body["ai"] == "error"
    assert gateway.runpod.creates == []
    assert gateway.runpod.actions == []


def test_external_compute_is_adopted_but_never_duplicated_or_destroyed(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.attached_pod("alex-llm-someone-else", pod_id="pod-ext")
    body = ensure(client, headers, operation_id="op-ext-00000001")
    assert body["state"] == "external_compute"
    assert body["ai"] == "waiting"
    assert gateway.runpod.creates == []
    assert body["session"]["managed"] is False
    assert body["session"]["adopted"] is True

    stop = client.post("/compute/stop", json={"operation_id": "op-ext-stop-0001"}, headers=headers)
    assert stop.status_code == 409
    assert stop.json()["code"] == "external_compute"
    assert gateway.runpod.actions == []


def test_idle_stop_never_touches_adopted_compute(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.attached_pod("alex-llm-someone-else", pod_id="pod-ext")
    ensure(client, headers, operation_id="op-ext-idle-0001")
    gateway.runpod.time += timedelta(minutes=45)
    run(gateway.authority.tick())
    assert gateway.runpod.actions == []
    assert gateway.runpod.pods[0]["status"] == "RUNNING"


def test_a_balance_below_the_budget_still_funds_the_session(gateway, client):
    """A $3 session ceiling is a maximum, not a prepaid requirement (RC money semantics)."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.balance = "1.00"
    body = ensure(client, headers, operation_id="op-money-0000001")
    assert len(gateway.runpod.creates) == 1
    assert float(body["session"]["budget_usd"]) == 1.00  # min($3 ceiling, $1.00 available)
    gateway.runpod.balance = "12.34"


def test_budget_reached_stops_compute_with_the_existing_code(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-udget-0000001", session_budget=0.10)
    gateway.runpod.time += timedelta(minutes=20)
    run(gateway.authority.tick())
    assert gateway.runpod.actions == [{"action": "terminate"}]
    body = status(client, headers)
    assert body["state"] == "stopped"
    assert body["error_code"] == "COMPUTE_BUDGET_REACHED"
    assert body["ai"] == "error"


def test_price_violation_stops_compute(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-price-0000001")
    gateway.runpod.pods[0]["cost"] = 4.5
    run(gateway.authority.tick())
    assert gateway.runpod.actions == [{"action": "terminate"}]
    assert status(client, headers)["error_code"] == "price_violation"


def test_idle_stop_uses_global_activity(gateway, client):
    first = enroll(gateway, client, label="PC A")
    second = enroll(gateway, client, label="PC B")
    ensure(client, auth_header(client, first), operation_id="op-idle-1000001", auto_stop_minutes=5)
    run(gateway.authority.tick())  # reaches ready

    # Installation B keeps working: compute is not idle even though A stopped talking.
    gateway.runpod.time += timedelta(minutes=4)
    gateway.authority.mark_activity()
    gateway.runpod.time += timedelta(minutes=4)
    run(gateway.authority.tick())
    assert gateway.runpod.actions == []

    # Everyone is quiet long enough: the shared compute stops once.
    gateway.runpod.time += timedelta(minutes=30)
    run(gateway.authority.tick())
    assert gateway.runpod.actions == [{"action": "terminate"}]
    assert status(client, auth_header(client, second))["state"] == "stopped"


def test_manual_stop_terminates_only_gateway_managed_compute(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-stop-0000001")
    response = client.post("/compute/stop", json={"operation_id": "op-stop-0000002"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["state"] == "stopped"
    assert response.json()["last_session"]["stop_reason"] == "manual"
    assert gateway.runpod.actions == [{"action": "terminate"}]
    # Idempotent replay of the same stop never terminates twice.
    again = client.post("/compute/stop", json={"operation_id": "op-stop-0000002"}, headers=headers)
    assert again.json() == response.json()
    assert len(gateway.runpod.actions) == 1


def test_provider_outage_keeps_local_authority_and_reports_unavailable(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-outage-00001")
    gateway.runpod.list_failure = 503
    run(gateway.authority.reconcile())
    body = status(client, headers)
    assert body["session"] is not None
    assert body["error_code"] == "runpod_unavailable"
    gateway.runpod.list_failure = None
    run(gateway.authority.reconcile())
    assert status(client, headers)["state"] in {"loading_model", "ready", "starting_pod"}


def test_pod_that_disappears_is_reported_stopped(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-gone-0000001")
    gateway.runpod.pods.clear()
    run(gateway.authority.reconcile())
    body = status(client, headers)
    assert body["state"] == "stopped"
    assert body["session"] is None
    assert body["last_session"]["managed"] is True
    assert body["last_session"]["stop_reason"] == "provider_missing"
    assert body["last_session"]["stopped_at"] is not None


def test_the_tick_finalizes_a_pod_the_provider_no_longer_knows(gateway, client):
    """The periodic tick — not only an explicit reconcile — must clear a vanished Pod.

    The provider answers `not_found` for the tracked Pod, which is evidence. Before this the tick
    returned with the control row still on `generating`/`not_found`, so the five-chip layer showed
    a green «Connected» while the account had no Pod at all and `/v1/models` answered 503.
    """
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-vanished-000001")
    assert status(client, headers)["state"] in {"starting_pod", "loading_model", "ready"}

    gateway.runpod.pods.clear()
    run(gateway.authority.tick())

    body = status(client, headers)
    assert body["state"] == "stopped"
    assert body["session"] is None
    assert body["error_code"] == "not_found"
    assert body["last_session"]["stop_reason"] == "provider_missing"


def test_unconfigured_provider_is_honest(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.set_provider_key("")
    body = status(client, headers)
    assert body["configured"] is False
    assert body["ai"] == "unavailable"
    run(gateway.authority.reconcile())
    assert status(client, headers)["state"] == "not_configured"
    assert gateway.runpod.creates == []
    gateway.set_provider_key("test-only-fake-runpod-key")


def test_provider_outage_reconciles_without_destroying_state(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-outage-100001")
    session_id = status(client, headers)["session"]["id"]
    gateway.runpod.list_failure = 503
    run(gateway.authority.reconcile())
    body = status(client, headers)
    assert body["session"]["id"] == session_id
    assert body["error_code"] == "runpod_unavailable"
    gateway.runpod.list_failure = None
    run(gateway.authority.reconcile())
    assert status(client, headers)["error_code"] is None


# ------------------------------------------------------------------- A01: create_unknown


def test_the_tick_keeps_an_ambiguous_create_under_observation(gateway, client):
    """A01: an ambiguous create is watched server-side after the client has gone.

    The Pod the provider actually created is found by the durable name the intent committed,
    adopted as *managed* and therefore kept under budget control — instead of hanging as an
    unresolvable intent until the next client call, and without a blind second create.
    """
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.create_failure = "timeout"

    response = client.post(
        "/compute/ensure", json=ensure_body(operation_id="op-a01-00000001"), headers=headers
    )
    assert response.json()["state"] == "create_unknown"
    assert gateway.runpod.pods == []

    # The create really did take effect: the Pod answers under the name the intent committed.
    intent_name = gateway.sessions_rows[0].pod_name
    assert intent_name
    gateway.runpod.attached_pod(name=intent_name)
    gateway.runpod.time += timedelta(seconds=5)

    # Only the background loop runs now: no client asks for anything.
    run(gateway.authority.tick())

    row = gateway.sessions_rows[0]
    assert row.managed is True
    assert row.adopted is False
    assert row.pod_id == "pod-1"
    assert row.state in {"starting_pod", "loading_model"}
    assert gateway.control.state in {"starting_pod", "loading_model"}
    assert gateway.control.active_session_id == row.id
    assert len(gateway.runpod.creates) == 1


def test_the_tick_resolves_an_ambiguous_create_by_provider_evidence(gateway, client):
    """A01: with no Pod on the Volume the intent is resolved by provider evidence — bounded,
    server-side, and never by firing a second create."""
    from gateway.compute import CREATE_RESOLVE_SECONDS

    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.create_failure = "timeout"
    client.post("/compute/ensure", json=ensure_body(operation_id="op-a01-00000002"), headers=headers)
    assert gateway.control.state == "create_unknown"

    gateway.runpod.time += timedelta(seconds=CREATE_RESOLVE_SECONDS + 1)
    run(gateway.authority.tick())

    assert gateway.control.state == "offline"
    assert gateway.control.error_code == "create_unknown"
    assert gateway.control.active_session_id is None
    assert len(gateway.runpod.creates) == 1
    assert gateway.runpod.pods == []


def test_a_late_appearing_pod_is_adopted_not_duplicated(gateway, client):
    """A01: a lost create response whose Pod becomes visible only after a lag is adopted.

    The dangerous sequence is create → timeout → a momentarily empty list → the Pod appears. The
    empty read is a consistency lag, never proof of absence, so the Gateway observes, adopts the
    Pod by its durable identity and never fires a second create. Invariant: create count == 1.
    """
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.create_after_timeout = True
    gateway.runpod.create_failure = "timeout"
    # Two empty reads: the ensure's own reconcile, then the first observation tick. The Pod only
    # becomes visible on the third read.
    gateway.runpod.list_empty_reads = 2

    body = ensure(client, headers, operation_id="op-a01-late-000001")
    assert body["state"] == "create_unknown"
    assert len(gateway.runpod.creates) == 1
    assert len(gateway.runpod.pods) == 1  # the Pod exists; only the response was lost

    # First observation: the list is empty (lag). That is not evidence, so the intent is kept and
    # nothing is created.
    run(gateway.authority.tick())
    assert len(gateway.runpod.creates) == 1
    assert status(client, headers)["state"] == "create_unknown"

    # The Pod becomes visible: it is adopted by its durable identity, still a single create.
    run(gateway.authority.tick())
    assert len(gateway.runpod.creates) == 1
    row = gateway.sessions_rows[0]
    assert row.managed is True
    assert row.pod_id == "pod-1"
    assert status(client, headers)["state"] in {"starting_pod", "loading_model"}

    # And it can be stopped normally — nothing is left billing.
    stop = client.post("/compute/stop", json={"operation_id": "op-a01-late-stop1"}, headers=headers)
    assert stop.json()["state"] == "stopped"
    assert gateway.runpod.actions == [{"action": "terminate"}]


# ------------------------------------------------------------------------ A02: stop retry


def test_a_refused_stop_is_retried_until_the_provider_confirms(gateway, client):
    """A02: «stopped» is only reported once the provider confirmed the termination.

    The intent is durable: the refused stop keeps owning the state (a later pod-status read
    does not re-report a healthy startup), the client keeps seeing «stopping», and the
    background loop retries from persisted state with a bounded backoff.
    """
    from gateway.compute import STOP_RETRY_SECONDS

    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-a02-00000001")
    assert gateway.runpod.pods[0]["status"] == "RUNNING"

    # The provider refuses the first termination and would accept the next one.
    gateway.runpod.action_failure = 500
    gateway.runpod.action_failures = 1
    stopped = client.post("/compute/stop", json={"operation_id": "op-a02-00000002"}, headers=headers)
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "stopping"
    assert gateway.runpod.pods[0]["status"] == "RUNNING"

    # A status read agrees, and the intent survives a pod-status read from the loop.
    assert status(client, headers)["state"] == "stopping"
    run(gateway.authority.tick())
    assert status(client, headers)["state"] == "stopping"
    assert len(gateway.runpod.actions) == 1

    # After the backoff window the retry lands — and only then is «stopped» reported.
    gateway.runpod.time += timedelta(seconds=STOP_RETRY_SECONDS + 1)
    run(gateway.authority.tick())
    assert gateway.runpod.pods[0]["status"] == "TERMINATED"
    assert len(gateway.runpod.actions) == 2
    assert status(client, headers)["state"] == "stopped"


def test_a_refused_idle_stop_is_retried_until_the_provider_confirms(gateway, client):
    """A02: the durable stop intent is not only for a manual stop.

    An idle stop the provider refuses keeps owning the state («stopping») while the Pod is still
    billing, and the background loop retries it from persisted state until termination is proven.
    """
    from gateway.compute import STOP_RETRY_SECONDS

    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-a02-idle-00001", auto_stop_minutes=5)
    run(gateway.authority.tick())
    assert status(client, headers)["state"] == "ready"

    gateway.runpod.action_failure = 503
    gateway.runpod.action_failures = 1
    gateway.runpod.time += timedelta(minutes=30)
    run(gateway.authority.tick())

    assert len(gateway.runpod.actions) == 1  # the idle stop was attempted
    assert gateway.runpod.pods[0]["status"] == "RUNNING"  # and refused: still billing
    body = status(client, headers)
    assert body["state"] == "stopping"
    assert body["error_code"] == "runpod_unavailable"

    gateway.runpod.time += timedelta(seconds=STOP_RETRY_SECONDS + 1)
    run(gateway.authority.tick())
    assert len(gateway.runpod.actions) == 2
    assert gateway.runpod.pods[0]["status"] == "TERMINATED"
    assert status(client, headers)["state"] == "stopped"


def test_a_stop_lost_to_a_network_timeout_is_retried(gateway, client):
    """A02: a transport failure on the terminate call is not a completed stop."""
    from gateway.compute import STOP_RETRY_SECONDS

    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-a02-net-000001")

    gateway.runpod.action_failure = "timeout"
    gateway.runpod.action_failures = 1
    stopped = client.post("/compute/stop", json={"operation_id": "op-a02-net-stop01"}, headers=headers)
    assert stopped.json()["state"] == "stopping"
    assert gateway.runpod.pods[0]["status"] == "RUNNING"

    gateway.runpod.time += timedelta(seconds=STOP_RETRY_SECONDS + 1)
    run(gateway.authority.tick())
    assert gateway.runpod.pods[0]["status"] == "TERMINATED"
    assert status(client, headers)["state"] == "stopped"


def test_a_refused_stop_survives_a_gateway_restart(gateway, client):
    """A02: the retry is driven by persisted state, so a restart never abandons a billing Pod."""
    import httpx

    from gateway.compute import STOP_RETRY_SECONDS, ComputeAuthority
    from gateway.provider import provider_api

    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure(client, headers, operation_id="op-a02-restart-01")

    gateway.runpod.action_failure = 500
    gateway.runpod.action_failures = 1
    stopped = client.post("/compute/stop", json={"operation_id": "op-a02-restart-02"}, headers=headers)
    assert stopped.json()["state"] == "stopping"

    api = provider_api(gateway.settings, httpx.MockTransport(gateway.runpod.handle))
    restarted = ComputeAuthority(gateway.settings, api=api, sessions=gateway.sessions)
    restarted.transport = httpx.MockTransport(gateway.llama.handle)
    restarted.clock = lambda: gateway.runpod.time

    gateway.runpod.time += timedelta(seconds=STOP_RETRY_SECONDS + 1)
    run(restarted.tick())

    assert gateway.runpod.pods[0]["status"] == "TERMINATED"
    assert status(client, headers)["state"] == "stopped"


# ------------------------------------------------------------------- A12: price ceiling


def test_a_more_expensive_second_candidate_keeps_running(gateway, client):
    """A12: a cheaper refused candidate must not cap the next, still-allowed one.

    The user allows $1.20/h; the $0.48 candidate is refused and the $1.09 one is booked. The
    running Pod must be compared with the *user's* ceiling, not the first candidate's price, so it
    keeps running instead of being stopped for a «price violation» it never committed.
    """
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    gateway.runpod.refuse_gpus = {"gpu-48"}
    gateway.runpod.create_costs = {"NVIDIA L40S": 1.09}
    body = ensure(
        client,
        headers,
        operation_id="op-a12-fallthrough",
        max_hourly_price=1.20,
        session_budget=5.00,
    )
    assert [item["gpu"]["id"] for item in gateway.runpod.creates] == ["gpu-48", "NVIDIA L40S"]
    assert body["session"]["gpu"] == "NVIDIA L40S"
    assert float(body["session"]["max_hourly_price_usd"]) == 1.20
    assert float(body["session"]["hourly_rate_usd"]) == 1.09

    run(gateway.authority.tick())
    run(gateway.authority.tick())
    assert gateway.runpod.actions == []  # nothing stopped it — it is inside the user's ceiling
    assert status(client, headers)["state"] in {"ready", "loading_model"}
    assert status(client, headers)["error_code"] is None


def test_a_running_price_above_the_authorized_ceiling_is_still_stopped(gateway, client):
    """A12: the real running price is compared with the authorized ceiling, and a breach is still
    rejected — the fix separates the ceilings, it does not widen them."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    gateway.runpod.refuse_gpus = {"gpu-48"}
    gateway.runpod.create_costs = {"NVIDIA L40S": 1.09}
    ensure(client, headers, operation_id="op-a12-breach-0001", max_hourly_price=1.20, session_budget=5.00)
    run(gateway.authority.tick())
    assert status(client, headers)["state"] == "ready"

    gateway.runpod.pods[0]["cost"] = 1.50  # above the $1.20 the user authorized
    run(gateway.authority.tick())
    assert gateway.runpod.actions == [{"action": "terminate"}]
    assert status(client, headers)["error_code"] == "price_violation"


def test_an_operator_ceiling_tightens_the_authorized_maximum(gateway, client):
    """A12: the operator's own optional ceiling is honoured alongside the user's maximum."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.settings.operator_max_hourly_price = Decimal("0.60")
    gateway.runpod.price = 0.48
    body = ensure(
        client,
        headers,
        operation_id="op-a12-operator-01",
        max_hourly_price=2.00,
        session_budget=5.00,
    )
    # The user allows $2.00 but the operator caps $0.60, so only the $0.48 card can be booked.
    assert [item["gpu"]["id"] for item in gateway.runpod.creates] == ["gpu-48"]
    assert float(body["session"]["max_hourly_price_usd"]) == 0.60
    assert body["policy"]["operator_max_hourly_price_usd"] == "0.60"


# ------------------------------------------------- the interface connection contract (§8)


def test_the_connection_contract_reports_one_operation_and_stage(gateway, client):
    """The additive, read-only connection block the interface integrates against.

    One operation id, one derived stage, one startup deadline, the observation time, whether the
    attempt has concluded, the typed error and the readiness expiry — nothing else, and nothing
    that starts or stops compute.
    """
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    body = ensure(client, headers, operation_id="op-conn-00000001", max_hourly_price=1.20)

    connection = body["connection"]
    assert set(connection) == {
        "operation_id",
        "started_at",
        "startup_deadline",
        "last_success_at",
        "observed_at",
        "current_stage",
        "terminal_state",
        "typed_error",
        "readiness_valid_until",
    }
    assert connection["operation_id"] == "op-conn-00000001"
    assert connection["current_stage"] in {"creating", "starting"}
    assert connection["terminal_state"] is False
    assert connection["started_at"] is not None
    assert connection["startup_deadline"] is not None
    assert connection["readiness_valid_until"] is None

    run(gateway.authority.tick())
    connection = status(client, headers)["connection"]
    assert connection["current_stage"] == "ready"
    assert connection["terminal_state"] is True
    assert connection["last_success_at"] is not None
    assert connection["readiness_valid_until"] is not None
    assert connection["typed_error"] is None
