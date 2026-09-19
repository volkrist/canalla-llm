from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Chat, User
from ..security import current_user
from .contracts import ToolError
from .local.task import LocalTaskController
from .models import LocalTask

router = APIRouter(tags=["tasks"])


class TaskControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(max_length=16)


def _owned(db, key, user_id) -> LocalTask:
    row = db.get(LocalTask, key)
    if not row or row.user_id != user_id:
        raise HTTPException(404, "Задача не найдена")
    return row


@router.get("/tasks")
def list_tasks(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    chat_id: str | None = None,
    offset: int = Query(default=0, ge=0, le=10000),
    limit: int = Query(default=50, ge=1, le=100),
):
    query = select(LocalTask).where(LocalTask.user_id == user.id)
    if chat_id:
        query = query.where(LocalTask.chat_id == chat_id)
    rows = db.scalars(query.order_by(LocalTask.started_at.desc()).offset(offset).limit(limit)).all()
    controller = LocalTaskController()
    return [controller.public(db, row) for row in rows]


@router.get("/tasks/{key}")
def get_task(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return LocalTaskController().public(db, _owned(db, key, user.id), include_events=True)


@router.post("/tasks/{key}/pause")
def pause_task(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    controller = LocalTaskController()
    row = controller.pause(db, task=_owned(db, key, user.id), user_id=user.id)
    return controller.public(db, row)


@router.post("/tasks/{key}/stop")
def stop_task(key: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    controller = LocalTaskController()
    row = _owned(db, key, user.id)
    controller.stop(db, task=row, user_id=user.id)
    if row.chat_id:
        request.app.state.generating.discard(row.chat_id)
    return controller.public(db, row)


@router.post("/tasks/{key}/resume")
async def resume_task(
    key: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    from ..chat_stream import stream_response

    row = _owned(db, key, user.id)
    if not row.chat_id:
        raise HTTPException(422, "У задачи нет диалога")
    chat = db.get(Chat, row.chat_id)
    if not chat or chat.user_id != user.id:
        raise HTTPException(404, "Диалог не найден")
    try:
        LocalTaskController().resume(db, row, user.id)
    except ToolError as error:
        raise HTTPException(409, error.code) from None
    return await stream_response(
        chat,
        row.original_user_request,
        request,
        user,
        db,
        action="resume",
        resume_task_id=row.id,
    )
