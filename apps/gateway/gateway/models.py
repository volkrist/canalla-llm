"""Gateway tables. Separate from the local Alex database: no chats, messages or documents."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def now():
    return datetime.now(timezone.utc)


def new_id():
    return str(uuid4())


class Installation(Base):
    """A trusted client installation. The raw secret exists only on the client device."""

    __tablename__ = "installations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), default="")
    platform: Mapped[str] = mapped_column(String(40), default="")
    client_version: Mapped[str] = mapped_column(String(40), default="")
    secret_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(200))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class EnrollmentCode(Base):
    """Single-use activation code. Only a digest is stored."""

    __tablename__ = "enrollment_codes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redeemed_by: Mapped[str | None] = mapped_column(String(36))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GatewayCompute(Base):
    """Singleton global compute state (v1: one Alex-managed compute for the whole account)."""

    __tablename__ = "gateway_compute"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(32), default="offline")
    active_session_id: Mapped[str | None] = mapped_column(String(36))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    # Server-side create-attempt budget for unresolved (ambiguous) creates.
    create_attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    last_operation_id: Mapped[str | None] = mapped_column(String(64))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GatewaySession(Base):
    """Provider compute ownership recorded by the Gateway, not by a client."""

    __tablename__ = "gateway_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    pod_id: Mapped[str | None] = mapped_column(String(80))
    pod_name: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(32), default="creating")
    gpu_type: Mapped[str] = mapped_column(String(80), default="")
    gpu_vram_mb: Mapped[int] = mapped_column(Integer, default=0)
    hourly_rate: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    max_hourly_price: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    session_budget: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    auto_stop_minutes: Mapped[int] = mapped_column(Integer, default=10)
    billable_seconds: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    create_attempts: Mapped[int] = mapped_column(Integer, default=0)
    intent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pending_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_installation_id: Mapped[str | None] = mapped_column(String(36))
    managed: Mapped[bool] = mapped_column(Boolean, default=True)
    adopted: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GatewayOperation(Base):
    """Idempotency record: a repeated operation id never creates a second effect."""

    __tablename__ = "gateway_operations"
    __table_args__ = (Index("ix_gateway_operations_installation", "installation_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    installation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    request_digest: Mapped[str] = mapped_column(String(64), default="")
    result_state: Mapped[str] = mapped_column(String(32), default="")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEvent(Base):
    """Operational audit trail. Never stores prompts, completions or secrets."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_created", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    installation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    operation: Mapped[str] = mapped_column(String(48))
    result: Mapped[str] = mapped_column(String(24))
    request_id: Mapped[str | None] = mapped_column(String(64))
    task_id: Mapped[str | None] = mapped_column(String(64))
    compute_session_id: Mapped[str | None] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(64))
    cost: Mapped[dict] = mapped_column(JSON, default=dict)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
