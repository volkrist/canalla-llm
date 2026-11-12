"""A durable task queue on the existing SQLite tables — no Redis, no Postgres, no second service.

The queue is the `local_tasks` table plus three columns (`queue_priority`, `claimed_by`,
`claimed_at`). A claim is an atomic conditional UPDATE, which is what SQLite serialises anyway:
two workers that race for the same row cannot both observe `rowcount == 1`.

`QUEUED` is a real state in the existing machine, so a queued task is not a task that "has not
started" — it is a row that survives a restart and is claimable exactly once.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update

from ..tools.local import machine
from ..tools.models import LocalTask


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(
    db,
    *,
    user_id: str,
    prompt: str,
    workspace: str = "",
    chat_id: str | None = None,
    title: str = "",
    priority: int = 0,
    depends_on: list[str] | None = None,
    time_budget_seconds: int = 1800,
    cost_budget_usd: float = 0.0,
) -> LocalTask:
    """Create a durable, claimable task. The request is stored verbatim; nothing is normalised away."""
    row = LocalTask(
        user_id=user_id,
        chat_id=chat_id,
        workspace=workspace,
        title=(title or prompt)[:200],
        original_user_request=prompt,
        normalized_goal="",
        status=machine.QUEUED,
        current_phase=machine.QUEUED,
        current_step="",
        queue_priority=int(priority),
        depends_on=list(depends_on or []),
        time_budget_seconds=int(time_budget_seconds),
        cost_budget_usd=float(cost_budget_usd),
        facts={
            "queue": {
                "enqueued_at": _now().isoformat(timespec="seconds"),
                "depends_on": list(depends_on or []),
            }
        },
    )
    db.add(row)
    db.commit()
    return row


def pending(db, *, user_id: str | None = None, limit: int = 50) -> list[LocalTask]:
    query = select(LocalTask).where(LocalTask.status == machine.QUEUED)
    if user_id:
        query = query.where(LocalTask.user_id == user_id)
    query = query.order_by(LocalTask.queue_priority.desc(), LocalTask.started_at).limit(limit)
    return list(db.scalars(query).all())


def claim_next(db, worker_id: str, *, now: datetime | None = None) -> LocalTask | None:
    """Claim one task. Returns None when the queue is empty or every task is still blocked."""
    stamp = now or _now()
    for row in pending(db, limit=20):
        blocked = [
            dependency for dependency in (row.depends_on or []) if not _dependency_done(db, dependency)
        ]
        if blocked:
            continue
        result = db.execute(
            update(LocalTask)
            .where(LocalTask.id == row.id, LocalTask.status == machine.QUEUED)
            .values(
                status=machine.PLANNING,
                current_phase=machine.PLANNING,
                claimed_by=str(worker_id),
                claimed_at=stamp,
                updated_at=stamp,
            )
        )
        db.commit()
        if result.rowcount == 1:
            db.refresh(row)
            return row
    return None


def release(db, task: LocalTask | str, *, status: str = machine.QUEUED, reason: str = "") -> LocalTask | None:
    """Put a task back (or into a named state) without losing its plan or evidence."""
    task_id = task if isinstance(task, str) else task.id
    row = db.get(LocalTask, task_id)
    if row is None:
        return None
    machine.transition(row, status)
    row.claimed_by = None
    row.claimed_at = None
    if reason:
        facts = dict(row.facts or {})
        queue = dict(facts.get("queue") or {})
        queue["last_release_reason"] = reason[:200]
        facts["queue"] = queue
        row.facts = facts
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return row


def recover_stale_claims(db, *, older_than_seconds: int = 900) -> list[str]:
    """Return tasks whose worker disappeared to the queue; never duplicate a completed step.

    Only a claim moves back: the task keeps its plan, checkpoints and evidence, so the next worker
    resumes from the checkpoint instead of repeating the work.
    """
    cutoff = _now() - timedelta(seconds=max(60, int(older_than_seconds)))
    rows = list(
        db.scalars(
            select(LocalTask).where(
                LocalTask.status == machine.PLANNING,
                LocalTask.claimed_at.is_not(None),
                LocalTask.claimed_at < cutoff,
            )
        ).all()
    )
    recovered = []
    for row in rows:
        machine.transition(row, machine.QUEUED)
        row.claimed_by = None
        row.claimed_at = None
        row.updated_at = _now()
        recovered.append(row.id)
    if recovered:
        db.commit()
    return recovered


def stats(db, *, user_id: str | None = None) -> dict[str, Any]:
    rows = db.scalars(select(LocalTask)).all()
    if user_id:
        rows = [row for row in rows if row.user_id == user_id]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return {"total": len(rows), "by_status": counts}


def _dependency_done(db, task_id: str) -> bool:
    row = db.get(LocalTask, str(task_id))
    return bool(row and row.status in {machine.COMPLETED})
