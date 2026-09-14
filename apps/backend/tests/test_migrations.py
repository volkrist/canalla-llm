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
        assert db.execute("SELECT search_state FROM compute_control WHERE id=1").fetchone() == ("offline",)
