"""Deterministic local backups: consistent SQLite snapshot + documents + manifest.

Everything here is synchronous file work behind an async lock, because the API is async
and the CLI mode (a one-shot sidecar process, see ``restore.py``) must be able to reuse
the exact same code paths.

Rules that are not negotiable:

* the database is snapshotted with the SQLite backup API, never copied byte by byte;
* every file is hashed and the manifest is written **last**, so a backup directory either
  has a complete verified manifest or is treated as incomplete;
* nothing outside the data root is ever read or written, and a backup directory can only
  be deleted when it sits directly inside the backups root.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import sqlite3
from pathlib import Path

from ..product import VERSION
from .format import (
    AUTOMATIC_KINDS,
    BACKUP_FORMAT_VERSION,
    DATABASE_DIR,
    DATABASE_NAME,
    DOCUMENTS_DIR,
    EXCLUDED_FROM_BACKUP,
    KIND_MANUAL,
    MANIFEST_NAME,
    STATE_NAME,
    SUPPORTED_FORMAT_VERSIONS,
    VERIFICATION_NAME,
    BackupError,
    assert_regular_file,
    backup_root,
    iter_files,
    new_backup_id,
    read_json,
    resolve_within,
    safe_relative,
    sha256_file,
    sqlite_integrity,
    sqlite_revision,
    sqlite_tables,
    utc_now,
    write_json_atomic,
)

logger = logging.getLogger(__name__)

DEFAULT_KEEP_AUTOMATIC = 3
DEFAULT_KEEP_MANUAL = 10
# Room for the copy itself plus the SQLite temp files a restore may create.
SPACE_FACTOR = 1.3
SPACE_RESERVE_BYTES = 64 * 1024 * 1024
REQUIRED_TABLES = {"users", "chats", "messages"}


def disk_free(path: Path) -> int:
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return -1


def require_space(target: Path, need: int) -> None:
    free = disk_free(target)
    if free >= 0 and free < need:
        raise BackupError(
            "insufficient_disk_space",
            "Недостаточно места на диске для операции с резервной копией.",
            f"нужно ≈{need // (1024 * 1024)} МБ, свободно {free // (1024 * 1024)} МБ",
        )


def estimate_need(*sizes: int) -> int:
    return int(sum(max(0, size) for size in sizes) * SPACE_FACTOR) + SPACE_RESERVE_BYTES


def snapshot_database(source: Path, target: Path) -> None:
    """Consistent snapshot through the SQLite backup API (WAL included, no file copy)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    uri = "file:" + Path(source).resolve().as_posix() + "?mode=ro"
    try:
        origin = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.Error as error:
        raise BackupError("backup_failed", "Не удалось открыть локальную базу.", str(error)) from None
    try:
        destination = sqlite3.connect(str(target), timeout=30)
        try:
            with destination:
                origin.backup(destination)
        finally:
            destination.close()
    except sqlite3.Error as error:
        raise BackupError("backup_failed", "Не удалось снять снимок базы.", str(error)) from None
    finally:
        origin.close()
    sqlite_integrity(target, immutable=True)


def copy_documents(source_root: Path, target_root: Path) -> list[dict]:
    """Copy the flat document store, hashing as we go. Nothing else is followed."""
    entries: list[dict] = []
    source_root = Path(source_root)
    if not source_root.is_dir():
        return entries
    for relative, path in iter_files(source_root):
        assert_regular_file(path, code="backup_failed")
        destination = resolve_within(target_root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        entries.append(
            {
                "path": f"{DOCUMENTS_DIR}/{relative}",
                "size": destination.stat().st_size,
                "sha256": sha256_file(destination),
            }
        )
    return entries


class BackupService:
    """Owns create/verify/list for one data root. Restore is deliberately elsewhere."""

    def __init__(
        self,
        data_root: Path,
        *,
        keep_automatic: int = DEFAULT_KEEP_AUTOMATIC,
        keep_manual: int = DEFAULT_KEEP_MANUAL,
    ):
        self.root = Path(data_root).resolve()
        self.keep_automatic = max(1, int(keep_automatic))
        self.keep_manual = max(1, int(keep_manual))
        self._lock = asyncio.Lock()
        self._busy = ""
        self.last_result: dict | None = None

    # ------------------------------------------------------------------ locations

    @property
    def backups_root(self) -> Path:
        return backup_root(self.root)

    @property
    def live_database(self) -> Path:
        return self.root / DATABASE_DIR / DATABASE_NAME

    @property
    def live_documents(self) -> Path:
        return self.root / DOCUMENTS_DIR

    def state(self) -> dict:
        return {
            "busy": self._busy,
            "last_result": self.last_result,
            "backups_root": str(self.backups_root),
            "format_version": BACKUP_FORMAT_VERSION,
        }

    # ------------------------------------------------------------------ create

    async def create(self, *, kind: str = KIND_MANUAL, label: str = "", record=None) -> dict:
        if self._busy:
            raise BackupError(
                "backup_busy", "Другая операция с резервными копиями уже выполняется.", self._busy
            )
        async with self._lock:
            self._busy = kind
            try:
                result = self._create(kind=kind, label=label)
            finally:
                self._busy = ""
            self.last_result = result
            if record is not None:
                record(kind, result)
            return result

    def create_now(self, *, kind: str = KIND_MANUAL, label: str = "") -> dict:
        """Synchronous create for callers that own the process (CLI, startup, restore)."""
        if self._busy:
            raise BackupError(
                "backup_busy", "Другая операция с резервными копиями уже выполняется.", self._busy
            )
        self._busy = kind
        try:
            result = self._create(kind=kind, label=label)
        finally:
            self._busy = ""
        self.last_result = result
        return result

    async def verify(self, backup_id: str, *, record=None) -> dict:
        if self._busy:
            raise BackupError(
                "backup_busy", "Другая операция с резервными копиями уже выполняется.", self._busy
            )
        async with self._lock:
            directory = self.resolve(backup_id)
            self._busy = "verifying"
            try:
                result = verify_backup(directory, write_marker=True)
            except BackupError as error:
                # A backup that failed verification must stop claiming it was verified.
                invalidate_marker(directory, error.code)
                raise
            finally:
                self._busy = ""
            self.last_result = result
            if record is not None:
                record("verify", result)
            return result

    def _create(self, *, kind: str, label: str) -> dict:
        try:
            return self._create_locked(kind=kind, label=label)
        except BackupError:
            raise
        except OSError as error:
            logger.warning("backup_failed error=%s", type(error).__name__)
            raise BackupError("backup_failed", "Не удалось создать резервную копию.", str(error)) from None

    def _create_locked(self, *, kind: str, label: str) -> dict:
        database_size = self.live_database.stat().st_size if self.live_database.is_file() else 0
        documents_size = (
            sum(path.stat().st_size for _, path in iter_files(self.live_documents))
            if self.live_documents.is_dir()
            else 0
        )
        # Check the space *before* creating anything, so a refused backup leaves no trace.
        require_space(self.root, estimate_need(database_size, documents_size))
        self.backups_root.mkdir(parents=True, exist_ok=True)
        backup_id = self._unique_id(kind)
        target = self.backups_root / backup_id
        staging = self.backups_root / f".staging-{backup_id}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)

        created_at = utc_now()
        try:
            database_target = staging / DATABASE_DIR / DATABASE_NAME
            if database_size:
                snapshot_database(self.live_database, database_target)
            documents = copy_documents(self.live_documents, staging / DOCUMENTS_DIR)
            state = {
                "restores": {"database": bool(database_size), "documents": True},
                "documents_count": len(documents),
                "note": "Машинная идентичность и секреты в копию не входят и не восстанавливаются.",
            }
            state_path = staging / STATE_NAME
            write_json_atomic(state_path, state)

            database_entry = None
            if database_size:
                database_entry = {
                    "path": f"{DATABASE_DIR}/{DATABASE_NAME}",
                    "size": database_target.stat().st_size,
                    "sha256": sha256_file(database_target),
                }
            manifest = {
                "backup_format_version": BACKUP_FORMAT_VERSION,
                "product": "alex-llm",
                "app_version": VERSION,
                "schema_revision": sqlite_revision(database_target, immutable=True)
                if database_size
                else None,
                "backup_id": backup_id,
                "kind": kind,
                "label": label[:120],
                "created_at": created_at.isoformat(),
                "source_machine": self._machine_hint(),
                "database": database_entry,
                "documents": {
                    "path": DOCUMENTS_DIR,
                    "count": len(documents),
                    "files": documents,
                },
                "state": {
                    "path": STATE_NAME,
                    "size": state_path.stat().st_size,
                    "sha256": sha256_file(state_path),
                },
                "excluded": list(EXCLUDED_FROM_BACKUP),
                "bytes_total": sum(
                    entry["size"] for entry in ([database_entry] if database_entry else []) + documents
                )
                + state_path.stat().st_size,
            }
            write_json_atomic(staging / MANIFEST_NAME, manifest)
            os.replace(staging, target)
        except BackupError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except OSError as error:
            shutil.rmtree(staging, ignore_errors=True)
            raise BackupError("backup_failed", "Не удалось создать резервную копию.", str(error)) from None

        try:
            result = verify_backup(target, write_marker=True)
        except BackupError:
            # An unverified backup is not a backup: keep the directory for inspection but
            # never present it as usable.
            logger.warning("backup_unverified backup_id=%s", backup_id)
            raise
        result["kind"] = kind
        result["label"] = label
        result["pruned"] = self.prune()
        logger.info(
            "backup_created id=%s kind=%s files=%s bytes=%s revision=%s",
            backup_id,
            kind,
            result.get("files"),
            result.get("bytes"),
            result.get("schema_revision"),
        )
        return result

    def _unique_id(self, kind: str) -> str:
        base = new_backup_id(kind)
        candidate = base
        index = 2
        while (self.backups_root / candidate).exists():
            candidate = f"{base}-{index}"
            index += 1
        return candidate

    def _machine_hint(self) -> str | None:
        """Advisory, non-identifying marker for diagnostics: a digest of the source install
        id, so a backup never carries the identity of the machine that produced it."""
        path = self.root / "runtime" / "install.id"
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not value:
            return None
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------ list / prune

    def resolve(self, backup_id: str) -> Path:
        """Resolve a backup id inside the backups root. No path may leave that root."""
        from .format import BACKUP_ID_PATTERN

        if not isinstance(backup_id, str) or not BACKUP_ID_PATTERN.fullmatch(backup_id):
            raise BackupError("backup_missing", "Некорректный идентификатор резервной копии.")
        target = self.backups_root / backup_id
        if target.is_symlink() or not target.is_dir():
            raise BackupError("backup_missing", "Резервная копия не найдена.", backup_id)
        resolved = target.resolve()
        if self.backups_root.resolve() not in resolved.parents:
            raise BackupError("restore_unsafe_path", "Копия находится вне папки резервных копий.")
        return resolved

    def list_backups(self) -> list[dict]:
        if not self.backups_root.is_dir():
            return []
        summaries: list[dict] = []
        for entry in sorted(self.backups_root.iterdir(), key=lambda path: path.name, reverse=True):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            summaries.append(self._summarize(entry))
        return summaries

    def _summarize(self, directory: Path) -> dict:
        summary = {
            "id": directory.name,
            "path": str(directory),
            "kind": None,
            "label": "",
            "created_at": None,
            "app_version": None,
            "schema_revision": None,
            "files": 0,
            "bytes": 0,
            "verified": False,
            "verified_at": None,
            "complete": False,
            "problem": None,
        }
        manifest_path = directory / MANIFEST_NAME
        if not manifest_path.is_file():
            summary["problem"] = "backup_incomplete"
            return summary
        try:
            manifest = read_json(manifest_path)
        except BackupError as error:
            summary["problem"] = error.code
            return summary
        summary.update(
            {
                "kind": manifest.get("kind"),
                "label": manifest.get("label") or "",
                "created_at": manifest.get("created_at"),
                "app_version": manifest.get("app_version"),
                "schema_revision": manifest.get("schema_revision"),
                "files": 1
                + (len((manifest.get("documents") or {}).get("files") or []))
                + (1 if manifest.get("database") else 0),
                "bytes": int(manifest.get("bytes_total") or 0),
                "complete": True,
            }
        )
        verification_path = directory / VERIFICATION_NAME
        if verification_path.is_file():
            try:
                marker = read_json(verification_path)
            except BackupError:
                marker = {}
            summary["verified"] = bool(marker.get("verified"))
            summary["verified_at"] = marker.get("verified_at")
            if not summary["verified"] and marker.get("reason"):
                summary["problem"] = marker["reason"]
        return summary

    def prune(self) -> list[str]:
        """Bounded retention. Never deletes the newest backup or the last verified one."""
        summaries = [row for row in self.list_backups() if row["complete"]]
        if len(summaries) <= 1:
            return []
        automatic = [row for row in summaries if row.get("kind") in AUTOMATIC_KINDS]
        manual = [row for row in summaries if row.get("kind") not in AUTOMATIC_KINDS]
        doomed: list[dict] = []
        if len(automatic) > self.keep_automatic:
            doomed += sorted(automatic, key=lambda row: row["created_at"] or "")[
                : len(automatic) - self.keep_automatic
            ]
        if len(manual) > self.keep_manual:
            doomed += sorted(manual, key=lambda row: row["created_at"] or "")[
                : len(manual) - self.keep_manual
            ]
        newest = max(summaries, key=lambda row: row["created_at"] or "")
        removed: list[str] = []
        for row in doomed:
            if row["id"] == newest["id"]:
                continue
            if row["verified"] and sum(1 for other in summaries if other["verified"]) <= 1:
                continue
            target = self.backups_root / row["id"]
            if target.is_symlink() or self.backups_root.resolve() not in target.resolve().parents:
                continue
            shutil.rmtree(target, ignore_errors=True)
            removed.append(row["id"])
            logger.info("backup_pruned id=%s kind=%s", row["id"], row.get("kind"))
        return removed

    # ------------------------------------------------------------------ diagnostics

    def diagnostic(
        self, *, schema_revision: str | None, head_revision: str | None, last_migration: dict | None
    ) -> dict:
        summaries = self.list_backups()
        verified = [row for row in summaries if row["verified"]]
        free = disk_free(self.backups_root if self.backups_root.exists() else self.root)
        return {
            "app_version": VERSION,
            "backup_format_version": BACKUP_FORMAT_VERSION,
            "schema_revision": schema_revision,
            "head_revision": head_revision,
            "migration_pending": bool(schema_revision and head_revision and schema_revision != head_revision),
            "last_migration": last_migration,
            "backups_root": str(self.backups_root),
            "backups_count": len(summaries),
            "verified_count": len(verified),
            "last_backup_at": max((row["created_at"] or "" for row in summaries), default=None),
            "last_verified_at": max((row["verified_at"] or "" for row in verified), default=None),
            "disk_free_bytes": free,
            "keep_automatic": self.keep_automatic,
            "keep_manual": self.keep_manual,
        }


def verify_backup(directory: Path, *, write_marker: bool = False) -> dict:
    """Deep verification of one backup directory. Raises BackupError on any problem."""
    directory = Path(directory)
    manifest = read_json(directory / MANIFEST_NAME)
    version = manifest.get("backup_format_version")
    if version not in SUPPORTED_FORMAT_VERSIONS:
        raise BackupError(
            "backup_unsupported_format",
            "Формат резервной копии не поддерживается этой версией Alex.",
            f"backup_format_version={version}",
        )
    files_checked = 0
    bytes_checked = 0

    def check(entry: dict) -> None:
        nonlocal files_checked, bytes_checked
        if not isinstance(entry, dict):
            raise BackupError("backup_corrupt", "Некорректная запись в описании копии.")
        relative = safe_relative(entry.get("path"))
        target = resolve_within(directory, relative)
        assert_regular_file(target, code="backup_incomplete")
        size = target.stat().st_size
        if entry.get("size") is not None and int(entry["size"]) != size:
            raise BackupError("backup_tampered", "Размер файла в копии не совпадает с описанием.", relative)
        expected = entry.get("sha256")
        if expected and sha256_file(target) != expected:
            raise BackupError("backup_tampered", "Контрольная сумма файла в копии не совпадает.", relative)
        files_checked += 1
        bytes_checked += size

    database = manifest.get("database")
    if database:
        check(database)
        database_path = resolve_within(directory, database["path"])
        sqlite_integrity(database_path, immutable=True)
        tables = sqlite_tables(database_path, immutable=True)
        missing = REQUIRED_TABLES - tables
        if missing:
            raise BackupError(
                "backup_corrupt",
                "В базе резервной копии нет обязательных таблиц.",
                ", ".join(sorted(missing)),
            )
    else:
        raise BackupError("backup_incomplete", "В резервной копии нет базы данных.")

    documents = manifest.get("documents") or {}
    document_entries = documents.get("files") or []
    for entry in document_entries:
        check(entry)
    if documents.get("count") is not None and int(documents["count"]) != len(document_entries):
        raise BackupError("backup_corrupt", "Опись документов не совпадает с описанием копии.")

    state = manifest.get("state")
    if isinstance(state, dict) and state.get("path"):
        check(state)

    on_disk = {relative for relative, _ in iter_files(directory)}
    declared = {DATABASE_DIR + "/" + DATABASE_NAME, STATE_NAME, MANIFEST_NAME}
    declared |= {entry["path"] for entry in document_entries}
    if isinstance(state, dict) and state.get("path"):
        declared.add(state["path"])
    # The verification marker is written by this verifier itself, so it is allowed to
    # exist but is never required and never part of the inventory.
    undeclared = sorted(on_disk - declared - {VERIFICATION_NAME})
    if undeclared:
        raise BackupError("backup_tampered", "В копии есть файлы, которых нет в описании.", undeclared[0])
    missing_files = sorted(declared - on_disk)
    if missing_files:
        raise BackupError("backup_incomplete", "В копии не хватает файлов.", missing_files[0])

    result = {
        "id": directory.name,
        "path": str(directory),
        "verified": True,
        "verified_at": utc_now().isoformat(),
        "backup_format_version": version,
        "app_version": manifest.get("app_version"),
        "schema_revision": manifest.get("schema_revision"),
        "created_at": manifest.get("created_at"),
        "files": files_checked,
        "bytes": bytes_checked,
        "documents": len(document_entries),
        "kind": manifest.get("kind"),
        "label": manifest.get("label") or "",
    }
    if write_marker:
        write_json_atomic(
            directory / VERIFICATION_NAME,
            {
                "verified": True,
                "verified_at": result["verified_at"],
                "backup_format_version": version,
                "files": files_checked,
                "bytes": bytes_checked,
                "schema_revision": manifest.get("schema_revision"),
            },
        )
    return result


def invalidate_marker(directory: Path, reason: str) -> None:
    """Record that a backup failed verification, so no client keeps showing it as verified.
    The backup directory itself is never modified beyond this marker."""
    directory = Path(directory)
    if not directory.is_dir():
        return
    try:
        write_json_atomic(
            directory / VERIFICATION_NAME,
            {"verified": False, "reason": reason, "verified_at": utc_now().isoformat()},
        )
    except OSError:  # pragma: no cover - a read-only backup directory
        logger.warning("backup_marker_not_invalidated reason=%s", reason)


def cleanup_staging(root: Path) -> list[str]:
    """Remove staging leftovers from an interrupted operation. Only inside backups/."""
    backups = backup_root(root)
    if not backups.is_dir():
        return []
    removed: list[str] = []
    for entry in backups.iterdir():
        if entry.name.startswith(".staging-") and entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(entry.name)
    return removed
