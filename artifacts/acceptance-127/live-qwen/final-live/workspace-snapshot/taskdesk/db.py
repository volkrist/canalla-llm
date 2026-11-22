"""TaskDesk: SQLite data access layer (stdlib sqlite3)."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "taskdesk.db"


def connect(path=None):
    p = Path(path) if path else DB_PATH
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path=None):
    conn = connect(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'todo',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    conn.close()


VALID_STATUSES = ("todo", "in_progress", "done")


def create_task(title, description="", status="todo", path=None):
    if not title or not str(title).strip():
        raise ValueError("title is required")
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}")
    conn = connect(path)
    cur = conn.execute(
        "INSERT INTO tasks (title, description, status) VALUES (?, ?, ?)",
        (str(title).strip(), description or "", status),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return dict(row)


def list_tasks(search=None, status=None, path=None):
    conn = connect(path)
    sql = "SELECT * FROM tasks WHERE 1=1"
    args = []
    if search:
        sql += " AND (title LIKE ? OR description LIKE ?)"
        like = f"%{search}%"
        args.extend([like, like])
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY id DESC"
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_task(task_id, path=None):
    conn = connect(path)
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_status(task_id, status, path=None):
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}")
    conn = connect(path)
    cur = conn.execute(
        "UPDATE tasks SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (status, task_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    if cur.rowcount == 0 or row is None:
        return None
    return dict(row)


def delete_task(task_id, path=None):
    conn = connect(path)
    cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0
