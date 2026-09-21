"""Gateway database: its own engine and its own Alembic history (never the local one).

The engine is created lazily so importing the package never requires a fully configured
environment (``--help`` and migrations must work without a JWT secret).
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str) -> Engine:
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False, "timeout": 30} if database_url.startswith("sqlite") else {},
    )
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def sqlite_settings(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def database_url() -> str:
    from .config import get_settings

    return get_settings().database_url


@lru_cache
def get_engine() -> Engine:
    return make_engine(database_url())


class LazySessionFactory:
    """``SessionLocal()`` works exactly like a sessionmaker, but builds the engine lazily."""

    def __init__(self) -> None:
        self._factory: sessionmaker | None = None

    def _call(self) -> sessionmaker:
        if self._factory is None:
            self._factory = sessionmaker(get_engine(), expire_on_commit=False)
        return self._factory

    def __call__(self, **kwargs):
        return self._call()(**kwargs)

    def configure(self, factory: sessionmaker) -> None:
        self._factory = factory

    def reset(self) -> None:
        self._factory = None
        get_engine.cache_clear()


SessionLocal = LazySessionFactory()


def get_db():
    with SessionLocal() as session:
        yield session
