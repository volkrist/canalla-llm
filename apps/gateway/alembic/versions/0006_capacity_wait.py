"""persistent capacity wait intent

Revision ID: 0006_capacity_wait
Revises: 0005_installation_entitlements
Create Date: 2026-10-11

A persistent GPU wait: the user may keep waiting for a free, affordable GPU instead of the search
collapsing after its bounded window. The intent is durable, so a brief network loss, a Gateway
restart, an app restart or a transient provider error never cancels it. Additive columns on the
``gateway_compute`` singleton; no row is rewritten and nothing else changes.
"""

import sqlalchemy as sa

from alembic import op

revision = "0006_capacity_wait"
down_revision = "0005_installation_entitlements"
branch_labels = None
depends_on = None

_COLUMNS = (
    sa.Column("wait_active", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("wait_started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("wait_next_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("wait_operation_id", sa.String(64), nullable=True),
    sa.Column("wait_installation_id", sa.String(36), nullable=True),
    sa.Column("wait_caps", sa.JSON(), nullable=True),
    sa.Column("wait_attempts", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("wait_background", sa.Boolean(), nullable=False, server_default=sa.false()),
)


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("gateway_compute", column)


def downgrade() -> None:
    for column in reversed(_COLUMNS):
        op.drop_column("gateway_compute", column.name)
