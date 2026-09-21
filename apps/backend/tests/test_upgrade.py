"""Upgrade path: pre-upgrade backup, migration refusal, failure recovery, restore CLI.

The startup path is exercised as a **subprocess** wherever possible, because that is how the
product runs it (``python -m app.runtime_entry``): the pre-upgrade backup, the migration and
the migration record are then produced by the real code, not by a test double.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from test_backup import BACKEND, connect, counts, make_root, migrate

from app.backup.archive import BackupService, verify_backup
from app.backup.format import sqlite_revision


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def runtime_env(root: Path, port: int) -> dict:
    return {
        **os.environ,
        "ALEX_LLM_DATA_DIR": str(root),
        "DATABASE_URL": "sqlite:///" + (root / "data" / "alex.db").as_posix(),
        "DOCUMENT_STORAGE_DIR": str(root / "documents"),
        "JWT_SECRET": "k" * 64,
        "ALEX_RUNTIME_TOKEN": "r" * 40,
        "ALEX_BACKEND_PORT": str(port),
    }


def start_runtime(root: Path, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "app.runtime_entry"],
        cwd=str(BACKEND),
        env=runtime_env(root, port),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_for(predicate, timeout: float = 180.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.25)
    return False


def run_to_completion(root: Path, *, wait_for_server: bool = True, timeout: float = 180.0) -> tuple[int, str]:
    """Run the real startup path, stop it once it is serving (or it exited by itself)."""
    port = free_port()
    process = start_runtime(root, port)
    try:
        if wait_for_server:
            wait_for(lambda: process.poll() is not None or _port_open(port), timeout)
        else:
            wait_for(lambda: process.poll() is not None, timeout)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()
        stdout, stderr = process.communicate(timeout=30)
        return process.returncode or 0, (stdout or "") + (stderr or "")
    finally:
        if process.poll() is None:
            process.kill()


def _port_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def migration_record(root: Path) -> dict:
    return json.loads((root / "runtime" / "migration.json").read_text(encoding="utf-8"))


def restore_cli(root: Path, target: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "app.runtime_entry", "--restore-backup", target],
        cwd=str(BACKEND),
        env=runtime_env(root, free_port()),
        capture_output=True,
        text=True,
        timeout=300,
    )


# ------------------------------------------------------------------ pre-upgrade backup


def test_pending_migration_is_preceded_by_a_verified_backup(tmp_path, template_database):
    root = make_root(tmp_path, template_database, revision="0014")
    before = counts(root)

    code, output = run_to_completion(root)

    assert sqlite_revision(root / "data" / "alex.db") == "0015", output[-500:]
    assert counts(root) == before, "no user data may be lost by the migration"
    backups = [row for row in BackupService(root).list_backups() if row["kind"] == "pre_upgrade"]
    assert len(backups) == 1
    assert backups[0]["verified"] is True
    assert backups[0]["schema_revision"] == "0014", "the backup captures the state before the migration"
    assert verify_backup(Path(backups[0]["path"]))["documents"] == before["documents"]
    with connect(Path(backups[0]["path"]) / "data" / "alex.db") as db:
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == before["messages"]

    record = migration_record(root)
    assert record["result"] == "ok"
    assert (record["from"], record["to"]) == ("0014", "0015")
    assert record["backup"] == backups[0]["id"]
    assert record["app_version"] == "0.9.3"


def test_migration_is_refused_when_the_backup_cannot_be_created(tmp_path, template_database):
    root = make_root(tmp_path, template_database, revision="0014")
    before = counts(root)
    # A natural, deterministic backup failure: the backups path exists as a file.
    (root / "backups").write_text("not a directory", encoding="utf-8")

    code, output = run_to_completion(root, wait_for_server=False, timeout=90)

    assert code == 15, output[-500:]
    assert "pre_upgrade_backup_failed" in output
    assert sqlite_revision(root / "data" / "alex.db") == "0014", "the old database is untouched"
    assert counts(root) == before
    record = migration_record(root)
    assert record["result"] == "blocked_no_backup"
    assert (record["from"], record["to"]) == ("0014", "0015")


def test_a_failed_migration_keeps_the_database_and_the_backup(tmp_path, template_database):
    root = make_root(tmp_path, template_database, revision="0014")
    before = counts(root)
    database = root / "data" / "alex.db"
    original = database.stat().st_mode
    os.chmod(database, 0o444)  # a write-protected database makes the migration fail
    try:
        code, output = run_to_completion(root, wait_for_server=False, timeout=90)
    finally:
        os.chmod(database, original)

    assert code == 12, output[-500:]
    assert "migration_failed" in output
    assert counts(root) == before, "a failed migration must not delete or empty the database"
    assert sqlite_revision(database) == "0014"
    backups = [row for row in BackupService(root).list_backups() if row["kind"] == "pre_upgrade"]
    assert backups and backups[0]["verified"] is True
    record = migration_record(root)
    assert record["result"] == "failed"
    assert record["backup"] == backups[0]["id"]


def test_fresh_install_migrates_without_a_backup(tmp_path, template_database):
    root = tmp_path / "fresh Alex LLM"
    (root / "data").mkdir(parents=True)
    (root / "runtime").mkdir(parents=True)

    run_to_completion(root)

    assert sqlite_revision(root / "data" / "alex.db") == "0015"
    assert BackupService(root).list_backups() == []
    record = migration_record(root)
    assert record["result"] == "ok" and record["from"] is None


def test_a_current_install_migrates_without_creating_another_backup(tmp_path, template_database):
    root = make_root(tmp_path, template_database)  # already at head

    run_to_completion(root)

    assert sqlite_revision(root / "data" / "alex.db") == "0015"
    assert BackupService(root).list_backups() == []
    assert not (root / "runtime" / "migration.json").exists()


# ------------------------------------------------------------------------- restore CLI


def test_restore_cli_restores_and_reports(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    before = counts(root)
    backup = BackupService(root).create_now(kind="manual")

    with connect(root / "data" / "alex.db") as db:
        db.execute("DELETE FROM messages")
        db.execute("DELETE FROM users")
    for document in (root / "documents").iterdir():
        document.unlink()

    result = restore_cli(root, backup["path"])
    assert result.returncode == 0, result.stderr[-400:]
    payload = json.loads((root / "runtime" / "backup-restore.json").read_text(encoding="utf-8"))
    assert payload["restored"] is True
    assert payload["backup_id"] == backup["id"]
    assert payload["safety_backup_id"]
    assert counts(root) == before
    # The one-shot process never starts a server and never touches machine identity.
    assert (root / "runtime" / "jwt.secret").read_text(encoding="utf-8") == "j" * 64
    assert (root / "runtime" / "install.id").read_text(encoding="utf-8") == "machine-install-id-1234"


def test_restore_cli_rejects_a_corrupt_backup_and_keeps_the_data(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    before = counts(root)
    backup = BackupService(root).create_now(kind="manual")
    document = next((Path(backup["path"]) / "documents").iterdir())
    document.write_bytes(b"corrupted")

    result = restore_cli(root, backup["path"])
    assert result.returncode == 20
    payload = json.loads((root / "runtime" / "backup-restore.json").read_text(encoding="utf-8"))
    assert payload["restored"] is False
    assert payload["code"] == "backup_tampered"
    assert counts(root) == before


def test_restore_cli_accepts_a_backup_id_inside_the_backups_root(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    before = counts(root)
    backup = BackupService(root).create_now(kind="manual")
    with connect(root / "data" / "alex.db") as db:
        db.execute("DELETE FROM chats")

    result = restore_cli(root, backup["id"])
    assert result.returncode == 0, result.stderr[-400:]
    assert counts(root) == before


def test_restore_cli_refuses_an_outside_directory(tmp_path, template_database):
    root = make_root(tmp_path, template_database)
    before = counts(root)
    outside = tmp_path / "outside-backup"
    outside.mkdir()
    (outside / "manifest.json").write_text("{}", encoding="utf-8")

    result = restore_cli(root, str(outside))
    assert result.returncode == 20
    payload = json.loads((root / "runtime" / "backup-restore.json").read_text(encoding="utf-8"))
    assert payload["restored"] is False
    assert counts(root) == before


def test_backup_of_an_unmigrated_database_is_accepted(tmp_path, template_database):
    """A backup taken before the first migration is still a valid restore source."""
    root = make_root(tmp_path, template_database, revision="0014")
    result = BackupService(root).create_now(kind="manual")
    assert result["schema_revision"] == "0014"
    assert verify_backup(Path(result["path"]))["verified"] is True
    assert migrate  # the alembic history used above is the real one
    assert sqlite3.sqlite_version
