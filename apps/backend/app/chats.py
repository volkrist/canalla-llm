import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import SessionLocal, get_db
from .models import Chat, Message, User, now
from .schemas import ChatCreate, ChatOut, MessageCreate, MessageOut
from .security import current_user

router = APIRouter(prefix="/chats", tags=["chats"])
logger = logging.getLogger(__name__)


def owned_chat(db: Session, chat_id: str, user_id: str):
    chat = db.scalar(select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id))
    if chat is None:
        raise HTTPException(404, "Chat not found")
    return chat


def idle(request: Request, chat_id: str):
    if chat_id in request.app.state.generating:
        raise HTTPException(409, "This chat is generating a response. Stop it before changing the chat.")


@router.get("", response_model=list[ChatOut])
def list_chats(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    return db.scalars(
        select(Chat)
        .where(Chat.user_id == user.id)
        .order_by(Chat.updated_at.desc(), Chat.id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.post("", response_model=ChatOut, status_code=201)
def create_chat(body: ChatCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    chat = Chat(title=body.title, user_id=user.id)
    db.add(chat)
    db.commit()
    return chat


@router.get("/{chat_id}", response_model=ChatOut)
def get_chat(chat_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return owned_chat(db, chat_id, user.id)


@router.get("/{chat_id}/generation")
async def generation_state(
    chat_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    owned_chat(db, chat_id, user.id)
    return {"active": chat_id in request.app.state.generating}


@router.delete("/{chat_id}", status_code=204)
async def delete_chat(
    chat_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    chat = owned_chat(db, chat_id, user.id)
    idle(request, chat_id)
    db.delete(chat)
    db.commit()
    return Response(status_code=204)


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
def get_messages(
    chat_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=200),
):
    owned_chat(db, chat_id, user.id)
    return db.scalars(
        select(Message)
        .where(Message.chat_id == chat_id)
        .order_by(Message.created_at, Message.id)
        .offset(offset)
        .limit(limit)
    ).all()


def add_user_message(db: Session, chat: Chat, content: str):
    message = Message(chat_id=chat.id, role="user", content=content)
    db.add(message)
    chat.updated_at = now()
    if chat.title == "New chat":
        chat.title = content[:80]
    db.commit()
    return message


@router.post("/{chat_id}/messages", response_model=MessageOut, status_code=201)
async def post_message(
    chat_id: str,
    body: MessageCreate,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    chat = owned_chat(db, chat_id, user.id)
    idle(request, chat_id)
    return add_user_message(db, chat, body.content)


def sse(event: str, data: dict):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/{chat_id}/stream")
async def stream_chat(
    chat_id: str,
    body: MessageCreate,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    chat = owned_chat(db, chat_id, user.id)
    idle(request, chat_id)
    # Single-worker MVP: all mutating chat routes enter here on the same event loop.
    request.app.state.generating.add(chat_id)
    try:
        user_message = add_user_message(db, chat, body.content)
        rows = db.scalars(
            select(Message)
            .where(Message.chat_id == chat_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(100)
        ).all()
        history = [{"role": row.role, "content": row.content} for row in reversed(rows)]
        # Bound provider context even when a conversation contains very large messages.
        while len(history) > 1 and sum(len(m["content"]) for m in history) > 64000:
            history.pop(0)
        assistant = Message(chat_id=chat_id, role="assistant", content="")
        db.add(assistant)
        db.commit()
        assistant_id = assistant.id
        meta = {
            "user": MessageOut.model_validate(user_message).model_dump(mode="json"),
            "assistant": MessageOut.model_validate(assistant).model_dump(mode="json"),
        }
    except BaseException:
        request.app.state.generating.discard(chat_id)
        raise

    async def generate():
        parts: list[str] = []
        error = False
        provider_stream = request.app.state.provider.stream_chat(history)
        try:
            yield sse("meta", meta)
            async for token in provider_stream:
                if await request.is_disconnected():
                    break
                parts.append(token)
                yield sse("delta", {"content": token})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Generation failed")
            error = True
            yield sse("error", {"detail": "Generation failed. Please try again."})
        finally:
            try:
                # Commit partial output on disconnect/Stop, using an independent session.
                with SessionLocal() as save_db:
                    saved = save_db.get(Message, assistant_id)
                    if saved:
                        if parts:
                            saved.content = "".join(parts)
                        else:
                            save_db.delete(saved)
                        saved_chat = save_db.get(Chat, chat_id)
                        if saved_chat:
                            saved_chat.updated_at = now()
                        save_db.commit()
            finally:
                request.app.state.generating.discard(chat_id)
                await provider_stream.aclose()
        if not error:
            yield sse("done", {"message_id": assistant_id})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
