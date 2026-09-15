import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import PurePosixPath
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import Chat, MessageContext, User, now
from ..personal import owned, validate_project
from ..security import current_user
from .model_manager import get_model_manager
from .models import Document, RagPreferences
from .service import DocumentIndexJob, mutation_lock
from .storage import LocalDocumentStorage
from .vector import SQLVectorStore

router = APIRouter(tags=["files"])


@router.get("/rag/model")
def model_status(user: User = Depends(current_user)):
    return get_model_manager().status(user.id, user.role == "admin")


@router.post("/rag/model/prepare")
@router.post("/rag/model/retry")
def model_prepare(user: User = Depends(current_user)):
    return get_model_manager().prepare(user.id)


@router.post("/rag/model/cancel")
def model_cancel(user: User = Depends(current_user)):
    try:
        return get_model_manager().cancel(user.id, user.role == "admin")
    except PermissionError as error:
        raise HTTPException(403, str(error)) from None


@router.get("/rag/model/events")
def model_events(user: User = Depends(current_user)):
    actor, admin = user.id, user.role == "admin"

    async def events():
        previous = None
        for _ in range(600):
            value = await asyncio.to_thread(get_model_manager().status, actor, admin)
            encoded = json.dumps(value, ensure_ascii=False)
            if encoded != previous:
                yield f"event: model\ndata: {encoded}\n\n"
                previous = encoded
            else:
                yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "md": "text/markdown",
}


class RagSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    include_general: bool = True
    max_chunks: int = Field(default=6, ge=1, le=12)
    max_chars: int = Field(default=12000, ge=100, le=20000)
    similarity_threshold: float = Field(default=0.72, ge=0, le=1)


def preferences(db, user_id):
    row = db.get(RagPreferences, user_id)
    value = RagSettings.model_validate(row.values if row else {})
    value.max_chars = min(value.max_chars, get_settings().rag_max_chars)
    return value


@router.get("/rag/preferences")
def get_preferences(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return preferences(db, user.id)


@router.put("/rag/preferences")
def put_preferences(body: RagSettings, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.max_chars > get_settings().rag_max_chars:
        raise HTTPException(422, f"Максимум {get_settings().rag_max_chars} символов RAG")
    row = db.get(RagPreferences, user.id)
    if not row:
        row = RagPreferences(user_id=user.id)
        db.add(row)
    row.values = body.model_dump()
    db.commit()
    return body


def public(row):
    result = {
        key: getattr(row, key)
        for key in (
            "id",
            "project_id",
            "original_filename",
            "display_name",
            "mime_type",
            "extension",
            "size_bytes",
            "status",
            "error_message",
            "created_at",
            "updated_at",
            "indexed_at",
            "chunk_count",
            "text_char_count",
        )
    }
    return {
        key: value.replace(tzinfo=timezone.utc).isoformat() if isinstance(value, datetime) else value
        for key, value in result.items()
    }


@router.get("/documents")
def list_documents(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [
        public(row)
        for row in db.scalars(
            select(Document).where(Document.user_id == user.id).order_by(Document.created_at.desc())
        )
    ]


@router.get("/documents/{key}")
def get_document(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return public(owned(db, Document, key, user.id))


@router.post("/documents", status_code=201)
def upload(
    tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_id: str | None = Form(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    name = file.filename or ""
    # Reject paths rather than sanitizing a potentially misleading display name.
    if not name or len(name) > 255 or any(c in name for c in "\\/:\x00") or any(ord(c) < 32 for c in name):
        raise HTTPException(422, "Недопустимое имя файла")
    extension = PurePosixPath(name).suffix.lower().lstrip(".")
    if extension not in TYPES or file.content_type not in {
        TYPES.get(extension),
        "application/octet-stream",
        "text/plain" if extension == "md" else TYPES.get(extension),
    }:
        raise HTTPException(422, "Разрешены PDF, DOCX, TXT и MD с корректным MIME")
    data = file.file.read(settings.document_max_bytes + 1)
    if not data or len(data) > settings.document_max_bytes:
        raise HTTPException(413, "Файл пустой или превышает лимит размера (до 25 MB)")
    if (extension == "pdf" and not data.startswith(b"%PDF-")) or (
        extension == "docx" and not data.startswith(b"PK")
    ):
        raise HTTPException(422, "Содержимое не соответствует типу файла")
    validate_project(db, project_id, user.id)
    storage = LocalDocumentStorage(settings.document_storage_dir)
    with mutation_lock:
        db.scalar(select(User).where(User.id == user.id).with_for_update())
        if (
            db.scalar(select(func.count()).select_from(Document).where(Document.user_id == user.id))
            >= settings.document_max_per_user
        ):
            raise HTTPException(409, "Достигнут лимит документов")
        digest = hashlib.sha256(data).hexdigest()
        if db.scalar(select(Document.id).where(Document.user_id == user.id, Document.sha256 == digest)):
            raise HTTPException(409, "Этот файл уже загружен")
        key, job = uuid4().hex, str(uuid4())
        storage.put(key, data)
        row = Document(
            user_id=user.id,
            project_id=project_id,
            original_filename=name,
            display_name=name,
            mime_type=TYPES[extension],
            extension=extension,
            size_bytes=len(data),
            storage_key=key,
            sha256=digest,
            job_id=job,
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            storage.delete(key)
            raise HTTPException(409, "Этот файл уже загружен") from None
        result = public(row)
        tasks.add_task(DocumentIndexJob().run, row.id, job)
        return result


class Rename(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)


@router.patch("/documents/{key}")
def rename(key: str, body: Rename, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = owned(db, Document, key, user.id)
    if not body.display_name.strip():
        raise HTTPException(422, "Введите название")
    row.display_name, row.updated_at = body.display_name.strip(), now()
    db.commit()
    return public(row)


@router.post("/documents/{key}/reindex")
def reindex(
    key: str, tasks: BackgroundTasks, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    with mutation_lock:
        row = owned(db, Document, key, user.id)
        if row.job_id:
            raise HTTPException(409, "Индексация уже выполняется")
        row.job_id, row.status, row.error_message = str(uuid4()), "uploaded", None
        db.commit()
        tasks.add_task(DocumentIndexJob().run, key, row.job_id)
        return public(row)


@router.delete("/documents/{key}")
def remove(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with mutation_lock:
        row = owned(db, Document, key, user.id)
        LocalDocumentStorage(get_settings().document_storage_dir).delete(row.storage_key)
        SQLVectorStore().delete_document(db, user.id, key)
        from ..models import Message

        contexts = db.scalars(select(MessageContext).join(Message).join(Chat).where(Chat.user_id == user.id))
        for context in contexts:
            if not context.snapshot:
                continue
            sources = context.snapshot.get("sources", [])
            if any(source["document_id"] == key for source in sources):
                context.snapshot = {
                    **context.snapshot,
                    "sources": [
                        {**source, "excerpt": None, "deleted": True}
                        if source["document_id"] == key
                        else source
                        for source in sources
                    ],
                }
        db.delete(row)
        db.commit()
    return {"deleted": True}
