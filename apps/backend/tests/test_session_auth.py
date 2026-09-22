"""Persistent device sessions, refresh rotation, revocation, first-owner bootstrap."""

import threading
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.auth import claim_first_owner
from app.database import SessionLocal
from app.models import AuthSession, BootstrapClaim, User
from app.security import can_start_compute
from app.session_auth import hash_secret, now, utc
from tests.db_helpers import require_row, require_scalar

PASSWORD = "test-password-123"


def _register(client, email="alice@example.com"):
    response = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201
    return response.json()


def _login(client, email="alice@example.com"):
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()


def _refresh(client, session_id, secret):
    return client.post("/auth/refresh", json={"session_id": session_id, "refresh_secret": secret})


def _revoke(client, session_id, secret):
    return client.post("/auth/revoke", json={"session_id": session_id, "refresh_secret": secret})


def test_register_returns_persistent_session_and_user(client):
    body = _register(client)
    assert body["access_token"] and body["token_type"] == "bearer"
    assert body["session_id"] and body["refresh_secret"] and body["expires_at"]
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["role"] == "user"


def test_state_is_first_run_then_auth_required(client):
    assert client.get("/auth/state").json() == {"users_exist": False, "state": "first_run"}
    _register(client)
    assert client.get("/auth/state").json() == {"users_exist": True, "state": "auth_required"}


def test_raw_refresh_secret_never_stored(client):
    body = _register(client)
    with SessionLocal() as db:
        row = require_row(db, AuthSession, body["session_id"])
        assert row.token_hash == hash_secret(body["refresh_secret"])
        assert row.token_hash != body["refresh_secret"]
        assert body["refresh_secret"] not in row.token_hash
        assert row.rotated_from is None and row.revoked_at is None
        assert utc(row.expires_at) > now() - timedelta(days=31)


def test_refresh_rotates_and_old_secret_is_rejected(client):
    body = _register(client)
    refreshed = _refresh(client, body["session_id"], body["refresh_secret"])
    assert refreshed.status_code == 200
    payload = refreshed.json()
    assert payload["access_token"] != body["access_token"]
    assert payload["refresh_secret"] != body["refresh_secret"]
    assert payload["user"]["email"] == "alice@example.com"
    # The rotated (consumed) secret must be rejected: replay protection.
    assert _refresh(client, body["session_id"], body["refresh_secret"]).status_code == 401
    # The new secret keeps working and the access token is valid.
    again = _refresh(client, body["session_id"], payload["refresh_secret"])
    assert again.status_code == 200
    me = client.get("/auth/me", headers={"Authorization": "Bearer " + payload["access_token"]})
    assert me.status_code == 200
    with SessionLocal() as db:
        row = require_row(db, AuthSession, body["session_id"])
        # After two rotations the consumed secret is the first issued one.
        assert row.rotated_from == hash_secret(payload["refresh_secret"])
        assert row.token_hash == hash_secret(again.json()["refresh_secret"])


def test_refresh_rejects_wrong_secret_and_wrong_session(client):
    body = _register(client)
    assert _refresh(client, body["session_id"], "x" * 43).status_code == 401
    assert _refresh(client, "no-such-session", body["refresh_secret"]).status_code == 401


def test_refresh_rejects_expired_session(client):
    body = _register(client)
    with SessionLocal() as db:
        row = require_row(db, AuthSession, body["session_id"])
        row.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert _refresh(client, body["session_id"], body["refresh_secret"]).status_code == 401


def test_revoke_rejects_further_refresh_and_is_idempotent(client):
    body = _register(client)
    assert _revoke(client, body["session_id"], body["refresh_secret"]).json() == {"revoked": True}
    assert _refresh(client, body["session_id"], body["refresh_secret"]).status_code == 401
    assert _revoke(client, body["session_id"], body["refresh_secret"]).json() == {"revoked": False}


def test_revoke_with_wrong_secret_fails_closed(client):
    _register(client)
    bob = _register(client, email="bob@example.com")
    assert _revoke(client, bob["session_id"], "y" * 43).json() == {"revoked": False}
    assert _refresh(client, bob["session_id"], bob["refresh_secret"]).status_code == 200


def test_multiple_users_sessions_are_isolated(client):
    alice = _register(client, email="alice@example.com")
    bob = _register(client, email="bob@example.com")
    _revoke(client, alice["session_id"], alice["refresh_secret"])
    assert _refresh(client, alice["session_id"], alice["refresh_secret"]).status_code == 401
    assert _refresh(client, bob["session_id"], bob["refresh_secret"]).status_code == 200


def test_relogin_as_other_user_maps_session_to_that_user(client):
    alice = _register(client, email="alice@example.com")
    _register(client, email="bob@example.com")
    bob = _login(client, email="bob@example.com")
    refreshed = _refresh(client, bob["session_id"], bob["refresh_secret"]).json()
    assert refreshed["user"]["email"] == "bob@example.com"
    with SessionLocal() as db:
        row = require_row(db, AuthSession, bob["session_id"])
        assert row.user_id == refreshed["user"]["id"]
    # Alice's own session was not affected.
    assert _refresh(client, alice["session_id"], alice["refresh_secret"]).status_code == 200


def test_revoke_one_session_keeps_another(client):
    first = _register(client)
    second = _login(client)
    _revoke(client, first["session_id"], first["refresh_secret"])
    assert _refresh(client, second["session_id"], second["refresh_secret"]).status_code == 200
    with SessionLocal() as db:
        rows = db.scalars(select(AuthSession).where(AuthSession.user_id == first["user"]["id"])).all()
        assert len(rows) == 2
        assert {row.revoked_at is not None for row in rows} == {True, False}


def test_existing_user_without_session_logs_in_once_then_restores(client):
    # Legacy DB shape: user exists but has no persistent session rows.
    body = _register(client)
    with SessionLocal() as db:
        db.execute(delete(AuthSession))
        db.commit()
    # The old access JWT stays valid (stateless) — no forced re-registration.
    me = client.get("/auth/me", headers={"Authorization": "Bearer " + body["access_token"]})
    assert me.status_code == 200
    # One login creates a fresh persistent session that survives refresh.
    login = _login(client)
    assert login["session_id"]
    assert _refresh(client, login["session_id"], login["refresh_secret"]).status_code == 200


def test_malformed_credentials_fail_closed(client):
    assert _refresh(client, "", "").status_code == 422
    assert _refresh(client, "x", "y").status_code == 401


def test_refresh_extends_sliding_expiry(client):
    body = _register(client)
    with SessionLocal() as db:
        row = require_row(db, AuthSession, body["session_id"])
        row.expires_at = now() + timedelta(days=1)
        db.commit()
    refreshed = _refresh(client, body["session_id"], body["refresh_secret"])
    assert refreshed.status_code == 200
    with SessionLocal() as db:
        row = require_row(db, AuthSession, body["session_id"])
        from app.config import get_settings

        assert utc(row.expires_at) >= now() + timedelta(days=get_settings().auth_session_days - 1)


def test_refresh_expiry_never_exceeds_absolute_max(client):
    from app.config import get_settings

    body = _register(client)
    with SessionLocal() as db:
        row = require_row(db, AuthSession, body["session_id"])
        row.created_at = now() - timedelta(days=get_settings().auth_session_max_days - 1)
        row.expires_at = now() + timedelta(hours=1)
        db.commit()
    refreshed = _refresh(client, body["session_id"], body["refresh_secret"])
    assert refreshed.status_code == 200
    with SessionLocal() as db:
        row = require_row(db, AuthSession, body["session_id"])
        # Capped at created_at + max: sliding would have been ~now+30d, the
        # absolute cap is ~now+1d.
        cap = utc(row.created_at) + timedelta(days=get_settings().auth_session_max_days)
        assert abs((utc(row.expires_at) - cap).total_seconds()) < 60
    assert refreshed.json()["expires_at"]


def test_active_session_rejected_after_absolute_max(client):
    from app.config import get_settings

    body = _register(client)
    with SessionLocal() as db:
        row = require_row(db, AuthSession, body["session_id"])
        row.created_at = now() - timedelta(days=get_settings().auth_session_max_days + 1)
        row.expires_at = now() + timedelta(hours=1)  # sliding window alone would allow it
        db.commit()
    assert _refresh(client, body["session_id"], body["refresh_secret"]).status_code == 401


def test_bootstrap_requires_runtime_proof(client):
    response = client.post("/auth/bootstrap", json={"email": "owner@example.com", "password": PASSWORD})
    assert response.status_code == 403
    assert client.get("/auth/state").json()["state"] == "first_run"


def test_bootstrap_creates_owner_with_session(client, monkeypatch):
    monkeypatch.setenv("ALEX_RUNTIME_TOKEN", "runtime-token-for-tests-0123456789")
    headers = {"X-Alex-Runtime-Token": "runtime-token-for-tests-0123456789"}
    response = client.post(
        "/auth/bootstrap",
        headers=headers,
        json={"email": "owner@example.com", "password": PASSWORD, "display_name": "Владелец"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "owner@example.com"
    assert body["user"]["display_name"] == "Владелец"
    assert body["session_id"] and body["refresh_secret"]
    with SessionLocal() as db:
        user = require_scalar(db, select(User))
        assert user.is_owner is True
        claim = require_row(db, BootstrapClaim, 1)
        assert claim.owner_user_id == user.id
        assert can_start_compute(user, db) is True
    # Wrong runtime token is rejected.
    monkeypatch.setenv("ALEX_RUNTIME_TOKEN", "different-token-0000000000000000")
    assert (
        client.post(
            "/auth/bootstrap",
            headers={"X-Alex-Runtime-Token": "runtime-token-for-tests-0123456789"},
            json={"email": "other@example.com", "password": PASSWORD},
        ).status_code
        == 403
    )


def test_bootstrap_closes_after_owner_exists(client, monkeypatch):
    monkeypatch.setenv("ALEX_RUNTIME_TOKEN", "rt-token")
    headers = {"X-Alex-Runtime-Token": "rt-token"}
    first = client.post(
        "/auth/bootstrap", headers=headers, json={"email": "owner@example.com", "password": PASSWORD}
    )
    assert first.status_code == 201
    second = client.post(
        "/auth/bootstrap", headers=headers, json={"email": "other@example.com", "password": PASSWORD}
    )
    assert second.status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(BootstrapClaim)) == 1


def test_bootstrap_closed_when_users_already_exist(client, monkeypatch):
    _register(client)
    monkeypatch.setenv("ALEX_RUNTIME_TOKEN", "rt-token")
    response = client.post(
        "/auth/bootstrap",
        headers={"X-Alex-Runtime-Token": "rt-token"},
        json={"email": "owner@example.com", "password": PASSWORD},
    )
    assert response.status_code == 409
    with SessionLocal() as db:
        user = require_scalar(db, select(User))
        assert user.is_owner is False
        assert db.scalar(select(func.count()).select_from(BootstrapClaim)) == 0


def test_register_after_owner_does_not_claim_owner(client, monkeypatch):
    monkeypatch.setenv("ALEX_RUNTIME_TOKEN", "rt-token")
    client.post(
        "/auth/bootstrap",
        headers={"X-Alex-Runtime-Token": "rt-token"},
        json={"email": "owner@example.com", "password": PASSWORD},
    )
    second = _register(client, email="second@example.com")
    with SessionLocal() as db:
        owner = require_scalar(db, select(User).where(User.email == "owner@example.com"))
        member = require_scalar(db, select(User).where(User.email == "second@example.com"))
        assert owner.is_owner is True
        assert member.is_owner is False
        assert can_start_compute(owner, db) is True
        assert can_start_compute(member, db) is False
    assert second["user"]["email"] == "second@example.com"


def test_bootstrap_race_has_exactly_one_winner():
    results = []
    barrier = threading.Barrier(2)

    def attempt(email):
        barrier.wait()
        with SessionLocal() as db:
            try:
                user = claim_first_owner(db, email, PASSWORD, None)
                results.append(("win", user.email))
            except HTTPException as error:
                results.append(("lose", error.status_code))

    threads = [threading.Thread(target=attempt, args=(f"owner{i}@example.com",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcome for outcome, _ in results) == ["lose", "win"]
    assert [status for outcome, status in results if outcome == "lose"] == [409]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(BootstrapClaim)) == 1
        owner = require_scalar(db, select(User))
        assert owner.is_owner is True
