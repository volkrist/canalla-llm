"""Local backup, verification and restore (upgrade safety).

Everything here runs on a temporary data root: no test touches the real
``%LOCALAPPDATA%\\Alex LLM`` tree, no Credential Manager entry and no Gateway.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.backup.archive import BackupService, verify_backup
from app.backup.format import (
    BACKUP_FORMAT_VERSION,
    MANIFEST_NAME,
    STATE_NAME,
    VERIFICATION_NAME,
    BackupError,
    sha256_file,
)
from app.backup.restore import cleanup_restore_leftovers, restore_backup, write_result

BACKEND = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------------------- fixtures


def migrate(database: Path, revision: str = "head") -> None:
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + database.as_posix()}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=str(BACKEND),
        env=env,
        check=True,
        capture_output=True,
    )


@contextmanager
def connect(database: Path):
    """SQLite connection that is really closed afterwards (Windows keeps a handle
    otherwise, and an open handle blocks the atomic rename a restore performs)."""
    connection = sqlite3.connect(database, timeout=30)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def seed(database: Path, *, users: int = 2, chats: int = 3, documents: list[str] | None = None) -> dict:
    """Representative content: users, chats, messages, a project, memory, settings."""
    with connect(database) as db:
        db.execute("PRAGMA foreign_keys=ON")
        counts = {}
        for index in range(users):
            user = f"user-{index}"
            db.execute(
                "INSERT INTO users (id,email,password_hash,created_at,display_name) VALUES (?,?,?,?,?)",
                (user, f"user{index}@example.com", "hash", "2026-09-21", f"Пользователь {index}"),
            )
            for chat_index in range(chats):
                chat = f"chat-{index}-{chat_index}"
                db.execute(
                    "INSERT INTO chats (id,user_id,title,created_at,updated_at) VALUES (?,?,?,?,?)",
                    (chat, user, f"Чат {index}-{chat_index}", "2026-09-21", "2026-09-21"),
                )
                for message in range(2):
                    db.execute(
                        "INSERT INTO messages (id,chat_id,role,content,created_at) VALUES (?,?,?,?,?)",
                        (
                            f"msg-{index}-{chat_index}-{message}",
                            chat,
                            "user" if message == 0 else "assistant",
                            f"Сообщение {message} — юникод: пример 🚀",
                            "2026-09-21",
                        ),
                    )
            db.execute(
                "INSERT INTO projects (id,user_id,name,description,status,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (f"project-{index}", user, f"Проект {index}", "", "active", "2026-09-21", "2026-09-21"),
            )
            db.execute(
                "INSERT INTO memories (id,user_id,category,content,importance,is_pinned,is_active,"
                "use_count,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"memory-{index}", user, "fact", f"Память {index}", 3, 0, 1, 0, "2026-09-21", "2026-09-21"),
            )
        counts["users"] = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        counts["chats"] = db.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        counts["messages"] = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        counts["projects"] = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        counts["memories"] = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    return counts


def make_root(tmp_path: Path, template: Path, *, with_data: bool = True, revision: str = "head") -> Path:
    """A data root that looks like an installed Alex (db + documents + machine files).

    ``revision`` selects the schema the database sits at. An older revision is built by
    migrating a fresh database up to it (that is what a real older install looks like);
    alembic would not downgrade a copy of a head database.
    """
    root = tmp_path / "Alex LLM"
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "documents").mkdir(parents=True, exist_ok=True)
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "models" / "embeddings" / "multilingual-e5-small").mkdir(parents=True, exist_ok=True)
    database = root / "data" / "alex.db"
    if revision == "head":
        shutil.copy2(template, database)
    else:
        migrate(database, revision)
    if with_data:
        seed(database)
    (root / "runtime" / "jwt.secret").write_text("j" * 64, encoding="utf-8")
    (root / "runtime" / "install.id").write_text("machine-install-id-1234", encoding="utf-8")
    (root / "runtime" / "session.id").write_text("session-pointer-5678", encoding="utf-8")
    (root / "runtime" / "shutdown.token").write_text("t" * 40, encoding="utf-8")
    (root / "device.json").write_text(
        '{"device_id":"deadbeef","display_name":"Windows device"}', encoding="utf-8"
    )
    (root / "logs" / "backend.log").write_text("log line\n", encoding="utf-8")
    (root / "models" / "embeddings" / "multilingual-e5-small" / "model.onnx").write_bytes(b"weights")
    for index, name in enumerate(("alpha", "beta")):
        (root / "documents" / (f"{index:032x}")).write_bytes(f"document body {name}".encode())
    return root


def counts(root: Path) -> dict:
    with connect(root / "data" / "alex.db") as db:
        return {
            "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "chats": db.execute("SELECT COUNT(*) FROM chats").fetchone()[0],
            "messages": db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            "projects": db.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
            "memories": db.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
            "documents": len(list((root / "documents").glob("*"))),
        }


def all_bytes(root: Path) -> bytes:
    payload = b""
    for path in sorted(root.rglob("*")):
        if path.is_file():
            payload += path.read_bytes()
    return payload


# --------------------------------------------------------------------------- create/verify


def test_backup_is_verified_and_describes_itself(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    expected = counts(root)
    result = BackupService(root).create_now(kind="manual", label="unit")

    assert result["verified"] is True
    assert result["schema_revision"] == "0015"
    assert result["documents"] == expected["documents"]
    backup = Path(result["path"])
    manifest = json.loads((backup / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["backup_format_version"] == BACKUP_FORMAT_VERSION
    assert manifest["app_version"] == "1.1.0"
    assert manifest["kind"] == "manual"
    assert manifest["database"]["sha256"] == sha256_file(backup / "data" / "alex.db")
    assert manifest["documents"]["count"] == expected["documents"]
    assert (backup / VERIFICATION_NAME).is_file()
    assert (backup / STATE_NAME).is_file()
    assert manifest["excluded"], "the manifest must state what it refuses to copy"
    # The snapshot has the real content, not a placeholder.
    restored_counts = counts_in(backup / "data" / "alex.db")
    assert restored_counts["users"] == expected["users"]
    assert restored_counts["messages"] == expected["messages"]


def counts_in(database: Path) -> dict:
    with connect(database) as db:
        return {
            "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "messages": db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            "projects": db.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
            "memories": db.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
        }


def test_backup_never_contains_secrets_or_machine_identity(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    result = BackupService(root).create_now(kind="manual")
    blob = all_bytes(Path(result["path"]))

    for secret in (
        "j" * 64,
        "machine-install-id-1234",
        "session-pointer-5678",
        "t" * 40,
        "deadbeef",
        "log line",
    ):
        assert secret.encode() not in blob, f"backup leaked {secret[:12]}…"
    assert b"weights" not in blob  # the embedding cache is recreatable
    names = {path.name for path in Path(result["path"]).rglob("*")}
    assert "jwt.secret" not in names and "install.id" not in names and "device.json" not in names


def test_backup_is_consistent_while_the_database_is_being_written(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    database = root / "data" / "alex.db"
    stop = threading.Event()

    def writer() -> None:
        with connect(database) as db:
            db.execute("PRAGMA journal_mode=WAL")
            index = 0
            while not stop.is_set():
                index += 1
                db.execute(
                    "INSERT INTO messages (id,chat_id,role,content,created_at) VALUES (?,?,?,?,?)",
                    (
                        f"live-{index}",
                        db.execute("SELECT id FROM chats LIMIT 1").fetchone()[0],
                        "user",
                        f"write {index}",
                        "2026-09-21",
                    ),
                )
                db.commit()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        time.sleep(0.3)
        result = BackupService(root).create_now(kind="manual")
    finally:
        stop.set()
        thread.join(timeout=10)

    snapshot = Path(result["path"]) / "data" / "alex.db"
    with connect(snapshot) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        live_rows = db.execute("SELECT COUNT(*) FROM messages WHERE id LIKE 'live-%'").fetchone()[0]
    # Consistent: every copied live row is complete, and the snapshot is from one instant.
    assert live_rows >= 1
    assert result["verified"] is True


def test_verification_detects_tampering_and_incompleteness(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    result = BackupService(root).create_now(kind="manual")
    backup = Path(result["path"])

    document = next((backup / "documents").iterdir())
    document.write_bytes(document.read_bytes() + b"x")
    with pytest.raises(BackupError) as error:
        verify_backup(backup)
    assert error.value.code == "backup_tampered"

    document.write_bytes(document.read_bytes()[:-1])
    verify_backup(backup)

    manifest = json.loads((backup / MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["documents"]["files"][0]["sha256"] = "0" * 64
    (backup / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError) as error:
        verify_backup(backup)
    assert error.value.code == "backup_tampered"


def test_verification_detects_missing_files_and_corrupt_database(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    result = BackupService(root).create_now(kind="manual")
    backup = Path(result["path"])

    (backup / "documents" / next((backup / "documents").iterdir()).name).unlink()
    with pytest.raises(BackupError) as error:
        verify_backup(backup)
    assert error.value.code == "backup_incomplete"

    root_two = make_root(tmp_path / "second", template_database)
    other = BackupService(root_two).create_now(kind="manual")
    database = Path(other["path"]) / "data" / "alex.db"
    database.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    manifest = json.loads((Path(other["path"]) / MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["database"]["sha256"] = sha256_file(database)
    manifest["database"]["size"] = database.stat().st_size
    (Path(other["path"]) / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError) as error:
        verify_backup(Path(other["path"]))
    assert error.value.code in ("backup_corrupt", "backup_incomplete")


def test_unsupported_format_is_rejected_cleanly(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    result = BackupService(root).create_now(kind="manual")
    backup = Path(result["path"])
    manifest = json.loads((backup / MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["backup_format_version"] = 99
    (backup / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError) as error:
        verify_backup(backup)
    assert error.value.code == "backup_unsupported_format"


def test_manifest_paths_cannot_escape_the_backup(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    result = BackupService(root).create_now(kind="manual")
    backup = Path(result["path"])
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    for evil in ("../outside.txt", "/etc/passwd", "C:/Windows/win.ini", "documents/../../outside.txt"):
        manifest = json.loads((backup / MANIFEST_NAME).read_text(encoding="utf-8"))
        manifest["documents"]["files"] = [
            {"path": evil, "size": outside.stat().st_size, "sha256": sha256_file(outside)}
        ]
        (backup / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(BackupError) as error:
            verify_backup(backup)
        assert error.value.code in ("restore_unsafe_path", "backup_tampered", "backup_incomplete"), evil


def test_disk_space_is_checked_before_writing_anything(tmp_path, template_database, monkeypatch):
    root = make_root(tmp_path, template_database)
    monkeypatch.setattr("app.backup.archive.disk_free", lambda _path: 1024)
    with pytest.raises(BackupError) as error:
        BackupService(root).create_now(kind="manual")
    assert error.value.code == "insufficient_disk_space"
    backups = root / "backups"
    assert not backups.exists() or not list(backups.iterdir())


def test_retention_is_bounded_and_never_drops_the_last_backup(tmp_path, template_database, monkeypatch):
    import itertools
    from datetime import datetime, timedelta, timezone

    root = make_root(tmp_path, template_database)
    service = BackupService(root, keep_automatic=3, keep_manual=2)
    counter = itertools.count()
    base = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.backup.archive.utc_now", lambda: base + timedelta(minutes=next(counter)))

    for kind in (
        "pre_upgrade",
        "pre_upgrade",
        "pre_upgrade",
        "pre_upgrade",
        "pre_restore",
        "manual",
        "manual",
        "manual",
    ):
        service.create_now(kind=kind)
    ids = {row["id"] for row in service.list_backups()}
    assert len([row for row in service.list_backups() if row["kind"] == "pre_upgrade"]) <= 3
    assert len([row for row in service.list_backups() if row["kind"] == "manual"]) <= 2
    assert ids, "pruning must never empty the backup directory"
    for row in service.list_backups():
        assert verify_backup(Path(row["path"]))["verified"] is True


# ------------------------------------------------------------------------------ restore


def test_restore_returns_the_exact_previous_state(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    before = counts(root)
    service = BackupService(root)
    backup = service.create_now(kind="manual")

    # Mutate everything the user cares about, then restore.
    with connect(root / "data" / "alex.db") as db:
        db.execute("DELETE FROM messages")
        db.execute("DELETE FROM chats")
        db.execute("DELETE FROM projects")
        db.execute("DELETE FROM memories")
        db.execute("DELETE FROM users")
    for document in (root / "documents").iterdir():
        document.unlink()
    (root / "documents" / ("f" * 32)).write_bytes(b"new document")
    assert counts(root)["users"] == 0

    report = restore_backup(Path(backup["path"]), root)

    assert report["restored"] is True
    assert report["to_revision"] == "0015"
    assert report["safety_backup_id"]
    assert counts(root) == before
    assert not list(root.glob("documents.pre-restore-*"))
    assert not list((root / "data").glob("restore-*"))
    # The safety backup is a verified copy of the state we just replaced.
    safety = Path(backup["path"]).parent / report["safety_backup_id"]
    assert verify_backup(safety)["verified"] is True
    # Machine identity is never part of a restore.
    assert (root / "runtime" / "install.id").read_text(encoding="utf-8") == "machine-install-id-1234"
    assert (root / "runtime" / "session.id").read_text(encoding="utf-8") == "session-pointer-5678"
    assert (root / "runtime" / "jwt.secret").read_text(encoding="utf-8") == "j" * 64
    assert (root / "device.json").is_file()


def test_restore_rejects_a_corrupt_backup_and_leaves_data_alone(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    before = counts(root)
    backup = BackupService(root).create_now(kind="manual")
    document = next((Path(backup["path"]) / "documents").iterdir())
    document.write_bytes(b"tampered")

    with pytest.raises(BackupError) as error:
        restore_backup(Path(backup["path"]), root)
    assert error.value.code == "backup_tampered"
    assert counts(root) == before


def test_restore_rejects_a_future_schema(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    before = counts(root)
    backup = BackupService(root).create_now(kind="manual")
    manifest_path = Path(backup["path"]) / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_revision"] = "9999"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BackupError) as error:
        restore_backup(Path(backup["path"]), root)
    assert error.value.code == "backup_unsupported_format"
    assert counts(root) == before


def test_restore_refuses_a_locked_database_and_keeps_the_current_state(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    before = counts(root)
    backup = BackupService(root).create_now(kind="manual")

    holder = sqlite3.connect(root / "data" / "alex.db", timeout=1, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(BackupError) as error:
            restore_backup(Path(backup["path"]), root)
        assert error.value.code == "restore_busy"
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert counts(root) == before


def test_restore_rolls_back_when_the_swapped_database_fails_validation(
    tmp_path, template_database, monkeypatch
):
    root = make_root(tmp_path, template_database)
    before = counts(root)
    backup = BackupService(root).create_now(kind="manual")

    import app.backup.restore as module

    calls = {"count": 0}
    original = module.sqlite_integrity

    def failing(path):
        calls["count"] += 1
        if calls["count"] >= 3:  # staging (1-2) passes, the post-swap check does not
            raise BackupError("restore_failed", "injected failure")
        return original(path)

    monkeypatch.setattr(module, "sqlite_integrity", failing)
    with pytest.raises(BackupError):
        restore_backup(Path(backup["path"]), root)
    assert counts(root) == before, "the previous state must be back"
    assert not list((root / "data").glob("restore-*"))


def test_concurrent_creates_do_not_corrupt_each_other(tmp_path, template_database):
    import asyncio

    root = make_root(tmp_path, template_database)
    service = BackupService(root)

    async def scenario():
        return await asyncio.gather(
            service.create(kind="manual", label="one"),
            service.create(kind="manual", label="two"),
            return_exceptions=True,
        )

    results = asyncio.run(scenario())
    successes = [row for row in results if isinstance(row, dict)]
    failures = [row for row in results if isinstance(row, BackupError)]
    assert successes, "at least one backup must succeed"
    assert all(error.code == "backup_busy" for error in failures)
    for row in successes:
        assert verify_backup(Path(row["path"]))["verified"] is True
    # A busy service refuses new work while an operation is in flight.
    service._busy = "manual"
    with pytest.raises(BackupError) as error:
        service.create_now(kind="manual")
    assert error.value.code == "backup_busy"
    service._busy = ""


def test_cleanup_removes_only_staging_leftovers(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    staging = root / "backups" / ".staging-20260921T000000Z-manual"
    staging.mkdir(parents=True)
    (staging / "partial").write_text("x", encoding="utf-8")
    (root / "data" / "restore-20260921T000000Z").mkdir(parents=True)
    (root / "documents.restore-20260921T000000Z").mkdir(parents=True)
    keep = next((root / "documents").iterdir())
    assert keep.is_file()

    from app.backup.archive import cleanup_staging

    cleaned = cleanup_staging(root)
    leftovers = cleanup_restore_leftovers(root)
    assert cleaned and leftovers
    assert not staging.exists()
    assert not (root / "data" / "restore-20260921T000000Z").exists()
    assert not (root / "documents.restore-20260921T000000Z").exists()
    assert keep.is_file()


def test_portable_path_strips_windows_verbatim_prefixes(tmp_path, template_database):
    """A verbatim path from another process must not break a SQLite URI."""
    from app.backup.format import portable_path

    assert portable_path(Path(r"\\?\C:\Users\Alex LLM\backups\x")) == Path(r"C:\Users\Alex LLM\backups\x")
    assert portable_path(Path("//?/C:/data/alex.db")) == Path("C:/data/alex.db")
    assert portable_path(Path(r"\\?\UNC\server\share\x")) == Path(r"\\server\share\x")
    plain = tmp_path / "plain.db"
    assert portable_path(plain) == plain


def test_restore_accepts_a_verbatim_path_from_another_process(tmp_path, template_database):
    """The Desktop hands the helper a canonicalized Windows path (`\\\\?\\C:\\…`)."""
    if os.name != "nt":
        pytest.skip("Windows-only path form")
    root = make_root(tmp_path, template_database)
    before = counts(root)
    backup = BackupService(root).create_now(kind="manual")
    with connect(root / "data" / "alex.db") as db:
        db.execute("DELETE FROM messages")

    verbatim = Path(str(Path(backup["path"]).resolve()))
    report = restore_backup(Path("\\\\?\\" + str(verbatim)), root)
    assert report["restored"] is True
    assert counts(root) == before


def test_readonly_uri_is_built_from_a_portable_path(tmp_path):
    """Pins the exact bug the Desktop found: a verbatim path makes SQLite reject the URI
    with "invalid uri authority: ?" instead of opening the snapshot."""
    from app.backup.format import _readonly_uri, portable_path

    database = tmp_path / "uri.db"
    connection = sqlite3.connect(str(database))
    connection.execute("create table marker(payload text)")
    connection.commit()
    connection.close()

    verbatim = Path("\\\\?\\" + str(database)) if os.name == "nt" else database
    assert portable_path(verbatim) == database
    uri = _readonly_uri(verbatim, True)
    assert "//?/" not in uri, uri
    reader = sqlite3.connect(uri, uri=True)
    try:
        assert reader.execute("select name from sqlite_master").fetchall() == [("marker",)]
    finally:
        reader.close()


def test_failed_verification_clears_the_verified_marker(tmp_path, template_database):
    """A backup that failed a fresh verification must stop claiming it was verified."""
    import asyncio

    root = make_root(tmp_path, template_database)
    service = BackupService(root)
    created = service.create_now(kind="manual")
    assert service.list_backups()[0]["verified"] is True

    document = next((Path(created["path"]) / "documents").iterdir())
    document.write_bytes(b"tampered after verification")
    with pytest.raises(BackupError):
        asyncio.run(service.verify(created["id"]))

    row = service.list_backups()[0]
    assert row["verified"] is False
    assert row["problem"] in ("backup_tampered", "backup_corrupt", "backup_incomplete")
    assert service.state()["busy"] == ""


def test_a_larger_history_round_trips(tmp_path, template_database):
    """A reasonable volume (hundreds of messages, several documents) must always survive."""
    root = make_root(tmp_path, template_database, with_data=False)
    with connect(root / "data" / "alex.db") as db:
        db.execute(
            "INSERT INTO users (id,email,password_hash,created_at) VALUES ('bulk','bulk@example.com','hash','2026-09-21')"
        )
        db.execute(
            "INSERT INTO chats (id,user_id,title,created_at,updated_at) VALUES ('bulk-chat','bulk','Большая история','2026-09-21','2026-09-21')"
        )
        for index in range(600):
            db.execute(
                "INSERT INTO messages (id,chat_id,role,content,created_at) VALUES (?,?,?,?,?)",
                (
                    f"bulk-{index}",
                    "bulk-chat",
                    "user" if index % 2 == 0 else "assistant",
                    f"Сообщение {index} 🚀",
                    "2026-09-21",
                ),
            )
    for index in range(8):
        (root / "documents" / f"{index:032x}").write_bytes(f"документ {index}".encode())
    before = counts(root)

    backup = BackupService(root).create_now(kind="manual")
    with connect(root / "data" / "alex.db") as db:
        db.execute("DELETE FROM messages")
    report = restore_backup(Path(backup["path"]), root)

    assert report["restored"] is True
    after = counts(root)
    assert after == before
    with connect(root / "data" / "alex.db") as db:
        assert db.execute("SELECT content FROM messages WHERE id='bulk-599'").fetchone() == (
            "Сообщение 599 🚀",
        )


def test_a_backup_from_another_pc_restores_data_but_not_identity(tmp_path, template_database):
    """Import semantics: user data yes, machine identity and enrollment no."""
    source = make_root(tmp_path / "source", template_database)
    backup = BackupService(source).create_now(kind="manual")

    # The other PC: its own identity files and its own (different) document set.
    target = make_root(tmp_path / "target", template_database, with_data=False)
    with connect(target / "data" / "alex.db") as db:
        db.execute(
            "INSERT INTO users (id,email,password_hash,created_at) VALUES ('other','other@example.com','hash','2026-09-21')"
        )
    for path in (target / "documents").iterdir():
        path.unlink()
    (target / "documents" / ("e" * 32)).write_bytes(b"other pc document")
    (target / "runtime" / "jwt.secret").write_text("z" * 64, encoding="utf-8")
    (target / "runtime" / "install.id").write_text("other-machine-identity", encoding="utf-8")
    (target / "runtime" / "session.id").write_text("other-session-pointer", encoding="utf-8")
    (target / "device.json").write_text('{"device_id":"other"}', encoding="utf-8")

    report = restore_backup(Path(backup["path"]), target)

    assert report["restored"] is True
    assert counts(target) == counts(source), "user data follows the backup"
    assert (target / "runtime" / "jwt.secret").read_text(encoding="utf-8") == "z" * 64
    assert (target / "runtime" / "install.id").read_text(encoding="utf-8") == "other-machine-identity"
    assert (target / "runtime" / "session.id").read_text(encoding="utf-8") == "other-session-pointer"
    assert (target / "device.json").read_text(encoding="utf-8") == '{"device_id":"other"}'
    # The source machine's identity never appears anywhere in the target data root.
    blob = b""
    for path in sorted(target.rglob("*")):
        if path.is_file():
            blob += path.read_bytes()
    for marker in (b"machine-install-id-1234", b"session-pointer-5678", b"j" * 64):
        assert marker not in blob


def test_restore_result_file_is_written_and_readable(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    backup = BackupService(root).create_now(kind="manual")
    report = restore_backup(Path(backup["path"]), root)
    write_result(root, {**report, "restored": True})
    payload = json.loads((root / "runtime" / "backup-restore.json").read_text(encoding="utf-8"))
    assert payload["restored"] is True
    assert payload["backup_id"] == backup["id"]
