"""The state machine must reach a legal state after a parked task's tool fails.

The live T4 run of 2026-11-11 ended in `generation_failed type=ToolError` because a retryable
tool timeout arrived while the task was waiting for a confirmation: `observe_tool` tried
WAITING_CONFIRMATION -> RETRYING, the machine refused the edge, and the refusal escaped as
`invalid_task_transition` into the chat stream.

Suppressing that exception is not the fix. The task must land in a state that is legal *and*
meaningful, the model must get the tool result, and the task must still be able to reach
COMPLETED. These tests pin the real behaviour, including the negative cases.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import select

from app.database import SessionLocal
from app.tools.contracts import ToolError
from app.tools.local import machine
from app.tools.local.task import LocalTaskController
from app.tools.models import LocalTask, TaskEvent
from app.tools.orchestrator import ToolOrchestrator


def _context(user_id, chat_id, *, autonomous=False):
    async def emit(event, value):
        return None

    return SimpleNamespace(
        user_id=user_id,
        chat_id=chat_id,
        generation_id=None,
        user_prompt="В папке X открой t4l.txt",
        limits=SimpleNamespace(calls=4, max_calls=24, max_seconds=600, remaining=600),
        settings=SimpleNamespace(workspace_roots=[]),
        secrets=(),
        sources=[],
        completed_tool_calls=3,
        task_id=None,
        autonomous=autonomous,
        emit=emit,
    )


def _task(db, user_id, chat_id, status):
    row = LocalTask(
        user_id=user_id,
        chat_id=chat_id,
        status=status,
        current_phase=status,
        title="T4L",
        original_user_request="В папке X открой t4l.txt",
        facts={},
    )
    db.add(row)
    db.commit()
    return row


def _events(db, task_id):
    return [
        item.kind
        for item in db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id)).all()
    ]


def test_a_retryable_timeout_while_waiting_for_confirmation_returns_to_executing(client, auth):
    """The task must leave the parked state through a legal edge, not sit there forever."""
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    context = _context(user_id, chat_id)
    controller = LocalTaskController()
    with SessionLocal() as db:
        row = _task(db, user_id, chat_id, machine.WAITING_CONFIRMATION)
        context.task_id = row.id

        controller.observe_tool(db, context, "search_files", {"error": "timeout"}, error="timeout")
        db.commit()

        stored = db.get(LocalTask, row.id)
        assert stored is not None
        assert stored.status == machine.EXECUTING, stored.status
        kinds = _events(db, row.id)
        assert "TOOL_COMPLETED" in kinds
        assert "RETRY" in kinds
        # The guard is a diagnostic, not the mechanism: a legal target must not need it.
        assert "TRANSITION_REJECTED" not in kinds, kinds


def test_the_task_can_still_complete_after_that_error(client, auth):
    """Reaching EXECUTING is only useful if the turn can then conclude the task."""
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    context = _context(user_id, chat_id)
    controller = LocalTaskController()
    with SessionLocal() as db:
        row = _task(db, user_id, chat_id, machine.WAITING_CONFIRMATION)
        context.task_id = row.id
        controller.observe_tool(db, context, "search_files", {"error": "timeout"}, error="timeout")
        db.commit()

        controller.conclude(db, context)
        db.commit()

        stored = db.get(LocalTask, row.id)
        assert stored is not None
        assert stored.status == machine.COMPLETED, stored.status
        assert "COMPLETED" in _events(db, row.id)


def test_a_completed_task_is_never_resurrected_by_a_late_error(client, auth):
    """Negative case: the guard must keep a terminal task terminal, not move it anywhere."""
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    context = _context(user_id, chat_id)
    controller = LocalTaskController()
    with SessionLocal() as db:
        row = _task(db, user_id, chat_id, machine.COMPLETED)
        context.task_id = row.id

        # A late delivery of the same failure must not raise and must not change the state.
        controller.observe_tool(db, context, "read_file", {"error": "timeout"}, error="timeout")
        db.commit()

        stored = db.get(LocalTask, row.id)
        assert stored is not None
        assert stored.status == machine.COMPLETED
        assert "TRANSITION_REJECTED" in _events(db, row.id)


def test_observing_the_same_failure_twice_is_stable(client, auth):
    """Idempotency: a retried delivery must not corrupt the state or the journal."""
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    context = _context(user_id, chat_id)
    controller = LocalTaskController()
    with SessionLocal() as db:
        row = _task(db, user_id, chat_id, machine.WAITING_CONFIRMATION)
        row.retry_budget = 5
        db.commit()
        context.task_id = row.id
        for _ in range(3):
            controller.observe_tool(db, context, "search_files", {"error": "timeout"}, error="timeout")
            db.commit()
        stored = db.get(LocalTask, row.id)
        assert stored is not None
        # Working states are the honest outcome; the point is that no edge is refused and no
        # failure is swallowed.
        assert stored.status in {machine.EXECUTING, machine.RETRYING}, stored.status
        assert stored.retry_count == 3
        assert "TRANSITION_REJECTED" not in _events(db, row.id)


def test_a_tool_timeout_returns_to_the_model_even_when_the_task_is_parked(client, auth):
    """End to end through the orchestrator: the model must receive `status=timeout`."""
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    context = _context(user_id, chat_id)

    class TimeoutExecutor:
        async def execute(self, name, arguments, ctx, origin="model"):
            raise ToolError("timeout")

    with SessionLocal() as db:
        row = _task(db, user_id, chat_id, machine.WAITING_CONFIRMATION)
        context.task_id = row.id

    orchestrator = ToolOrchestrator(registry=None, executor=TimeoutExecutor())
    notes: list[str] = []
    packed = asyncio.run(
        orchestrator._run(
            "search_files", json.dumps({"root": "C:\\work", "query": "x"}), context, notes, "model"
        )
    )
    assert packed["status"] == "timeout"
