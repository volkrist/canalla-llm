"""durable stop attempt clock

Revision ID: 0002_stop_attempt
Revises: 0001_gateway_core
Create Date: 2026-10-11

A02: a stop the provider refused is retried with backoff until the termination is confirmed.
The attempt clock is persisted on the session row — never an in-process timer — so the retry
survives a Gateway restart and a client that has already gone away, and «stopped» is only ever
reported after the provider confirmed it.
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_stop_attempt"
down_revision = "0001_gateway_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gateway_sessions",
        sa.Column("stop_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("gateway_sessions", "stop_attempt_at")
