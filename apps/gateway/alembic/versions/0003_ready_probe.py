"""readiness liveness clock

Revision ID: 0003_ready_probe
Revises: 0002_stop_attempt
Create Date: 2026-10-11

A06: readiness is not a fact that stays true forever. The Gateway re-verifies a proven model at
``COMPUTE_READY_TTL_SECONDS`` and, when the model stops answering, owns a grace window before the
Pod is stopped. Both clocks are persisted on the session row so they survive a restart and are
read by the background loop rather than by an in-process timer.
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_ready_probe"
down_revision = "0002_stop_attempt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gateway_sessions",
        sa.Column("ready_probe_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "gateway_sessions",
        sa.Column("unready_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("gateway_sessions", "unready_since")
    op.drop_column("gateway_sessions", "ready_probe_at")
