"""On-demand GPU wait, park, and paid-start confirmation. Reuses RunPodController."""

from __future__ import annotations

import asyncio
import inspect
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

from ..database import SessionLocal
from ..models import Chat, User, now
from ..providers import LLMError
from ..tools.confirmation import consume_if_valid, make_envelope, store_envelope
from ..tools.local import machine
from ..tools.local.journal import append_event
from ..tools.local.task import LocalTaskController
from ..tools.models import LocalTask, ToolRun
from ..tools.security import digest
from .runtime import money_prompt


def production_llm_required(settings) -> bool:
    return (
        settings.llm_provider == "llamacpp" and getattr(settings, "llm_connection_mode", "runpod") == "runpod"
    )


async def provider_ready(provider) -> bool:
    status = getattr(provider, "status", None)
    if callable(status):
        try:
            observed = status()
            # Duck-typed provider: a status() that is not awaitable is not a status, so it
            # falls through to the health probe exactly as the bare await used to.
            if inspect.isawaitable(observed):
                return await observed == "ready"
        except Exception:
            pass
    health = getattr(provider, "health", None)
    if callable(health):
        try:
            observed = health()
            if inspect.isawaitable(observed):
                return bool(await observed)
        except Exception:
            return False
    return False


def park_waiting_llm(
    *,
    user: User,
    chat_id: str,
    content: str,
    assistant_id: str | None,
    user_message_id: str | None,
    reason: str,
    compute_session_id: str | None,
    task_id: str | None = None,
    simple_chat: bool = True,
):
    with SessionLocal() as db:
        row = db.get(LocalTask, task_id) if task_id else None
        if row is None:
            row = db.scalar(
                select(LocalTask).where(
                    LocalTask.chat_id == chat_id,
                    LocalTask.status == machine.WAITING_LLM,
                )
            )
        if row is None:
            row = LocalTask(
                user_id=user.id,
                chat_id=chat_id,
                generation_id=assistant_id,
                status=machine.CREATED,
                current_phase=machine.CREATED,
                original_user_request=(content or "")[:16000],
                title=(content or "AI")[:80],
                facts={},
            )
            db.add(row)
            db.flush()
            machine.transition(row, machine.PLANNING)
            append_event(db, row.id, "TASK_CREATED", {"reason": reason})
        if row.status not in machine.TERMINAL and row.status != machine.WAITING_LLM:
            LocalTaskController().waiting_llm(
                db, SimpleNamespace(task_id=row.id, user_id=user.id, secrets=()), reason
            )
            refreshed = db.get(LocalTask, row.id)
            if refreshed is None:  # pragma: no cover - waiting_llm commits this same row
                raise RuntimeError("local task row is missing")
            row = refreshed
        facts = dict(row.facts or {})
        facts.update(
            {
                "waiting_for_compute": True,
                "resume_generation": True,
                "simple_chat": simple_chat and not facts.get("autonomy"),
                "reason": reason,
                "compute_session_id": compute_session_id,
                "assistant_id": assistant_id or facts.get("assistant_id"),
                "user_message_id": user_message_id or facts.get("user_message_id"),
                "auto_continue": True,
            }
        )
        row.facts = facts
        row.generation_id = assistant_id or row.generation_id
        row.updated_at = now()
        db.commit()
        return row.id


def request_start_confirmation(db, *, user: User, chat_id: str, task_id: str | None, gpu, hourly: Decimal):
    chat = db.get(Chat, chat_id) if chat_id else None
    if chat is None:
        return None
    payload = {
        "gpu_id": gpu.id,
        "hourly_rate": str(hourly),
        "action": "compute.start",
    }
    digest_value = digest(payload, "compute.start")
    row = ToolRun(
        user_id=user.id,
        chat_id=chat_id,
        tool_name="compute.start",
        provider="compute",
        risk_level="SENSITIVE",
        status="waiting_confirmation",
        input_summary={
            "reason": "Нужна production-модель для ответа",
            "action_detail": money_prompt(gpu.name or gpu.id, hourly),
            "target": gpu.id,
            "consequences": "Оплата GPU, пока сессия активна. Network Volume не удаляется.",
            "risk_level": "SENSITIVE",
        },
        input_digest=digest_value,
        cost_estimate=hourly,
        origin="server_policy",
        task_id=task_id,
        result_metadata={},
    )
    db.add(row)
    db.flush()
    store_envelope(
        row,
        make_envelope(
            user_id=user.id,
            task_id=task_id,
            tool_name="compute.start",
            risk_level="SENSITIVE",
            payload=payload,
            confirmation_id=row.id,
        ),
    )
    db.flush()
    return row


def consume_start_confirmation(db, *, user_id: str, run_id: str, digest_value: str):
    return consume_if_valid(db, run_id=run_id, user_id=user_id, expected_digest=digest_value)


async def wait_for_production(request, user, chat, user_message, assistant, content, task_id=None):
    """Yield SSE tuples until the production model is ready, or park the same task."""
    from ..config import get_settings

    settings = get_settings()
    if not production_llm_required(settings):
        yield ("_done", "ready")
        return
    compute = request.app.state.compute
    provider = request.app.state.provider
    deadline = asyncio.get_event_loop().time() + max(60, int(settings.runpod_startup_timeout) + 30)
    while True:
        if await request.is_disconnected():
            park_waiting_llm(
                user=user,
                chat_id=chat.id,
                content=content,
                assistant_id=assistant.id,
                user_message_id=user_message.id,
                reason="client_disconnected",
                compute_session_id=_active_session_id(compute),
                task_id=task_id,
            )
            yield ("progress", {"state": "waiting", "text": "Запускаю AI…"})
            yield ("_done", "parked")
            return
        if asyncio.get_event_loop().time() > deadline:
            park_waiting_llm(
                user=user,
                chat_id=chat.id,
                content=content,
                assistant_id=assistant.id,
                user_message_id=user_message.id,
                reason="startup_timeout",
                compute_session_id=_active_session_id(compute),
                task_id=task_id,
            )
            raise LLMError("startup_timeout")
        result = await compute.ensure_on_demand(user, chat_id=chat.id, task_id=task_id)
        if result.get("tool"):
            yield ("tool", result["tool"])
        if result.get("kind") == "waiting_confirmation":
            yield ("progress", {"state": "waiting", "text": result.get("prompt") or "Подтвердите запуск AI"})
            await asyncio.sleep(1)
            continue
        if result.get("kind") == "denied":
            raise LLMError("confirmation_denied")
        if result.get("kind") in {"unavailable", "error", "multiple_compute"}:
            park_waiting_llm(
                user=user,
                chat_id=chat.id,
                content=content,
                assistant_id=assistant.id,
                user_message_id=user_message.id,
                reason=result.get("code") or result["kind"],
                compute_session_id=_active_session_id(compute),
                task_id=task_id,
            )
            if result.get("kind") == "multiple_compute" or result.get("code") in {
                "not_configured",
                "multiple_compute",
                "COMPUTE_BUDGET_REACHED",
            }:
                yield (
                    "progress",
                    {
                        "state": "error",
                        "text": "AI Unavailable",
                        "code": result.get("code") or result["kind"],
                    },
                )
                yield ("_done", "parked")
                return
            yield ("progress", {"state": "starting_ai", "text": "Запускаю AI…"})
            await asyncio.sleep(3)
            continue
        yield ("progress", {"state": "starting_ai", "text": "Запускаю AI…"})
        if result.get("kind") in {"ready", "starting", "create_unknown", "external_compute"}:
            await compute.tick()
        if await provider_ready(provider) and result.get("kind") in {"ready", "starting"}:
            status = compute.get_compute_status(user).get("state")
            if status in {"ready", "generating"}:
                yield ("_done", "ready")
                return
        await asyncio.sleep(1.5)


async def resume_parked_demand(compute):
    """Parked WAITING_LLM / approved compute.start must start the same GPU session."""
    from ..config import get_settings
    from .controller import control_row

    if not production_llm_required(get_settings()):
        return
    with SessionLocal() as db:
        jobs: list[tuple[User | None, str | None, str | None]] = [
            (db.get(User, row.user_id), row.chat_id, row.id)
            for row in db.scalars(select(LocalTask).where(LocalTask.status == machine.WAITING_LLM))
        ]
        control = control_row(db)
        if control.confirmation_run_id:
            run = db.get(ToolRun, control.confirmation_run_id)
            if run and run.status == "approved":
                jobs.append((db.get(User, run.user_id), run.chat_id, run.task_id))
    seen = set()
    for user, chat_id, task_id in jobs:
        if not user or user.id in seen:
            continue
        seen.add(user.id)
        try:
            await compute.ensure_on_demand(user, chat_id=chat_id, task_id=task_id)
        except Exception:
            continue


def _active_session_id(compute) -> str | None:
    try:
        from .controller import control_row

        with SessionLocal() as db:
            return control_row(db).active_session_id
    except Exception:
        return None


async def finish_simple_chat(task_id: str, provider):
    from ..context_builder import ContextBuilder
    from ..models import Chat, Message, User

    with SessionLocal() as db:
        task = db.get(LocalTask, task_id)
        if not task:
            return None
        chat = db.get(Chat, task.chat_id)
        user = db.get(User, task.user_id)
        facts = dict(task.facts or {})
        assistant_id = facts.get("assistant_id") or task.generation_id
        user_message = db.get(Message, facts.get("user_message_id"))
        if chat is None or user is None or user_message is None:
            return None
        context = ContextBuilder().build(db, user, chat, user_message, track=False)
        history = context["messages"]
    parts = []
    async for part in provider.stream_chat(history):
        parts.append(part)
    text = "".join(parts)
    with SessionLocal() as db:
        saved = db.get(Message, assistant_id)
        if saved:
            saved.content = text
            saved.status = "complete"
            saved.completed_at = now()
        row = db.get(LocalTask, task_id)
        if row and row.status not in machine.TERMINAL:
            if row.status == machine.READY:
                machine.transition(row, machine.COMPLETED)
            row.completion_summary = text[:500]
            row.finished_at = now()
            facts = dict(row.facts or {})
            facts["waiting_for_compute"] = False
            facts["auto_continue"] = False
            row.facts = facts
            append_event(db, row.id, "COMPLETED", {"simple_chat": True})
        db.commit()
    return text
