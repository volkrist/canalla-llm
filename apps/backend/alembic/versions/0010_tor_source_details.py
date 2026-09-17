"""Tor source snapshot details for research metadata."""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("web_source_snapshots") as batch:
        batch.add_column(sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade():
    with op.batch_alter_table("web_source_snapshots") as batch:
        batch.drop_column("details")
