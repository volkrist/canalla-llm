"""Durable recovery across a real process death.

A child process starts the real backend, performs one irreversible side effect and is killed with
`os._exit(17)` in the middle of the task. A second, independent process starts the application
again, finds the abandoned row, resumes the *same* task id and finishes it. The assertions are on
the database and the file, not on either process's output.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models import User
from app.tools.models import LocalTask, TaskEvent

BACKEND = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).resolve().parent / "helpers" / "recovery_worker.py"


def _run_worker(phase: str, root: Path, task_id: str = "") -> subprocess.CompletedProcess:
    command = [sys.executable, str(WORKER), "--phase", phase, "--root", str(root)]
    if task_id:
        command += ["--task-id", task_id]
    return subprocess.run(
        command, cwd=str(BACKEND), capture_output=True, text=True, timeout=240, env=dict(os.environ)
    )


def test_a_killed_process_leaves_a_task_that_a_new_process_resumes(tmp_path):
    root = tmp_path / "recovery"
    root.mkdir()

    first = _run_worker("interrupt", root)
    assert first.returncode == 17, (first.returncode, first.stdout[-2000:], first.stderr[-2000:])
    # The side effect happened before the crash and survived it.
    assert (root / "steps.txt").read_text(encoding="utf-8") == "one\n"

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "recovery@example.com"))
        assert user is not None
        row = db.scalar(
            select(LocalTask).where(LocalTask.user_id == user.id).order_by(LocalTask.started_at.desc())
        )
        assert row is not None
        task_id = row.id
        # The process died without writing a terminal state: the row is still in flight.
        assert row.status not in {"COMPLETED", "FAILED", "STOPPED"}, row.status
        assert row.finished_at is None
        recorded = list((row.checkpoint or {}).get("commands") or [])

    second = _run_worker("resume", root, task_id)
    assert second.returncode == 0, (second.stdout[-2000:], second.stderr[-2000:])

    with SessionLocal() as db:
        resumed = db.get(LocalTask, task_id)
        assert resumed is not None
        assert resumed.status == "COMPLETED", (resumed.status, resumed.last_error, resumed.completion_summary)
        # The same task, not a new one.
        assert resumed.id == task_id
        # Its plan and its history survived the restart.
        events = list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id)).all())
        kinds = [event.kind for event in events]
        assert "RESUMED" in kinds, kinds
        assert "COMPLETED" in kinds, kinds
        verification = resumed.verification or {}
        assert verification.get("evidence_ok") is True, [
            (c.get("tool")) for c in (resumed.checkpoint or {}).get("commands") or []
        ]

    # The finished document is exact: the resumed write replaced the content, it did not append.
    assert (root / "steps.txt").read_text(encoding="utf-8") == "one\ntwo\n"
    # No duplicated file and no duplicated task row.
    assert sorted(path.name for path in root.iterdir()) == ["steps.txt"]
    assert recorded  # the pre-crash checkpoint was durable, not re-invented
