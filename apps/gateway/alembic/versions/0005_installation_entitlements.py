"""installation entitlements and operator role

Revision ID: 0005_installation_entitlements
Revises: 0004_session_placement
Create Date: 2026-10-11

A10/A11: several installations share one RunPod account, so the Gateway must be able to hold each
one to an operator-defined money ceiling and to tell an ordinary installation from an operator.
Purely additive: a new ``role`` column defaulting to ``member`` (so every existing installation,
including REGYNA and the owner's own install, keeps working unchanged) and four nullable
entitlement columns (``None`` = only the deployment/global policy applies). No row is rewritten and
no credential or identity column is touched.
"""

import sqlalchemy as sa

from alembic import op

revision = "0005_installation_entitlements"
down_revision = "0004_session_placement"
branch_labels = None
depends_on = None

_COLUMNS = (
    sa.Column("role", sa.String(16), nullable=False, server_default="member"),
    sa.Column("entitlement_max_hourly_price", sa.Numeric(12, 6), nullable=True),
    sa.Column("entitlement_session_budget", sa.Numeric(12, 6), nullable=True),
    sa.Column("entitlement_period_budget", sa.Numeric(12, 6), nullable=True),
    sa.Column("entitlement_period_days", sa.Integer(), nullable=True),
)


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("installations", column)


def downgrade() -> None:
    for column in reversed(_COLUMNS):
        op.drop_column("installations", column.name)
