"""Durable queue and the structured request for autonomous tasks (1.2.7).

Additive only: ten columns on the existing ``local_tasks`` table. Every 1.2.6 row keeps its
values; the new columns default to "not queued, no explicit budget", which is exactly what those
rows mean. `downgrade()` drops only these columns, so a rollback returns the 1.2.6 schema without
touching a single task, tool run, message or checkpoint.
"""

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

_COLUMNS = (
    sa.Column("queue_priority", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("claimed_by", sa.String(length=64), nullable=True),
    sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("depends_on", sa.JSON(), nullable=False, server_default="[]"),
    sa.Column("time_budget_seconds", sa.Integer(), nullable=False, server_default="1800"),
    sa.Column("cost_budget_usd", sa.Float(), nullable=False, server_default="0"),
    sa.Column("normalized_goal", sa.Text(), nullable=False, server_default=""),
    sa.Column("scope", sa.JSON(), nullable=False, server_default="{}"),
    sa.Column("permissions", sa.JSON(), nullable=False, server_default="{}"),
    sa.Column("artifacts", sa.JSON(), nullable=False, server_default="{}"),
)


def upgrade():
    with op.batch_alter_table("local_tasks") as batch:
        for column in _COLUMNS:
            batch.add_column(column)
        batch.create_index("ix_local_tasks_status_priority", ["status", "queue_priority"], unique=False)


def downgrade():
    with op.batch_alter_table("local_tasks") as batch:
        batch.drop_index("ix_local_tasks_status_priority")
        for column in reversed(_COLUMNS):
            batch.drop_column(column.name)
