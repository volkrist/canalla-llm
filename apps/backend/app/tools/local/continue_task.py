"""Promote WAITING_WORKSPACE → READY → execute without a new user message."""

import logging
from types import SimpleNamespace

from sqlalchemy import select

from ...context_builder import ContextBuilder
from ...database import SessionLocal
from ...models import Chat, Message, User, now
from ...providers import LLMError
from ..executor import ExecutionContext
from ..models import LocalTask
from ..policy import ToolLimits, preferences
from . import machine
from .facts import bump_metric
from .public_text import public_assistant_text
from .task import LocalTaskController, llm_retry_due

logger = logging.getLogger(__name__)


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
    # The answer is generated after the tool phase, so the task can still park on a fresh
    # LLM failure instead of being concluded before its answer exists.
    context.defer_conclusion = True
    context.assigned_device_id = snapshot.device_id
    history = _answer_history(task_id, snapshot.original)
    usage = {}
    try:
        enriched = await orchestrator.prepare(provider, history, 1, context, usage)
    except Exception:
        with SessionLocal() as db:
            row = db.get(LocalTask, task_id)
            if row and row.status == machine.READY:
                facts = dict(row.facts or {})
                facts["auto_continue"] = True
                row.facts = facts
                db.commit()
        raise
    if not getattr(context, "skip_final_stream", False) and not getattr(context, "task_halt", None):
        text = await finish_resumed_task(task_id, provider, enriched, context)
        if text is None:
            return SimpleNamespace(context=context, events=events)
        with SessionLocal() as db:
            LocalTaskController().conclude(db, context)
    return SimpleNamespace(context=context, events=events)


def _answer_history(task_id: str, original: str) -> list[dict]:
    """The real chat context for the resumed answer, not just the original prompt."""
    from sqlalchemy import select

    with SessionLocal() as db:
        task = db.get(LocalTask, task_id)
        if task is None or not task.chat_id or not task.generation_id:
            return [{"role": "user", "content": original}]
        chat = db.get(Chat, task.chat_id)
        user = db.get(User, task.user_id)
        assistant = db.get(Message, task.generation_id)
        if chat is None or user is None or assistant is None:
            return [{"role": "user", "content": original}]
        current = db.scalars(
            select(Message)
            .where(
                Message.chat_id == task.chat_id,
                Message.role == "user",
                Message.created_at <= assistant.created_at,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        ).first()
        if current is None:
            return [{"role": "user", "content": original}]
        return ContextBuilder().build(db, user, chat, current, track=False)["messages"]


async def finish_resumed_task(task_id: str, provider, history, context) -> str | None:
    """Generate the final answer of a background resume and write it into the chat.

    The original SSE stream is gone; without this the task completed while the chat kept saying
    "LLM unavailable". A failure here parks the task again (with the same backoff) instead of
    recording a completed task with no answer.
    """
    tool_messages = [
        message
        for message in (getattr(context, "tool_transcript", None) or [])
        if message.get("role") in {"assistant", "tool"}
    ]
    if tool_messages:
        history = [*history, *tool_messages]
    parts: list[str] = []
    try:
        async for part in provider.stream_chat(history):
            parts.append(part)
    except LLMError as error:
        logger.warning("resumed_answer_waiting_llm code=%s task_id=%s", error.code, task_id)
        with SessionLocal() as db:
            LocalTaskController().waiting_llm(db, context, error.code)
        return None
    except Exception as error:
        logger.warning("resumed_answer_failed type=%s task_id=%s", type(error).__name__, task_id)
        with SessionLocal() as db:
            LocalTaskController().waiting_llm(db, context, "llm_unavailable")
        return None
    text = public_assistant_text("".join(parts)).strip()
    if not text:
        return None
    with SessionLocal() as db:
        row = db.get(LocalTask, task_id)
        if row is None:
            return None
        assistant_id = row.generation_id
        message = db.get(Message, assistant_id) if assistant_id else None
        if message is not None:
            message.content = text
            message.status = "complete"
            message.completed_at = now()
            message.first_token_at = message.first_token_at or now()
        chat = db.get(Chat, row.chat_id) if row.chat_id else None
        if chat is not None:
            chat.updated_at = now()
        row.completion_summary = text[:500]
        db.commit()
    return text


async def continue_pending(orchestrator, provider, *, user_id=None, host_online=True, emit=None):
    from ...compute.demand import finish_simple_chat, provider_ready
    from . import machine

    if await provider_ready(provider):
        with SessionLocal() as db:
            query = select(LocalTask).where(LocalTask.status == machine.WAITING_LLM)
            if user_id:
                query = query.where(LocalTask.user_id == user_id)
            for row in db.scalars(query).all():
                facts = dict(row.facts or {})
                # Backoff: an outage is not a reason to call the model every two seconds. The
                # window bounds the automatic retries; an explicit Continue still works.
                if not llm_retry_due(facts):
                    continue
                # Never race the stream that is still writing this message. A task parked by
                # `park_waiting_llm` (before any generation exists) says so in its facts; its
                # message is intentionally still `generating`.
                message = db.get(Message, row.generation_id) if row.generation_id else None
                if (
                    message is not None
                    and message.status == "generating"
                    and not facts.get("resume_generation")
                ):
                    continue
                if row.status == machine.WAITING_LLM:
                    machine.transition(row, machine.RECOVERING)
                    machine.transition(row, machine.READY)
                facts["auto_continue"] = True
                facts["waiting_for_compute"] = False
                facts["task_continuation"] = True
                row.facts = facts
                row.updated_at = now()
            db.commit()
    with SessionLocal() as db:
        ids = claim_pending(db, user_id)
        simple = []
        rest = []
        for task_id in ids:
            row = db.get(LocalTask, task_id)
            if row and (row.facts or {}).get("simple_chat"):
                simple.append(task_id)
            else:
                rest.append(task_id)
    results = []
    for task_id in simple:
        await finish_simple_chat(task_id, provider)
    for task_id in rest:
        result = await continue_task(task_id, orchestrator, provider, host_online=host_online, emit=emit)
        if result is not None:
            results.append(result)
    return results
