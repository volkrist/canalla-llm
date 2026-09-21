"""Transactional restore: staged, validated, and rollback-able.

This module runs **only** while no backend owns the database (the Desktop stops its owned
sidecar, then runs ``alex-backend.exe --restore-backup <dir>``). It never overwrites the
live database in place, never restores machine identity, and if anything fails after the
first swap it puts the previous state back.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path

from .archive import (
    REQUIRED_TABLES,
    BackupService,
    estimate_need,
    invalidate_marker,
    require_space,
    verify_backup,
)
from .format import (
    AUTOMATIC_KINDS,
    DATABASE_DIR,
    DATABASE_NAME,
    DOCUMENTS_DIR,
    KIND_PRE_RESTORE,
    MANIFEST_NAME,
    BackupError,
    assert_regular_file,
    read_json,
    resolve_within,
    sha256_file,
    sqlite_integrity,
    sqlite_revision,
    sqlite_tables,
    utc_now,
    utc_stamp,
    write_json_atomic,
)

logger = logging.getLogger(__name__)

# Where the result of a one-shot restore is reported back to the Desktop.
RESULT_NAME = "backup-restore.json"


def _alembic_chain() -> list[str]:
    """Ordered alembic revisions from the packaged migration history."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        from ..packaging import alembic_config
    except Exception:  # pragma: no cover - alembic is always packaged
        return []
    config: Config = alembic_config()
    script = ScriptDirectory.from_config(config)
    chain = [revision.revision for revision in script.walk_revisions("base", "head")]
    return list(reversed(chain))


def _assert_supported_revision(revision: str | None, chain: list[str]) -> None:
    if revision is None:
        return  # a database created before the first migration; migrations run on start
    if revision not in chain:
        raise BackupError(
            "backup_unsupported_format",
            "Резервная копия создана более новой версией Alex и не может быть восстановлена.",
            f"revision={revision}",
        )


def _assert_not_locked(database: Path) -> None:
    """The live database must not be held by another process. Fail closed if it is."""
    if not database.is_file():
        return
    try:
        connection = sqlite3.connect(str(database), timeout=2, isolation_level=None)
    except sqlite3.Error as error:
        raise BackupError("restore_busy", "Локальная база занята другим процессом.", str(error)) from None
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("ROLLBACK")
    except sqlite3.Error as error:
        raise BackupError(
            "restore_busy",
            "Локальная база используется запущенным сервером. Остановите Alex и повторите.",
            str(error),
        ) from None
    finally:
        connection.close()


def _copy_verified(source: Path, target: Path, entry: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    expected = entry.get("sha256")
    if expected and sha256_file(target) != expected:
        raise BackupError("backup_tampered", "Файл копии изменился при восстановлении.", entry.get("path"))


def _unique(path: Path) -> Path:
    candidate = path
    index = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}-{index}")
        index += 1
    return candidate


def _replace_with_retry(source: Path, target: Path, *, attempts: int = 8, delay: float = 0.25) -> None:
    """Windows keeps a handle open for a moment after a reader closes it (antivirus,
    search indexer, a just-closed connection). Retry briefly, then fail closed."""
    last: OSError | None = None
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except OSError as error:  # pragma: no cover - depends on Windows timing
            last = error
            time.sleep(delay * (attempt + 1) / 2)
    raise last or OSError("replace failed")


def restore_backup(backup_directory: Path, data_root: Path, *, now=None) -> dict:
    """Restore one backup into `data_root`. Returns a report; raises BackupError."""
    data_root = Path(data_root).resolve()
    backup_directory = Path(backup_directory).resolve()
    service = BackupService(data_root)
    stamp = utc_stamp(now)

    # 1. The backup is untrusted input: verify it completely before touching anything.
    try:
        verified = verify_backup(backup_directory, write_marker=False)
    except BackupError as error:
        # A backup that cannot be verified must not keep claiming that it was.
        invalidate_marker(backup_directory, error.code)
        raise
    manifest = read_json(backup_directory / MANIFEST_NAME)
    chain = _alembic_chain()
    target_revision = manifest.get("schema_revision")
    _assert_supported_revision(target_revision, chain)

    live_database = data_root / DATABASE_DIR / DATABASE_NAME
    live_documents = data_root / DOCUMENTS_DIR
    database_entry = manifest.get("database") or {}
    document_entries = (manifest.get("documents") or {}).get("files") or []

    need = estimate_need(
        int(database_entry.get("size") or 0),
        sum(int(entry.get("size") or 0) for entry in document_entries),
        live_database.stat().st_size if live_database.is_file() else 0,
    )
    require_space(data_root, need)

    # 2. Safety backup of the *current* state: a restore must always be reversible.
    safety = service.create_now(kind=KIND_PRE_RESTORE, label=f"before restore of {verified['id']}")

    report = {
        "restored": True,
        "backup_id": verified["id"],
        "safety_backup_id": safety["id"],
        "from_revision": sqlite_revision(live_database) if live_database.is_file() else None,
        "to_revision": target_revision,
        "documents": verified.get("documents", 0),
        "at": utc_now().isoformat(),
        "rolled_back": False,
    }

    staging = data_root / DATABASE_DIR / f"restore-{stamp}"
    staging_documents = data_root / f"{DOCUMENTS_DIR}.restore-{stamp}"
    moved_database = _unique(data_root / DATABASE_DIR / f"{DATABASE_NAME}.pre-restore-{stamp}")
    moved_documents = _unique(data_root / f"{DOCUMENTS_DIR}.pre-restore-{stamp}")
    swap_started = False
    try:
        # 3. Stage everything first, then validate the staged copy.
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(staging_documents, ignore_errors=True)
        staging.mkdir(parents=True)
        source_database = resolve_within(backup_directory, database_entry["path"])
        assert_regular_file(source_database, code="backup_incomplete")
        _copy_verified(source_database, staging / DATABASE_NAME, database_entry)
        sqlite_integrity(staging / DATABASE_NAME, immutable=True)
        missing = REQUIRED_TABLES - sqlite_tables(staging / DATABASE_NAME, immutable=True)
        if missing:
            raise BackupError(
                "backup_corrupt",
                "В восстанавливаемой базе нет обязательных таблиц.",
                ", ".join(sorted(missing)),
            )

        staging_documents.mkdir(parents=True)
        if document_entries:
            for entry in document_entries:
                relative = entry["path"]
                if not relative.startswith(DOCUMENTS_DIR + "/"):
                    raise BackupError("restore_unsafe_path", "Документ вне папки документов.", relative)
                inside = relative[len(DOCUMENTS_DIR) + 1 :]
                source = resolve_within(backup_directory, relative)
                assert_regular_file(source, code="backup_incomplete")
                _copy_verified(source, resolve_within(staging_documents, inside), entry)

        # 4. Nothing else may hold the database while the swap happens.
        _assert_not_locked(live_database)
        swap_started = True

        if live_database.is_file():
            _replace_with_retry(live_database, moved_database)
        for suffix in ("-wal", "-shm"):
            sidecar = live_database.with_name(live_database.name + suffix)
            if sidecar.exists():
                sidecar.unlink()
        _replace_with_retry(staging / DATABASE_NAME, live_database)

        if live_documents.exists():
            _replace_with_retry(live_documents, moved_documents)
        _replace_with_retry(staging_documents, live_documents)

        # 5. Post-restore validation; a failure here rolls the previous state back.
        sqlite_integrity(live_database)
        missing = REQUIRED_TABLES - sqlite_tables(live_database)
        if missing:
            raise BackupError(
                "restore_failed", "Восстановленная база не прошла проверку.", ", ".join(sorted(missing))
            )
    except BaseException as error:
        rolled = False
        if swap_started:
            rolled = _rollback(live_database, moved_database, live_documents, moved_documents)
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(staging_documents, ignore_errors=True)
        if isinstance(error, BackupError):
            if rolled:
                error.detail = (
                    error.detail + " " if error.detail else ""
                ) + "Предыдущее состояние возвращено."
            raise
        raise BackupError(
            "restore_failed",
            "Не удалось восстановить резервную копию.",
            f"{type(error).__name__}{' (состояние возвращено)' if rolled else ''}",
        ) from None

    shutil.rmtree(staging, ignore_errors=True)
    if moved_database.is_file():
        moved_database.unlink(missing_ok=True)
    if moved_documents.is_dir():
        shutil.rmtree(moved_documents, ignore_errors=True)
    report["documents_on_disk"] = len(list(live_documents.glob("*"))) if live_documents.is_dir() else 0
    logger.info(
        "backup_restored backup_id=%s safety_backup_id=%s from=%s to=%s",
        report["backup_id"],
        report["safety_backup_id"],
        report["from_revision"],
        report["to_revision"],
    )
    return report


def _rollback(live_database: Path, moved_database: Path, live_documents: Path, moved_documents: Path) -> bool:
    """Put the previous database/documents back after a failed swap."""
    restored = False
    try:
        if moved_database.exists():
            for suffix in ("-wal", "-shm"):
                sidecar = live_database.with_name(live_database.name + suffix)
                if sidecar.exists():
                    sidecar.unlink()
            if live_database.exists():
                live_database.unlink()
            _replace_with_retry(moved_database, live_database)
            restored = True
        if moved_documents.exists():
            if live_documents.exists():
                shutil.rmtree(live_documents, ignore_errors=True)
            _replace_with_retry(moved_documents, live_documents)
            restored = True
    except OSError:  # pragma: no cover - last resort
        logger.warning("backup_restore_rollback_failed")
        return False
    logger.warning("backup_restore_rolled_back")
    return restored


def write_result(data_root: Path, payload: dict) -> Path:
    target = Path(data_root) / "runtime" / RESULT_NAME
    write_json_atomic(target, payload)
    return target


def read_result(data_root: Path) -> dict | None:
    path = Path(data_root) / "runtime" / RESULT_NAME
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except BackupError:
        return None
    if not isinstance(payload, dict):
        return None
    # A result is only interesting until the next operation replaces it.
    return payload


def automatic_kinds() -> tuple[str, ...]:
    return AUTOMATIC_KINDS


def cleanup_restore_leftovers(root: Path) -> list[str]:
    """Remove staging from an interrupted restore. Only the exact staging names, only
    inside this data root — a half-swapped state is never left behind silently."""
    root = Path(root)
    removed: list[str] = []
    data_dir = root / DATABASE_DIR
    if data_dir.is_dir():
        for entry in data_dir.iterdir():
            if entry.name.startswith("restore-") and entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
                removed.append(entry.name)
    if root.is_dir():
        for entry in root.iterdir():
            if (
                entry.name.startswith(f"{DOCUMENTS_DIR}.restore-")
                and entry.is_dir()
                and not entry.is_symlink()
            ):
                shutil.rmtree(entry, ignore_errors=True)
                removed.append(entry.name)
    if removed:
        logger.info("backup_restore_leftovers_removed count=%s", len(removed))
    return removed


def restore_from_id(backup_id: str, data_root: Path) -> dict:
    """Restore by id: the id resolves only inside this installation's backups root."""
    service = BackupService(data_root)
    return restore_backup(service.resolve(backup_id), data_root)
