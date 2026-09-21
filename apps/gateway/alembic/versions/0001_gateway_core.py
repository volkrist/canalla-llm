"""gateway core

Revision ID: 0001_gateway_core
Revises:
Create Date: 2026-09-21

First Gateway revision. It is deliberately independent of the local Alex backend history
(revisions 0001-0014): the Gateway owns its own database, which holds installations,
enrollment codes, the global compute lease, idempotency records and audit events — never
chats, messages, memories or documents.
"""

import sqlalchemy as sa

from alembic import op

revision = "0001_gateway_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "installations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("client_version", sa.String(length=40), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=200), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
    )
    op.create_index("ix_installations_secret_hash", "installations", ["secret_hash"])

    op.create_table(
        "enrollment_codes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_by", sa.String(length=36), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_enrollment_codes_code_hash", "enrollment_codes", ["code_hash"], unique=True)

    op.create_table(
        "gateway_compute",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("active_session_id", sa.String(length=36), nullable=True),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("create_attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("last_operation_id", sa.String(length=64), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "gateway_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("pod_id", sa.String(length=80), nullable=True),
        sa.Column("pod_name", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("gpu_type", sa.String(length=80), nullable=False),
        sa.Column("gpu_vram_mb", sa.Integer(), nullable=False),
        sa.Column("hourly_rate", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("max_hourly_price", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("session_budget", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("auto_stop_minutes", sa.Integer(), nullable=False),
        sa.Column("billable_seconds", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("create_attempts", sa.Integer(), nullable=False),
        sa.Column("intent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_stop", sa.Boolean(), nullable=False),
        sa.Column("created_by_installation_id", sa.String(length=36), nullable=True),
        sa.Column("managed", sa.Boolean(), nullable=False),
        sa.Column("adopted", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_reason", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "gateway_operations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("installation_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("result_state", sa.String(length=32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_gateway_operations_installation_id", "gateway_operations", ["installation_id"])
    op.create_index(
        "ix_gateway_operations_installation", "gateway_operations", ["installation_id", "created_at"]
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("installation_id", sa.String(length=36), nullable=True),
        sa.Column("operation", sa.String(length=48), nullable=False),
        sa.Column("result", sa.String(length=24), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("compute_session_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("cost", sa.JSON(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_installation_id", "audit_events", ["installation_id"])
    op.create_index("ix_audit_events_created", "audit_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("gateway_operations")
    op.drop_table("gateway_sessions")
    op.drop_table("gateway_compute")
    op.drop_table("enrollment_codes")
    op.drop_table("installations")
