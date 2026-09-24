"""Exclusive workspace write lock plus a persistent FIFO waiter queue."""

import os

from sqlalchemy import func, select

from ...models import now
from ..contracts import ToolError
from ..models import LocalTask, WorkspaceLock, WorkspaceWaiter
from .journal import append_event
from .machine import PAUSED, READY, RECOVERING, TERMINAL, WAITING_WORKSPACE, can_transition, transition
from .paths import native_path


def normalize_workspace(path: str) -> str:
    value = native_path(path).strip()
    if not value:
        return ""
    return os.path.normcase(os.path.normpath(value))


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


def owns_write(db, workspace: str, task_id: str) -> bool:
    key = normalize_workspace(workspace)
    if not key or not task_id:
        return False
    row = db.get(WorkspaceLock, key)
    return bool(row and row.task_id == task_id)


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


def enqueue_write(db, user_id: str, workspace: str, task_id: str):
    key = normalize_workspace(workspace)
    if not key:
        return None
    existing = db.scalar(select(WorkspaceWaiter).where(WorkspaceWaiter.task_id == task_id))
    if existing:
        return existing
    maximum = db.scalar(select(func.max(WorkspaceWaiter.position)).where(WorkspaceWaiter.workspace == key))
    waiter = WorkspaceWaiter(
        workspace=key,
        task_id=task_id,
        user_id=user_id,
        position=int(maximum or 0) + 1,
        requested_at=now(),
    )
    db.add(waiter)
    db.commit()
    return waiter


def waiter_position(db, task_id: str) -> int | None:
    row = db.scalar(select(WorkspaceWaiter).where(WorkspaceWaiter.task_id == task_id))
    if not row:
        return None
    earlier = db.scalar(
        select(func.count())
        .select_from(WorkspaceWaiter)
        .where(
            WorkspaceWaiter.workspace == row.workspace,
            WorkspaceWaiter.requested_at < row.requested_at,
        )
    )
    return int(earlier or 0) + 1


def _drop_waiter(db, task_id: str):
    rows = db.scalars(select(WorkspaceWaiter).where(WorkspaceWaiter.task_id == task_id)).all()
    for row in rows:
        db.delete(row)


def promote(db, workspace: str):
    key = normalize_workspace(workspace)
    if not key:
        return None
    if busy_writer(db, key):
        return None
    waiters = db.scalars(
        select(WorkspaceWaiter)
        .where(WorkspaceWaiter.workspace == key)
        .order_by(WorkspaceWaiter.requested_at, WorkspaceWaiter.position, WorkspaceWaiter.id)
    ).all()
    for waiter in waiters:
        task = db.get(LocalTask, waiter.task_id)
        if not task or task.status in TERMINAL:
            db.delete(waiter)
            continue
        if task.status not in {WAITING_WORKSPACE, PAUSED}:
            db.delete(waiter)
            continue
        if task.status == PAUSED:
            continue
        try:
            acquire_write(db, waiter.user_id, key, waiter.task_id)
        except ToolError:
            return None
        db.delete(waiter)
        facts = dict(task.facts or {})
        facts["promoted_from_queue"] = True
        facts.pop("waiting_workspace", None)
        task.facts = facts
        if task.status == WAITING_WORKSPACE:
            if task.pause_requested:
                transition(task, PAUSED)
            else:
                if can_transition(task.status, RECOVERING):
                    transition(task, RECOVERING)
                transition(task, READY)
        append_event(db, task.id, "WORKSPACE_PROMOTED", {"workspace": key})
        db.commit()
        return task
    db.commit()
    return None


def release(db, task_id: str):
    rows = db.scalars(select(WorkspaceLock).where(WorkspaceLock.task_id == task_id)).all()
    workspaces = [row.workspace for row in rows]
    for row in rows:
        db.delete(row)
    _drop_waiter(db, task_id)
    if rows:
        db.commit()
    promoted = None
    for workspace in workspaces:
        promoted = promote(db, workspace) or promoted
    return promoted


def reconcile_locks(db):
    locks = db.scalars(select(WorkspaceLock)).all()
    stale = []
    for lock in locks:
        owner = db.get(LocalTask, lock.task_id)
        if not owner or owner.status in TERMINAL:
            stale.append(lock)
    workspaces = []
    for lock in stale:
        workspaces.append(lock.workspace)
        db.delete(lock)
    if stale:
        db.commit()
    for workspace in workspaces:
        promote(db, workspace)
    waiters = db.scalars(select(WorkspaceWaiter)).all()
    dropped = False
    for waiter in waiters:
        task = db.get(LocalTask, waiter.task_id)
        if not task or task.status in TERMINAL:
            db.delete(waiter)
            dropped = True
    if dropped:
        db.commit()
