"""Persistent device sessions and first-owner bootstrap claim."""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    # Plain add_column: SQLite applies ALTER TABLE ADD COLUMN natively. Batch
    # mode would recreate the users table, and with foreign_keys=ON the drop
    # of the old table cascades into chats/messages — never batch-alter users.
    op.add_column("users", sa.Column("is_owner", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_id", sa.String(length=64), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("rotated_from", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"])
    op.create_table(
        "bootstrap_claim",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Backfill for existing DBs: the historical first-owner heuristic is the
    # oldest registered local user. Keep it as the persisted owner.
    op.execute(
        "UPDATE users SET is_owner = 1 "
        "WHERE id = (SELECT id FROM users ORDER BY created_at ASC, id ASC LIMIT 1)"
    )


def downgrade():
    op.drop_table("bootstrap_claim")
    op.drop_index("ix_auth_sessions_token_hash", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        # Native DROP COLUMN avoids the cascade-prone table recreation.
        connection.exec_driver_sql("ALTER TABLE users DROP COLUMN is_owner")
    else:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("is_owner")
