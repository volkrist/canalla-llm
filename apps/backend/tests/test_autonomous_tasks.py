import asyncio
import hashlib
import json
import subprocess
import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.database import SessionLocal
from app.models import now
from app.tools.contracts import ToolError
from app.tools.executor import ExecutionContext, ToolExecutor, reconcile_tools
from app.tools.local import machine
from app.tools.local.compact import compact_text
from app.tools.local.plan import default_plan, looks_like_autonomous
from app.tools.local.task import LocalTaskController
from app.tools.models import LocalTask, PairedDevice, ToolRun
from app.tools.orchestrator import ToolOrchestrator
from app.tools.policy import ToolLimits, WebSettings
from app.tools.registry import make_registry
from tests.db_helpers import require_row
from tests.fake_host import FakeHost
from tests.fakes_web import FakeWebProvider


class ScriptedPlanner:
    supports_tools = True

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.i = 0

    async def plan_tools(self, messages, tools, usage):
        if self.i >= len(self.scripts):
            return {"tool_calls": []}
        item = self.scripts[self.i]
        self.i += 1
        return {"tool_calls": item() if callable(item) else item}


def _call(name, args, call_id):
    return [
        {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        }
    ]


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _context(user_id, chat_id, generation_id, workspace, prompt, events, **limits):
    async def emit(event, value):
        events.append((event, value))

    settings = WebSettings(computer_mode="trusted", workspace_roots=[workspace], default_mode="auto")
    extra = {
        key: value for key, value in limits.items() if key not in {"max_calls", "hard_max_calls", "mode"}
    }
    context = ExecutionContext(
        user_id,
        chat_id,
        generation_id,
        ToolLimits(
            max_calls=limits.get("max_calls", 24), hard_max_calls=limits.get("hard_max_calls", 40), **extra
        ),
        emit,
        mode=limits.get("mode", "off"),
        computer_mode="trusted",
        settings=settings,
        user_prompt=prompt,
        host_online=True,
    )
    return context


def _project(tmp_path, *, second_bug=False):
    root = tmp_path / "demo"
    root.mkdir()
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "task@example.com"], cwd=root, check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "Task"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=root, check=True, capture_output=True)
    (root / "pyproject.toml").write_text(
        "[project]\nname='demo'\n[tool.pytest.ini_options]\n", encoding="utf-8", newline="\n"
    )
    source = "def add(a, b):\n    return a - b\n"
    if second_bug:
        source += "\nTIMEOUT = 1\n"
    (root / "app.py").write_text(source, encoding="utf-8", newline="\n")
    tests = "from app import add\n\ndef test_add():\n    assert add(2, 2) == 4\n"
    if second_bug:
        tests += "\nfrom app import TIMEOUT\n\ndef test_timeout():\n    assert TIMEOUT == 30\n"
    (root / "test_app.py").write_text(tests, encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    return root


@pytest.fixture
def setup(client, auth, tmp_path):
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    root = _project(tmp_path)
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
    from app.models import Message

    with SessionLocal() as db:
        message = Message(chat_id=chat_id, role="assistant", content="")
        db.add(message)
        db.commit()
        generation_id = message.id
    events = []
    prompt = "Полностью проверь этот проект. Найди причины падающих тестов, исправь их и не завершай, пока тесты не проходят."
    context = _context(user_id, chat_id, generation_id, str(root), prompt, events)
    host = FakeHost(client, device_headers, root).start()
    yield headers, context, events, root, host, device_headers
    host.close()


def test_state_machine_rejects_completed_to_executing():
    row = SimpleNamespace(status=machine.COMPLETED, current_phase=machine.COMPLETED, finished_at=None)
    with pytest.raises(ToolError, match="invalid_task_transition"):
        machine.transition(row, machine.EXECUTING)


def test_plan_is_actionable_and_does_not_invent_cause():
    plan = default_plan("Исправь failing tests", coding=True, research=False, tor=False)
    titles = " ".join(item["title"] for item in plan)
    assert "baseline" in " ".join(item["key"] for item in plan)
    assert "причину бага" not in titles.lower()
    assert looks_like_autonomous("Полностью разберись с этим проектом и исправь ошибки")


def test_compact_keeps_failure_line():
    text = "ok\n" * 4000 + "FAILED test_app.py::test_add - assert 0 == 4\n"
    packed = compact_text(text, 400)
    assert packed["truncated"] is True
    assert any("FAILED" in item for item in packed["failures"])


def test_open_queues_second_writer(setup):
    _headers, context, _events, _root, _host, _device = setup
    with SessionLocal() as db:
        first = LocalTaskController()
        first.attach(context)
        row = first.open(db, context)
        assert row.status == machine.READY
        assert LocalTaskController().steps(db, row.id)
        other = ExecutionContext(
            context.user_id,
            context.chat_id,
            context.generation_id,
            ToolLimits(max_calls=8),
            context.emit,
            computer_mode="trusted",
            settings=context.settings,
            user_prompt=context.user_prompt,
        )
        LocalTaskController().attach(other)
        waiting = LocalTaskController().open(db, other)
        assert waiting.status == machine.WAITING_WORKSPACE
        assert waiting.id != row.id
        LocalTaskController().finish(db, context, "COMPLETED")
        db.expire_all()
        waiting = require_row(db, LocalTask, waiting.id)
        assert waiting.status == machine.READY
        assert (waiting.facts or {}).get("promoted_from_queue") is True


def test_pause_blocks_new_tools_then_resume(setup):
    _headers, context, _events, root, host, _device = setup
    scripts = [
        _call("list_directory", {"path": str(root), "purpose": "Inspect"}, "c1"),
        _call("git_status", {"cwd": str(root), "purpose": "git"}, "c2"),
    ]
    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    with SessionLocal() as db:
        LocalTaskController().attach(context)
        row = LocalTaskController().open(db, context)
        LocalTaskController().pause(db, context)
        context.resume_task_id = row.id
        context.task_id = row.id
    asyncio.run(
        orchestrator.prepare(
            ScriptedPlanner(scripts), [{"role": "user", "content": context.user_prompt}], 0, context, {}
        )
    )
    with SessionLocal() as db:
        row = require_row(db, LocalTask, context.task_id)
        assert row.status == machine.PAUSED
        assert row.tool_calls_used == 0
    assert getattr(context, "skip_final_stream", False) is True
    assert context.task_halt == "task_paused"
    with SessionLocal() as db:
        row = require_row(db, LocalTask, context.task_id)
        LocalTaskController().resume(db, row, context.user_id)
        context.resume_task_id = row.id
        context.resuming = True
    host.handled.clear()
    asyncio.run(
        orchestrator.prepare(
            ScriptedPlanner(scripts), [{"role": "user", "content": context.user_prompt}], 0, context, {}
        )
    )
    assert "list_directory" in host.handled
    with SessionLocal() as db:
        row = require_row(db, LocalTask, context.task_id)
        assert row.status != machine.PAUSED
        assert row.tool_calls_used >= 1


def test_stop_halts_further_tools(setup):
    _headers, context, _events, root, host, _device = setup
    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))

    class SlowPlanner:
        supports_tools = True
        i = 0

        async def plan_tools(self, messages, tools, usage):
            if self.i == 0:
                self.i += 1
                return {
                    "tool_calls": _call("list_directory", {"path": str(root), "purpose": "Inspect"}, "c1")
                }
            await asyncio.sleep(6)
            return {"tool_calls": _call("read_file", {"path": str(root / "app.py"), "purpose": "read"}, "c2")}

    def worker():
        asyncio.run(
            orchestrator.prepare(
                SlowPlanner(), [{"role": "user", "content": context.user_prompt}], 0, context, {}
            )
        )

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    for _ in range(80):
        if "list_directory" in host.handled and getattr(context, "task_id", None):
            break
        time.sleep(0.05)
    with SessionLocal() as db:
        LocalTaskController().stop(db, context)
    thread.join(timeout=12)
    with SessionLocal() as db:
        row = require_row(db, LocalTask, context.task_id)
        assert row.status == machine.STOPPED
    assert "read_file" not in host.handled


def test_budget_exhausted_blocks_fourth_tool(setup):
    _headers, context, _events, root, host, _device = setup
    context.limits = ToolLimits(max_calls=3, hard_max_calls=3, max_local_calls=3)
    scripts = [
        _call("list_directory", {"path": str(root), "purpose": "Inspect"}, "c1"),
        _call("git_status", {"cwd": str(root), "purpose": "git"}, "c2"),
        _call("read_file", {"path": str(root / "app.py"), "purpose": "read"}, "c3"),
        _call("read_file", {"path": str(root / "test_app.py"), "purpose": "read2"}, "c4"),
    ]
    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    asyncio.run(
        orchestrator.prepare(
            ScriptedPlanner(scripts), [{"role": "user", "content": context.user_prompt}], 0, context, {}
        )
    )
    with SessionLocal() as db:
        row = require_row(db, LocalTask, context.task_id)
        assert row.last_error == "task_budget"
        assert row.status == machine.FAILED
        assert row.tool_calls_used == 3
    assert host.handled.count("read_file") == 1


def test_restart_does_not_rerun_completed_tools(setup, client):
    headers, context, events, root, host, device_headers = setup
    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    scripts = [_call("list_directory", {"path": str(root), "purpose": "Inspect"}, "c1")]
    asyncio.run(
        orchestrator.prepare(
            ScriptedPlanner(scripts), [{"role": "user", "content": context.user_prompt}], 0, context, {}
        )
    )
    with SessionLocal() as db:
        row = require_row(db, LocalTask, context.task_id)
        completed = db.scalars(select_completed(row.id)).all()
        assert completed
        row.status = machine.EXECUTING
        row.finished_at = None
        db.commit()
        task_id = row.id
    reconcile_tools()
    with SessionLocal() as db:
        row = require_row(db, LocalTask, task_id)
        assert row.status == machine.INTERRUPTED
        assert row.finished_at is None
        context.resume_task_id = row.id
    host.handled.clear()
    context.resuming = True
    asyncio.run(
        orchestrator.prepare(
            ScriptedPlanner(scripts), [{"role": "user", "content": context.user_prompt}], 0, context, {}
        )
    )
    assert "list_directory" not in host.handled
    with SessionLocal() as db:
        runs = db.scalars(select_completed(context.task_id)).all()
        assert sum(1 for item in runs if item.tool_name == "list_directory") == 1


def select_completed(task_id):
    from sqlalchemy import select

    return select(ToolRun).where(ToolRun.task_id == task_id, ToolRun.status == "completed")


def test_cross_user_task_hidden(setup, client, auth):
    _headers, context, _events, _root, _host, _device = setup
    with SessionLocal() as db:
        LocalTaskController().attach(context)
        row = LocalTaskController().open(db, context)
        task_id = row.id
    other = auth("bob@example.com")
    assert client.get(f"/tasks/{task_id}", headers=other).status_code == 404


def test_sensitive_still_requires_confirmation(setup):
    _headers, context, _events, root, _host, _device = setup
    registry = make_registry()
    from app.tools.local.coding import GitPushArgs
    from app.tools.policy import ToolPolicy

    push = registry.get("git_push")[0]
    decision = ToolPolicy().validate(
        push,
        context.settings,
        mode="off",
        computer_mode="trusted",
        args=GitPushArgs(cwd=str(root), remote="origin"),
    )
    assert decision == "confirmation_required"


class FixPlanner:
    supports_tools = True

    def __init__(self, app_file, root, *, web=False):
        self.app_file = app_file
        self.root = root
        self.searched = not web

    async def plan_tools(self, messages, tools, usage):
        names = {item.get("function", {}).get("name") for item in tools if isinstance(item, dict)}
        if "web_search" in names and not self.searched:
            self.searched = True
            return {"tool_calls": _call("web_search", {"query": "FastAPI official timeout docs"}, "w1")}
        text = self.app_file.read_text(encoding="utf-8")
        digest = _sha(self.app_file)
        if "return a - b" in text:
            return {
                "tool_calls": _call(
                    "patch_file",
                    {
                        "path": str(self.app_file),
                        "old_text": "return a - b",
                        "new_text": "return a + b",
                        "expected_before_sha256": digest,
                        "purpose": "fix add",
                    },
                    "fix-add",
                )
            }
        previous = [m for m in messages if m["role"] == "tool"]
        tested = any('"tool": "run_python"' in str(m.get("content")) for m in previous)
        if "TIMEOUT = 1" in text and tested:
            return {
                "tool_calls": _call(
                    "patch_file",
                    {
                        "path": str(self.app_file),
                        "old_text": "TIMEOUT = 1",
                        "new_text": "TIMEOUT = 30",
                        "expected_before_sha256": digest,
                        "purpose": "fix timeout from docs",
                    },
                    "fix-timeout",
                )
            }
        if "TIMEOUT = 1" in text and not tested:
            return {
                "tool_calls": _call(
                    "run_python",
                    {"argv": ["-m", "pytest", "-q"], "cwd": str(self.root), "purpose": "baseline"},
                    "baseline",
                )
            }
        passed = any(
            '"exit_code": 0' in str(m.get("content")) and '"tool": "run_python"' in str(m.get("content"))
            for m in previous
        )
        if not passed:
            return {
                "tool_calls": _call(
                    "run_python",
                    {"argv": ["-m", "pytest", "-q"], "cwd": str(self.root), "purpose": "verify"},
                    "verify",
                )
            }
        if not any(
            "git_status" in str(m.get("content")) or '"tool": "git_status"' in str(m.get("content"))
            for m in previous
        ):
            return {"tool_calls": _call("git_status", {"cwd": str(self.root), "purpose": "review"}, "git")}
        return {"tool_calls": []}


def test_mock_e2e_fix_and_verify(setup):
    _headers, context, events, root, host, _device = setup
    context.mode = "off"
    app_file = root / "app.py"
    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    asyncio.run(
        orchestrator.prepare(
            FixPlanner(app_file, root), [{"role": "user", "content": context.user_prompt}], 0, context, {}
        )
    )
    assert "return a + b" in app_file.read_text(encoding="utf-8")
    with SessionLocal() as db:
        row = require_row(db, LocalTask, context.task_id)
        assert row.status == machine.COMPLETED
        assert (row.verification or {}).get("tests") == "passed"
        steps = LocalTaskController().steps(db, row.id)
        assert steps
        assert all(step.status in {"COMPLETED", "SKIPPED"} for step in steps)
        assert any(step.status == "COMPLETED" for step in steps)


def test_web_plus_coding_mock_e2e(setup, client):
    headers, context, events, root, host, device_headers = setup
    context.mode = "on"
    client.put(
        "/tools/preferences",
        headers=headers,
        json={
            "computer_mode": "trusted",
            "workspace_roots": [str(root)],
            "default_mode": "on",
            "search_enabled": True,
            "fetch_enabled": True,
        },
    )
    app_file = root / "app.py"
    app_file.write_text("def add(a, b):\n    return a - b\n\nTIMEOUT = 1\n", encoding="utf-8", newline="\n")
    (root / "test_app.py").write_text(
        "from app import add, TIMEOUT\n\ndef test_add():\n    assert add(2, 2) == 4\n\ndef test_timeout():\n    assert TIMEOUT == 30\n",
        encoding="utf-8",
        newline="\n",
    )
    registry = make_registry()
    web = FakeWebProvider("search")
    fetch = FakeWebProvider("fetch")
    context.user_prompt = (
        "Проверь проект, посмотри актуальную документацию FastAPI если нужно, исправь тесты и перезапусти их."
    )
    executor = ToolExecutor(registry)
    original = executor.execute

    async def execute(name, arguments, context, origin="model"):
        if name in {"web_search", "web_fetch"}:
            provider = web if name == "web_search" else fetch
            args = registry.get(name)[0].input_model.model_validate(
                json.loads(arguments) if isinstance(arguments, str) else arguments
            )
            return await provider.execute(args, context)
        return await original(name, arguments, context, origin=origin)

    executor.execute = execute
    orchestrator = ToolOrchestrator(registry, executor)
    asyncio.run(
        orchestrator.prepare(
            FixPlanner(app_file, root, web=True),
            [{"role": "user", "content": context.user_prompt}],
            0,
            context,
            {},
        )
    )
    text = app_file.read_text(encoding="utf-8")
    assert "return a + b" in text
    assert "TIMEOUT = 30" in text
    with SessionLocal() as db:
        row = require_row(db, LocalTask, context.task_id)
        assert row.status == machine.COMPLETED
        assert row.plan_revision >= 2


def test_device_disconnect_waits(setup, client):
    _headers, context, _events, root, host, device_headers = setup
    host.close()
    with SessionLocal() as db:
        device = require_row(db, PairedDevice, device_headers["X-Alex-Device-Id"])
        device.last_seen = now() - timedelta(minutes=5)
        db.commit()
    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    asyncio.run(
        orchestrator.prepare(
            ScriptedPlanner([_call("list_directory", {"path": str(root), "purpose": "Inspect"}, "c1")]),
            [{"role": "user", "content": context.user_prompt}],
            0,
            context,
            {},
        )
    )
    with SessionLocal() as db:
        row = require_row(db, LocalTask, context.task_id)
        assert row.status == machine.WAITING_DEVICE
        assert row.finished_at is None


def test_stale_patch_is_conflict(setup):
    _headers, context, _events, root, host, _device = setup
    app_file = root / "app.py"
    stale = hashlib.sha256(b"not-the-file").hexdigest()
    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    scripts = [
        _call(
            "patch_file",
            {
                "path": str(app_file),
                "old_text": "return a - b",
                "new_text": "return a + b",
                "expected_before_sha256": stale,
                "purpose": "stale",
            },
            "stale",
        )
    ]
    asyncio.run(
        orchestrator.prepare(
            ScriptedPlanner(scripts), [{"role": "user", "content": context.user_prompt}], 0, context, {}
        )
    )
    assert "return a - b" in app_file.read_text(encoding="utf-8")
    with SessionLocal() as db:
        row = require_row(db, LocalTask, context.task_id)
        assert row.status == machine.CONFLICT
        assert row.last_error == "conflict"


def test_retry_then_succeeds(setup):
    _headers, context, _events, root, host, _device = setup
    app_file = root / "app.py"
    attempts = {"n": 0}

    class RetryPlanner:
        supports_tools = True

        async def plan_tools(self, messages, tools, usage):
            attempts["n"] += 1
            digest = _sha(app_file) if attempts["n"] > 1 else hashlib.sha256(b"stale").hexdigest()
            if "return a - b" in app_file.read_text(encoding="utf-8"):
                return {
                    "tool_calls": _call(
                        "patch_file",
                        {
                            "path": str(app_file),
                            "old_text": "return a - b",
                            "new_text": "return a + b",
                            "expected_before_sha256": digest,
                            "purpose": "retry-fix",
                        },
                        f"retry-{attempts['n']}",
                    )
                }
            return {"tool_calls": []}

    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    asyncio.run(
        orchestrator.prepare(
            RetryPlanner(), [{"role": "user", "content": context.user_prompt}], 0, context, {}
        )
    )
    assert "return a + b" in app_file.read_text(encoding="utf-8")
    assert attempts["n"] >= 2
