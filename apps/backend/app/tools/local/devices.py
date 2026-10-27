import hashlib
import secrets
from datetime import timedelta, timezone

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import User, now
from ..models import PairedDevice


def hash_credential(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def issue_credential() -> str:
    # token_urlsafe(32) is 256 bits of cryptographic randomness.
    return secrets.token_urlsafe(32)


def _aware(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def public_device(row: PairedDevice):
    seen = _aware(row.last_seen)
    online = bool(seen and seen >= now() - timedelta(seconds=45) and not row.revoked_at)
    return {
        "device_id": row.id,
        "display_name": row.display_name,
        "platform": row.platform,
        "capabilities": row.capabilities,
        "last_seen": row.last_seen,
        "online": online,
        "revoked": bool(row.revoked_at),
    }


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(default="Windows device", min_length=1, max_length=80)
    platform: str = Field(default="windows", max_length=32)
    capabilities: list[str] = Field(
        default_factory=lambda: ["fs", "process", "registry", "credential", "system"],
        max_length=20,
    )
    # The device this client is replacing. A re-pair after a real credential loss is a
    # continuation of one installation, not a new machine: the old row is revoked so a reconnect
    # cannot leave one active row per hour. Only a row owned by the authenticated user is touched.
    previous_device_id: str | None = Field(default=None, max_length=64)


class HostResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    digest: str = Field(min_length=64, max_length=64)
    claim_id: str = Field(default="", max_length=64)
    status: str = Field(default="completed", max_length=32)
    # A Windows process exit code is 32-bit: 9009 ("command not found" through the shell alias)
    # and negative crash codes such as -1073741510 are ordinary results. A tighter bound rejected
    # them as 422, the host retried the same delivery forever and the tool reported `timeout`
    # instead of the real exit code.
    exit_code: int | None = Field(default=None, ge=-(2**31), le=2**31 - 1)
    stdout: str = Field(default="", max_length=20000)
    stderr: str = Field(default="", max_length=20000)
    text: str = Field(default="", max_length=20000)
    metadata: dict = Field(default_factory=dict)


class HostClaim(BaseModel):
    """The lease a device references when it starts, acks or declines a job."""

    model_config = ConfigDict(extra="forbid")
    claim_id: str = Field(default="", max_length=64)


def require_device(db: Session, user: User, device_id: str | None, credential: str | None) -> PairedDevice:
    if not device_id or not credential:
        raise HTTPException(401, "device_auth")
    row = db.scalar(
        select(PairedDevice).where(
            PairedDevice.id == device_id, PairedDevice.user_id == user.id, PairedDevice.revoked_at.is_(None)
        )
    )
    if not row or row.credential_hash != hash_credential(credential):
        raise HTTPException(401, "device_auth")
    row.last_seen = now()
    db.commit()
    return row


def active_device(db: Session, user_id: str) -> PairedDevice | None:
    rows = db.scalars(
        select(PairedDevice)
        .where(PairedDevice.user_id == user_id, PairedDevice.revoked_at.is_(None))
        .order_by(PairedDevice.last_seen.desc())
    )
    cutoff = now() - timedelta(seconds=45)
    for row in rows:
        seen = _aware(row.last_seen)
        if seen and seen >= cutoff:
            return row
    return None


def newest_device(db: Session, user_id: str) -> PairedDevice | None:
    """The most recent pairing, alive or not: the reconnect window needs its last sighting."""
    return db.scalar(
        select(PairedDevice)
        .where(PairedDevice.user_id == user_id, PairedDevice.revoked_at.is_(None))
        .order_by(PairedDevice.last_seen.desc())
        .limit(1)
    )


def last_seen_seconds_ago(row: PairedDevice) -> float | None:
    seen = _aware(row.last_seen)
    if seen is None:
        return None
    return max(0.0, (now() - seen).total_seconds())
