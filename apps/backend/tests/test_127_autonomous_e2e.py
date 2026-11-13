"""Level-3 acceptance: one user goal, the real executor, real files, real HTTP, real browser.

Everything is real except the model, which is scripted: the backend, the SQLite task row, the host
tools, pytest, uvicorn, Playwright and the 1.2.7 verifier. The scripted planner only *decides*;
the product does the work. A live Qwen run is a separate, paid acceptance and is never implied by
this test.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Message
from app.tools.executor import ExecutionContext, ToolExecutor
from app.tools.local import machine
from app.tools.models import LocalTask
from app.tools.orchestrator import ToolOrchestrator
from app.tools.policy import ToolLimits, WebSettings
from app.tools.registry import make_registry

PROMPT = (
    "Создай на моём компьютере приложение TaskDesk для управления задачами: локальная база, "
    "создание, поиск, статусы, простой веб-интерфейс и тесты. Запусти его, проверь интерфейс, "
    "найди дефект поиска, исправь и подтверди результат."
)

BUGGY = '''
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
    return query in title


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
'''

FIXED = BUGGY.replace(
    "def matches(title: str, query: str) -> bool:\n    return query in title",
    "def matches(title: str, query: str) -> bool:\n    return query.casefold() in title.casefold()",
)

TESTS = """
from fastapi.testclient import TestClient

import main


def test_search_is_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "tasks.db"))
    main.init_db()
    client = TestClient(main.app)
    created = client.post("/tasks", json={"title": "Alpha task"})
    assert created.status_code == 201
    assert client.get("/tasks", params={"q": "alpha"}).json()[0]["title"] == "Alpha task"


def test_create_and_status(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "tasks.db"))
    main.init_db()
    client = TestClient(main.app)
    task_id = client.post("/tasks", json={"title": "Beta"}).json()["id"]
    assert client.patch(f"/tasks/{task_id}", json={"title": "Beta", "status": "done"}).json()["status"] == "done"
    assert client.get("/tasks", params={"status": "done"}).json()[0]["status"] == "done"
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _call(name, arguments, call_id):
    return {
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            }
        ]
    }


class Planner:
    """The decisions a model would make; every effect is produced by the real tools."""

    supports_tools = True

    def __init__(self, root, port):
        self.root = root
        self.port = port
        self.step = 0
        self.verified: dict = {}
        self.gui: dict = {}

    async def plan_tools(self, messages, tools, usage):
        self.step += 1
        root = self.root
        sequence = {
            1: (
                "write_file",
                {"path": str(root / "main.py"), "content": BUGGY, "purpose": "TaskDesk application"},
            ),
            2: (
                "write_file",
                {"path": str(root / "test_main.py"), "content": TESTS, "purpose": "acceptance tests"},
            ),
            3: (
                "run_python",
                {"cwd": str(root), "argv": ["-m", "pytest", "-q"], "purpose": "baseline tests"},
            ),
            4: ("read_file", {"path": str(root / "main.py"), "purpose": "diagnose the failing search"}),
            5: (
                "write_file",
                {"path": str(root / "main.py"), "content": FIXED, "purpose": "fix case-insensitive search"},
            ),
            6: (
                "run_python",
                {"cwd": str(root), "argv": ["-m", "pytest", "-q"], "purpose": "verify the fix"},
            ),
            7: (
                "run_python",
                {
                    "cwd": str(root),
                    "argv": [
                        "-m",
                        "uvicorn",
                        "main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(self.port),
                        "--log-level",
                        "warning",
                    ],
                    "wait": False,
                    "purpose": "run TaskDesk",
                },
            ),
            8: (
                "verify_result",
                {
                    "checks": [
                        {"kind": "port_open", "target": f"127.0.0.1:{self.port}"},
                        {
                            "kind": "http_ok",
                            "target": f"http://127.0.0.1:{self.port}/tasks",
                            "contains": "[]",
                        },
                        {
                            "kind": "command",
                            "target": "pytest -q",
                            "argv": [sys.executable, "-m", "pytest", "-q"],
                            "cwd": str(root),
                        },
                    ],
                    "purpose": "readiness",
                },
            ),
            9: (
                "browser_gui",
                {
                    "url": f"http://127.0.0.1:{self.port}/",
                    "steps": [
                        {"action": "fill", "selector": "testid=title", "value": "Alpha task"},
                        {"action": "click", "selector": "testid=add", "expect": "Alpha task"},
                        {"action": "fill", "selector": "testid=search", "value": "alpha"},
                        {"action": "click", "selector": "testid=find", "expect": "Alpha task"},
                    ],
                    "screenshot_dir": str(root / "gui"),
                },
            ),
            10: (
                "verify_result",
                {
                    "checks": [
                        {
                            "kind": "db_row",
                            "target": str(root / "tasks.db"),
                            "query": "select count(*) from tasks",
                            "at_least": 1,
                        },
                        {
                            "kind": "http_ok",
                            "target": f"http://127.0.0.1:{self.port}/tasks?q=alpha",
                            "contains": "Alpha task",
                        },
                        {"kind": "file_contains", "target": str(root / "main.py"), "expected": "casefold()"},
                    ],
                    "purpose": "persisted data and fixed code",
                },
            ),
        }
        if self.step in sequence:
            name, arguments = sequence[self.step]
            if self.step == 8:
                # uvicorn is started with wait=false; give it a bounded moment to bind before the
                # verifier looks, so the check measures readiness rather than the race.
                for _ in range(60):
                    with socket.socket() as probe:
                        probe.settimeout(0.2)
                        if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                            break
                    await asyncio.sleep(0.2)
            return _call(name, arguments, f"step-{self.step}")
        if self.step == 11:
            # Read the process handle from the transcript so the stop is exact, not by name.
            return _call(
                "process_status", {"tool_run_id": self._run_id(messages), "purpose": "running app"}, "step-11"
            )
        if self.step == 12:
            return _call(
                "stop_process", {"tool_run_id": self._run_id(messages), "purpose": "stop TaskDesk"}, "step-12"
            )
        return {"tool_calls": []}

    @staticmethod
    def _run_id(messages):
        for message in reversed(messages or []):
            text = json.dumps(message) if not isinstance(message, str) else message
            marker = '"tool_run_id": "'
            if marker in text:
                start = text.rindex(marker) + len(marker)
                return text[start : start + 36]
        raise AssertionError("no started process in the transcript")


def _browser_available() -> bool:
    """Is a real Chromium launchable? A missing browser is a BLOCKED GUI proof, never a PASS."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:  # noqa: BLE001 - missing binary, missing deps, sandbox refusal
        return False


def _read(path, attempts=25):
    last = None
    for _ in range(attempts):
        try:
            return path.read_text(encoding="utf-8")
        except (PermissionError, FileNotFoundError) as error:
            last = error
            time.sleep(0.1)
    raise last


def test_one_goal_research_free_build_run_gui_fix_and_verify(client, auth, tmp_path):
    from tests.fake_host import FakeHost

    if not _browser_available():
        import pytest

        pytest.skip(
            "Playwright Chromium is not installed here; the GUI part of this acceptance is "
            "BLOCKED, not passed. Run `python -m playwright install chromium`."
        )

    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    root = tmp_path / "taskdesk"
    root.mkdir()
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "trusted", "workspace_roots": [str(root)]},
    )
    paired = client.post("/tools/devices/pair", headers=headers, json={"platform": "windows"}).json()
    device_headers = {
        **headers,
        "X-Alex-Device-Id": paired["device_id"],
        "X-Alex-Device-Credential": paired["credential"],
    }
    client.post("/tools/devices/heartbeat", headers=device_headers)
    with SessionLocal() as db:
        message = Message(chat_id=chat_id, role="assistant", content="")
        db.add(message)
        db.commit()
        generation_id = message.id

    port = _free_port()

    async def emit(event, value):
        return None

    context = ExecutionContext(
        user_id,
        chat_id,
        generation_id,
        ToolLimits(max_calls=40, hard_max_calls=60),
        emit,
        mode="off",
        computer_mode="trusted",
        settings=WebSettings(computer_mode="trusted", workspace_roots=[str(root)], default_mode="off"),
        user_prompt=PROMPT,
        host_online=True,
    )
    host = FakeHost(client, device_headers, root).start()
    planner = Planner(root, port)
    try:
        orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
        asyncio.run(orchestrator.prepare(planner, [{"role": "user", "content": PROMPT}], 0, context, {}))
    finally:
        host.close()

    # -- the user's artifact exists and is the fixed version
    assert "casefold()" in _read(root / "main.py")
    assert (root / "test_main.py").is_file()

    # -- the independent verifier confirmed the running app, the tests and the data
    with SessionLocal() as db:
        row = db.scalar(select(LocalTask).where(LocalTask.id == context.task_id))
        assert row is not None
        verification = row.verification or {}
        evidence = verification.get("evidence") or []
        kinds = {item["kind"] for item in evidence}
        assert {"port_open", "http_ok", "command", "db_row", "file_contains"} <= kinds, kinds
        assert verification.get("evidence_ok") is True, evidence
        assert row.status == machine.COMPLETED, (row.status, row.last_error, row.completion_summary)
        commands = (row.checkpoint or {}).get("commands") or []
        tools = [item.get("tool") for item in commands]
        assert tools.count("run_python") >= 3  # two pytest runs and the server
        assert "verify_result" in tools and "browser_gui" in tools

    # -- every model turn and every executed tool is linked in the provenance journal, and a
    # scripted planner is never classified as a live model.
    from app.agent import provenance as provenance_module
    from app.config import get_settings

    with SessionLocal() as db:
        events, runs = provenance_module.from_task(db, context.task_id)
    verdict = provenance_module.classify(events, secret=get_settings().jwt_secret, tool_runs=runs)
    assert verdict.model_responses >= 1, "no MODEL_RESPONSE was recorded for the planner turns"
    assert verdict.tool_calls >= 1, verdict.to_dict()
    assert any(item["kind"] == "TOOL_RESULT" and item.get("tool_run_id") for item in verdict.chain)
    assert verdict.status == provenance_module.MOCK_MODEL, verdict.status
    assert verdict.ok is False

    # -- the browser really drove the GUI: screenshots exist and data persisted in SQLite
    screenshots = sorted((root / "gui").glob("*.png"))
    assert len(screenshots) >= 4, screenshots
    import sqlite3

    connection = sqlite3.connect(root / "tasks.db")
    try:
        titles = [row[0] for row in connection.execute("select title from tasks")]
    finally:
        connection.close()
    assert "Alpha task" in titles

    # -- and the app is no longer running (a server needs a moment to release the port)
    deadline = time.monotonic() + 15
    closed = False
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                closed = True
                break
        time.sleep(0.25)
    assert closed, f"the app still answers on 127.0.0.1:{port} after stop_process"


def test_deep_research_tool_reads_real_pages_and_requeries(client, auth, tmp_path):
    """The tool, the engine and real HTTP: a local server is the source; gaps drive the requery."""
    import http.server

    pages = {
        "/docs": (
            "<html><body><h1>Framework</h1><p>Version 3.2 is the latest stable release and "
            "supports Windows platforms.</p></body></html>"
        ),
        "/license": "<html><body><p>This project is distributed under the MIT license.</p></body></html>",
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib signature
            body = pages.get(self.path, "<html><body>not found</body></html>").encode()
            self.send_response(200 if self.path in pages else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        headers = auth()
        user_id = client.get("/auth/me", headers=headers).json()["id"]
        chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
        with SessionLocal() as db:
            message = Message(chat_id=chat_id, role="assistant", content="")
            db.add(message)
            db.commit()
            generation_id = message.id

        async def emit(event, value):
            return None

        context = ExecutionContext(
            user_id,
            chat_id,
            generation_id,
            ToolLimits(max_calls=10, hard_max_calls=12),
            emit,
            mode="off",
            computer_mode="trusted",
            settings=WebSettings(
                computer_mode="trusted", workspace_roots=[str(tmp_path)], default_mode="off"
            ),
            user_prompt="research the framework version and license",
            host_online=True,
        )
        registry = make_registry()
        executor = ToolExecutor(registry)
        result = asyncio.run(
            executor.execute(
                "deep_research",
                {
                    "question": "framework latest version and license",
                    "required_facts": ["version", "license"],
                    "urls": [f"{base}/docs"],
                    "allow_local": True,
                    "max_rounds": 2,
                    "max_pages": 4,
                    "max_seconds": 30,
                },
                context,
            )
        )
        assert result.metadata["research"]["facts"]["version"], result.text
        # The seed URL answers `version`; `license` remains a gap and is reported as such.
        assert "license" in result.metadata["research"]["gaps"]
        assert "MISSING" in result.text
    finally:
        server.shutdown()
