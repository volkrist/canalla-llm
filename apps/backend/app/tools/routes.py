import socket
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import Chat, Message, User, now
from ..security import current_user
from .contracts import ToolError
from .executor import ExecutionContext, ToolExecutor, public_job, public_run, public_source
from .local.devices import HostResult, PairRequest, public_device, require_device
from .models import PairedDevice, ToolPreferences, ToolRun, WebSourceSnapshot
from .policy import ToolLimits, WebSettings, preferences

router = APIRouter(tags=["tools"])


def _device(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    device_id: str | None = Header(default=None, alias="X-Alex-Device-Id"),
    credential: str | None = Header(default=None, alias="X-Alex-Device-Credential"),
):
    return require_device(db, user, device_id, credential)


def _tor_connected(settings):
    try:
        with socket.create_connection((settings.tor_socks_host, settings.tor_socks_port), 0.25):
            return True
    except OSError:
        return False


class ExplicitTool(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(max_length=36)
    name: str = Field(max_length=80)
    arguments: dict


@router.post("/tools/execute")
async def execute_tool(
    body: ExplicitTool, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    import asyncio
    import json

    import anyio

    if not db.scalar(select(Chat.id).where(Chat.id == body.chat_id, Chat.user_id == user.id)):
        raise HTTPException(404, "Диалог не найден")
    if len(json.dumps(body.arguments)) > 16000:
        raise HTTPException(422, "Слишком большой запрос инструмента")
    owner = user.id

    async def stream():
        queue = asyncio.Queue(maxsize=64)

        async def emit(event, value):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait((event, jsonable_encoder(value)))

        settings = get_settings()
        context = ExecutionContext(
            owner,
            body.chat_id,
            None,
            ToolLimits(max_calls=1, max_seconds=settings.tools_max_seconds),
            emit,
            mode="on",
            explicit=True,
            secrets=(
                settings.tinyfish_api_key.get_secret_value(),
                settings.jwt_secret,
                settings.runpod_api_key.get_secret_value(),
                settings.llm_api_key,
            ),
            resolver=getattr(request.app.state, "tool_dns_override", None),
        )
        task = asyncio.create_task(
            ToolExecutor(request.app.state.tools).execute(body.name, body.arguments, context)
        )
        try:
            while not task.done() or not queue.empty():
                try:
                    event, data = await asyncio.wait_for(queue.get(), 0.25)
                    yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    if await request.is_disconnected():
                        raise asyncio.CancelledError()
            result = await task
            yield (
                "event: tool_result\ndata: "
                + json.dumps({"text": result.text, "metadata": result.metadata}, ensure_ascii=False)
                + "\n\n"
            )
            yield "event: done\ndata: {}\n\n"
        except ToolError as error:
            yield "event: tool_error\ndata: " + json.dumps({"code": error.code}) + "\n\n"
        finally:
            if not task.done():
                task.cancel()
            with anyio.CancelScope(shield=True):
                try:
                    await task
                except (asyncio.CancelledError, ToolError):
                    pass

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/tools/browser/{session_id}/stop")
async def stop_browser(session_id: str, request: Request, user: User = Depends(current_user)):
    try:
        _, provider = request.app.state.tools.get("browser_start")
        return await provider.stop(session_id, user.id)
    except ToolError as error:
        raise HTTPException(404 if error.code == "not_found" else 409, error.code) from None


@router.get("/tools/browser/{session_id}/screenshot")
def browser_screenshot(session_id: str, request: Request, user: User = Depends(current_user)):
    try:
        _, provider = request.app.state.tools.get("browser_start")
        session = provider.owned(session_id, user.id)
        if not session.image:
            raise ToolError("not_found")
        return Response(session.image, media_type="image/png", headers={"Cache-Control": "no-store"})
    except ToolError:
        raise HTTPException(404, "Снимок не найден") from None


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
        "agent_read_only_enforced": False,
        "agent_budget_enforcement": "provider_steps_and_local"
        if settings.tinyfish_agent_max_steps_supported
        else "local_soft",
        "browser_delete_supported": settings.tinyfish_browser_delete_supported,
        "pricing_checked_at": "2026-09-15",
        "tor_search_configured": bool(settings.tor_search_providers),
        "tor_status": "Connected" if _tor_connected(settings) else "Not configured",
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


@router.post("/tools/devices/pair")
def pair_device(body: PairRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .local.devices import hash_credential, issue_credential

    credential = issue_credential()
    row = PairedDevice(
        user_id=user.id,
        display_name=body.display_name,
        platform=body.platform,
        capabilities={"tools": body.capabilities},
        credential_hash=hash_credential(credential),
        last_seen=now(),
    )
    db.add(row)
    db.commit()
    payload = public_device(row)
    payload["credential"] = credential
    return payload


@router.get("/tools/devices")
def list_devices(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [
        public_device(row)
        for row in db.scalars(select(PairedDevice).where(PairedDevice.user_id == user.id))
        if not row.revoked_at
    ]


@router.post("/tools/devices/heartbeat")
def device_heartbeat(device: PairedDevice = Depends(_device)):
    return public_device(device)


@router.get("/tools/devices/jobs")
def device_jobs(
    user: User = Depends(current_user), db: Session = Depends(get_db), device: PairedDevice = Depends(_device)
):
    rows = db.scalars(
        select(ToolRun).where(
            ToolRun.user_id == user.id,
            ToolRun.assigned_device_id == device.id,
            ToolRun.status == "waiting_host",
        )
    )
    return [public_job(row) for row in rows]


@router.post("/tools/runs/{key}/host-result")
def host_result(
    key: str,
    body: HostResult,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    device: PairedDevice = Depends(_device),
):
    from .security import sanitized

    row = db.scalar(
        select(ToolRun).where(
            ToolRun.id == key,
            ToolRun.user_id == user.id,
            ToolRun.assigned_device_id == device.id,
            ToolRun.status == "waiting_host",
        )
    )
    if not row:
        raise HTTPException(404, "Запуск не найден")
    if row.input_digest != body.digest:
        raise HTTPException(409, "confirmation_mismatch")
    started = row.started_at.replace(tzinfo=timezone.utc) if row.started_at.tzinfo is None else row.started_at
    if started < now() - timedelta(minutes=5):
        raise HTTPException(409, "confirmation_expired")
    metadata = {
        **row.result_metadata,
        "host_result": {
            "exit_code": body.exit_code,
            "stdout": sanitized(body.stdout, (), 20000),
            "stderr": sanitized(body.stderr, (), 20000),
            "text": sanitized(body.text or body.stdout, (), 20000),
            **{
                field: body.metadata[field]
                for field in ("cwd", "before_sha256", "after_sha256", "files_changed")
                if field in body.metadata
            },
        },
    }
    changed = db.execute(
        update(ToolRun)
        .where(ToolRun.id == key, ToolRun.status == "waiting_host", ToolRun.assigned_device_id == device.id)
        .values(status="host_ready", result_metadata=metadata)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if changed.rowcount != 1:
        raise HTTPException(409, "Результат уже принят")
    return {"status": "host_ready"}
