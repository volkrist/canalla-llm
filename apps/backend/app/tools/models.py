from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
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
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


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
    chat_id: Mapped[str | None] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="CREATED")
    workspace: Mapped[str] = mapped_column(String(500), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    original_user_request: Mapped[str] = mapped_column(Text, default="")
    plan_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    current_step: Mapped[str] = mapped_column(String(80), default="")
    current_phase: Mapped[str] = mapped_column(String(40), default="CREATED")
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    stop_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    tool_calls_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tool_budget: Mapped[int] = mapped_column(Integer, default=40, server_default="40")
    elapsed_runtime: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    runtime_budget: Mapped[int] = mapped_column(Integer, default=1800, server_default="1800")
    files_changed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    file_change_budget: Mapped[int] = mapped_column(Integer, default=20, server_default="20")
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    retry_budget: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    last_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    completion_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    success_criteria: Mapped[dict] = mapped_column(JSON, default=dict)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskStep(Base):
    __tablename__ = "task_steps"
    __table_args__ = (Index("ix_task_steps_task_order", "task_id", "position"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("local_tasks.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    tool_category: Mapped[str] = mapped_column(String(40), default="")
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_summary: Mapped[str] = mapped_column(Text, default="")
    verification_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    key: Mapped[str] = mapped_column(String(40), default="")


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (Index("ix_task_events_task_created", "task_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("local_tasks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    tool_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TaskCheckpoint(Base):
    __tablename__ = "task_checkpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("local_tasks.id", ondelete="CASCADE"), index=True)
    plan_revision: Mapped[int] = mapped_column(Integer, default=1)
    current_step: Mapped[str] = mapped_column(String(80), default="")
    completed_steps: Mapped[list] = mapped_column(JSON, default=list)
    workspace_state: Mapped[dict] = mapped_column(JSON, default=dict)
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkspaceLock(Base):
    __tablename__ = "workspace_locks"
    workspace: Mapped[str] = mapped_column(String(500), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("local_tasks.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(12), default="WRITE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkspaceWaiter(Base):
    __tablename__ = "workspace_waiters"
    __table_args__ = (
        Index("ix_workspace_waiters_workspace", "workspace"),
        Index("ix_workspace_waiters_workspace_requested", "workspace", "requested_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace: Mapped[str] = mapped_column(String(500))
    task_id: Mapped[str] = mapped_column(ForeignKey("local_tasks.id", ondelete="CASCADE"), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
