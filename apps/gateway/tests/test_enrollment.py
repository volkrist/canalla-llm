"""Enrollment: one-time codes, hash-only storage, race safety, protocol version."""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta, timezone

from conftest import enroll

from gateway.models import EnrollmentCode, Installation
from gateway.security import activate_code, hash_secret, new_installation_secret


def test_health_reports_readiness_without_secrets(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["database"] == "ok"
    assert body["gateway_protocol_version"] == 1
    assert body["provider_configured"] is True
    serialized = response.text.lower()
    for forbidden in ("runpod", "secret", "jwt", "bearer", "key", "password", "token"):
        assert forbidden not in serialized


def test_enrollment_requires_a_known_code(client):
    response = client.post("/enroll", json={"activation_code": "not-a-real-code"})
    assert response.status_code == 400
    assert response.json()["code"] == "activation_code_rejected"


def test_enrollment_code_is_single_use(gateway, client):
    first = enroll(gateway, client)
    second = client.post("/enroll", json={"activation_code": first["activation_code"]})
    assert second.status_code == 409
    assert second.json()["code"] == "activation_code_used"
    with gateway.sessions() as db:
        installations = db.query(Installation).count()
    assert installations == 1


def test_expired_code_is_rejected(gateway, client):
    with gateway.sessions() as db:
        code, _ = activate_code(db, "", ttl_minutes=60, label="later")
        row = db.query(EnrollmentCode).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    response = client.post("/enroll", json={"activation_code": code})
    assert response.status_code == 400
    assert response.json()["code"] == "activation_code_expired"


def test_revoked_code_is_rejected(gateway, client):
    with gateway.sessions() as db:
        code, _ = activate_code(db, "", ttl_minutes=60, label="revoked")
        row = db.query(EnrollmentCode).one()
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()
    response = client.post("/enroll", json={"activation_code": code})
    assert response.status_code == 400
    assert response.json()["code"] == "activation_code_rejected"


def test_server_stores_only_digests(gateway, client):
    installation = enroll(gateway, client)
    with gateway.sessions() as db:
        row = db.query(Installation).one()
        code_row = db.query(EnrollmentCode).one()
    assert row.secret_hash == hash_secret(installation["installation_secret"])
    assert installation["installation_secret"] not in row.secret_hash
    assert code_row.code_hash == hash_secret(installation["activation_code"])
    assert installation["activation_code"] != code_row.code_hash
    # Nothing in the response echoes what is stored.
    assert "secret_hash" not in installation


def test_installation_secret_has_256_bits():
    secret = new_installation_secret()
    assert len(secret) >= 43  # 32 random bytes in urlsafe base64
    assert new_installation_secret() != secret


def test_enrollment_race_has_exactly_one_winner(gateway, client):
    with gateway.sessions() as db:
        code, _ = activate_code(db, "", ttl_minutes=60, label="race")
    results: list[int] = []
    lock = threading.Lock()

    def attempt():
        response = client.post("/enroll", json={"activation_code": code})
        with lock:
            results.append(response.status_code)

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [200, 400, 400, 400] or sorted(results) == [200, 409, 409, 409]
    assert results.count(200) == 1
    with gateway.sessions() as db:
        assert db.query(Installation).count() == 1


def test_protocol_mismatch_is_explicit(client):
    response = client.post("/enroll", json={"activation_code": "anything", "gateway_protocol_version": 99})
    assert response.status_code == 409
    assert response.json()["code"] == "gateway_protocol_mismatch"


def test_enrollment_rejects_unknown_fields(client):
    response = client.post("/enroll", json={"activation_code": "whatever", "runpod_api_key": "leak"})
    assert response.status_code == 422


def test_activation_code_digest_is_stable_across_whitespace(gateway):
    with gateway.sessions() as db:
        activate_code(db, "ALEX-CODE-1234", ttl_minutes=60)
    with gateway.sessions() as db:
        row = db.query(EnrollmentCode).one()
    assert row.code_hash == hashlib.sha256("ALEX-CODE-1234".encode()).hexdigest()
