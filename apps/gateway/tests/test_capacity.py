"""Capacity and money semantics: no stock is not a price problem, and a budget is a ceiling.

A user's session budget says how much a session *may* spend, never how much money must
already sit on the RunPod account. The account balance still bounds every session, and a
genuinely empty account is refused honestly.
"""

from conftest import auth_header, enroll, ensure_body


def ensure(client, headers, **body):
    response = client.post("/compute/ensure", json=ensure_body(**body), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_case_a_capacity_only_under_the_max_is_gpu_unavailable(gateway, client):
    """Compatible hardware exists in the catalogue and its price fits, but nothing is bookable."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.availability = "NONE"
    gateway.runpod.price = 0.48
    body = ensure(client, headers, operation_id="op-capacity-a001", max_hourly_price=1.20)
    assert gateway.runpod.creates == []
    assert body["state"] == "searching"
    assert body["error_code"] == "gpu_unavailable"


def test_case_b_stock_above_the_user_max_is_price_limit(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 2.00
    body = ensure(client, headers, operation_id="op-capacity-b001", max_hourly_price=0.52)
    assert gateway.runpod.creates == []
    assert body["state"] == "searching"
    assert body["error_code"] == "price_limit"


def test_case_c_stock_within_the_user_max_is_chosen(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 0.48
    body = ensure(client, headers, operation_id="op-capacity-c001", max_hourly_price=0.52)
    assert len(gateway.runpod.creates) == 1
    assert body["state"] == "starting_pod"
    assert gateway.runpod.creates[0]["gpu"]["id"] == "gpu-48"
    assert float(body["session"]["max_hourly_price_usd"]) <= 0.52


def test_case_d_mixed_catalogue_reports_price_not_capacity(gateway, client):
    """Some entries are out of stock, but a bookable one exists and is simply too expensive."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.price = 2.00  # gpu-48 and the L40S row are in stock, both above the max
    body = ensure(client, headers, operation_id="op-capacity-d001", max_hourly_price=0.52)
    assert gateway.runpod.creates == []
    assert body["error_code"] == "price_limit"


def test_case_e_no_compatible_capacity_anywhere_is_gpu_unavailable(gateway, client):
    """The cheap price must not mask a catalogue where nothing is bookable."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.availability = "NONE"
    gateway.runpod.price = 0.10
    body = ensure(client, headers, operation_id="op-capacity-e001", max_hourly_price=1.20)
    assert gateway.runpod.creates == []
    assert body["error_code"] == "gpu_unavailable"


def test_no_compatible_hardware_at_all_is_still_its_own_code(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    body = ensure(
        client, headers, operation_id="op-capacity-f001", max_hourly_price=1.20, min_vram_gb=1024
    )
    assert gateway.runpod.creates == []
    assert body["error_code"] == "no_compatible_gpu"


def test_a_small_balance_does_not_block_a_larger_user_ceiling(gateway, client):
    """$3 budget next to a $0.81 balance starts: the ceiling is min(budget, balance)."""
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.balance = "0.81"
    gateway.runpod.price = 0.48
    body = ensure(
        client, headers, operation_id="op-money-000001", max_hourly_price=1.20, session_budget=3.00
    )
    assert len(gateway.runpod.creates) == 1
    assert body["state"] == "starting_pod"
    assert float(body["session"]["budget_usd"]) == 0.81  # what the account can actually fund
    assert float(gateway.sessions_rows[0].session_budget) == 0.81


def test_the_users_own_ceiling_still_wins_over_a_larger_balance(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    body = ensure(client, headers, operation_id="op-money-000002", session_budget=3.00)
    assert float(body["session"]["budget_usd"]) == 3.00  # 12.34 available, $3 ceiling kept
    assert float(gateway.sessions_rows[0].session_budget) == 3.00


def test_an_empty_account_is_refused_honestly(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.balance = "0"
    body = ensure(client, headers, operation_id="op-money-000003", session_budget=3.00)
    assert gateway.runpod.creates == []
    assert body["error_code"] == "runpod_balance"


def test_a_cent_is_not_enough_to_start_paid_compute(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.balance = "0.004"
    body = ensure(client, headers, operation_id="op-money-000004", session_budget=3.00)
    assert gateway.runpod.creates == []
    assert body["error_code"] == "runpod_balance"
