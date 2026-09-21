"""The Gateway has its own Alembic history, and that history matches the models.

Runs in a subprocess, exactly like an operator would run it:

    alembic -c apps/gateway/alembic.ini upgrade head
    alembic -c apps/gateway/alembic.ini check
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
INI = GATEWAY_ROOT / "alembic.ini"


def alembic(database_url: str, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "JWT_SECRET": "migration-test-secret-" + "m" * 40,
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(INI), *args],
        cwd=str(GATEWAY_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_upgrade_creates_the_gateway_schema(tmp_path):
    url = "sqlite:///" + (tmp_path / "gateway.db").as_posix()
    result = alembic(url, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    path = tmp_path / "gateway.db"
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        revision = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert tables == {
        "alembic_version",
        "installations",
        "enrollment_codes",
        "gateway_compute",
        "gateway_sessions",
        "gateway_operations",
        "audit_events",
    }
    assert revision == "0001_gateway_core"
    # The Gateway owns its own history: the local backend's revisions are absent.
    assert revision not in {f"00{index:02d}" for index in range(1, 15)}


def test_migration_matches_the_models(tmp_path):
    """``alembic check`` fails if the models drift from the migration."""
    url = "sqlite:///" + (tmp_path / "check.db").as_posix()
    assert alembic(url, "upgrade", "head").returncode == 0
    result = alembic(url, "check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_downgrade_removes_every_gateway_table(tmp_path):
    url = "sqlite:///" + (tmp_path / "down.db").as_posix()
    assert alembic(url, "upgrade", "head").returncode == 0
    assert alembic(url, "downgrade", "base").returncode == 0
    with sqlite3.connect(tmp_path / "down.db") as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables <= {"alembic_version"}


def test_enrollments_and_codes_survive_a_later_upgrade(tmp_path):
    url = "sqlite:///" + (tmp_path / "keep.db").as_posix()
    assert alembic(url, "upgrade", "head").returncode == 0
    with sqlite3.connect(tmp_path / "keep.db") as db:
        db.execute(
            "INSERT INTO installations (id,name,platform,client_version,secret_hash,created_at,"
            "last_seen_at,meta) VALUES ('i1','PC A','windows','0.9.3','digest',"
            "'2026-09-21 00:00:00','2026-09-21 00:00:00','{}')"
        )
        db.execute(
            "INSERT INTO enrollment_codes (id,code_hash,label,created_at,expires_at) VALUES "
            "('c1','digest','PC B','2026-09-21 00:00:00','2026-09-21 01:00:00')"
        )
    assert alembic(url, "upgrade", "head").returncode == 0
    with sqlite3.connect(tmp_path / "keep.db") as db:
        assert db.execute("SELECT name FROM installations").fetchone() == ("PC A",)
        assert db.execute("SELECT label FROM enrollment_codes").fetchone() == ("PC B",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
