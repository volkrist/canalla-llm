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


class HostResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    digest: str = Field(min_length=64, max_length=64)
    status: str = Field(default="completed", max_length=32)
    exit_code: int | None = Field(default=None, ge=-1, le=255)
    stdout: str = Field(default="", max_length=20000)
    stderr: str = Field(default="", max_length=20000)
    text: str = Field(default="", max_length=20000)
    metadata: dict = Field(default_factory=dict)


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
        if _aware(row.last_seen) and _aware(row.last_seen) >= cutoff:
            return row
    return None
