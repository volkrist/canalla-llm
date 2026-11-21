"""The recovery matrix: each row is one controlled failure, one expected safe state.

These are mechanics, not a model run. A row proves that the controller survives a specific kind of
interruption without losing the task, repeating an irreversible step or inventing a second
completion. The live counterpart (a real model resuming) is what LEVEL 7 still needs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Chat, User
from app.tools.executor import reconcile_tools
from app.tools.local import machine
from app.tools.local.task import recover_interrupted
from app.tools.models import LocalTask, TaskEvent, ToolRun


def _user(db) -> User:
    row = db.scalar(select(User).where(User.email == "recovery-matrix@example.com"))
    if row is None:
        row = User(email="recovery-matrix@example.com", password_hash="test-only")
        db.add(row)
        db.commit()
    return row


def _chat(db, user) -> Chat:
    row = Chat(user_id=user.id, title="matrix")
    db.add(row)
    db.commit()
    return row


def _task(db, user, status: str, **extra) -> LocalTask:
    row = LocalTask(
        user_id=user.id,
        status=status,
        current_phase=status,
        title="matrix",
        original_user_request="finish the matrix",
        **extra,
    )
    db.add(row)
    db.commit()
    return row


def _events(db, task_id: str) -> list[str]:
    rows = db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id)).all()
    return [row.kind for row in rows]


def test_a_tool_run_that_never_returned_is_failed_not_replayed():
    """A backend restart in the middle of a call must not leave a call 'in flight' forever."""
    with SessionLocal() as db:
        user = _user(db)
        chat = _chat(db, user)
        task = _task(db, user, machine.EXECUTING)
        run = ToolRun(
            user_id=user.id,
            chat_id=chat.id,
            tool_name="delete_file",
            provider="local_device",
            risk_level="SENSITIVE",
            status="planning",
            input_digest="d" * 64,
            input_summary={"path": "important.txt"},
        )
        db.add(run)
        db.commit()
        task_id, run_id = task.id, run.id

    reconcile_tools()

    with SessionLocal() as db:
        run = db.get(ToolRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error_code == "backend_interrupted"
        assert run.finished_at is not None
        task = db.get(LocalTask, task_id)
        assert task is not None
        assert task.status == machine.INTERRUPTED, "an in-flight task must be resumable, not silently done"
        assert task.finished_at is None


def test_a_task_parked_on_inference_is_not_overwritten_by_startup_recovery():
    """`WAITING_LLM` is recoverable: the reconciliation must leave it for the retry window."""
    with SessionLocal() as db:
        user = _user(db)
        task = _task(db, user, machine.WAITING_LLM, last_error="llm_unavailable")
        task_id = task.id

    recover_interrupted()

    with SessionLocal() as db:
        task = db.get(LocalTask, task_id)
        assert task is not None
        assert task.status == machine.WAITING_LLM
        assert task.last_error == "llm_unavailable"


def test_a_stale_claim_returns_to_the_queue_with_its_checkpoint_intact():
    with SessionLocal() as db:
        user = _user(db)
        task = _task(
            db,
            user,
            machine.PLANNING,
            claimed_by="dead-worker",
            claimed_at=datetime.now(timezone.utc) - timedelta(hours=2),
            checkpoint={"digests_completed": ["a" * 64], "commands": [{"tool": "write_file"}]},
        )
        task_id = task.id

    with SessionLocal() as db:
        from app.agent import queue as agent_queue

        recovered = agent_queue.recover_stale_claims(db, older_than_seconds=60)
        assert recovered == [task_id]
        task = db.get(LocalTask, task_id)
        assert task is not None
        assert task.status == machine.QUEUED
        assert task.claimed_by is None
        # The completed action is still remembered, so the next worker does not repeat it.
        assert (task.checkpoint or {}).get("digests_completed") == ["a" * 64]
        assert task.original_user_request == "finish the matrix"


def test_finishing_twice_records_one_completion():
    with SessionLocal() as db:
        user = _user(db)
        task = _task(db, user, machine.EXECUTING)
        task_id = task.id

    class Context:
        user_id = None
        task_id = None
        generation_id = None
        secrets = ()
        promoted_task_id = None

    with SessionLocal() as db:
        from app.tools.local.task import LocalTaskController

        context = Context()
        context.user_id = db.get(LocalTask, task_id).user_id
        context.task_id = task_id
        LocalTaskController().finish(db, context, "COMPLETED")
        LocalTaskController().finish(db, context, "COMPLETED")

    with SessionLocal() as db:
        kinds = _events(db, task_id)
        assert kinds.count("COMPLETED") == 1, kinds
        task = db.get(LocalTask, task_id)
        assert task is not None
        assert task.status == machine.COMPLETED


def test_recovery_never_turns_a_paused_task_into_an_interrupted_one():
    with SessionLocal() as db:
        user = _user(db)
        paused = _task(db, user, machine.PAUSED)
        paused_id = paused.id

    recover_interrupted()

    with SessionLocal() as db:
        row = db.get(LocalTask, paused_id)
        assert row is not None
        assert row.status == machine.PAUSED, "a user pause is a decision, not a crash"
