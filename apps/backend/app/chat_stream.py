import asyncio
import json
import logging
from datetime import timedelta, timezone

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, delete, or_, select

from .compute.models import GenerationUsage
from .context_builder import ContextBuilder
from .database import SessionLocal
from .models import Chat, Message, MessageContext, now
from .providers import LLMError
from .schemas import MessageOut

logger = logging.getLogger(__name__)


def ordered_messages(db, chat_id):
    return db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at, Message.id)
    ).all()


def after(message):
    return or_(
        Message.created_at > message.created_at,
        and_(Message.created_at == message.created_at, Message.id > message.id),
    )


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_response(chat, content, request, user, db, action="send", target_id=None):
    if chat.id in request.app.state.generating:
        raise HTTPException(409, "В этом диалоге уже идёт генерация")
    if request.app.state.provider_name == "llamacpp":
        state = await request.app.state.provider.status()
        if state != "ready":
            raise LLMError(state)
    target = None
    if target_id:
        rows = ordered_messages(db, chat.id)
        target = next((row for row in rows if row.id == target_id), None)
        if target is None:
            raise HTTPException(404, "Сообщение не найдено")
        if action == "resend" and target.role != "user":
            raise HTTPException(422, "Редактировать можно только своё сообщение")
        if action == "regenerate":
            if target.role != "assistant":
                raise HTTPException(422, "Повторная генерация доступна для ответа AI")
            previous = [row for row in rows[: rows.index(target)] if row.role == "user"]
            if not previous:
                raise HTTPException(422, "Перед ответом нет сообщения пользователя")
            target = previous[-1]
    compute = request.app.state.compute
    usage_id = await compute.begin_generation(user.id, chat.id, request.app.state.provider_name)
    request.app.state.generating.add(chat.id)
    try:
        if action == "send":
            prior = ordered_messages(db, chat.id)
            created = now()
            if prior:
                created = max(
                    created, prior[-1].created_at.replace(tzinfo=timezone.utc) + timedelta(microseconds=1)
                )
            user_message = Message(chat_id=chat.id, role="user", content=content, created_at=created)
            db.add(user_message)
            if chat.title == "New chat":
                chat.title = content[:80]
        else:
            user_message = target
            db.execute(delete(Message).where(Message.chat_id == chat.id, after(target)))
            if action == "resend":
                target.content, target.edited_at = content, now()
        chat.updated_at = now()
        db.flush()
        context = ContextBuilder().build(db, user, chat, user_message, track=True)
        history = context["messages"]
        assistant = Message(
            chat_id=chat.id,
            role="assistant",
            content="",
            status="generating",
            generation_started_at=now(),
            created_at=max(
                now(), user_message.created_at.replace(tzinfo=timezone.utc) + timedelta(microseconds=1)
            ),
        )
        db.add(assistant)
        db.flush()
        db.add(
            MessageContext(
                message_id=assistant.id,
                memory_ids=context["memory_ids"],
                snapshot={k: v for k, v in context.items() if k not in {"messages", "memories"}},
            )
        )
        db.get(GenerationUsage, usage_id).message_id = assistant.id
        db.commit()
        assistant_id, chat_id = assistant.id, chat.id
        meta = {
            "user": MessageOut.model_validate(user_message).model_dump(mode="json"),
            "assistant": MessageOut.model_validate(assistant).model_dump(mode="json"),
            "replace_after_id": user_message.id,
        }
    except BaseException:
        db.rollback()
        request.app.state.generating.discard(chat.id)
        compute.finish_generation(usage_id, "error")
        raise

    async def generate():
        parts = []
        status = "stopped"
        tokens = {}
        first_token_at = None
        cancellation = {"upstream_cancel_confirmed": None}
        started = assistant.generation_started_at
        iterator = request.app.state.provider.stream_with_usage(history, tokens)
        try:
            await request.app.state.presence.publish()
            yield sse("meta", meta)
            async for token in iterator:
                if await request.is_disconnected():
                    cancellation.update(
                        application_cancelled=True,
                        cancel_requested_at=now().isoformat(),
                        client_stream_closed_at=now().isoformat(),
                    )
                    break
                if first_token_at is None:
                    first_token_at = now()
                parts.append(token)
                yield sse("delta", {"content": token})
            else:
                status = "complete"
        except asyncio.CancelledError:
            cancellation["cancel_requested_at"] = now().isoformat()
            cancellation["client_stream_closed_at"] = now().isoformat()
            cancellation["application_cancelled"] = True
            raise
        except Exception as error:
            status = "error"
            logger.warning("generation_failed type=%s request_id=%s", type(error).__name__, usage_id)
            yield sse(
                "error",
                {
                    "detail": str(error)
                    if isinstance(error, LLMError)
                    else "Ответ прерван. Нажмите «Повторить».",
                    "code": error.code if isinstance(error, LLMError) else "stream_interrupted",
                    "request_id": usage_id,
                },
            )
        finally:
            # Shield cleanup so cancellation cannot skip provider closure and persistence.
            import anyio

            with anyio.CancelScope(shield=True):
                try:
                    await iterator.aclose()
                    cancellation["provider_stream_closed_at"] = now().isoformat()
                    cancellation["provider_stream_closed"] = True
                except Exception:
                    cancellation["provider_stream_closed"] = False
            try:
                with SessionLocal() as save_db:
                    saved = save_db.get(Message, assistant_id)
                    if saved:
                        saved.content, saved.status = "".join(parts), status
                        saved.first_token_at = first_token_at
                        saved.completed_at = now()
                        saved.ttft_ms = (
                            max(0, int((first_token_at - started).total_seconds() * 1000))
                            if first_token_at
                            else None
                        )
                        saved.cancellation = cancellation
                        saved_chat = save_db.get(Chat, chat_id)
                        if saved_chat:
                            saved_chat.updated_at = now()
                        save_db.commit()
                compute.finish_generation(usage_id, status, assistant_id, tokens)
            finally:
                request.app.state.generating.discard(chat_id)
                with anyio.CancelScope(shield=True):
                    await request.app.state.presence.publish()
        if status == "complete":
            yield sse("done", {"message_id": assistant_id})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
