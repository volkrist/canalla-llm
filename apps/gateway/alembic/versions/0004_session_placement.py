"""session placement identity and ensure operation

Revision ID: 0004_session_placement
Revises: 0003_ready_probe
Create Date: 2026-10-11

A09: with more than one placement, the primary Network Volume is not the only one a managed Pod
may mount, so reconciliation must not identify compute by the primary Volume alone. The session
records the placement it owns — the Volume it mounts and the datacenter it runs in — plus the
ensure ``operation_id`` that opened the attempt. All three are persisted, so a Gateway restart
recognises the existing Pod in any allowed placement, restores the session and never creates a
second one, without a live provider read to reconstruct the record.
"""

import sqlalchemy as sa

from alembic import op

revision = "0004_session_placement"
down_revision = "0003_ready_probe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gateway_sessions", sa.Column("volume_id", sa.String(80), nullable=True))
    op.add_column("gateway_sessions", sa.Column("datacenter", sa.String(40), nullable=True))
    op.add_column("gateway_sessions", sa.Column("operation_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("gateway_sessions", "operation_id")
    op.drop_column("gateway_sessions", "datacenter")
    op.drop_column("gateway_sessions", "volume_id")
