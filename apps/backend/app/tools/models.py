from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..models import new_id, now


class ToolPreferences(Base):
    __tablename__ = "tool_preferences"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    values: Mapped[dict] = mapped_column(JSON, default=dict)


class ToolRun(Base):
    __tablename__ = "tool_runs"
    __table_args__ = (Index("ix_tool_runs_owner_started", "user_id", "started_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    generation_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(40))
    risk_level: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="planning")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    input_digest: Mapped[str] = mapped_column(String(64))
    cost_estimate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cost_actual: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    provider_run_id: Mapped[str | None] = mapped_column(String(160))
    error_code: Mapped[str | None] = mapped_column(String(80))
    result_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    origin: Mapped[str] = mapped_column(String(32), default="model")
    assigned_device_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebSourceSnapshot(Base):
    __tablename__ = "web_source_snapshots"
    __table_args__ = (Index("ix_web_sources_generation_label", "generation_id", "label", unique=True),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    generation_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    tool_run_id: Mapped[str] = mapped_column(ForeignKey("tool_runs.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(12))
    url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(400))
    excerpt: Mapped[str] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(String(300))
    published_at: Mapped[str | None] = mapped_column(String(80))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    searched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rank: Mapped[int]
    provider: Mapped[str] = mapped_column(String(40))
    etag: Mapped[str | None] = mapped_column(String(300))
    last_modified: Mapped[str | None] = mapped_column(String(100))
    channel: Mapped[str] = mapped_column(String(12), default="web")
    authority: Mapped[str | None] = mapped_column(String(40))
    canonical_url: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str | None] = mapped_column(String(20))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class PairedDevice(Base):
    __tablename__ = "paired_devices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str] = mapped_column(String(80), default="Windows device")
    platform: Mapped[str] = mapped_column(String(32), default="windows")
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    credential_hash: Mapped[str] = mapped_column(String(64))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LocalTask(Base):
    __tablename__ = "local_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="PLANNING")
    workspace: Mapped[str] = mapped_column(String(500), default="")
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
