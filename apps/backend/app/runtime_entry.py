"""Desktop-owned backend entry: data dir, JWT, Alembic, then uvicorn.

Developers may still run `scripts/start-backend.ps1`. This module is the
zero-terminal path used when Desktop supervises the API process.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def prepare() -> dict:
    from .data_paths import ensure_layout, load_or_create_jwt, sqlite_url

    raw = (os.environ.get("ALEX_LLM_DATA_DIR") or "").strip()
    layout = ensure_layout(Path(raw).expanduser() if raw else None)
    os.environ["ALEX_LLM_DATA_DIR"] = str(layout["root"])
    os.environ.setdefault("DATABASE_URL", sqlite_url(layout["db"]))
    os.environ.setdefault("DOCUMENT_STORAGE_DIR", str(layout["documents"]))
    if not (os.environ.get("JWT_SECRET") or "").strip():
        os.environ["JWT_SECRET"] = load_or_create_jwt(layout["jwt"])
    return layout


def migrate() -> None:
    from alembic.config import Config

    from alembic import command

    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    config = Config(str(ini))
    command.upgrade(config, "head")


def main() -> int:
    try:
        prepare()
        migrate()
    except Exception as error:
        sys.stderr.write(f"ALEX_RUNTIME_ERROR migration_failed {type(error).__name__}\n")
        return 12
    host = os.environ.get("ALEX_BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("ALEX_BACKEND_PORT", "8000"))
    import uvicorn

    uvicorn.run("app.main:app", host=host, port=port, workers=1, factory=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
