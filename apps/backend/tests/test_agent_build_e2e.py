"""Free end-to-end: build → run → observe a failure → fix → re-test → run the program.

This is the product's main promise, proven without a GPU and without a model: the controller's
real plan, the real host tools (files, processes, pytest), the real verification gate, and the real
completion predicate. The scripted planner stands in for the model; nothing else is mocked. A live
Qwen run remains a separate, paid acceptance.
"""

from __future__ import annotations

import asyncio
import json

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
    "Создай в этой папке небольшой Python-проект: модуль calc.py с функцией add, тест "
    "test_calc.py и запуск calc.py, который печатает BUILD_E2E_OK и результат. Запусти pytest, "
    "исправь ошибки и покажи проверенный результат."
)

BUGGY = (
    "def add(a, b):\n    return a - b\n\n\nif __name__ == '__main__':\n    print('BUILD_E2E_OK', add(2, 2))\n"
)
FIXED = (
    "def add(a, b):\n    return a + b\n\n\nif __name__ == '__main__':\n    print('BUILD_E2E_OK', add(2, 2))\n"
)
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n"


def _call(name, arguments, call_id):
    return {
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ]
    }


class Runner:
    """The whole sequence: create, fail, fix, re-test, then run the program."""

    supports_tools = True

    def __init__(self, root):
        self.root = root
        self.step = 0

    async def plan_tools(self, messages, tools, usage):
        self.step += 1
        calc = self.root / "calc.py"
        tests = self.root / "test_calc.py"
        sequence = {
            1: ("write_file", {"path": str(calc), "content": BUGGY, "purpose": "module"}),
            2: ("write_file", {"path": str(tests), "content": TEST, "purpose": "tests"}),
            3: (
                "run_python",
                {"cwd": str(self.root), "argv": ["-m", "pytest", "-q"], "purpose": "baseline tests"},
            ),
            4: ("write_file", {"path": str(calc), "content": FIXED, "purpose": "fix the failing test"}),
            5: (
                "run_python",
                {"cwd": str(self.root), "argv": ["-m", "pytest", "-q"], "purpose": "verify tests"},
            ),
            6: ("run_python", {"cwd": str(self.root), "argv": ["calc.py"], "purpose": "run the program"}),
        }
        if self.step in sequence:
            name, arguments = sequence[self.step]
            return _call(name, arguments, f"step-{self.step}")
        return {"tool_calls": []}


def _read_text(path, attempts=20):
    """Windows can briefly lock a file another process just wrote (Defender/indexer).

    The product itself never relies on an immediate read after a write; this helper only keeps the
    assertion from racing the scanner.
    """
    import time

    last = None
    for _ in range(attempts):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError as error:  # pragma: no cover - Windows scanner timing
            last = error
            time.sleep(0.1)
    raise last


def test_build_fix_retest_and_run_with_real_tools(client, auth, tmp_path):
    from tests.fake_host import FakeHost

    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    root = tmp_path / "greenfield"
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

    async def emit(event, value):
        return None

    context = ExecutionContext(
        user_id,
        chat_id,
        generation_id,
        ToolLimits(max_calls=30, hard_max_calls=40),
        emit,
        mode="off",
        computer_mode="trusted",
        settings=WebSettings(computer_mode="trusted", workspace_roots=[str(root)], default_mode="off"),
        user_prompt=PROMPT,
        host_online=True,
    )
    host = FakeHost(client, device_headers, root).start()
    try:
        orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
        asyncio.run(orchestrator.prepare(Runner(root), [{"role": "user", "content": PROMPT}], 0, context, {}))
    finally:
        host.close()

    assert _read_text(root / "calc.py") == FIXED
    assert (root / "test_calc.py").is_file()
    with SessionLocal() as db:
        row = db.scalar(select(LocalTask).where(LocalTask.id == context.task_id))
        assert row is not None
        assert row.status == machine.COMPLETED
        assert (row.verification or {}).get("tests") == "passed"
        commands = (row.checkpoint or {}).get("commands") or []
    tools = [item["tool"] for item in commands]
    assert "write_file" in tools
    assert tools.count("run_python") >= 2  # a failing run, then a passing one
    # The CLI really ran and printed the marker; the fact is durable, not a claim in prose.
    assert "BUILD_E2E_OK" in json.dumps(row.facts, ensure_ascii=False)
