"""A rejected task transition must never kill the turn.

The live T4 run of 2026-11-11 ended in `generation_failed type=ToolError` because a retryable
tool timeout arrived while the task was waiting for a confirmation: `observe_tool` tried
WAITING_CONFIRMATION -> RETRYING, the state machine refused it, and the refusal escaped as
`invalid_task_transition` into the chat stream. The user saw an empty `error` message although
the product had done the right things (typed state, recovery, one task, no repeated payloads).
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


def _context(user_id, chat_id, generation_id):
    async def emit(event, value):
        return None

    return SimpleNamespace(
        user_id=user_id,
        chat_id=chat_id,
        generation_id=generation_id,
        user_prompt="В папке X открой t4l.txt",
        limits=SimpleNamespace(calls=4, max_calls=24, max_seconds=600, remaining=600),
        settings=SimpleNamespace(workspace_roots=[]),
        secrets=(),
        sources=[],
        completed_tool_calls=3,
        task_id=None,
        emit=emit,
    )


def test_a_retryable_error_while_waiting_for_confirmation_is_recorded_not_raised(client, auth):
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    context = _context(user_id, chat_id, None)
    controller = LocalTaskController()
    with SessionLocal() as db:
        row = LocalTask(
            user_id=user_id,
            chat_id=chat_id,
            status=machine.WAITING_CONFIRMATION,
            current_phase=machine.WAITING_CONFIRMATION,
            title="T4L",
            original_user_request=context.user_prompt,
            facts={},
        )
        db.add(row)
        db.commit()
        task_id = row.id
        context.task_id = task_id

        # The timeout arrives while the confirmation is still pending.
        controller.observe_tool(db, context, "search_files", {"error": "timeout"}, error="timeout")
        db.commit()

        stored = db.get(LocalTask, task_id)
        assert stored is not None
        assert stored.status == machine.WAITING_CONFIRMATION, stored.status
        kinds = {
            item.kind
            for item in db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id)).all()
        }
        assert "TOOL_COMPLETED" in kinds
        assert "TRANSITION_REJECTED" in kinds, kinds


def test_a_tool_timeout_returns_to_the_model_even_when_the_task_is_parked(client, auth):
    """End to end through the orchestrator: the model must receive `status=timeout`."""
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    context = _context(user_id, chat_id, None)

    class TimeoutExecutor:
        async def execute(self, name, arguments, ctx, origin="model"):
            raise ToolError("timeout")

    with SessionLocal() as db:
        row = LocalTask(
            user_id=user_id,
            chat_id=chat_id,
            status=machine.WAITING_CONFIRMATION,
            current_phase=machine.WAITING_CONFIRMATION,
            title="T4L",
            original_user_request=context.user_prompt,
            facts={},
        )
        db.add(row)
        db.commit()
        context.task_id = row.id

    orchestrator = ToolOrchestrator(registry=None, executor=TimeoutExecutor())
    notes: list[str] = []
    packed = asyncio.run(
        orchestrator._run("search_files", json.dumps({"root": "C:\\work", "query": "x"}), context, notes, "model")
    )
    assert packed["status"] == "timeout"
