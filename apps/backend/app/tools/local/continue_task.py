"""Promote WAITING_WORKSPACE → READY → execute without a new user message."""

from types import SimpleNamespace

from sqlalchemy import select

from ...database import SessionLocal
from ..executor import ExecutionContext
from ..models import LocalTask
from ..policy import ToolLimits, preferences
from . import machine
from .facts import bump_metric


def mark_auto_continue(task: LocalTask):
    facts = dict(task.facts or {})
    facts["auto_continue"] = True
    facts = bump_metric(facts, "queue_promotions")
    task.facts = facts


def pending(db, user_id: str | None = None):
    rows = db.scalars(select(LocalTask).where(LocalTask.status == machine.READY)).all()
    out = []
    for row in rows:
        if not (row.facts or {}).get("auto_continue"):
            continue
        if user_id and row.user_id != user_id:
            continue
        out.append(row)
    return out


def claim_pending(db, user_id: str | None = None) -> list[str]:
    ids = []
    for row in pending(db, user_id):
        facts = dict(row.facts or {})
        if not facts.get("auto_continue"):
            continue
        facts["auto_continue"] = False
        facts["task_continuation"] = True
        row.facts = facts
        ids.append(row.id)
    db.commit()
    return ids


async def continue_task(task_id: str, orchestrator, provider, *, host_online=True, emit=None):
    async def _emit(event, value):
        if emit:
            await emit(event, value)

    with SessionLocal() as db:
        row = db.get(LocalTask, task_id)
        if not row:
            return None
        from .devices import active_device

        settings = preferences(db, row.user_id)
        device = active_device(db, row.user_id)
        device_id = device.id if device else row.device_id
        if device:
            row.device_id = device.id
            db.commit()
        snapshot = SimpleNamespace(
            user_id=row.user_id,
            chat_id=row.chat_id,
            generation_id=row.generation_id,
            original=row.original_user_request,
            budget=row.tool_budget,
            computer_mode=getattr(settings, "computer_mode", "trusted") or "trusted",
            settings=settings,
            device_id=device_id,
        )
    events = []

    async def capture(event, value):
        events.append((event, value))
        await _emit(event, value)

    context = ExecutionContext(
        snapshot.user_id,
        snapshot.chat_id,
        snapshot.generation_id,
        ToolLimits(max_calls=min(snapshot.budget or 24, 40), hard_max_calls=40),
        capture,
        mode="off",
        computer_mode=snapshot.computer_mode,
        settings=snapshot.settings,
        user_prompt=snapshot.original,
        host_online=host_online,
        resume_task_id=task_id,
        resuming=True,
        autonomous=True,
        task_id=task_id,
    )
    context.assigned_device_id = snapshot.device_id
    history = [{"role": "user", "content": snapshot.original}]
    usage = {}
    try:
        await orchestrator.prepare(provider, history, 1, context, usage)
    except Exception:
        with SessionLocal() as db:
            row = db.get(LocalTask, task_id)
            if row and row.status == machine.READY:
                facts = dict(row.facts or {})
                facts["auto_continue"] = True
                row.facts = facts
                db.commit()
        raise
    return SimpleNamespace(context=context, events=events)


async def continue_pending(orchestrator, provider, *, user_id=None, host_online=True, emit=None):
    with SessionLocal() as db:
        ids = claim_pending(db, user_id)
    results = []
    for task_id in ids:
        result = await continue_task(task_id, orchestrator, provider, host_online=host_online, emit=emit)
        if result is not None:
            results.append(result)
    return results
