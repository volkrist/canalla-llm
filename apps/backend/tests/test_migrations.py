import os
import sqlite3
import subprocess
import sys


def test_upgrade_preserves_existing_history(tmp_path):
    path = tmp_path / "migration.db"
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + path.as_posix()}

    def migrate(revision):
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision], env=env, check=True, capture_output=True
        )

    migrate("0001")
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO users (id,email,password_hash,created_at) VALUES ('u','keep@example.com','hash','2026-09-14')"
        )
        db.execute(
            "INSERT INTO chats (id,user_id,title,created_at,updated_at) VALUES ('c','u','Keep me','2026-09-14','2026-09-14')"
        )
        db.execute(
            "INSERT INTO messages (id,chat_id,role,content,created_at) VALUES ('m','c','user','Existing text','2026-09-14')"
        )
    migrate("head")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT content,status,edited_at FROM messages").fetchone() == (
            "Existing text",
            "complete",
            None,
        )
        assert db.execute("SELECT title,pinned FROM chats").fetchone() == ("Keep me", 0)
        assert db.execute("SELECT role FROM users").fetchone() == ("user",)
        assert db.execute("SELECT is_owner FROM users").fetchone() == (1,)
        assert db.execute("SELECT search_state FROM compute_control WHERE id=1").fetchone() == ("offline",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0015",)
        for table in (
            "tool_runs",
            "web_source_snapshots",
            "tool_preferences",
            "paired_devices",
            "local_tasks",
            "task_steps",
            "task_events",
            "task_checkpoints",
            "workspace_locks",
            "workspace_waiters",
            "auth_sessions",
            "bootstrap_claim",
        ):
            assert db.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    subprocess.run([sys.executable, "-m", "alembic", "check"], env=env, check=True, capture_output=True)


def test_clean_install_tools_schema(tmp_path):
    path = tmp_path / "clean.db"
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + path.as_posix()}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True, capture_output=True
    )
    subprocess.run([sys.executable, "-m", "alembic", "check"], env=env, check=True, capture_output=True)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT count(*) FROM tool_runs").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM backup_events").fetchone() == (0,)


def test_upgrade_from_0014_preserves_representative_data(tmp_path):
    """The backup slice adds one table (0015) and must not disturb anything a user owns."""
    path = tmp_path / "upgrade.db"
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + path.as_posix()}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0014"], env=env, check=True, capture_output=True
    )
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO users (id,email,password_hash,created_at,display_name,role,is_owner)"
            " VALUES ('u1','owner@example.com','hash','2026-09-21','Владелец','user',1)"
        )
        db.execute(
            "INSERT INTO users (id,email,password_hash,created_at,display_name,role,is_owner)"
            " VALUES ('u2','second@example.com','hash','2026-09-21','Второй','user',0)"
        )
        db.execute(
            "INSERT INTO chats (id,user_id,title,created_at,updated_at) VALUES ('c1','u1','Чат','2026-09-21','2026-09-21')"
        )
        db.execute(
            "INSERT INTO messages (id,chat_id,role,content,created_at)"
            " VALUES ('m1','c1','user','Сообщение — юникод 🚀','2026-09-21')"
        )
        db.execute(
            "INSERT INTO projects (id,user_id,name,description,status,created_at,updated_at)"
            " VALUES ('p1','u1','Проект','','active','2026-09-21','2026-09-21')"
        )
        db.execute(
            "INSERT INTO memories (id,user_id,category,content,importance,is_pinned,is_active,use_count,"
            "created_at,updated_at) VALUES ('mem1','u1','fact','Память',3,0,1,0,'2026-09-21','2026-09-21')"
        )

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True, capture_output=True
    )
    subprocess.run([sys.executable, "-m", "alembic", "check"], env=env, check=True, capture_output=True)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0015",)
        assert db.execute("SELECT count(*) FROM users").fetchone() == (2,)
        assert db.execute("SELECT count(*) FROM chats").fetchone() == (1,)
        assert db.execute("SELECT content FROM messages").fetchone() == ("Сообщение — юникод 🚀",)
        assert db.execute("SELECT name FROM projects").fetchone() == ("Проект",)
        assert db.execute("SELECT content FROM memories").fetchone() == ("Память",)
        assert db.execute("SELECT is_owner FROM users WHERE id='u1'").fetchone() == (1,)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT count(*) FROM backup_events").fetchone() == (0,)
