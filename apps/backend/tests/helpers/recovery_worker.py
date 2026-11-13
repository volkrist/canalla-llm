"""A separate process that starts the real backend, works and then dies on purpose.

Two phases, two processes, one SQLite file:

* `interrupt` creates a task through the product, performs one real side effect, then calls
  `os._exit(17)` in the middle of the next decision — no cleanup, no exception, no chance to write
  a "failed" status;
* `resume` starts the same application again (its lifespan marks the abandoned task INTERRUPTED),
  resumes that exact task id, verifies the side effect that already happened instead of repeating
  it, and completes the task with evidence.

The parent test asserts on the database and on the file, not on what either process printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.tools.executor import ExecutionContext, ToolExecutor  # noqa: E402
from app.tools.orchestrator import ToolOrchestrator  # noqa: E402
from app.tools.policy import ToolLimits, WebSettings  # noqa: E402
from app.tools.registry import make_registry  # noqa: E402

PROMPT = (
    "Подготовь отчёт в этой папке: создай steps.txt с первой строкой, затем продолжи работу и "
    "подтверди содержимое."
)
EMAIL = "recovery@example.com"
PASSWORD = "recovery-password-123"


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


class InterruptPlanner:
    supports_tools = True

    def __init__(self, root: Path):
        self.root = root
        self.step = 0

    async def plan_tools(self, messages, tools, usage):
        self.step += 1
        if self.step == 1:
            return _call(
                "write_file",
                {"path": str(self.root / "steps.txt"), "content": "one\n", "purpose": "first line"},
                "step-1",
            )
        if self.step == 2:
            return _call(
                "run_python",
                {"cwd": str(self.root), "argv": ["-c", "print('phase1')"], "purpose": "sanity"},
                "step-2",
            )
        # The process dies here: an ordinary crash in the middle of a task.
        sys.stdout.write("CRASH\n")
        sys.stdout.flush()
        os._exit(17)


class ResumePlanner:
    supports_tools = True

    def __init__(self, root: Path):
        self.root = root
        self.step = 0

    async def plan_tools(self, messages, tools, usage):
        self.step += 1
        if self.step == 1:
            # Idempotent: the final content, not an append, so a resumed write can never
            # duplicate the pre-crash line no matter how many times it runs.
            return _call(
                "write_file",
                {"path": str(self.root / "steps.txt"), "content": "one\ntwo\n", "purpose": "finish"},
                "r-1",
            )
        if self.step == 2:
            return _call(
                "verify_result",
                {
                    "checks": [
                        {"kind": "file_contains", "target": str(self.root / "steps.txt"), "expected": "two"},
                    ],
                    "purpose": "confirm the finished report",
                },
                "r-2",
            )
        return {"tool_calls": []}


def _login(client) -> dict:
    response = client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
    if response.status_code == 409:
        response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code in {200, 201}, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def _run(client, headers, root: Path, planner, *, resume_id: str | None) -> None:
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
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

    from app.database import SessionLocal
    from app.models import Message

    with SessionLocal() as db:
        message = Message(chat_id=chat_id, role="assistant", content="")
        db.add(message)
        db.commit()
        generation_id = message.id

    from tests.fake_host import FakeHost

    async def emit(event, value):
        return None

    context = ExecutionContext(
        user_id,
        chat_id,
        generation_id,
        ToolLimits(max_calls=20, hard_max_calls=30),
        emit,
        mode="off",
        computer_mode="trusted",
        settings=WebSettings(computer_mode="trusted", workspace_roots=[str(root)], default_mode="off"),
        user_prompt=PROMPT,
        host_online=True,
    )
    context.resume_task_id = resume_id
    host = FakeHost(client, device_headers, root).start()
    try:
        orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
        asyncio.run(orchestrator.prepare(planner, [{"role": "user", "content": PROMPT}], 0, context, {}))
    finally:
        host.close()
    if context.task_id:
        print(json.dumps({"task_id": context.task_id}), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["interrupt", "resume"], required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--task-id", default="")
    args = parser.parse_args()
    root = Path(args.root)

    with TestClient(app) as client:
        headers = _login(client)
        planner = InterruptPlanner(root) if args.phase == "interrupt" else ResumePlanner(root)
        _run(client, headers, root, planner, resume_id=args.task_id or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
