"""Desktop-owned backend entry: data dir, JWT, pre-upgrade backup, Alembic, then uvicorn.

Developers may still run `scripts/start-backend.ps1`. This module is the
zero-terminal path used when Desktop supervises the API process, including
the packaged Windows sidecar.

Two upgrade-safety rules live here (0.9.3 → 1.0):

* a pending migration is preceded by a **verified** backup, otherwise the migration is
  refused and the old database is left untouched (fail closed);
* the same binary can perform a one-shot ``--restore-backup <dir>`` while the Desktop owns
  the lifecycle, so a restore never runs against a live server.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

RESTORE_EXIT_OK = 0
RESTORE_EXIT_FAILED = 20
MIGRATION_EXIT_FAILED = 12
JWT_EXIT_FAILED = 13
DATA_ROOT_EXIT_FAILED = 14
PRE_UPGRADE_BACKUP_EXIT_FAILED = 15


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


def migration_state(database: Path) -> dict:
    """Current revision, head revision and whether a migration is pending."""
    from .backup.format import sqlite_revision
    from .packaging import alembic_config

    current = sqlite_revision(database) if Path(database).is_file() else None
    head = None
    try:
        from alembic.script import ScriptDirectory

        head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    except Exception:  # pragma: no cover - packaged history is always present
        head = None
    exists = Path(database).is_file()
    pending = bool(exists and head and current != head)
    return {
        "exists": exists,
        "current": current,
        "head": head,
        "pending": pending,
        "fresh": not exists,
    }


def pre_upgrade_backup(layout: dict, state: dict) -> dict | None:
    """Verified backup before a destructive migration. None means "refuse to migrate"."""
    import logging

    from .backup.archive import BackupService
    from .backup.format import KIND_PRE_UPGRADE, BackupError

    logger = logging.getLogger("alex.runtime")
    service = BackupService(layout["root"])
    service.prune()
    from .backup.archive import cleanup_staging

    cleanup_staging(layout["root"])
    try:
        result = service.create_now(
            kind=KIND_PRE_UPGRADE,
            label=f"{state.get('current') or 'fresh'} → {state.get('head') or '?'}",
        )
    except BackupError as error:
        logger.error("pre_upgrade_backup_failed code=%s", error.code)
        return None
    logger.info(
        "pre_upgrade_backup_ok id=%s files=%s bytes=%s revision=%s",
        result.get("id"),
        result.get("files"),
        result.get("bytes"),
        result.get("schema_revision"),
    )
    return result


def write_migration_record(layout: dict, payload: dict) -> None:
    from .backup.format import write_json_atomic

    payload = {**payload, "app_version": _version()}
    try:
        write_json_atomic(Path(layout["runtime"]) / "migration.json", payload)
    except OSError:  # pragma: no cover - a read-only runtime dir
        pass


def read_migration_record(root: Path | None = None) -> dict | None:
    from .backup.format import BackupError, read_json

    base = Path(root) if root else Path(os.environ.get("ALEX_LLM_DATA_DIR") or "")
    if not str(base):
        return None
    path = base / "runtime" / "migration.json"
    if not path.is_file():
        return None
    try:
        return read_json(path)
    except BackupError:
        return None


def _version() -> str:
    try:
        from .product import VERSION

        return VERSION
    except Exception:  # pragma: no cover
        return ""


def restore_command(arguments: list[str]) -> int:
    """One-shot ``--restore-backup <dir>`` mode: no server is started."""
    import logging

    from .backup.format import BackupError, utc_now
    from .backup.restore import cleanup_restore_leftovers, restore_backup, write_result

    logger = logging.getLogger("alex.runtime")
    target = ""
    for index, argument in enumerate(arguments):
        if argument == "--restore-backup" and index + 1 < len(arguments):
            target = arguments[index + 1]
            break
    layout = prepare()
    root = Path(layout["root"])
    try:
        from .runtime_log import configure_backend_logging

        configure_backend_logging(Path(layout["logs"]) / "backend.log", port=0, mode="restore")
    except Exception:  # pragma: no cover - logging must never break a restore
        pass
    if not target:
        write_result(root, {"restored": False, "code": "backup_missing", "message": "Не указан путь копии."})
        return RESTORE_EXIT_FAILED
    cleanup_restore_leftovers(root)
    try:
        from .backup.archive import BackupService

        directory = Path(target)
        if not directory.is_absolute():
            directory = BackupService(root).resolve(target)
        report = restore_backup(directory, root)
        logger.info(
            "backup_restore_ok backup_id=%s safety_backup_id=%s from=%s to=%s",
            report.get("backup_id"),
            report.get("safety_backup_id"),
            report.get("from_revision"),
            report.get("to_revision"),
        )
    except BackupError as error:
        logger.error("backup_restore_failed code=%s", error.code)
        write_result(
            root,
            {
                "restored": False,
                "code": error.code,
                "message": error.message,
                "detail": error.detail,
                "at": utc_now().isoformat(),
            },
        )
        return RESTORE_EXIT_FAILED
    except Exception as error:  # pragma: no cover - defensive
        logger.error("backup_restore_failed code=restore_failed error=%s", type(error).__name__)
        write_result(
            root,
            {
                "restored": False,
                "code": "restore_failed",
                "message": "Не удалось восстановить резервную копию.",
                "at": utc_now().isoformat(),
            },
        )
        return RESTORE_EXIT_FAILED
    write_result(root, {**report, "restored": True, "code": "", "message": ""})
    return RESTORE_EXIT_OK


def main() -> int:
    from .packaging import is_packaged
    from .runtime_log import configure_backend_logging

    if "--restore-backup" in sys.argv[1:]:
        try:
            result = restore_command(sys.argv[1:])
        except ValueError:
            sys.stderr.write("ALEX_RUNTIME_ERROR jwt_secret_failed\n")
            return JWT_EXIT_FAILED
        except OSError as error:
            sys.stderr.write(f"ALEX_RUNTIME_ERROR data_root_unavailable {type(error).__name__}\n")
            return DATA_ROOT_EXIT_FAILED
        return result

    try:
        layout = prepare()
    except ValueError:
        sys.stderr.write("ALEX_RUNTIME_ERROR jwt_secret_failed\n")
        return JWT_EXIT_FAILED
    except OSError as error:
        sys.stderr.write(f"ALEX_RUNTIME_ERROR data_root_unavailable {type(error).__name__}\n")
        return DATA_ROOT_EXIT_FAILED
    host = os.environ.get("ALEX_BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("ALEX_BACKEND_PORT", "8000"))
    mode = "packaged" if is_packaged() else os.environ.get("ALEX_RUNTIME_MODE", "dev_owned")
    configure_backend_logging(layout["logs"] / "backend.log", port=port, mode=mode)

    from .backup.archive import cleanup_staging
    from .backup.restore import cleanup_restore_leftovers

    cleanup_staging(layout["root"])
    cleanup_restore_leftovers(layout["root"])

    state = migration_state(layout["db"])
    backup: dict | None = None
    if state["pending"]:
        backup = pre_upgrade_backup(layout, state)
        if backup is None:
            # Fail closed: without a verified backup there is no destructive migration.
            write_migration_record(
                layout,
                {
                    "result": "blocked_no_backup",
                    "from": state["current"],
                    "to": state["head"],
                    "at": _now(),
                    "backup": None,
                },
            )
            sys.stderr.write("ALEX_RUNTIME_ERROR pre_upgrade_backup_failed\n")
            return PRE_UPGRADE_BACKUP_EXIT_FAILED

    try:
        migrate()
        logging_status("migration_ok")
    except Exception as error:
        logging_status(f"migration_failed {type(error).__name__}")
        write_migration_record(
            layout,
            {
                "result": "failed",
                "from": state["current"],
                "to": state["head"],
                "at": _now(),
                "error": type(error).__name__,
                "backup": (backup or {}).get("id"),
            },
        )
        sys.stderr.write(f"ALEX_RUNTIME_ERROR migration_failed {type(error).__name__}\n")
        return MIGRATION_EXIT_FAILED

    if state["pending"] or state["fresh"]:
        write_migration_record(
            layout,
            {
                "result": "ok",
                "from": state["current"],
                "to": state["head"],
                "at": _now(),
                "backup": (backup or {}).get("id"),
            },
        )
    import uvicorn

    from app.main import app as fastapi_app

    uvicorn.run(fastapi_app, host=host, port=port, workers=1, factory=False, log_config=None)
    return 0


def _now() -> str:
    from .backup.format import utc_now

    return utc_now().isoformat()


def logging_status(message: str) -> None:
    import logging

    logging.getLogger("alex.runtime").info("%s", message)


if __name__ == "__main__":
    raise SystemExit(main())
