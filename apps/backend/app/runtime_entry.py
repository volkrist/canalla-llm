"""Desktop-owned backend entry: data dir, JWT, Alembic, then uvicorn.

Developers may still run `scripts/start-backend.ps1`. This module is the
zero-terminal path used when Desktop supervises the API process, including
the packaged Windows sidecar.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def prepare() -> dict:
    from .data_paths import ensure_layout, load_or_create_jwt, load_or_create_runtime_token, sqlite_url
    from .packaging import apply_runtime_environment

    apply_runtime_environment()
    raw = (os.environ.get("ALEX_LLM_DATA_DIR") or "").strip()
    try:
        layout = ensure_layout(Path(raw).expanduser() if raw else None)
    except OSError as error:
        sys.stderr.write(f"ALEX_RUNTIME_ERROR data_root_unavailable {type(error).__name__}\n")
        raise
    os.environ["ALEX_LLM_DATA_DIR"] = str(layout["root"])
    os.environ.setdefault("DATABASE_URL", sqlite_url(layout["db"]))
    os.environ.setdefault("DOCUMENT_STORAGE_DIR", str(layout["documents"]))
    if not (os.environ.get("JWT_SECRET") or "").strip():
        os.environ["JWT_SECRET"] = load_or_create_jwt(layout["jwt"])
    secret = (os.environ.get("JWT_SECRET") or "").strip()
    if len(secret) < 48:
        sys.stderr.write("ALEX_RUNTIME_ERROR jwt_secret_failed\n")
        raise ValueError("jwt_secret_failed")
    if not (os.environ.get("ALEX_RUNTIME_TOKEN") or "").strip():
        os.environ["ALEX_RUNTIME_TOKEN"] = load_or_create_runtime_token(layout["shutdown"])
    return layout


def migrate() -> None:
    from alembic import command

    from .packaging import alembic_config

    command.upgrade(alembic_config(), "head")


def main() -> int:
    from .packaging import is_packaged
    from .runtime_log import configure_backend_logging

    try:
        layout = prepare()
    except ValueError:
        sys.stderr.write("ALEX_RUNTIME_ERROR jwt_secret_failed\n")
        return 13
    except OSError as error:
        sys.stderr.write(f"ALEX_RUNTIME_ERROR data_root_unavailable {type(error).__name__}\n")
        return 14
    host = os.environ.get("ALEX_BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("ALEX_BACKEND_PORT", "8000"))
    mode = "packaged" if is_packaged() else os.environ.get("ALEX_RUNTIME_MODE", "dev_owned")
    configure_backend_logging(layout["logs"] / "backend.log", port=port, mode=mode)
    try:
        migrate()
        logging_status("migration_ok")
    except Exception as error:
        logging_status(f"migration_failed {type(error).__name__}")
        sys.stderr.write(f"ALEX_RUNTIME_ERROR migration_failed {type(error).__name__}\n")
        return 12
    import uvicorn

    from app.main import app as fastapi_app

    uvicorn.run(fastapi_app, host=host, port=port, workers=1, factory=False, log_config=None)
    return 0


def logging_status(message: str) -> None:
    import logging

    logging.getLogger("alex.runtime").info("%s", message)


if __name__ == "__main__":
    raise SystemExit(main())
