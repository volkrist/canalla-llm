"""Chat management, roles and persistent compute control/accounting."""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("role", sa.String(16), nullable=False, server_default="user"))
    op.add_column("chats", sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("messages", sa.Column("status", sa.String(16), nullable=False, server_default="complete"))
    op.add_column("messages", sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "compute_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("started_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(64), unique=True, nullable=False),
        sa.Column("pod_id", sa.String(64)),
        sa.Column("pod_name", sa.String(120), nullable=False),
        sa.Column("managed", sa.Boolean(), nullable=False),
        sa.Column("network_volume_id", sa.String(64), nullable=False),
        sa.Column("datacenter", sa.String(32), nullable=False),
        sa.Column("gpu_type", sa.String(160), nullable=False),
        sa.Column("gpu_vram_mb", sa.Integer(), nullable=False),
        sa.Column("hourly_rate", sa.Numeric(14, 6), nullable=False),
        sa.Column("max_hourly_price", sa.Numeric(14, 6), nullable=False),
        sa.Column("session_budget", sa.Numeric(14, 6), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ready_at", sa.DateTime(timezone=True)),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("auto_stop_minutes", sa.Integer(), nullable=False),
        sa.Column("pending_stop", sa.Boolean(), nullable=False),
        sa.Column("billable_seconds", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(16, 6), nullable=False),
        sa.Column("actual_cost", sa.Numeric(16, 6)),
        sa.Column("actual_cost_at", sa.DateTime(timezone=True)),
        sa.Column("stop_reason", sa.String(48)),
        sa.Column("error_code", sa.String(48)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_compute_sessions_started_by_user_id", "compute_sessions", ["started_by_user_id"])
    op.create_index("ix_compute_sessions_pod_id", "compute_sessions", ["pod_id"])
    op.create_table(
        "compute_control",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("active_session_id", sa.String(36), sa.ForeignKey("compute_sessions.id")),
        sa.Column("lease_owner", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("search_user_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("search_settings", sa.JSON()),
        sa.Column("search_quote_id", sa.String(36)),
        sa.Column("search_state", sa.String(32), nullable=False),
        sa.Column("next_search_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(48)),
    )
    op.execute(
        sa.table("compute_control", sa.column("id"), sa.column("search_state"))
        .insert()
        .values(id=1, search_state="offline")
    )
    op.create_table(
        "compute_quotes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("preferences", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_compute_quotes_user_id", "compute_quotes", ["user_id"])
    op.create_table(
        "compute_preferences",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("values", sa.JSON(), nullable=False),
    )
    op.create_table(
        "compute_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("compute_sessions.id")),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("code", sa.String(48)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for field in ["session_id", "actor_id", "created_at"]:
        op.create_index(f"ix_compute_events_{field}", "compute_events", [field])
    op.create_table(
        "generation_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("chat_id", sa.String(36), sa.ForeignKey("chats.id", ondelete="SET NULL")),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("compute_session_id", sa.String(36), sa.ForeignKey("compute_sessions.id")),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    for field in ["user_id", "created_at"]:
        op.create_index(f"ix_generation_usage_{field}", "generation_usage", [field])


def downgrade():
    for table in [
        "generation_usage",
        "compute_events",
        "compute_preferences",
        "compute_quotes",
        "compute_control",
        "compute_sessions",
    ]:
        op.drop_table(table)
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("edited_at")
        batch.drop_column("status")
    with op.batch_alter_table("chats") as batch:
        batch.drop_column("pinned")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("role")
