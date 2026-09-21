"""Enrollment, installation credentials, short-lived access tokens and rate limits.

Three different secrets exist in this service and they must never be confused:

* an **activation code** — one-time, short TTL, stored only as a digest;
* an **installation secret** — returned once at enrollment, stored only as a digest, and
  used only to mint tokens;
* a **gateway access token** — short-lived JWT held in the client's memory, carrying the
  installation id. It is the only credential business endpoints accept.

The RunPod master key is not part of this module at all: it never leaves server config.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import GatewaySettings
from .errors import GatewayError
from .models import EnrollmentCode, Installation

INSTALLATION_SECRET_BYTES = 32  # 256 bits of entropy before urlsafe encoding
ACTIVATION_CODE_BYTES = 24


def now() -> datetime:
    return datetime.now(timezone.utc)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def hash_secret(value: str) -> str:
    """Digest for stored codes and installation secrets. Raw values are never persisted."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def new_installation_secret() -> str:
    return secrets.token_urlsafe(INSTALLATION_SECRET_BYTES)


def new_activation_code() -> str:
    return secrets.token_urlsafe(ACTIVATION_CODE_BYTES)


def normalize_activation_code(value: str) -> str:
    """Activation codes travel through chat/mail; normalise before hashing."""
    return "".join(value.split()).strip()


def activate_code(db: Session, raw_code: str, *, ttl_minutes: int, label: str = "") -> tuple[str, str]:
    """Create an activation code. Returns (raw code, digest). Only the digest is stored."""
    code = raw_code or new_activation_code()
    digest = hash_secret(normalize_activation_code(code))
    db.add(
        EnrollmentCode(
            code_hash=digest,
            label=label[:120],
            expires_at=now() + timedelta(minutes=ttl_minutes),
        )
    )
    db.commit()
    return code, digest


def redeem_code(db: Session, code: str, installation: Installation) -> None:
    """Single-use, race-safe redemption.

    The row is updated only while it is unredeemed, unrevoked and unexpired, so two
    concurrent enrollments can never both succeed. The caller commits; on failure the
    caller rolls the whole transaction (including the new installation) back.
    """
    digest = hash_secret(normalize_activation_code(code))
    result = db.execute(
        update(EnrollmentCode)
        .where(
            EnrollmentCode.code_hash == digest,
            EnrollmentCode.redeemed_at.is_(None),
            EnrollmentCode.revoked_at.is_(None),
            EnrollmentCode.expires_at > now(),
        )
        .values(redeemed_at=now(), redeemed_by=installation.id)
    )
    if result.rowcount == 1:
        return
    row = db.scalar(select(EnrollmentCode).where(EnrollmentCode.code_hash == digest))
    if row is None:
        raise GatewayError("activation_code_rejected")
    if row.revoked_at is not None:
        raise GatewayError("activation_code_rejected")
    if row.redeemed_at is not None:
        raise GatewayError("activation_code_used")
    raise GatewayError("activation_code_expired")


def issue_token(
    settings: GatewaySettings, installation_id: str, *, now_value: datetime | None = None
) -> dict:
    issued = now_value or now()
    expires = issued + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": installation_id,
        "jti": secrets.token_urlsafe(16),
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "scope": "installation",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.jwt_expire_minutes * 60,
        "expires_at": expires.isoformat(),
        "gateway_protocol_version": settings.gateway_protocol_version,
    }


def decode_token(settings: GatewaySettings, token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError:
        raise GatewayError("gateway_auth_failed") from None


def installation_for_token(db: Session, settings: GatewaySettings, token: str) -> Installation:
    claims = decode_token(settings, token)
    installation = db.get(Installation, str(claims.get("sub")))
    if installation is None:
        raise GatewayError("installation_unknown")
    if installation.revoked_at is not None:
        raise GatewayError("installation_revoked")
    return installation


class RateLimiter:
    """In-process sliding window.

    The v1 deployment runs a single uvicorn worker (the compute lease is authoritative in
    the database, so extra workers would be safe for correctness but the counters are not
    shared). Documented in docs/gateway-deployment.md.
    """

    def __init__(self, window_seconds: int, limits: dict[str, int]):
        self.window = window_seconds
        self.limits = limits
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, scope: str, key: str) -> None:
        limit = self.limits.get(scope)
        if not limit:
            return
        bucket = self._hits[f"{scope}:{key}"]
        cutoff = now() - timedelta(seconds=self.window)
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            raise GatewayError("gateway_rate_limited")
        bucket.append(now())

    def reset(self) -> None:
        self._hits.clear()


def limiter_for(settings: GatewaySettings) -> RateLimiter:
    return RateLimiter(
        settings.rate_window_seconds,
        {
            "enroll": settings.enrollment_rate_limit,
            "token": settings.token_rate_limit,
            "compute": settings.compute_rate_limit,
            "inference": settings.inference_rate_limit,
        },
    )
