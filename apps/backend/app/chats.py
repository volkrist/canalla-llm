import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .chat_stream import ordered_messages, stream_response
from .compute.models import GenerationUsage
from .database import SessionLocal, get_db
from .models import Chat, Message, User, now
from .schemas import ChatCreate, ChatOut, ChatUpdate, MessageCreate, MessageOut
from .security import current_user

router = APIRouter(prefix="/chats", tags=["chats"])
logger = logging.getLogger(__name__)


def owned_chat(db: Session, chat_id: str, user_id: str):
    chat = db.scalar(select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id))
    if chat is None:
        raise HTTPException(404, "Chat not found")
    return chat


def idle(request: Request, chat_id: str):
    with SessionLocal() as db:
        pending = db.scalar(
            select(GenerationUsage.id).where(
                GenerationUsage.chat_id == chat_id, GenerationUsage.completed_at.is_(None)
            )
        )
    if chat_id in request.app.state.generating or pending:
        raise HTTPException(409, "This chat is generating a response. Stop it before changing the chat.")


@router.get("", response_model=list[ChatOut])
def list_chats(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    q: str = Query("", max_length=200),
):
    return db.scalars(
        select(Chat)
        .where(Chat.user_id == user.id)
        .where(Chat.title.icontains(q.strip(), autoescape=True))
        .order_by(Chat.pinned.desc(), Chat.updated_at.desc(), Chat.id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.post("", response_model=ChatOut, status_code=201)
def create_chat(body: ChatCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    chat = Chat(title=body.title, user_id=user.id)
    db.add(chat)
    db.commit()
    return chat


def export_content(db, chats, format):
    data = [
        {
            "chat": ChatOut.model_validate(chat).model_dump(mode="json"),
            "messages": [
                MessageOut.model_validate(message).model_dump(mode="json")
                for message in ordered_messages(db, chat.id)
            ],
        }
        for chat in chats
    ]
    if format == "json":
        return Response(
            json.dumps(data, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="alex-chats.json"'},
        )
    parts = []
    for item in data:
        parts.append("# " + item["chat"]["title"])
        for message in item["messages"]:
            parts.append(
                f"## {'Р’С‹' if message['role'] == 'user' else 'Alex LLM'} В· {message['created_at']}\n\n{message['content']}"
            )
    return Response(
        "\n\n".join(parts),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="alex-chats.md"'},
    )


@router.get("/export")
def export_all(
    format: str = Query("json", pattern="^(json|markdown)$"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    chats = db.scalars(select(Chat).where(Chat.user_id == user.id).order_by(Chat.updated_at.desc())).all()
    return export_content(db, chats, format)


@router.get("/{chat_id}/export")
def export_chat(
    chat_id: str,
    format: str = Query("markdown", pattern="^(json|markdown)$"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return export_content(db, [owned_chat(db, chat_id, user.id)], format)


@router.patch("/{chat_id}", response_model=ChatOut)
async def update_chat(
    chat_id: str,
    body: ChatUpdate,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    chat = owned_chat(db, chat_id, user.id)
    idle(request, chat_id)
    if "project_id" in body.model_fields_set:
        from .personal import validate_project

        validate_project(db, body.project_id, user.id)
        chat.project_id = body.project_id
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(chat, key, value)
    chat.updated_at = now()
    db.commit()
    return chat


@router.patch("/{chat_id}/messages/{message_id}", response_model=MessageOut)
async def edit_message(
    chat_id: str,
    message_id: str,
    body: MessageCreate,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    chat = owned_chat(db, chat_id, user.id)
    idle(request, chat_id)
    message = db.scalar(select(Message).where(Message.id == message_id, Message.chat_id == chat_id))
    if not message:
        raise HTTPException(404, "РЎРѕРѕР±С‰РµРЅРёРµ РЅРµ РЅР°Р№РґРµРЅРѕ")
    if message.role != "user":
        raise HTTPException(
            422, "Р РµРґР°РєС‚РёСЂРѕРІР°С‚СЊ РјРѕР¶РЅРѕ С‚РѕР»СЊРєРѕ СЃРІРѕС‘ СЃРѕРѕР±С‰РµРЅРёРµ"
        )
    message.content, message.edited_at, chat.updated_at = body.content, now(), now()
    db.commit()
    return message


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


@router.post("/{chat_id}/stream")
async def stream_chat(
    chat_id: str,
    body: MessageCreate,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return await stream_response(
        owned_chat(db, chat_id, user.id), body.content, request, user, db, web_mode=body.web_mode
    )


@router.post("/{chat_id}/messages/{message_id}/resend")
async def resend(
    chat_id: str,
    message_id: str,
    body: MessageCreate,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return await stream_response(
        owned_chat(db, chat_id, user.id),
        body.content,
        request,
        user,
        db,
        action="resend",
        target_id=message_id,
        web_mode=body.web_mode,
    )


@router.post("/{chat_id}/messages/{message_id}/regenerate")
async def regenerate(
    chat_id: str,
    message_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return await stream_response(
        owned_chat(db, chat_id, user.id), None, request, user, db, action="regenerate", target_id=message_id
    )
