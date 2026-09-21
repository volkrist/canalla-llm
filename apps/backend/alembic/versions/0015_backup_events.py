"""Local backup audit trail (upgrade/backup slice).

Additive only: one new table, no change to any existing table, so an existing 0013/0014
installation keeps every user, chat, message, project, memory and document row. The
pre-upgrade backup is taken *before* this revision is applied (see ``runtime_entry``).
"""

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "backup_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="created"),
        sa.Column("backup_id", sa.String(length=96), nullable=False, server_default=""),
        sa.Column("code", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("detail", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("app_version", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("schema_revision", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_backup_events_created_at", "backup_events", ["created_at"])


def downgrade():
    op.drop_index("ix_backup_events_created_at", table_name="backup_events")
    op.drop_table("backup_events")
