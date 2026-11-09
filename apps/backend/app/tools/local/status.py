"""One authoritative, user-facing description of an autonomous task.

The defect this module removes: the UI used to read three independent sources — the SSE stream
lifetime ("Модель обрабатывает запрос… 680 с"), a step counter ("3/3 steps") and a per-tool
family label ("Завершено") — and each of them could claim a different thing about the same
task. A step counter is plan progress, a finished tool family is *that family's* progress, and
a live stream is transport. None of them is the task outcome.

Everything here is derived from the persisted row, its steps and its journal, so a relaunch
renders exactly what the backend knows. ``phase`` is the single value the UI switches on; the
remaining fields exist so the UI can explain it without inventing anything.
"""

from datetime import datetime, timezone

from ...models import now
from ..models import ToolRun
from . import machine

DONE_STEPS = {"COMPLETED", "SKIPPED", "CANCELLED"}

# Compute readiness is not a model outage. `waiting_llm` with one of these codes means the
# session the user is allowed to start does not exist yet; the task waits for it, it is not
# retrying a dead provider.
COMPUTE_WAIT_CODES = {
    "offline",
    "gateway_not_connected",
    "loading_model",
    "creating",
    "starting_pod",
    "gpu_found",
    "mounting_storage",
    "starting",
}

PHASES = {
    machine.CREATED: "queued",
    machine.PLANNING: "planning",
    machine.READY: "ready",
    machine.INSPECTING: "inspecting",
    machine.RESEARCHING: "researching",
    machine.EXECUTING: "executing",
    machine.VERIFYING: "verifying",
    machine.RETRYING: "retrying",
    machine.RECOVERING: "recovering",
    machine.WAITING_CONFIRMATION: "waiting_permission",
    machine.WAITING_DEVICE: "waiting_device",
    machine.WAITING_WORKSPACE: "waiting_workspace",
    machine.WAITING_LLM: "waiting_model",
    machine.PAUSED: "paused",
    machine.STOPPING: "stopping",
    machine.STOPPED: "cancelled",
    machine.COMPLETED: "completed",
    machine.FAILED: "failed",
    machine.INTERRUPTED: "interrupted",
    machine.CONFLICT: "conflict",
}

# A phase that advances on its own, and how long it may legitimately stay silent before the UI
# says so out loud. A waiting state is bounded by its own timeout, not by this table.
STALL_AFTER_SECONDS = {
    "planning": 120,
    "inspecting": 600,
    "researching": 600,
    "executing": 900,
    "verifying": 900,
    "retrying": 300,
    "recovering": 300,
    "waiting_model": 180,
}

TERMINAL_PHASES = {"completed", "failed", "partial", "cancelled"}
WAITING_PHASES = {
    "waiting_permission",
    "waiting_device",
    "waiting_workspace",
    "waiting_model",
    "waiting_resource",
}
WORKING_PHASES = {
    "planning",
    "inspecting",
    "researching",
    "executing",
    "verifying",
    "retrying",
    "recovering",
    "ready",
    "queued",
    "conflict",
    "paused",
    "stopping",
}


def utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def phase_of(row, facts: dict | None = None, worked: bool = False) -> str:
    status = getattr(row, "status", "") or ""
    facts = facts if facts is not None else (getattr(row, "facts", None) or {})
    if status == machine.WAITING_LLM and (
        facts.get("waiting_for_compute") or (getattr(row, "last_error", "") or "") in COMPUTE_WAIT_CODES
    ):
        return "waiting_resource"
    phase = PHASES.get(status, str(status or "").lower() or "unknown")
    if status == machine.FAILED and phase == "failed":
        # A failed task that really changed something is partial, not empty. Calling it "failed"
        # hid the completed work; calling it "completed" would be a lie. A *skipped* step is not
        # completed work: a plan whose steps were all skipped did nothing at all.
        used = int(getattr(row, "tool_calls_used", 0) or 0)
        if worked or used > 0:
            return "partial"
    return phase


def describe(row, steps, last_event_at: datetime | None = None, reference: datetime | None = None):
    """Return the authoritative status payload. Pure: no I/O, no clock except ``reference``."""
    current = reference or now()
    facts = row.facts or {}
    done = [step for step in steps if step.status in DONE_STEPS]
    pending = [step for step in steps if step.status not in DONE_STEPS]
    worked = any(step.status == "COMPLETED" for step in steps)
    phase = phase_of(row, facts, worked=worked)
    running = next((step for step in steps if step.status == "RUNNING"), None)

    updated = utc(getattr(row, "updated_at", None))
    event_at = utc(last_event_at)
    marks = [value for value in (updated, event_at) if value is not None]
    last_progress = max(marks) if marks else utc(getattr(row, "started_at", None))
    idle_seconds = int(max(0, (current - last_progress).total_seconds())) if last_progress else 0

    if running is not None:
        action = running.title
    elif pending:
        action = pending[0].title
    elif getattr(row, "current_step", ""):
        action = str(row.current_step)
    else:
        action = ""

    stall_after = STALL_AFTER_SECONDS.get(phase)
    stopped = bool(getattr(row, "stop_requested", False))
    error_code = getattr(row, "last_error", None) or None
    if phase == "cancelled":
        stop_reason = "user" if stopped else (facts.get("stop_reason") or "cancelled")
    elif phase in {"failed", "partial", "interrupted"}:
        stop_reason = error_code or phase
    else:
        stop_reason = None

    terminal = phase in TERMINAL_PHASES
    recoverable = bool(getattr(row, "status", "") in machine.RECOVERABLE)
    # STOPPED and FAILED are terminal *and* resumable: the remaining work is still on disk.
    resume_available = bool(recoverable and (pending or not steps))

    return {
        "phase": phase,
        "terminal": terminal,
        "waiting": phase in WAITING_PHASES,
        "current_action": action,
        "completed_actions": [step.title for step in done][:20],
        "remaining_actions": [step.title for step in pending][:20],
        "completed_count": len(done),
        "worked_count": sum(1 for step in steps if step.status == "COMPLETED"),
        "remaining_count": len(pending),
        "last_progress_at": last_progress.isoformat() if last_progress else None,
        "idle_seconds": idle_seconds,
        "stall_after_seconds": stall_after,
        # Only a phase that owes progress on its own can be stalled. Waiting for the user, the
        # device or the workspace is not a stall, and a step counter cannot override this.
        "stalled": bool(stall_after is not None and idle_seconds >= stall_after),
        "stop_reason": stop_reason,
        "error_code": error_code,
        # The last refused call, with its real counters. `None` until a budget actually ran out.
        "limit": facts.get("last_limit") or None,
        "recoverable": recoverable,
        "resume_available": resume_available,
        # Budgets the user can act on, with the name of the thing that is capped.
        "budgets": {
            "tools_used": int(getattr(row, "tool_calls_used", 0) or 0),
            "tools_max": int(getattr(row, "tool_budget", 0) or 0),
            "files_changed": int(getattr(row, "files_changed", 0) or 0),
            "files_max": int(getattr(row, "file_change_budget", 0) or 0),
            "seconds_used": int(getattr(row, "elapsed_runtime", 0) or 0),
            "seconds_max": int(getattr(row, "runtime_budget", 0) or 0),
        },
    }


def web_accounting(db, task_id: str | None) -> dict:
    """Count what really happened on the web for this task, split by who asked.

    ``model`` is the only origin that counts as autonomous model work; ``server_policy`` runs are
    the controller's own follow-ups and must never be presented as the model's decisions.
    """
    empty = {
        "searches": 0,
        "fetches": 0,
        "tor_searches": 0,
        "tor_fetches": 0,
        "pages_requested": 0,
        "pages_opened": 0,
        "pages_failed": 0,
        "by_origin": {"model": 0, "server_policy": 0, "other": 0},
    }
    if not task_id:
        return empty
    rows = db.query(ToolRun).filter(ToolRun.task_id == task_id).all()
    result = {**empty, "by_origin": dict(empty["by_origin"])}
    for run in rows:
        origin = run.origin or "model"
        if origin not in result["by_origin"]:
            origin = "other"
        result["by_origin"][origin] += 1
        if run.status != "completed":
            continue
        if run.tool_name == "web_search":
            result["searches"] += 1
        elif run.tool_name == "web_fetch":
            result["fetches"] += 1
        elif run.tool_name == "tor_search":
            result["tor_searches"] += 1
        elif run.tool_name == "tor_fetch":
            result["tor_fetches"] += 1
        accounting = (run.result_metadata or {}).get("web") or {}
        result["pages_requested"] += int(accounting.get("requested") or 0)
        result["pages_opened"] += int(accounting.get("opened") or 0)
        result["pages_failed"] += int(accounting.get("failed") or 0)
    return result
