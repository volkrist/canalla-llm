"""Alembic environment for the Gateway database.

Run from anywhere:

    alembic -c apps/gateway/alembic.ini upgrade head

``DATABASE_URL`` wins over the settings file so an operator can migrate a specific
database explicitly. Postgres is the production target; SQLite is used for development
and tests, and needs batch mode for future ALTERs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine

from alembic import context

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

from gateway import models  # noqa: E402,F401 - imported for metadata
from gateway.database import Base  # noqa: E402

target_metadata = Base.metadata


def database_url() -> str:
    explicit = (os.environ.get("DATABASE_URL") or "").strip()
    if explicit:
        return explicit
    from gateway.config import get_settings

    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), pool_pre_ping=True)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=engine.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
