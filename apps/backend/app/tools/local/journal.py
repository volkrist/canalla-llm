"""Persistent task journal. Payloads are bounded and secret-redacted."""

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
    "CONFLICT",
    "BUDGET_EXHAUSTED",
}


def append_event(db, task_id, kind, payload=None, secrets=(), tool_run_id=None):
    data = {}
    for key, value in (payload or {}).items():
        if key in {"secret", "password", "token", "credential", "api_key"}:
            continue
        text = sanitized(str(value), secrets, 500 if key != "summary" else 1200)
        data[key] = text
    row = TaskEvent(task_id=task_id, kind=kind, payload=data, tool_run_id=tool_run_id, created_at=now())
    db.add(row)
    return row
