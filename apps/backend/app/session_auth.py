"""Persistent device sessions: hashed refresh secrets, rotation, revocation.

The raw refresh secret exists only on the client device (Windows Credential
Manager / DPAPI fallback). The backend stores a SHA-256 digest and enforces
expiry, revocation and single-use rotation: every successful refresh consumes
the presented secret and issues a new one, so a replayed (rotated) secret is
rejected. Expiry is a sliding window bounded by an absolute cap measured from
created_at (AUTH_SESSION_DAYS / AUTH_SESSION_MAX_DAYS): no session can be
extended forever.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from sqlalchemy import CursorResult, delete, or_, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import AuthSession, User


def now() -> datetime:
    return datetime.now(timezone.utc)


def utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes for DateTime(timezone=True) columns.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_refresh_secret() -> str:
    # token_urlsafe(32) is 256 bits of cryptographic randomness.
    return secrets.token_urlsafe(32)


def session_lifetime() -> timedelta:
    return timedelta(days=get_settings().auth_session_days)


def session_max_lifetime() -> timedelta:
    """Absolute cap from created_at: activity can never extend a session forever."""
    return timedelta(days=get_settings().auth_session_max_days)


def next_expiry(row: AuthSession, current: datetime) -> datetime:
    """min(now + sliding window, created_at + absolute max)."""
    sliding = current + session_lifetime()
    absolute = utc(row.created_at) + session_max_lifetime()
    return min(sliding, absolute)


class SessionRejected(Exception):
    """The presented persistent session credential is not acceptable."""


def create_session(db: Session, user: User, device_id: str | None = None) -> tuple[str, str]:
    """Create a persistent session row; returns (session_id, raw_secret)."""
    raw = new_refresh_secret()
    row = AuthSession(
        user_id=user.id,
        device_id=_clean_device_id(device_id),
        token_hash=hash_secret(raw),
        expires_at=now() + session_lifetime(),
    )
    db.add(row)
    db.commit()
    return row.id, raw


def refresh_session(
    db: Session, session_id: str, raw_secret: str, device_id: str | None = None
) -> tuple[User, AuthSession, str]:
    """Validate and rotate a persistent session.

    Returns (user, session_row, new_raw_secret). Raises SessionRejected for an
    unknown, wrong, expired, revoked or replayed credential.
    """
    digest = hash_secret(raw_secret or "")
    row = db.scalar(select(AuthSession).where(AuthSession.id == session_id))
    if row is None:
        raise SessionRejected()
    current = now()
    if row.revoked_at is not None or utc(row.expires_at) < current:
        raise SessionRejected()
    if utc(row.created_at) + session_max_lifetime() <= current:
        # Absolute lifetime exhausted: even an active session must end.
        raise SessionRejected()
    if row.rotated_from is not None and digest == row.rotated_from:
        # Replay of a consumed (rotated) secret.
        raise SessionRejected()
    if not secrets.compare_digest(row.token_hash, digest):
        raise SessionRejected()
    user = db.get(User, row.user_id)
    if user is None:
        raise SessionRejected()
    fresh = new_refresh_secret()
    row.rotated_from = row.token_hash
    row.replaced_at = current
    row.token_hash = hash_secret(fresh)
    row.last_used_at = current
    # Rolling sliding window, bounded by the absolute cap from created_at:
    # a session cannot be extended indefinitely.
    row.expires_at = next_expiry(row, current)
    clean_device = _clean_device_id(device_id)
    if clean_device and row.device_id is None:
        row.device_id = clean_device
    db.commit()
    return user, row, fresh


def revoke_session(db: Session, session_id: str, raw_secret: str) -> bool:
    """Revoke the session if the presented credential matches. Idempotent."""
    row = db.scalar(select(AuthSession).where(AuthSession.id == session_id))
    if row is None or row.revoked_at is not None:
        return False
    if not secrets.compare_digest(row.token_hash, hash_secret(raw_secret or "")):
        return False
    row.revoked_at = now()
    db.commit()
    return True


def revoke_all_for_user(db: Session, user_id: str) -> int:
    """Account-wide revocation (future password change / account block)."""
    rows = db.scalars(
        select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    ).all()
    current = now()
    for row in rows:
        row.revoked_at = current
    db.commit()
    return len(rows)


def purge_stale(db: Session) -> int:
    """Housekeeping: drop sessions expired or revoked more than a week ago."""
    cutoff = now() - timedelta(days=7)
    result = db.execute(
        delete(AuthSession).where(or_(AuthSession.expires_at < cutoff, AuthSession.revoked_at < cutoff))
    )
    db.commit()
    # A DELETE always runs on a cursor; the base Result type does not expose rowcount.
    return cast("CursorResult[Any]", result).rowcount or 0


def _clean_device_id(device_id: str | None) -> str | None:
    value = (device_id or "").strip()
    if not value or len(value) > 64 or not all(c.isalnum() or c in "._-" for c in value):
        return None
    return value
