"""Shared balance: one upstream read for everyone, single-flight, never a fake zero."""

from __future__ import annotations

from datetime import timedelta

from conftest import auth_header, enroll, make_existing_installation


def balance(client, headers):
    response = client.get("/balance", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_balance_carries_money_as_a_string_without_provider_details(gateway, client):
    installation = enroll(gateway, client)
    body = balance(client, auth_header(client, installation))
    assert body["configured"] is True
    assert body["available"] is True
    assert body["balance_usd"] == "12.34"
    assert body["shared_account"] is True
    assert body["read_only"] is True
    assert body["stale"] is False
    assert gateway.runpod.queries == ["query AlexAccount { myself { id clientBalance currentSpendPerHr } }"]


def test_two_installations_share_one_upstream_snapshot(gateway, client):
    first = enroll(gateway, client, label="PC A")
    second = make_existing_installation(gateway, "PC B")
    one = balance(client, auth_header(client, first))
    two = balance(client, auth_header(client, second))
    assert one["balance_usd"] == two["balance_usd"]
    assert len(gateway.runpod.queries) == 1
    assert gateway.authority.balance.fetches == 1


def test_repeated_reads_inside_the_ttl_reuse_the_cache(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    for _ in range(5):
        balance(client, headers)
    assert gateway.authority.balance.fetches == 1


def test_a_new_snapshot_replaces_the_old_one_after_the_ttl(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    assert balance(client, headers)["balance_usd"] == "12.34"
    gateway.runpod.balance = "9.99"
    gateway.runpod.time += timedelta(seconds=20)
    refreshed = balance(client, headers)
    assert refreshed["balance_usd"] == "9.99"
    assert refreshed["last_success_at"] is not None
    assert gateway.authority.balance.fetches == 2


def test_provider_failure_never_fakes_zero_and_keeps_the_last_value(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    assert balance(client, headers)["balance_usd"] == "12.34"
    gateway.runpod.balance_failure = 500
    gateway.runpod.time += timedelta(seconds=20)
    body = balance(client, headers)
    assert body["available"] is True
    assert body["stale"] is True
    assert body["balance_usd"] == "12.34"
    assert body["error_code"] == "runpod_unavailable"


def test_unconfigured_provider_reports_not_configured_without_a_value(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.set_provider_key("")
    body = balance(client, headers)
    assert body["configured"] is False
    assert body["available"] is False
    assert body["balance_usd"] is None
    assert body["error_code"] == "not_configured"
    gateway.set_provider_key("test-only-fake-runpod-key")


def test_compute_status_embeds_the_same_shared_snapshot(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    body = client.get("/compute/status", headers=headers).json()
    assert body["balance"]["balance_usd"] == "12.34"
    assert len(gateway.runpod.queries) == 1
