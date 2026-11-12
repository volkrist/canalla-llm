
"""TaskDesk: a small task board with a local SQLite database."""
import os
import sqlite3

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

DB_PATH = os.environ.get("TASKDESK_DB", os.path.join(os.path.dirname(__file__), "tasks.db"))
app = FastAPI(title="TaskDesk")


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open')"
        )


def matches(title: str, query: str) -> bool:
    return query.casefold() in title.casefold()


class TaskIn(BaseModel):
    title: str
    status: str = "open"


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/tasks")
def list_tasks(q: str = "", status: str = ""):
    with connect() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM tasks ORDER BY id")]
    if q:
        rows = [row for row in rows if matches(row["title"], q)]
    if status:
        rows = [row for row in rows if row["status"] == status]
    return rows


@app.post("/tasks", status_code=201)
def create_task(task: TaskIn):
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO tasks (title, status) VALUES (?, ?)", (task.title, task.status)
        )
        return {"id": cursor.lastrowid, "title": task.title, "status": task.status}


@app.patch("/tasks/{task_id}")
def update_task(task_id: int, task: TaskIn):
    with connect() as conn:
        conn.execute("UPDATE tasks SET title=?, status=? WHERE id=?", (task.title, task.status, task_id))
    return {"id": task_id, "title": task.title, "status": task.status}


@app.delete("/tasks/{task_id}")
def delete_task(task_id: int):
    with connect() as conn:
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    return {"deleted": task_id}


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>TaskDesk</title></head>
<body>
<h1>TaskDesk</h1>
<input data-testid="title" aria-label="title" />
<button data-testid="add" onclick="add()">Добавить</button>
<input data-testid="search" aria-label="search" />
<button data-testid="find" onclick="find()">Найти</button>
<ul data-testid="list"></ul>
<script>
async function refresh(q) {
  const url = q ? "/tasks?q=" + encodeURIComponent(q) : "/tasks";
  const rows = await (await fetch(url)).json();
  document.querySelector('[data-testid=list]').innerHTML =
    rows.map(r => '<li data-testid="task">' + r.title + ' [' + r.status + ']</li>').join('');
}
async function add() {
  const input = document.querySelector('[data-testid=title]');
  await fetch("/tasks", {method:"POST", headers:{"Content-Type":"application/json"},
                        body: JSON.stringify({title: input.value})});
  input.value = "";
  refresh("");
}
async function find() {
  const q = document.querySelector('[data-testid=search]').value;
  refresh(q);
}
refresh("");
</script>
</body></html>"""
