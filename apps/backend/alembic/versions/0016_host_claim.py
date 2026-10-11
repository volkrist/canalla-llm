"""Durable host claim/lease ledger for local computer jobs (A05).

Additive only: eight columns (seven nullable plus a defaulted counter) and one index on the
existing ``tool_runs`` table, so an existing 0015 installation keeps every tool run and every
host result already recorded. A run that was ``waiting_host`` under 0015 keeps that status and
is simply claimable under the new protocol; nothing is rewritten and no row is deleted.
"""

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_COLUMNS = (
    sa.Column("claim_id", sa.String(length=64), nullable=True),
    sa.Column("claim_owner", sa.String(length=36), nullable=True),
    sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("exec_started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("result_digest", sa.String(length=64), nullable=True),
    sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
)


def upgrade():
    for column in _COLUMNS:
        op.add_column("tool_runs", column)
    op.create_index("ix_tool_runs_status", "tool_runs", ["status"])


def downgrade():
    op.drop_index("ix_tool_runs_status", table_name="tool_runs")
    for column in reversed(_COLUMNS):
        op.drop_column("tool_runs", column.name)
