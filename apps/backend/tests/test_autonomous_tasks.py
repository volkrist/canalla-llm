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
from app.models import Message, now
from app.tools.contracts import ToolError
from app.tools.executor import ExecutionContext, ToolExecutor, reconcile_tools
from app.tools.local import machine
from app.tools.local.compact import compact_text
from app.tools.local.plan import default_plan, looks_like_autonomous, looks_like_greenfield
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


def test_scope_keeps_a_directory_with_spaces(tmp_path):
    """A task path like `Canalla E2E 1.2.3gent-live` is not truncated at the space."""
    from app.tools.local.scope import assert_scope, build_scope, infer_primary

    root = tmp_path / "Canalla E2E 1.2.3" / "agent-live"
    root.mkdir(parents=True)
    prompt = f"In {root} create agent_hello.py whose content is exactly print(2 + 2)."
    assert infer_primary(prompt, [], "") == str(root)
    scope = build_scope(prompt, [], "scope-test")
    assert str(root) == scope.primary_root
    assert_scope("write_file", {"path": str(root / "agent_hello.py")}, scope)
    assert_scope("run_python", {"cwd": str(root), "argv": ["agent_hello.py"]}, scope)

    readme = root / "readme.txt"
    readme.write_text("marker", encoding="utf-8")
    assert infer_primary(f"Use read_file on {readme} and reply.", [], "") == str(root)
    assert infer_primary(f'Use read_file on "{readme}" and reply.', [], "") == str(root)


def test_target_resolution_keeps_a_file_name_with_spaces(tmp_path):
    """A model-supplied absolute path is never rewritten into a bare file name.

    Regression: ``coerce_file_argument`` resolved ``readme with spaces.txt`` from the prompt and
    returned ``spaces.txt`` even though the model had passed the full absolute path, so the host
    refused a correct call with ``path_denied``.
    """
    from app.tools.local.paths import native_path
    from app.tools.local.targets import (
        coerce_file_argument,
        extract_absolute_paths,
        looks_like_file_path,
        resolve_target,
    )

    root = tmp_path / "Canalla E2E 1.2.3" / "agent-live"
    root.mkdir(parents=True)
    target = root / "readme with spaces.txt"
    target.write_text("S3_CANALLA_READ_OK", encoding="utf-8")
    prompt = f"Открой файл {target} через инструмент read_file и перескажи содержимое."
    expected = native_path(str(target))

    assert looks_like_file_path(str(target))
    assert not looks_like_file_path(str(root))
    assert expected in extract_absolute_paths(prompt)
    resolved = resolve_target(prompt, workspace_root=str(root), roots=[str(root)], expect="file")
    assert resolved.kind == "file" and resolved.path == expected
    assert coerce_file_argument("read_file", str(target), prompt) == expected
    assert coerce_file_argument("read_file", expected, prompt, roots=[str(root)]) == expected


def test_scope_handles_normal_nested_and_unicode_paths(tmp_path):
    from app.tools.local.scope import assert_scope, build_scope, infer_primary

    normal = tmp_path / "demo" / "src" / "pkg"
    normal.mkdir(parents=True)
    assert infer_primary(f"Work in {normal} and write x.py", [], "") == str(normal)

    cyrillic = tmp_path / "Проект Каналья" / "вложенная папка"
    cyrillic.mkdir(parents=True)
    assert infer_primary(f"Создай {cyrillic}\файл.py", [], "") == str(cyrillic)
    scope = build_scope(f"desktop {cyrillic}", [], "scope-test")
    assert scope.primary_root == str(cyrillic)
    assert_scope("write_file", {"path": str(cyrillic / "файл.py")}, scope)


def test_scope_rejects_parent_escape_and_outside_argv(tmp_path):
    from app.tools.local.scope import assert_scope, build_scope

    root = tmp_path / "Canalla E2E 1.2.3" / "agent-live"
    root.mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')", encoding="utf-8")
    scope = build_scope(f"desktop {root}", [], "scope-test")

    assert_scope("write_file", {"path": str(root / "sub" / ".." / "ok.txt")}, scope)
    with pytest.raises(ToolError, match="workspace_scope"):
        assert_scope("write_file", {"path": str(root / ".." / "outside.txt")}, scope)
    with pytest.raises(ToolError, match="workspace_scope"):
        assert_scope("run_python", {"cwd": str(root), "argv": [str(outside)]}, scope)


def test_scope_blocks_a_link_that_escapes_the_root(tmp_path):
    from app.tools.local.scope import assert_scope, build_scope

    root = tmp_path / "Canalla E2E 1.2.3" / "agent-live"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            pytest.skip("cannot create a symlink or junction in this environment")
    scope = build_scope(f"desktop {root}", [], "scope-test")
    with pytest.raises(ToolError, match="workspace_scope"):
        assert_scope("write_file", {"path": str(link / "evil.txt")}, scope)


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


def test_a_build_from_scratch_gets_a_build_plan():
    """H1 live: the repair plan told the model to "run baseline tests" and "edit failing source";
    it wrote one package file and stopped. A greenfield request gets deliverable steps instead."""
    h1 = (
        "В папке C:\\Users\\Volkr\\Canalla Acceptance\\H1 создай рабочий Python CLI-проект "
        "h1_cli: пакет h1_cli/__main__.py, h1_cli/core.py, tests/test_core.py и README.md. "
        "python -m h1_cli должен печатать H1_CLI_OK, pytest -q должен проходить."
    )
    assert looks_like_greenfield(h1)

    plan = default_plan(h1, coding=True, research=False, tor=False)
    keys = [item["key"] for item in plan]

    assert keys[:3] == ["inspect", "scaffold", "implement"]
    assert "tests" in keys and "readme" in keys
    assert "run_cli" in keys and "run_tests" in keys

    repair = default_plan("Исправь failing tests", coding=True, research=False, tor=False)
    repair_keys = [item["key"] for item in repair]
    assert "baseline" in repair_keys
    assert "scaffold" not in repair_keys
    assert not looks_like_greenfield("Выполни безопасный рефакторинг существующего проекта")


def test_a_plan_with_pending_steps_is_never_goal_met(setup):
    """H1 live: one `write_file` matched «создай», so the turn ended after __init__.py.

    A completed file write is a goal only when the controller's own plan has nothing left.
    Otherwise the model is never asked for the remaining modules, tests and README and the
    user gets a summary that claims a project that does not exist.
    """
    from app.tools.local.facts import record

    _headers, context, _events, root, _host, _device = setup
    h1 = (
        f"В папке {root} создай рабочий Python CLI-проект h1_cli: пакет "
        "h1_cli/__main__.py, h1_cli/core.py, tests/test_core.py и README.md. "
        "python -m h1_cli должен печатать H1_CLI_OK, pytest -q должен проходить."
    )
    context.user_prompt = h1
    controller = LocalTaskController()
    controller.attach(context)
    orchestrator = ToolOrchestrator(None, None)

    with SessionLocal() as db:
        row = controller.open(db, context)
        assert row is not None
        context.task_id = row.id
        steps = controller.steps(db, row.id)
        assert {"inspect", "scaffold", "implement", "tests", "readme", "run_cli", "run_tests"} <= {
            step.key for step in steps
        }
        row.facts = record({}, "FILE_CREATED", {"path": str(root / "h1_cli" / "__init__.py")})
        db.commit()
        assert orchestrator._goal_satisfied(context, h1) is False

        for step in steps:
            step.status = "COMPLETED"
        db.commit()
        assert orchestrator._goal_satisfied(context, h1) is True


def test_a_plain_folder_is_not_failed_for_missing_git_review(tmp_path):
    """H1 live: a completed, tested project was marked FAILED as `git_not_reviewed`.

    The acceptance folder is not a repository, and neither is a user's brand-new project
    directory. A git review can only be required where a repository exists; in a real
    repository the criterion still applies.
    """
    row = SimpleNamespace(
        original_user_request=(
            "В папке C:\\tmp создай рабочий Python CLI-проект: пакет, модуль, тесты и README. "
            "python -m h1_cli должен печатать H1_CLI_OK, pytest -q должен проходить."
        ),
        facts={"scope": {"primary_root": str(tmp_path)}, "file_hashes": {"a": "b"}},
        verification={"tests": "passed"},
        tool_calls_used=8,
        checkpoint={"commands": [{"tool": "write_file"}], "changed_files": []},
        success_criteria={"all": ["done"]},
    )
    controller = LocalTaskController()
    assert controller.final_review(row)["ok"] is True

    (tmp_path / ".git").mkdir()
    row.verification = {"tests": "passed"}
    review = controller.final_review(row)
    assert review["ok"] is False
    assert review["reason"] == "git_not_reviewed"


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


def test_stop_cancels_a_pending_confirmation(setup):
    """A stopped task leaves no approvable confirmation behind it."""
    from app.tools.confirmation import confirm_allow, make_envelope, store_envelope

    _headers, context, _events, _root, _host, _device = setup
    with SessionLocal() as db:
        task = LocalTask(
            user_id=context.user_id,
            chat_id=context.chat_id,
            status=machine.WAITING_CONFIRMATION,
            current_phase=machine.WAITING_CONFIRMATION,
            original_user_request="stage a pending command",
            updated_at=now(),
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        run = ToolRun(
            user_id=context.user_id,
            chat_id=context.chat_id,
            tool_name="run_process",
            provider="local_device",
            risk_level="NORMAL_CHANGE",
            status="waiting_confirmation",
            input_summary={"action": "run_process"},
            input_digest="a" * 64,
            result_metadata={},
            origin="model",
            task_id=task.id,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        store_envelope(
            run,
            make_envelope(
                user_id=context.user_id,
                task_id=task.id,
                tool_name="run_process",
                risk_level="NORMAL_CHANGE",
                payload={"executable": "python"},
                confirmation_id=run.id,
            ),
        )
        db.commit()
        run_id = run.id

        LocalTaskController().stop(db, task=task, user_id=context.user_id)

        assert require_row(db, ToolRun, run_id).status == "stopped"
        assert require_row(db, ToolRun, run_id).error_code == "task_stopped"
        assert require_row(db, LocalTask, task.id).status == machine.STOPPED
        with pytest.raises(ToolError, match="confirmation_already_used"):
            confirm_allow(db, run_id=run_id, user_id=context.user_id)


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


class RecoveringProvider:
    """A model that comes back after the outage and answers from the restored tool result."""

    supports_tools = True

    def __init__(self, answer, *, expect=None):
        self.answer = answer
        self.expect = expect
        self.saw_tool_result = False

    async def health(self):
        return True

    async def plan_tools(self, messages, tools, usage):
        if self.expect:
            self.saw_tool_result = any(
                message.get("role") == "tool" and self.expect in str(message.get("content"))
                for message in messages
            )
        return {"tool_calls": []}

    async def stream_chat(self, messages):
        yield self.answer


def test_a_gateway_outage_after_a_tool_keeps_the_result_and_finishes(setup):
    """The 1.2.3 live failure: read_file succeeded, the next planner turn timed out.

    The task must resume from the real tool result, never replay the completed call, and write
    its final answer into the chat instead of leaving "LLM unavailable" behind.
    """
    from app.providers import LLMError
    from app.tools.local.continue_task import continue_pending

    _headers, context, _events, root, host, _device = setup
    # `desktop` makes this a computer task (a real LocalTask exists to resume); it deliberately
    # avoids the deterministic "read this file" route so the planner is the one that acts.
    prompt = "Посмотри на файл app.py на desktop."
    context.user_prompt = prompt

    class FailingAfterTool:
        supports_tools = True

        def __init__(self):
            self.calls = 0

        async def plan_tools(self, messages, tools, usage):
            self.calls += 1
            if self.calls == 1:
                return {
                    "tool_calls": _call("read_file", {"path": str(root / "app.py"), "purpose": "read"}, "c1")
                }
            raise LLMError("llm_unavailable")

    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    asyncio.run(
        orchestrator.prepare(FailingAfterTool(), [{"role": "user", "content": prompt}], 0, context, {})
    )

    assert context.task_halt == "llm_unavailable"
    assert host.handled.count("read_file") == 1
    with SessionLocal() as db:
        row = require_row(db, LocalTask, context.task_id)
        assert row.status == machine.WAITING_LLM
        transcript = (row.checkpoint or {}).get("tool_transcript")
        assert transcript
        assert any(
            item.get("role") == "tool" and "return a - b" in str(item.get("content")) for item in transcript
        )
        # The stream finalized the parked message before the queue monitor may resume it.
        message = require_row(db, Message, context.generation_id)
        message.status, message.completed_at = "complete", now()
        facts = dict(row.facts or {})
        # Skip the first backoff interval; the schedule itself is covered separately.
        facts["llm_retry_at"] = (now() - timedelta(seconds=1)).isoformat()
        row.facts = facts
        db.commit()

    provider = RecoveringProvider("app.py: return a - b", expect="return a - b")
    asyncio.run(continue_pending(orchestrator, provider, user_id=context.user_id, host_online=True))

    assert provider.saw_tool_result is True
    assert host.handled.count("read_file") == 1  # the completed read was not replayed
    with SessionLocal() as db:
        row = require_row(db, LocalTask, context.task_id)
        assert row.status == machine.COMPLETED
        message = require_row(db, Message, context.generation_id)
        assert message.status == "complete"
        assert message.content == "app.py: return a - b"
        assert not (row.checkpoint or {}).get("tool_transcript")


def test_a_planner_stall_after_tools_keeps_the_answer_in_the_same_turn(setup, client):
    """H2 live: pytest passed, then the planner went silent and the user saw only a failure.

    The completed tool work is on disk. A fresh answer request usually succeeds, so the turn must
    answer from those results and conclude the task instead of ending with "LLM unavailable".
    """
    from app.main import app
    from app.providers import LLMError

    headers, context, _events, root, host, _device = setup
    calls = {"plans": 0}

    class StallThenAnswer:
        supports_tools = True

        async def plan_tools(self, messages, tools, usage):
            calls["plans"] += 1
            if calls["plans"] == 1:
                return {
                    "tool_calls": _call("read_file", {"path": str(root / "app.py"), "purpose": "read"}, "c1")
                }
            raise LLMError("llm_stalled")

        async def stream_with_usage(self, messages, usage):
            yield "Файл прочитан: add(a, b) возвращает a - b."

        async def chat(self, messages):
            return "Файл прочитан: add(a, b) возвращает a - b."

        async def health(self):
            return True

        async def status(self):
            return "ready"

    previous = app.state.provider
    app.state.provider = StallThenAnswer()
    try:
        response = client.post(
            f"/chats/{context.chat_id}/stream",
            headers=headers,
            json={"content": "Посмотри на файл app.py на desktop."},
        )
    finally:
        app.state.provider = previous

    assert response.status_code == 200
    assert "event: done" in response.text
    assert "Файл прочитан: add(a, b) возвращает a - b." in response.text
    # The transient task label may still say WAITING_LLM, but the answer itself must not
    # be the old blanket failure text.
    assert "LLM unavailable. Task will continue after recovery." not in response.text
    assert host.handled.count("read_file") == 1

    from sqlalchemy import select

    with SessionLocal() as db:
        messages = db.scalars(
            select(Message).where(Message.chat_id == context.chat_id).order_by(Message.created_at)
        ).all()
        answer = messages[-1]
        assert answer.role == "assistant"
        assert answer.content.startswith("Файл прочитан")
        assert answer.status == "complete"
        row = db.scalars(select(LocalTask)).all()[-1]
        assert row.status == machine.COMPLETED
        # Nothing is left to replay on a later resume.
        assert not (row.checkpoint or {}).get("tool_transcript")


def test_a_halted_answer_still_reports_the_real_work(setup):
    """When even the answer request fails, the message names the cause and the saved results."""
    from app.providers import LLMError
    from app.tools.local.public_text import public_assistant_text

    _headers, context, _events, root, host, _device = setup

    class StallAfterTool:
        supports_tools = True

        def __init__(self):
            self.calls = 0

        async def plan_tools(self, messages, tools, usage):
            self.calls += 1
            if self.calls == 1:
                return {
                    "tool_calls": _call("read_file", {"path": str(root / "app.py"), "purpose": "read"}, "c1")
                }
            raise LLMError("llm_stalled")

    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    asyncio.run(
        orchestrator.prepare(
            StallAfterTool(), [{"role": "user", "content": context.user_prompt}], 0, context, {}
        )
    )

    assert context.final_after_halt is True
    assert context.skip_final_stream is False
    assert host.handled.count("read_file") == 1
    with SessionLocal() as db:
        row = require_row(db, LocalTask, context.task_id)
        text = public_assistant_text("", "llm_unavailable", row.facts)
        assert row.status == machine.WAITING_LLM
    assert "llm_stalled" in text
    assert "прочитан файл" in text
    assert "LLM unavailable" not in text


def test_a_client_disconnect_does_not_destroy_the_task(setup, client, monkeypatch):
    """A reload/navigation while tools run must not cancel the task or replay its work.

    The client is gone, the stream ends, but the tool phase continues in the background, the
    answer is written into the same assistant message and the task concludes normally.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.chat_stream import stream_response
    from app.database import SessionLocal
    from app.main import app
    from app.models import Chat, Message, User

    headers, context, _events, root, host, _device = setup
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]

    class DetachingProvider:
        supports_tools = True

        def __init__(self):
            self.plans = 0

        async def plan_tools(self, messages, tools, usage):
            self.plans += 1
            if self.plans == 1:
                # The client is gone long before this turn returns its tool call.
                await asyncio.sleep(1.0)
                return {
                    "tool_calls": _call("read_file", {"path": str(root / "app.py"), "purpose": "read"}, "c1")
                }
            return {"tool_calls": []}

        async def stream_chat(self, messages):
            yield "DETACHED_OK"

        async def stream_with_usage(self, messages, usage):
            yield "DETACHED_OK"

        async def chat(self, messages):
            return "DETACHED_OK"

        async def health(self):
            return True

        async def status(self):
            return "ready"

    monkeypatch.setattr(app.state, "provider", DetachingProvider())
    start = time.monotonic()

    async def disconnected():
        return time.monotonic() - start > 0.2

    request = SimpleNamespace(app=app, is_disconnected=disconnected)

    async def scenario():
        with SessionLocal() as db:
            chat = db.get(Chat, chat_id)
            user = db.get(User, chat.user_id)
            response = await stream_response(chat, "Посмотри на файл app.py на desktop.", request, user, db)
            async for _ in response.body_iterator:
                pass
        # `asyncio.run` cancels pending tasks, so the event loop has to stay alive until the
        # detached turn has written its answer.
        deadline = time.monotonic() + 20
        answer = ""
        while time.monotonic() < deadline:
            with SessionLocal() as db:
                rows = db.scalars(
                    select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
                ).all()
                tasks = db.scalars(select(LocalTask).where(LocalTask.chat_id == chat_id)).all()
            if rows and rows[-1].role == "assistant" and rows[-1].content:
                answer = rows[-1].content
                if tasks and tasks[-1].status == machine.COMPLETED:
                    return answer, tasks[-1].status
            await asyncio.sleep(0.2)
        return answer, (tasks[-1].status if tasks else "")

    answer, status = asyncio.run(scenario())

    assert answer == "DETACHED_OK"
    assert status == machine.COMPLETED
    assert host.handled.count("read_file") == 1
    assert chat_id not in app.state.generating


def test_write_run_fix_rerun_returns_real_stdout(setup):
    """A deterministic planner still uses the real host executor for every step."""
    _headers, context, _events, root, host, _device = setup
    prompt = "Подготовь hello.py на desktop и покажи результат."
    context.user_prompt = prompt
    script = [
        _call(
            "write_file",
            {"path": str(root / "hello.py"), "content": "print(2 + 2)\n", "purpose": "create"},
            "w1",
        ),
        _call("run_python", {"argv": ["hello.py"], "cwd": str(root), "purpose": "run"}, "r1"),
        _call(
            "write_file",
            {"path": str(root / "hello.py"), "content": "print(2 * 3)\n", "purpose": "fix"},
            "w2",
        ),
        _call("run_python", {"argv": ["hello.py"], "cwd": str(root), "purpose": "rerun"}, "r2"),
    ]
    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    asyncio.run(
        orchestrator.prepare(ScriptedPlanner(script), [{"role": "user", "content": prompt}], 0, context, {})
    )

    assert host.handled.count("write_file") == 2
    assert host.handled.count("run_python") == 2
    assert (root / "hello.py").read_text(encoding="utf-8") == "print(2 * 3)\n"
    with SessionLocal() as db:
        runs = db.scalars(select_completed(context.task_id)).all()
        stdout = []
        for run in runs:
            hosted = (run.result_metadata or {}).get("host_result") or {}
            if run.tool_name == "run_python":
                stdout.append(str(hosted.get("stdout") or ""))
        assert stdout == ["4\n", "6\n"]


def test_orchestrator_uses_a_directory_with_spaces(setup, tmp_path, client):
    """The real orchestrator + real host executor, with `Canalla E2E 1.2.3` in the path."""
    headers, context, _events, _root, host, _device = setup
    live_dir = tmp_path / "Canalla E2E 1.2.3" / "agent-live"
    live_dir.mkdir(parents=True)
    # The user granted this directory; the task scope is inferred from the prompt. With `ask`
    # the same action is confirmed through the real UI/confirmation API, never silently widened.
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "trusted", "workspace_roots": [str(live_dir)]},
    )
    context.user_prompt = (
        f"На desktop в {live_dir} подготовь agent_hello.py с содержимым print(2 + 2), "
        "выполни agent_hello.py через run_python, затем измени на print(2 * 3) и выполни снова."
    )
    script = [
        _call(
            "write_file",
            {"path": str(live_dir / "agent_hello.py"), "content": "print(2 + 2)\n", "purpose": "create"},
            "w1",
        ),
        _call("run_python", {"argv": ["agent_hello.py"], "cwd": str(live_dir), "purpose": "run"}, "r1"),
        _call(
            "write_file",
            {"path": str(live_dir / "agent_hello.py"), "content": "print(2 * 3)\n", "purpose": "fix"},
            "w2",
        ),
        _call("run_python", {"argv": ["agent_hello.py"], "cwd": str(live_dir), "purpose": "rerun"}, "r2"),
    ]
    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    asyncio.run(
        orchestrator.prepare(
            ScriptedPlanner(script),
            [{"role": "user", "content": context.user_prompt}],
            0,
            context,
            {},
        )
    )

    assert host.handled.count("write_file") == 2
    assert host.handled.count("run_python") == 2
    assert (live_dir / "agent_hello.py").read_text(encoding="utf-8") == "print(2 * 3)\n"
    with SessionLocal() as db:
        runs = db.scalars(select_completed(context.task_id)).all()
        stdout = [
            str(((run.result_metadata or {}).get("host_result") or {}).get("stdout") or "")
            for run in runs
            if run.tool_name == "run_python"
        ]
        row = require_row(db, LocalTask, context.task_id)
        assert stdout == ["4\n", "6\n"]
        assert not (row.facts or {}).get("workspace_scope_violations_blocked")

    # The final answer is a separate model turn: it must receive the tool results, or the model
    # answers that it cannot access the computer right after a successful run.
    from app.context_builder import ContextBuilder

    history = [{"role": "user", "content": context.user_prompt}]
    final = ContextBuilder.with_tool_results(history, context.tool_transcript, 20000)
    outputs = []
    for message in final:
        if message.get("role") != "user" or message is history[0]:
            continue
        outputs.append(str(message.get("content") or ""))
    tool_text = "\n".join(outputs)
    assert '"text": "4\\n"' in tool_text and '"text": "6\\n"' in tool_text, outputs


def test_tool_results_are_bounded_and_ordered():
    from app.context_builder import ContextBuilder

    history = [{"role": "user", "content": "go"}]
    transcript = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "function": {"name": "read_file"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "x" * 900},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c2", "function": {"name": "run_python"}}],
        },
        {"role": "tool", "tool_call_id": "c2", "content": "y" * 900},
    ]
    assert ContextBuilder.with_tool_results(history, [], 4000) == history
    kept = ContextBuilder.with_tool_results(history, transcript, 1200)
    # The request stays last; the newest result is the one kept when the budget is tight.
    assert kept[-1] == history[-1]
    assert kept[-2]["role"] == "user" and "y" * 10 in kept[-2]["content"]
    assert "x" * 10 not in kept[-2]["content"]
    assert len(kept) == 2  # the results note plus the original request


def test_deterministic_write_runs_the_written_script(setup, tmp_path, client):
    """The 20 Oct paid-run defect: the route wrote the argv filename instead of the body and never
    ran the script. The deterministic route must write the requested content and execute it."""
    headers, context, _events, _root, host, _device = setup
    live = tmp_path / "Canalla E2E 1.2.3" / "agent-live"
    live.mkdir(parents=True)
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "trusted", "workspace_roots": [str(live)]},
    )
    prompt = (
        f"In {live} use write_file to create agent_hello.py whose content is exactly "
        "print(2 + 2). Do not use run_process or python -c. Then use run_python "
        f'(argv ["agent_hello.py"], cwd {live}). Reply with only the stdout.'
    )
    context.user_prompt = prompt

    class EmptyPlanner:
        supports_tools = True

        async def plan_tools(self, messages, tools, usage):
            return {"tool_calls": []}

    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    asyncio.run(orchestrator.prepare(EmptyPlanner(), [{"role": "user", "content": prompt}], 0, context, {}))

    assert (live / "agent_hello.py").read_text(encoding="utf-8") == "print(2 + 2)"
    assert "write_file" in host.handled
    assert "run_python" in host.handled
    with SessionLocal() as db:
        runs = db.scalars(select_completed(context.task_id)).all()
        stdout = [
            str(((run.result_metadata or {}).get("host_result") or {}).get("stdout") or "")
            for run in runs
            if run.tool_name == "run_python"
        ]
        assert stdout == ["4\n"]


def test_a_duplicate_write_is_not_applied_twice(setup):
    """After a recovery the model may re-propose the same bytes; the file write is not repeated."""
    from app.tools.local.facts import record
    from app.tools.local.progress import applied_result, should_block

    _headers, context, _events, root, _host, _device = setup
    path = str(root / "hello.py")
    content = "print(2 + 2)\n"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    facts = record(
        {},
        "FILE_CREATED",
        {"path": path, "after_sha": digest, "expected_sha256": digest, "verified": True},
    )

    assert should_block(facts, "write_file", {"path": path, "content": content}) == "already_applied"
    assert should_block(facts, "write_file", {"path": path, "content": "print(2 * 3)\n"}) is None
    reused = applied_result(facts, "write_file", {"path": path, "content": content})
    assert reused["metadata"]["already_applied"] is True
    assert reused["metadata"]["sha256"] == digest


def test_the_recovery_retry_schedule_is_bounded():
    from app.tools.local.task import llm_retry_delay, llm_retry_due

    assert [llm_retry_delay(value) for value in (0, 1, 2, 3, 4, 9)] == [5, 10, 20, 40, 60, 60]
    future = (now() + timedelta(seconds=30)).isoformat()
    assert llm_retry_due({"llm_retry_at": future}) is False
    past = (now() - timedelta(seconds=1)).isoformat()
    assert llm_retry_due({"llm_retry_at": past}) is True
    # A dead session is not retried forever inside the automatic loop.
    old = (now() - timedelta(minutes=31)).isoformat()
    assert llm_retry_due({"llm_retry_at": past, "llm_retry_since": old}) is False


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
