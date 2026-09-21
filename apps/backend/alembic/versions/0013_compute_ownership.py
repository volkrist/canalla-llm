"""Persisted compute ownership and on-demand confirmation fields."""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("compute_sessions") as batch:
        batch.add_column(sa.Column("created_by_alex", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(
            sa.Column("adopted_by_alex", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table("compute_control") as batch:
        batch.add_column(sa.Column("demand_idempotency_key", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("last_confirmed_gpu", sa.String(length=160), nullable=True))
        batch.add_column(sa.Column("last_confirmed_hourly", sa.Numeric(14, 6), nullable=True))
        batch.add_column(sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_confirm_digest", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("confirmation_run_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("create_attempts", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("demand_user_id", sa.String(length=36), nullable=True))


def downgrade():
    with op.batch_alter_table("compute_control") as batch:
        batch.drop_column("demand_user_id")
        batch.drop_column("create_attempts")
        batch.drop_column("confirmation_run_id")
        batch.drop_column("last_confirm_digest")
        batch.drop_column("last_confirmed_at")
        batch.drop_column("last_confirmed_hourly")
        batch.drop_column("last_confirmed_gpu")
        batch.drop_column("demand_idempotency_key")
    with op.batch_alter_table("compute_sessions") as batch:
        batch.drop_column("adopted_by_alex")
        batch.drop_column("created_by_alex")
