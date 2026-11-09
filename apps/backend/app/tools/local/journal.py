"""Persistent task journal. Payloads are bounded and secret-redacted."""

from datetime import timedelta, timezone

from sqlalchemy import func, select

from ...models import now
from ..models import TaskEvent
from ..security import sanitized

KINDS = {
    "TASK_CREATED",
    "PLAN_CREATED",
    "PLAN_UPDATED",
    "STEP_STARTED",
    "STEP_COMPLETED",
    "STEP_FAILED",
    "TOOL_REQUESTED",
    "TOOL_COMPLETED",
    "FILE_CHANGED",
    "CONFIRMATION_REQUESTED",
    "CONFIRMATION_APPROVED",
    "CONFIRMATION_DENIED",
    "CHECKPOINT_CREATED",
    "VERIFY_STARTED",
    "VERIFY_FAILED",
    "VERIFY_PASSED",
    "RETRY",
    "PAUSED",
    "RESUMED",
    "STOPPED",
    "COMPLETED",
    "FAILED",
    "INTERRUPTED",
    "WAITING_DEVICE",
    "WAITING_LLM",
    "WAITING_WORKSPACE",
    "CONFLICT",
    "BUDGET_EXHAUSTED",
    "BUDGET_UPDATED",
    "TINYFISH_AGENT_STARTED",
    "TINYFISH_AGENT_STEP",
    "TINYFISH_AGENT_COMPLETED",
    "TINYFISH_AGENT_CANCELLED",
    "TINYFISH_BROWSER_STARTED",
    "TINYFISH_BROWSER_CONNECTED",
    "TINYFISH_BROWSER_CLOSED",
}


def append_event(db, task_id, kind, payload=None, secrets=(), tool_run_id=None):
    data = {}
    for key, value in (payload or {}).items():
        if key in {
            "secret",
            "password",
            "token",
            "credential",
            "api_key",
            "cdp_url",
            "cdp",
            "cookie",
            "cookies",
            "vault",
            "authorization",
        }:
            continue
        text = sanitized(str(value), secrets, 500 if key != "summary" else 1200)
        data[key] = text
    # The timeline is shown with exact times, so it must be totally ordered. Two events written in
    # the same microsecond used to tie, and the read ordered them by a random id: the panel could
    # show «проверка» before the «файл создан» that produced it. Stamps advance monotonically.
    stamp = now()
    last = None
    if hasattr(db, "scalar"):
        last = db.scalar(select(func.max(TaskEvent.created_at)).where(TaskEvent.task_id == task_id))
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if stamp <= last:
            stamp = last + timedelta(milliseconds=1)
    row = TaskEvent(task_id=task_id, kind=kind, payload=data, tool_run_id=tool_run_id, created_at=stamp)
    db.add(row)
    return row
