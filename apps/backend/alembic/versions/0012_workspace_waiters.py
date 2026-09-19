"""Persistent FIFO workspace WRITE waiters."""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workspace_waiters",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace", sa.String(length=500), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["local_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    with op.batch_alter_table("workspace_waiters") as batch:
        batch.create_index("ix_workspace_waiters_workspace", ["workspace"], unique=False)
        batch.create_index(
            "ix_workspace_waiters_workspace_requested", ["workspace", "requested_at"], unique=False
        )


def downgrade():
    op.drop_table("workspace_waiters")
