"""The exact T4L shape, free: read → write → read with a real device, real confirmations.

No GPU and no model: a scripted planner plays Qwen, and everything else is the product —
FastAPI backend, SQLite, the device queue and its claims, the confirmation workflow, the tool
executor, the task state machine and the durable journal. The point is to prove the shape of the
run that will be paid for: the file the prompt names exists, each tool is confirmed, the write
lands on disk, and the task can still reach COMPLETED.

The one layer this file does not exercise is the real window/process suspend (T4's transport
loss), which `artifacts/acceptance-126/free_restart_selftest.py t4` covers against the installed
app with the product's own mock model.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.tools.executor import ExecutionContext, ToolExecutor
from app.tools.local import machine
from app.tools.local.task import LocalTaskController
from app.tools.models import LocalTask, TaskEvent, ToolRun
from app.tools.orchestrator import ToolOrchestrator
from app.tools.policy import ToolLimits, WebSettings
from app.tools.registry import make_registry
from tests.fake_host import FakeHost

FIXTURE = "T4L_HOST_VALUE_417\n"


class ScriptedPlanner:
    """Plays the model: the same three tools the T4L prompt asks for."""

    supports_tools = True

    def __init__(self, root) -> None:
        self.root = root
        self.steps = [
            ("read_file", {"path": str(root / "t4l.txt"), "purpose": "Read the source file"}),
            (
                "write_file",
                {
                    "path": str(root / "t4l_out.txt"),
                    "content": FIXTURE.strip(),
                    "purpose": "Save the content into the output file",
                },
            ),
            ("read_file", {"path": str(root / "t4l_out.txt"), "purpose": "Confirm the output"}),
        ]
        self.index = 0

    async def plan_tools(self, messages, tools, usage):
        if self.index >= len(self.steps):
            return {"tool_calls": []}
        name, args = self.steps[self.index]
        self.index += 1
        return {
            "tool_calls": [
                {
                    "id": f"call-{self.index}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                }
            ]
        }


class Approver:
    """The user's side of `computer_mode=ask`, driven through the real confirmation API."""

    def __init__(self, client, headers, chat_id) -> None:
        self.client = client
        self.headers = headers
        self.chat_id = chat_id
        self.stop = threading.Event()
        self.log: list[dict] = []
        self.seen: set[str] = set()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> Approver:
        self.thread.start()
        return self

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=3)

    def _loop(self) -> None:
        while not self.stop.wait(0.05):
            rows = self.client.get(
                f"/tools/runs?chat_id={self.chat_id}", headers=self.headers
            ).json()
            for row in rows:
                run_id = str(row.get("id") or "")
                if row.get("status") != "waiting_confirmation" or not run_id or run_id in self.seen:
                    continue
                self.seen.add(run_id)
                status = self.client.post(
                    f"/tools/runs/{run_id}/confirm",
                    headers=self.headers,
                    json={"allow": True, "digest": row.get("input_digest")},
                )
                self.log.append(
                    {
                        "run_id": run_id,
                        "tool": row.get("tool_name"),
                        "http": status.status_code,
                        "body": status.text[:120],
                        "at": time.time(),
                    }
                )


def _context(user_id, chat_id, generation_id, root, prompt):
    async def emit(event, value):
        return None

    return ExecutionContext(
        user_id,
        chat_id,
        generation_id,
        ToolLimits(max_calls=24, hard_max_calls=40),
        emit,
        mode="off",
        computer_mode="ask",
        tor_enabled=False,
        tor_mode="off",
        settings=WebSettings(
            computer_mode="ask", workspace_roots=[str(root)], default_mode="auto"
        ),
        user_prompt=prompt,
        host_online=True,
    )


def _prepare(client, auth, tmp_path):
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    # The workspace must not live under pytest's own temp tree: a path containing `pytest` is a
    # coding signal, and this scenario is a plain file task.
    base = Path(tempfile.mkdtemp(prefix="canalla-t4l-"))
    root = base / "Canalla E2E" / "T4L"
    root.mkdir(parents=True)
    (root / "t4l.txt").write_text(FIXTURE, encoding="utf-8", newline="\n")
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "ask", "workspace_roots": [str(base)]},
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
    prompt = (
        f"В папке {root} открой t4l.txt, сохрани его содержимое в t4l_out.txt, "
        "затем открой t4l_out.txt и подтверди содержимое. В конце выведи T4L_DONE."
    )
    context = _context(user_id, chat_id, generation_id, root, prompt)
    host = FakeHost(client, device_headers, root).start()
    approver = Approver(client, headers, chat_id).start()
    return headers, context, root, host, approver, base


def test_the_t4l_shape_completes_on_a_real_device(client, auth):
    headers, context, root, host, approver, base = _prepare(client, auth, None)
    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    try:
        with SessionLocal() as db:
            LocalTaskController().attach(context)
            row = LocalTaskController().open(db, context)
            assert row is not None, "the named-file prompt must create a local_tasks row"
            task_id = row.id
        context.task_id = task_id

        asyncio.run(
            orchestrator.prepare(
                ScriptedPlanner(root),
                [{"role": "user", "content": context.user_prompt}],
                0,
                context,
                {},
            )
        )

        assert (root / "t4l_out.txt").is_file(), "the write must land on disk"
        assert (root / "t4l_out.txt").read_text(encoding="utf-8").strip() == FIXTURE.strip()
        assert (root / "t4l.txt").read_text(encoding="utf-8") == FIXTURE, "the source is untouched"
        assert "read_file" in host.handled and "write_file" in host.handled
        assert host.handled.count("read_file") == 2

        with SessionLocal() as db:
            runs = db.scalars(
                select(ToolRun).where(ToolRun.chat_id == context.chat_id)
            ).all()
            model_runs = [run for run in runs if run.origin == "model"]
            assert [run.tool_name for run in model_runs] == [
                "read_file",
                "write_file",
                "read_file",
            ]
            assert all(run.status == "completed" for run in model_runs), [
                (run.tool_name, run.status, run.error_code) for run in model_runs
            ]
            digests = [run.input_digest for run in model_runs]
            assert len(digests) == len(set(digests)), "no payload was repeated"

            controller = LocalTaskController()
            controller.conclude(db, context)
            db.commit()
            task = db.get(LocalTask, task_id)
            assert task is not None
            assert task.status == machine.COMPLETED, (
                task.status,
                task.last_error,
                [
                    (item.kind, item.payload)
                    for item in db.scalars(
                        select(TaskEvent).where(TaskEvent.task_id == task_id)
                    ).all()
                ],
            )
            assert task.tool_calls_used >= 3

        assert len(approver.log) == 3, approver.log
        assert all(item["http"] == 200 for item in approver.log), approver.log
        assert {"read_file", "write_file"} <= {item["tool"] for item in approver.log}
    finally:
        approver.close()
        host.close()
        shutil.rmtree(base, ignore_errors=True)


def test_the_fixture_is_the_one_the_prompt_names(client, auth):
    """A prompt that names a file must never point at a folder without it."""
    headers, context, root, host, approver, base = _prepare(client, auth, None)
    try:
        assert "t4l.txt" in context.user_prompt
        assert (root / "t4l.txt").read_text(encoding="utf-8") == FIXTURE
        assert str(root) in context.user_prompt
        assert str(root) in (context.settings.workspace_roots or [])
    finally:
        approver.close()
        host.close()
        shutil.rmtree(base, ignore_errors=True)
