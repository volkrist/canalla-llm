from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import QueuePool

from .config import get_settings


class Base(DeclarativeBase):
    pass


# A desktop backend serves the UI, the device host and a chat turn at the same time. The pool has
# to cover that concurrency: a burst of device polls that had to wait for a free connection held
# every connection for the full 30 s checkout timeout, and the chat turn's own query then failed
# with an untyped TimeoutError in the middle of the tool phase (T4 LIVE, 2026-11-11). Reads are
# cheap and WAL does not block them, so the pool is sized for the concurrent readers, and a
# checkout that still cannot succeed fails in ten seconds as a typed `database_busy` instead of
# hanging a generation for half a minute.
POOL_SIZE = 10
MAX_OVERFLOW = 20
POOL_TIMEOUT_SECONDS = 10

settings = get_settings()
_url = settings.database_url
_pool = (
    {}
    if ":memory:" in _url or _url.rstrip("/") in {"sqlite:", "sqlite://"}
    else {
        "poolclass": QueuePool,
        "pool_size": POOL_SIZE,
        "max_overflow": MAX_OVERFLOW,
        "pool_timeout": POOL_TIMEOUT_SECONDS,
    }
)
engine = create_engine(
    _url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False, "timeout": 30} if _url.startswith("sqlite") else {},
    **_pool,
)
if _url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def sqlite_settings(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as session:
        yield session
