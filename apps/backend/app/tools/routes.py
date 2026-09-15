from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import Chat, Message, User, now
from ..security import current_user
from .executor import public_run, public_source
from .models import ToolPreferences, ToolRun, WebSourceSnapshot
from .policy import WebSettings, preferences

router = APIRouter(tags=["tools"])


@router.get("/tools/preferences")
def get_preferences(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return preferences(db, user.id)


@router.put("/tools/preferences")
def put_preferences(body: WebSettings, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.agent_run_budget > body.agent_daily_budget:
        raise HTTPException(422, "Бюджет одного запуска превышает дневной")
    row = db.get(ToolPreferences, user.id)
    if not row:
        row = ToolPreferences(user_id=user.id)
        db.add(row)
    row.values = body.model_dump()
    db.commit()
    return body


@router.get("/tools/status")
def provider_status(user: User = Depends(current_user)):
    settings = get_settings()
    return {
        "provider": "TinyFish",
        "configured": bool(settings.tinyfish_api_key.get_secret_value()),
        "search_fetch_free": settings.tinyfish_search_fetch_free,
        "agent_step_price": settings.tinyfish_agent_step_price,
        "browser_minute_price": settings.tinyfish_browser_minute_price,
        "agent_max_steps_supported": settings.tinyfish_agent_max_steps_supported,
        "agent_budget_enforcement": "provider_steps_and_local"
        if settings.tinyfish_agent_max_steps_supported
        else "local_soft",
        "browser_delete_supported": settings.tinyfish_browser_delete_supported,
        "pricing_checked_at": "2026-09-15",
        "limits": {
            "calls": settings.tools_max_calls,
            "searches": settings.tools_max_search,
            "fetches": settings.tools_max_fetch,
            "pages": settings.tools_max_pages,
            "chars": settings.tools_max_chars,
            "seconds": settings.tools_max_seconds,
        },
    }


@router.get("/tools/runs")
def runs(
    chat_id: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(ToolRun).where(ToolRun.user_id == user.id)
    if chat_id:
        query = query.where(ToolRun.chat_id == chat_id)
    return [public_run(row) for row in db.scalars(query.order_by(ToolRun.started_at.desc()).limit(limit))]


@router.get("/tools/runs/{key}")
def run(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.scalar(select(ToolRun).where(ToolRun.id == key, ToolRun.user_id == user.id))
    if not row:
        raise HTTPException(404, "Запуск не найден")
    return public_run(row)


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow: bool


@router.post("/tools/runs/{key}/confirm")
def confirm(key: str, body: Confirmation, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.scalar(select(ToolRun).where(ToolRun.id == key, ToolRun.user_id == user.id))
    if not row:
        raise HTTPException(404, "Запуск не найден")
    values = (
        {"status": "approved", "confirmed_at": now()}
        if body.allow
        else {
            "status": "stopped",
            "cancelled_at": now(),
            "finished_at": now(),
            "error_code": "confirmation_denied",
            "result_metadata": {**row.result_metadata, "reserved_budget": 0},
        }
    )
    changed = db.execute(
        update(ToolRun)
        .where(
            ToolRun.id == key,
            ToolRun.user_id == user.id,
            ToolRun.status == "waiting_confirmation",
            ToolRun.started_at >= now() - timedelta(minutes=5),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if changed.rowcount != 1:
        raise HTTPException(409, "Подтверждение уже использовано или истекло")
    return {"status": "approved" if body.allow else "stopped"}


@router.get("/messages/{key}/web-sources")
def sources(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = db.scalar(select(Message).join(Chat).where(Message.id == key, Chat.user_id == user.id))
    if not message:
        raise HTTPException(404, "Сообщение не найдено")
    return [
        public_source(row)
        for row in db.scalars(
            select(WebSourceSnapshot)
            .where(WebSourceSnapshot.generation_id == key, WebSourceSnapshot.user_id == user.id)
            .order_by(WebSourceSnapshot.rank)
        )
    ]
