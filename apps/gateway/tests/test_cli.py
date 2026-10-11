"""Operator CLI: activation codes, listings, revocation — without a web admin panel."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

GATEWAY_ROOT = Path(__file__).resolve().parents[1]


def cli(gateway, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "DATABASE_URL": gateway.settings.database_url,
        "JWT_SECRET": gateway.settings.jwt_secret,
        "RUNPOD_API_KEY": "test-only-fake-runpod-key",
    }
    return subprocess.run(
        [sys.executable, "-m", "gateway.cli", *args],
        cwd=str(GATEWAY_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_create_code_prints_the_code_once_and_stores_only_a_digest(gateway, client):
    result = cli(gateway, "create-code", "--label", "PC A")
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines[0].startswith("activation code")
    code = lines[1].strip()
    assert len(code) >= 24
    assert result.stdout.count(code) == 1  # printed exactly once

    from gateway.security import hash_secret

    with gateway.sessions() as db:
        from gateway.models import EnrollmentCode

        row = db.query(EnrollmentCode).filter_by(code_hash=hash_secret(code)).one()
    assert row.label == "PC A"
    assert row.code_hash == hash_secret(code)

    # The code it printed really does enroll an installation.
    response = client.post("/enroll", json={"activation_code": code})
    assert response.status_code == 200
    assert response.json()["installation_id"]


def test_installations_listing_reports_state_without_secrets(gateway, client):
    from conftest import enroll

    enrollment = enroll(gateway, client, label="PC A")
    result = cli(gateway, "installations")
    assert result.returncode == 0, result.stderr
    assert enrollment["installation_id"] in result.stdout
    assert enrollment["installation_secret"] not in result.stdout
    assert "compute: state=" in result.stdout


def test_revoke_command_disables_the_installation(gateway, client):
    from conftest import auth_header, enroll

    enrollment = enroll(gateway, client, label="PC A")
    headers = auth_header(client, enrollment)
    assert client.get("/balance", headers=headers).status_code == 200
    result = cli(gateway, "revoke", "--installation-id", enrollment["installation_id"], "--reason", "manual")
    assert result.returncode == 0, result.stderr
    assert client.get("/balance", headers=headers).status_code == 403


def test_revoke_command_fails_loudly_for_unknown_installations(gateway):
    result = cli(gateway, "revoke", "--installation-id", "00000000-0000-0000-0000-000000000000")
    assert result.returncode == 1
    assert "not found" in result.stderr


def test_health_command_prints_configuration_without_secrets(gateway):
    result = cli(gateway, "health")
    assert result.returncode == 0, result.stderr
    assert "gateway_protocol_version" in result.stdout
    assert "test-only-fake-runpod-key" not in result.stdout
    assert gateway.settings.jwt_secret not in result.stdout


def test_reset_create_budget_clears_the_attempt_counter(gateway, client):
    from datetime import timedelta

    from conftest import auth_header, enroll, ensure_body

    enrollment = enroll(gateway, client, label="PC A")
    headers = auth_header(client, enrollment)
    gateway.runpod.create_failure = "timeout"
    client.post("/compute/ensure", json=ensure_body(operation_id="op-cli-000000001"), headers=headers)
    gateway.runpod.time += timedelta(seconds=120)
    client.post("/compute/ensure", json=ensure_body(operation_id="op-cli-000000002"), headers=headers)
    assert gateway.control.create_attempts >= 1

    result = cli(gateway, "reset-create-budget")
    assert result.returncode == 0, result.stderr
    assert gateway.control.create_attempts == 0


def test_audit_command_lists_operations_without_prompts(gateway, client):
    from conftest import enroll

    enroll(gateway, client, label="PC A")
    result = cli(gateway, "audit", "--limit", "5")
    assert result.returncode == 0, result.stderr
    assert "enroll" in result.stdout
    assert "activation" not in result.stdout.lower()


@pytest.mark.parametrize("command", ["create-code", "installations", "codes", "health", "audit"])
def test_cli_help_does_not_require_a_database(gateway, command):
    result = cli(gateway, command, "--help")
    assert result.returncode == 0, result.stderr


def test_set_role_promotes_an_installation_to_operator(gateway, client):
    """A11: the operator role is assigned only through the local operator CLI."""
    from conftest import enroll

    enrollment = enroll(gateway, client, label="Operator")
    result = cli(
        gateway, "set-role", "--installation-id", enrollment["installation_id"], "--role", "operator"
    )
    assert result.returncode == 0, result.stderr

    from gateway.models import Installation

    with gateway.sessions() as db:
        row = db.get(Installation, enrollment["installation_id"])
    assert row.role == "operator"


def test_set_entitlement_writes_and_clears_the_money_ceiling(gateway, client):
    """A10: the operator grants and revokes a money ceiling; no secret is printed."""
    from decimal import Decimal

    from conftest import enroll

    from gateway.models import Installation

    enrollment = enroll(gateway, client, label="REGYNA")
    result = cli(
        gateway,
        "set-entitlement",
        "--installation-id",
        enrollment["installation_id"],
        "--max-hourly-price",
        "0.52",
        "--session-budget",
        "1.00",
        "--period-budget",
        "5.00",
        "--period-days",
        "30",
    )
    assert result.returncode == 0, result.stderr
    assert enrollment["installation_secret"] not in result.stdout
    with gateway.sessions() as db:
        row = db.get(Installation, enrollment["installation_id"])
        assert row.entitlement_max_hourly_price == Decimal("0.52")
        assert row.entitlement_session_budget == Decimal("1.00")
        assert row.entitlement_period_budget == Decimal("5.00")
        assert row.entitlement_period_days == 30

    cleared = cli(gateway, "set-entitlement", "--installation-id", enrollment["installation_id"], "--clear")
    assert cleared.returncode == 0, cleared.stderr
    with gateway.sessions() as db:
        row = db.get(Installation, enrollment["installation_id"])
        assert row.entitlement_max_hourly_price is None
        assert row.entitlement_period_budget is None
