"""Local backup format: paths, manifest, hashing and the validation rules.

The format is deliberately a **directory**, not an opaque archive: every artifact is a
plain file that a support engineer can inspect, and every rule here is about refusing to
touch anything outside the data root.

Layout of one backup (``<data root>/backups/<backup id>/``)::

    manifest.json          inventory + hashes + format version (written last)
    state.json             non-secret plan for a restore
    verification.json      written by the verifier that accepted this backup
    data/alex.db           consistent SQLite snapshot (never the live file bytes)
    documents/<key>        user documents exactly as the storage layer keeps them

Never inside: ``runtime/jwt.secret``, ``runtime/install.id``, ``runtime/session.id``,
``runtime/shutdown.token``, ``device.json`` (machine identity), ``models/embeddings``
(recreatable cache), ``logs`` and **any Windows Credential Manager material**.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

BACKUP_FORMAT_VERSION = 1
SUPPORTED_FORMAT_VERSIONS = (1,)

BACKUPS_DIR_NAME = "backups"
MANIFEST_NAME = "manifest.json"
VERIFICATION_NAME = "verification.json"
STATE_NAME = "state.json"
DATABASE_DIR = "data"
DATABASE_NAME = "alex.db"
DOCUMENTS_DIR = "documents"

BACKUP_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")

# Machine-local identity, secrets and recreatable cache: never copied into a backup and
# never written back by a restore. Restoring these would clone a machine identity.
EXCLUDED_FROM_BACKUP = (
    "runtime/jwt.secret",
    "runtime/install.id",
    "runtime/session.id",
    "runtime/shutdown.token",
    "runtime/backend.lock",
    "runtime/migration.json",
    "runtime/backup-restore.json",
    "device.json",
    "models/embeddings",
    "logs",
)

KIND_MANUAL = "manual"
KIND_PRE_UPGRADE = "pre_upgrade"
KIND_PRE_RESTORE = "pre_restore"
AUTOMATIC_KINDS = (KIND_PRE_UPGRADE, KIND_PRE_RESTORE)


class BackupError(Exception):
    """Stable code + human message. Codes are part of the API contract."""

    def __init__(self, code: str, message: str = "", detail: str = ""):
        super().__init__(code)
        self.code = code
        self.message = message or code
        self.detail = detail

    def as_dict(self) -> dict:
        payload = {"code": self.code, "message": self.message}
        if self.detail:
            payload["detail"] = self.detail
        return payload


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_stamp(moment: datetime | None = None) -> str:
    return (moment or utc_now()).strftime("%Y%m%dT%H%M%SZ")


def backup_root(data_root: Path) -> Path:
    return Path(data_root) / BACKUPS_DIR_NAME


def new_backup_id(kind: str, moment: datetime | None = None) -> str:
    return f"{utc_stamp(moment)}-{kind}"


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_json_atomic(path: Path, payload: dict) -> None:
    """Temp file + replace, so a reader never sees a half-written manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BackupError("backup_missing", "Резервная копия не найдена.", str(path)) from None
    except (OSError, ValueError):
        raise BackupError("backup_corrupt", "Файл резервной копии повреждён.", str(path)) from None
    if not isinstance(payload, dict):
        raise BackupError("backup_corrupt", "Некорректный формат резервной копии.", str(path))
    return payload


def safe_relative(relative: object) -> str:
    """Validate a path stored in a manifest before it is used on disk."""
    if not isinstance(relative, str) or not relative:
        raise BackupError("restore_unsafe_path", "Путь внутри копии пуст.")
    if "\x00" in relative:
        raise BackupError("restore_unsafe_path", "Путь внутри копии содержит недопустимый символ.")
    normalized = relative.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise BackupError("restore_unsafe_path", "Абсолютный путь внутри копии запрещён.", relative)
    parts = PurePosixPath(normalized).parts
    if any(part in ("..", "") for part in parts):
        raise BackupError("restore_unsafe_path", "Выход за пределы папки копии запрещён.", relative)
    return "/".join(parts)


def resolve_within(root: Path, relative: object) -> Path:
    """Resolve `relative` under `root`, refusing traversal and symlink escapes."""
    root = Path(root).resolve()
    clean = safe_relative(relative)
    target = root
    for part in PurePosixPath(clean).parts:
        target = target / part
        if target.is_symlink():
            raise BackupError("restore_unsafe_path", "Символические ссылки внутри копии запрещены.", clean)
    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        raise BackupError("restore_unsafe_path", "Путь выходит за пределы копии.", clean)
    return resolved


def assert_regular_file(path: Path, code: str = "restore_unsafe_path") -> None:
    if path.is_symlink() or not path.is_file():
        raise BackupError(code, "Ожидался обычный файл.", str(path))


def iter_files(root: Path) -> list[tuple[str, Path]]:
    """Every regular file under `root`, sorted by relative posix path. Symlinks raise."""
    root = Path(root)
    found: list[tuple[str, Path]] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(current) / name
            if path.is_symlink():
                raise BackupError("restore_unsafe_path", "Символические ссылки запрещены.", str(path))
            found.append((path.relative_to(root).as_posix(), path))
    return sorted(found, key=lambda row: row[0])


def portable_path(path: Path) -> Path:
    r"""Strip a Windows verbatim prefix (`\\?\C:\...`), which `Path.resolve()` can return
    and which would otherwise turn a SQLite `file:` URI into an invalid one
    (`file://?/C:/...`). Callers may pass a path produced by any tool, so this is enforced
    at the point every read-only URI is built.
    """
    text = str(path)
    if text.startswith("\\\\?\\"):
        text = text[4:]
        if text.upper().startswith("UNC\\"):
            text = "\\\\" + text[4:]
    elif text.startswith("//?/"):
        text = text[4:]
        if text.upper().startswith("UNC/"):
            text = "//" + text[4:]
    return Path(text)


def _readonly_uri(path: Path, immutable: bool) -> str:
    """A read-only SQLite URI. ``immutable`` also forbids WAL/SHM side files, which is
    what a backup snapshot needs: verifying a backup must never write into it."""
    query = "?mode=ro&immutable=1" if immutable else "?mode=ro"
    return "file:" + portable_path(Path(path).resolve()).as_posix() + query


def sqlite_integrity(path: Path, *, immutable: bool = False) -> str:
    """``PRAGMA integrity_check`` on a snapshot. Returns the first row."""
    if not Path(path).is_file():
        raise BackupError("backup_incomplete", "В копии нет файла базы данных.")
    try:
        connection = sqlite3.connect(_readonly_uri(path, immutable), uri=True, timeout=10)
    except sqlite3.Error as error:  # pragma: no cover - depends on the filesystem
        raise BackupError("backup_corrupt", "База в копии не открывается.", str(error)) from None
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as error:
        raise BackupError("backup_corrupt", "База в копии повреждена.", str(error)) from None
    finally:
        connection.close()
    result = (row or ["unknown"])[0]
    if result != "ok":
        raise BackupError("backup_corrupt", "Проверка целостности базы не пройдена.", str(result))
    return result


def sqlite_revision(path: Path, *, immutable: bool = False) -> str | None:
    """Alembic revision stored in the snapshot, or None for an unmigrated database."""
    try:
        connection = sqlite3.connect(_readonly_uri(path, immutable), uri=True, timeout=10)
    except sqlite3.Error:
        return None
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    return str(row[0]) if row else None


def sqlite_tables(path: Path, *, immutable: bool = False) -> set[str]:
    try:
        connection = sqlite3.connect(_readonly_uri(path, immutable), uri=True, timeout=10)
    except sqlite3.Error:
        return set()
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    except sqlite3.Error:
        return set()
    finally:
        connection.close()
    return {str(row[0]) for row in rows}
