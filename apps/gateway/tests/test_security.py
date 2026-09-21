"""Secret hygiene: what the Gateway stores, what it returns, and what it never leaks."""

from __future__ import annotations

import json

from conftest import FAKE_KEY, POD_KEY, auth_header, enroll, ensure_body, run

from gateway.models import (
    AuditEvent,
    EnrollmentCode,
    GatewayCompute,
    GatewayOperation,
    GatewaySession,
    Installation,
)

SECRET_NAMES = (
    "secret",
    "password",
    "token",
    "api_key",
    "apikey",
    "bearer",
    "authorization",
    "runpod",
    "jwt",
)

TABLES = (Installation, EnrollmentCode, GatewayCompute, GatewaySession, GatewayOperation, AuditEvent)


def all_stored_text(gateway) -> str:
    parts: list[str] = []
    for model in TABLES:
        with gateway.sessions() as db:
            for row in db.query(model).all():
                for column in model.__table__.columns:
                    parts.append(str(getattr(row, column.name)))
    return "\n".join(parts)


def keys_of(payload, prefix=""):
    found = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.append(str(key))
            found.extend(keys_of(value, f"{prefix}{key}."))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(keys_of(item, prefix))
    return found


def test_gateway_database_holds_no_raw_secrets(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    client.post("/compute/ensure", json=ensure_body(operation_id="op-secret-000001"), headers=headers)
    run(gateway.authority.tick())
    client.post("/v1/chat/completions", json={"messages": []}, headers=headers)

    stored = all_stored_text(gateway)
    for secret in (
        installation["installation_secret"],
        installation["activation_code"],
        FAKE_KEY,
        POD_KEY,
        gateway.settings.jwt_secret,
    ):
        assert secret not in stored
    with gateway.sessions() as db:
        row = db.query(Installation).one()
    assert row.secret_hash
    assert row.secret_hash != installation["installation_secret"]


def test_installation_secret_digest_is_irreversible(gateway, client):
    installation = enroll(gateway, client)
    with gateway.sessions() as db:
        row = db.query(Installation).one()
    assert len(row.secret_hash) == 64
    assert installation["installation_secret"] not in row.secret_hash


def test_client_facing_payloads_expose_no_provider_or_credential_fields(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    ensure = client.post(
        "/compute/ensure", json=ensure_body(operation_id="op-secret-000002"), headers=headers
    )
    payloads = [
        client.get("/health").json(),
        client.get("/compute/status", headers=headers).json(),
        client.get("/balance", headers=headers).json(),
        ensure.json(),
    ]
    for payload in payloads:
        text = json.dumps(payload, ensure_ascii=False).lower()
        for name in ("pod_id", "pod-", "proxy.runpod.net", "secret", "api_key", "token", "bearer"):
            assert name not in text, name
        for value in (FAKE_KEY, POD_KEY, installation["installation_secret"]):
            assert value not in text


def test_audit_events_never_store_prompts_or_credentials(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    client.post("/compute/ensure", json=ensure_body(operation_id="op-secret-000003"), headers=headers)
    run(gateway.authority.tick())
    prompt = "секретный пользовательский промпт 42"
    client.post(
        "/v1/chat/completions", json={"messages": [{"role": "user", "content": prompt}]}, headers=headers
    )

    with gateway.sessions() as db:
        events = db.query(AuditEvent).all()
        rows = [
            {
                "operation": event.operation,
                "result": event.result,
                "detail": event.detail,
                "error_code": event.error_code,
            }
            for event in events
        ]
    assert rows
    serialized = json.dumps(rows, ensure_ascii=False)
    assert prompt not in serialized
    for secret in (installation["installation_secret"], FAKE_KEY, POD_KEY):
        assert secret not in serialized
    assert {row["operation"] for row in rows} & {"enroll", "ensure", "creating", "starting_pod"}


def test_error_envelopes_are_stable_and_hide_internals(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.create_failure = 503
    response = client.post(
        "/compute/ensure", json=ensure_body(operation_id="op-secret-000004"), headers=headers
    )
    assert response.status_code == 200  # ambiguous create is a state, not a transport error
    failure = client.get("/compute/status", headers=headers)
    assert failure.status_code == 200

    missing = client.get("/v1/models", headers=headers)
    assert set(missing.json()) == {"detail", "code", "request_id"}
    assert missing.json()["code"] == "compute_offline"
    assert "Traceback" not in missing.text
    assert "site-packages" not in missing.text
    assert "alex-llm" not in missing.text


def test_health_never_echoes_configuration_values(gateway, client):
    body = client.get("/health").json()
    assert set(body) == {
        "product",
        "version",
        "gateway_protocol_version",
        "ready",
        "database",
        "provider_configured",
        "time",
    }
    text = json.dumps(body)
    for secret in (FAKE_KEY, POD_KEY, gateway.settings.jwt_secret):
        assert secret not in text
