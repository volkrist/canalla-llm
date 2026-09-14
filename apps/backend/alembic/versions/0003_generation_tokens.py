"""Persist supplier-reported total tokens, without estimating missing usage."""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("generation_usage", sa.Column("total_tokens", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("generation_usage") as batch:
        batch.drop_column("total_tokens")
