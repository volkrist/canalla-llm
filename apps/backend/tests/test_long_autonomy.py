"""Long autonomy: the planner loop must not stop before its own verification is honest.

Two live LA runs ended after 12 model-origin calls with the project built, the tests run once and
then a final edit. The controller called that `verification_required` and failed the task, because
nothing forced the re-test after the last write: `next_forced_action` decided on the *last tool*
instead of on the staleness the controller had already recorded.
"""

from __future__ import annotations

import asyncio

import pytest

from app.database import SessionLocal
from app.models import Message
from app.tools.executor import ToolExecutor
from app.tools.local import machine
from app.tools.models import LocalTask
from app.tools.orchestrator import ToolOrchestrator
from app.tools.registry import make_registry
from tests.db_helpers import require_row
from tests.fake_host import FakeHost
from tests.test_autonomous_tasks import ScriptedPlanner, _call, _context, _project


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
    with SessionLocal() as db:
        message = Message(chat_id=chat_id, role="assistant", content="")
        db.add(message)
        db.commit()
        generation_id = message.id
    events: list[tuple[str, dict]] = []
    prompt = "Полностью проверь этот проект, исправь падающие тесты и не завершай, пока проверка не пройдёт."
    context = _context(user_id, chat_id, generation_id, str(root), prompt, events)
    host = FakeHost(client, device_headers, root).start()
    yield context, events, root, host
    host.close()


def test_the_loop_runs_at_least_fifteen_model_calls(setup):
    """No hidden 12-call ceiling: 16 scripted model calls are all executed, in order."""
    context, _events, root, host = setup
    reads = [
        _call("read_file", {"path": str(root / "app.py"), "purpose": f"read {index}"}, f"c{index}")
        for index in range(16)
    ]
    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    asyncio.run(
        orchestrator.prepare(
            ScriptedPlanner(reads), [{"role": "user", "content": context.user_prompt}], 0, context, {}
        )
    )

    assert host.handled.count("read_file") == 16
    assert context.limits.calls >= 16
    with SessionLocal() as db:
        row = require_row(db, LocalTask, context.task_id)
        assert row.tool_calls_used >= 15


def test_a_write_after_the_test_run_forces_verification(setup):
    """The LA failure: write → run_process → list_directory ended the loop with stale tests."""
    context, _events, root, host = setup
    app = root / "app.py"

    def fix():
        app.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8", newline="\n")
        return _call(
            "write_file",
            {"path": str(app), "content": "def add(a, b):\n    return a + b\n", "purpose": "fix"},
            "w1",
        )

    scripts = [
        _call("read_file", {"path": str(app), "purpose": "read"}, "r1"),
        # A first test run that still fails, then the model "finishes" with a listing.
        _call("run_python", {"argv": ["-m", "pytest", "-q"], "cwd": str(root), "purpose": "tests"}, "t1"),
        fix,
        _call("list_directory", {"path": str(root), "purpose": "look"}, "l1"),
    ]
    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    asyncio.run(
        orchestrator.prepare(
            ScriptedPlanner(scripts), [{"role": "user", "content": context.user_prompt}], 0, context, {}
        )
    )

    with SessionLocal() as db:
        row = require_row(db, LocalTask, context.task_id)
        # The write made the earlier test run stale, and the controller recorded exactly that.
        assert (row.verification or {}).get("tests") in {"stale", "passed"}
        # A verification call happened after the last write.
        commands = (row.checkpoint or {}).get("commands") or []
        tools = [item.get("tool") for item in commands]
        last_write = max(
            (index for index, name in enumerate(tools) if name in {"write_file", "patch_file"}),
            default=-1,
        )
        assert any(name in {"run_python", "run_process"} for name in tools[last_write + 1 :]), (
            f"no verification after the final write: {tools}"
        )


def test_a_plain_folder_does_not_force_a_git_review(tmp_path, client, auth):
    """A build into a non-repository folder must not be sent to `git_status` forever."""
    from app.tools.local.task import LocalTaskController

    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    root = tmp_path / "plain"
    root.mkdir()
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    with SessionLocal() as db:
        task = LocalTask(
            user_id=user_id,
            chat_id=chat_id,
            title="plain",
            status=machine.EXECUTING,
            original_user_request=f"Проверь проект в {root} и исправь ошибки",
            tool_budget=40,
            file_change_budget=20,
            facts={"scope": {"primary_root": str(root)}, "tests_passed": True},
            verification={"tests": "passed"},
            checkpoint={"commands": [{"tool": "run_python"}], "test_command": ["pytest", "-q"]},
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        class _Context:
            task_id = task.id
            autonomous = True
            coding_task = True
            computer_mode = "trusted"
            workspace = type(
                "W", (), {"root": str(root), "test_command": ["pytest"], "test_via": "run_python"}
            )()

        assert LocalTaskController().next_forced_action(_Context()) is None
