from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..models import new_id, now


class ComputeSession(Base):
    __tablename__ = "compute_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    started_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    pod_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    pod_name: Mapped[str] = mapped_column(String(120))
    managed: Mapped[bool] = mapped_column(Boolean, default=True)
    network_volume_id: Mapped[str] = mapped_column(String(64))
    datacenter: Mapped[str] = mapped_column(String(32))
    gpu_type: Mapped[str] = mapped_column(String(160))
    gpu_vram_mb: Mapped[int] = mapped_column(Integer)
    hourly_rate: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    max_hourly_price: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    session_budget: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    auto_stop_minutes: Mapped[int] = mapped_column(Integer, default=10)
    pending_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    billable_seconds: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(16, 6), default=0)
    actual_cost: Mapped[Decimal | None] = mapped_column(Numeric(16, 6), nullable=True)
    actual_cost_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ComputeControl(Base):
    """One row per deployment. CAS lease serializes mutations across processes."""

    __tablename__ = "compute_control"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    active_session_id: Mapped[str | None] = mapped_column(ForeignKey("compute_sessions.id"), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    search_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    search_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    search_quote_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    search_state: Mapped[str] = mapped_column(String(32), default="offline")
    next_search_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)


class ComputeQuote(Base):
    __tablename__ = "compute_quotes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    options: Mapped[list] = mapped_column(JSON)
    preferences: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ComputePreference(Base):
    __tablename__ = "compute_preferences"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    values: Mapped[dict] = mapped_column(JSON)


class ComputeEvent(Base):
    __tablename__ = "compute_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("compute_sessions.id"), nullable=True, index=True
    )
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(48))
    code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class GenerationUsage(Base):
    __tablename__ = "generation_usage"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    chat_id: Mapped[str | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"), nullable=True)
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    compute_session_id: Mapped[str | None] = mapped_column(ForeignKey("compute_sessions.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="generating")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
