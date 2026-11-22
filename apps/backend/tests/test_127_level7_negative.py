"""LEVEL 7 negative matrix, part 2: missing checkpoint and a restarted verifier.

The other rows already exist: duplicate/completed actions (`progress.should_block` ->
`already_applied`), an interrupted tool run and a stale claim (`test_127_recovery_matrix.py`), a lost
model connection (`WAITING_LLM` in the same matrix), and double completion (`finish()` records one
`COMPLETED`). Budget termination is covered by `test_batch_budget.py`. This file pins the two rows
that had no test: a task whose checkpoint is gone must not be claimed as recoverable success, and a
verifier that is interrupted must be safely re-runnable.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.agent.verifier import Verifier
from app.database import SessionLocal
from app.models import User
from app.tools.local import machine
from app.tools.local.task import LocalTaskController
from app.tools.models import LocalTask, TaskEvent


def _user(db) -> User:
    row = db.scalar(select(User).where(User.email == "level7-negative@example.com"))
    if row is None:
        row = User(email="level7-negative@example.com", password_hash="test-only")
        db.add(row)
        db.commit()
    return row


def _interrupted(db, user) -> LocalTask:
    row = LocalTask(
        user_id=user.id,
        status=machine.INTERRUPTED,
        current_phase=machine.INTERRUPTED,
        title="no checkpoint",
        original_user_request="finish the thing",
        checkpoint=None,
        verification={},
    )
    db.add(row)
    db.commit()
    return row


def test_a_task_without_a_checkpoint_is_recoverable_but_not_complete():
    with SessionLocal() as db:
        user = _user(db)
        task = _interrupted(db, user)
        task_id = task.id
        LocalTaskController().resume(db, task, user.id)

    with SessionLocal() as db:
        row = db.get(LocalTask, task_id)
        assert row is not None
        assert row.status == machine.READY, "a lost checkpoint must not become a finished task"
        assert row.finished_at is None
        assert LocalTaskController()._verified(row) is False
        kinds = [event.kind for event in db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id))]
        assert "RESUMED" in kinds
        assert "COMPLETED" not in kinds


def test_the_verifier_is_runnable_after_an_interruption_and_changes_nothing(tmp_path: Path):
    target = tmp_path / "artifact.txt"
    target.write_text("canalla", encoding="utf-8")
    before = target.read_bytes()
    checks = [
        {"kind": "file_exists", "target": str(target)},
        {"kind": "file_contains", "target": str(target), "contains": "canalla"},
    ]
    verifier = Verifier()
    first = verifier.run(checks)
    # A process that died mid-verification is replaced by a new one: the same checks, same files.
    second = Verifier().run(checks)
    assert first.ok is True and second.ok is True
    assert target.read_bytes() == before, "the verifier must never modify what it observes"
    first_items = [(item.kind, item.target, item.ok) for item in first.ledger]
    second_items = [(item.kind, item.target, item.ok) for item in second.ledger]
    assert first_items == second_items


def test_a_failed_check_cannot_be_turned_into_a_pass_by_repeating_it(tmp_path: Path):
    missing = tmp_path / "not-there.txt"
    checks = [{"kind": "file_exists", "target": str(missing)}]
    for _ in range(3):
        report = Verifier().run(checks)
        assert report.ok is False
        assert list(report.ledger)[-1].ok is False
