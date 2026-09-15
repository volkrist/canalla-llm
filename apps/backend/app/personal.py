"""Personal data APIs: ownership is enforced for every reference, including sources."""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import Chat, Memory, Message, MessageContext, Project, User, now
from .security import current_user

router = APIRouter(tags=["personal"])
Category = Literal["identity", "preference", "project", "decision", "fact", "instruction", "other"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, from_attributes=True)

    @field_serializer("*")
    def utc_dates(self, value):
        return value.replace(tzinfo=timezone.utc).isoformat() if isinstance(value, datetime) else value


class Profile(Strict):
    display_name: str = Field(min_length=1, max_length=80)
    custom_instructions: str = Field(default="", max_length=2000)
    use_memory: bool = True
    relevant_memory: bool = True
    max_memories: int = Field(default=12, ge=1, le=20)


class ProfileOut(Profile):
    email: str
    created_at: datetime
    updated_at: datetime


class ProjectBody(Strict):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=3000)
    status: Literal["active", "archived"] = "active"


class ProjectOut(ProjectBody):
    id: str
    created_at: datetime
    updated_at: datetime


class MemoryBody(Strict):
    content: str = Field(min_length=1, max_length=3000)
    category: Category = "fact"
    importance: int = Field(default=3, ge=1, le=5)
    project_id: str | None = None
    source_chat_id: str | None = None
    source_message_id: str | None = None
    is_pinned: bool = False
    is_active: bool = True


class MemoryPatch(Strict):
    content: str | None = Field(default=None, min_length=1, max_length=3000)
    category: Category | None = None
    importance: int | None = Field(default=None, ge=1, le=5)
    project_id: str | None = None
    is_pinned: bool | None = None
    is_active: bool | None = None


class MemoryOut(MemoryBody):
    id: str
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
    use_count: int


def owned(db, model, key, user_id):
    row = db.scalar(select(model).where(model.id == key, model.user_id == user_id))
    if row is None:
        raise HTTPException(404, "Запись не найдена")
    return row


def validate_project(db, key, user_id):
    if key:
        project = owned(db, Project, key, user_id)
        if project.status != "active":
            raise HTTPException(422, "Проект архивирован")


@router.get("/profile", response_model=ProfileOut)
def profile(user: User = Depends(current_user)):
    return user


@router.patch("/profile", response_model=ProfileOut)
def update_profile(body: Profile, user: User = Depends(current_user), db: Session = Depends(get_db)):
    for key, value in body.model_dump().items():
        setattr(user, key, value)
    user.updated_at = now()
    db.commit()
    return user


@router.get("/projects", response_model=list[ProjectOut])
def projects(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    return db.scalars(
        select(Project)
        .where(Project.user_id == user.id)
        .order_by(Project.updated_at.desc(), Project.id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = Project(user_id=user.id, **body.model_dump())
    db.add(row)
    db.commit()
    return row


@router.patch("/projects/{key}", response_model=ProjectOut)
def update_project(
    key: str, body: ProjectBody, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    row = owned(db, Project, key, user.id)
    for field, value in body.model_dump().items():
        setattr(row, field, value)
    row.updated_at = now()
    db.commit()
    return row


@router.get("/memory", response_model=list[MemoryOut])
def memories(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    q: str = Query("", max_length=200),
    category: Category | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    query = select(Memory).where(Memory.user_id == user.id, Memory.content.icontains(q, autoescape=True))
    if category:
        query = query.where(Memory.category == category)
    return db.scalars(
        query.order_by(Memory.is_pinned.desc(), Memory.updated_at.desc(), Memory.id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.post("/memory", response_model=MemoryOut, status_code=201)
def create_memory(body: MemoryBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    validate_project(db, body.project_id, user.id)
    if body.source_chat_id:
        owned(db, Chat, body.source_chat_id, user.id)
    if body.source_message_id:
        message = db.scalar(
            select(Message)
            .join(Chat, Chat.id == Message.chat_id)
            .where(Message.id == body.source_message_id, Chat.user_id == user.id)
        )
        if not message or (body.source_chat_id and body.source_chat_id != message.chat_id):
            raise HTTPException(404, "Сообщение не найдено")
        body.source_chat_id = message.chat_id
    row = Memory(user_id=user.id, **body.model_dump())
    db.add(row)
    db.commit()
    return row


@router.patch("/memory/{key}", response_model=MemoryOut)
def update_memory(
    key: str, body: MemoryPatch, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    row = owned(db, Memory, key, user.id)
    values = body.model_dump(exclude_unset=True)
    for field, value in values.items():
        if value is None and field != "project_id":
            raise HTTPException(422, "Поле не может быть пустым")
    if "project_id" in values:
        validate_project(db, values["project_id"], user.id)
    for field, value in values.items():
        setattr(row, field, value)
    row.updated_at = now()
    db.commit()
    return row


@router.delete("/memory/{key}")
def delete_memory(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(owned(db, Memory, key, user.id))
    db.commit()
    return {"deleted": True}


@router.get("/messages/{key}/memory", response_model=list[MemoryOut])
def used_memory(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = db.scalar(select(Message).join(Chat).where(Message.id == key, Chat.user_id == user.id))
    if not message:
        raise HTTPException(404, "Сообщение не найдено")
    context = db.get(MessageContext, key)
    return db.scalars(
        select(Memory).where(Memory.user_id == user.id, Memory.id.in_(context.memory_ids if context else []))
    ).all()


@router.get("/messages/{key}/context")
def used_context(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = db.scalar(select(Message).join(Chat).where(Message.id == key, Chat.user_id == user.id))
    if not message:
        raise HTTPException(404, "Сообщение не найдено")
    context = db.get(MessageContext, key)
    memories = used_memory(key, user, db)
    snapshot = context.snapshot if context else None
    return {
        **(snapshot or {"metadata_available": False}),
        "memories": [MemoryOut.model_validate(m).model_dump(mode="json") for m in memories],
    }
