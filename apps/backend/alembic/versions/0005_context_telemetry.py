"""Persist generation metadata and honest cancellation telemetry."""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("generation_started_at", "first_token_at", "completed_at"):
        op.add_column("messages", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("ttft_ms", sa.Integer(), nullable=True))
    op.add_column("messages", sa.Column("cancellation", sa.JSON(), nullable=True))
    op.add_column("message_contexts", sa.Column("snapshot", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("messages") as batch:
        for name in ("generation_started_at", "first_token_at", "completed_at", "ttft_ms", "cancellation"):
            batch.drop_column(name)
    with op.batch_alter_table("message_contexts") as batch:
        batch.drop_column("snapshot")
