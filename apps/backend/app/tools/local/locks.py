"""Exclusive workspace write lock for autonomous tasks."""

from sqlalchemy import select

from ...models import now
from ..contracts import ToolError
from ..models import LocalTask, WorkspaceLock
from .machine import TERMINAL


def normalize_workspace(path: str) -> str:
    return (path or "").replace("/", "\\").rstrip("\\").casefold()


def busy_writer(db, workspace: str, task_id: str | None = None):
    key = normalize_workspace(workspace)
    if not key:
        return None
    row = db.get(WorkspaceLock, key)
    if not row:
        return None
    owner = db.get(LocalTask, row.task_id)
    if owner and owner.status not in TERMINAL and owner.id != task_id:
        return owner
    return None


def acquire_write(db, user_id: str, workspace: str, task_id: str):
    key = normalize_workspace(workspace)
    if not key:
        return None
    owner = busy_writer(db, workspace, task_id)
    if owner:
        raise ToolError("workspace_busy")
    row = db.get(WorkspaceLock, key)
    if row:
        row.task_id = task_id
        row.user_id = user_id
        row.kind = "WRITE"
        row.created_at = now()
        db.commit()
        return row
    lock = WorkspaceLock(workspace=key, task_id=task_id, user_id=user_id, kind="WRITE")
    db.add(lock)
    db.commit()
    return lock


def release(db, task_id: str):
    rows = db.scalars(select(WorkspaceLock).where(WorkspaceLock.task_id == task_id)).all()
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
