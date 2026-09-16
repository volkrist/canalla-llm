"""local computer host, tor sources, device pairing"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tool_runs") as batch:
        batch.add_column(sa.Column("origin", sa.String(length=32), nullable=False, server_default="model"))
        batch.add_column(sa.Column("assigned_device_id", sa.String(length=36), nullable=True))
        batch.create_index("ix_tool_runs_assigned_device_id", ["assigned_device_id"], unique=False)
    with op.batch_alter_table("web_source_snapshots") as batch:
        batch.add_column(sa.Column("channel", sa.String(length=12), nullable=False, server_default="web"))
        batch.add_column(sa.Column("authority", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("canonical_url", sa.Text(), nullable=True))
        batch.add_column(sa.Column("kind", sa.String(length=20), nullable=True))
    op.create_table(
        "paired_devices",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("credential_hash", sa.String(length=64), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("paired_devices") as batch:
        batch.create_index("ix_paired_devices_user_id", ["user_id"], unique=False)


def downgrade():
    op.drop_table("paired_devices")
    with op.batch_alter_table("web_source_snapshots") as batch:
        batch.drop_column("kind")
        batch.drop_column("canonical_url")
        batch.drop_column("authority")
        batch.drop_column("channel")
    with op.batch_alter_table("tool_runs") as batch:
        batch.drop_index("ix_tool_runs_assigned_device_id")
        batch.drop_column("assigned_device_id")
        batch.drop_column("origin")
