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
from .tor.browser import browser_status

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
    prefs = preferences(db, user.id)

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
            computer_mode=prefs.computer_mode,
            tor_enabled=prefs.tor_enabled,
            tor_mode=prefs.tor_mode,
            explicit=True,
            settings=prefs,
            secrets=(
                settings.tinyfish_api_key.get_secret_value(),
                settings.jwt_secret,
                settings.runpod_api_key.get_secret_value(),
                settings.llm_api_key,
            ),
            resolver=getattr(request.app.state, "tool_dns_override", None),
        )
        task = asyncio.create_task(
            ToolExecutor(request.app.state.tools).execute(
                body.name, body.arguments, context, origin="explicit"
            )
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
    settings = get_settings()
    body.tinyfish_paid_task_budget_usd = min(
        body.tinyfish_paid_task_budget_usd, settings.tinyfish_paid_hard_usd
    )
    body.agent_max_steps = min(body.agent_max_steps, settings.tinyfish_agent_hard_steps)
    body.browser_max_minutes = min(body.browser_max_minutes, settings.tinyfish_browser_hard_minutes)
    row = db.get(ToolPreferences, user.id)
    if not row:
        row = ToolPreferences(user_id=user.id)
        db.add(row)
    row.values = body.model_dump()
    db.commit()
    return body


@router.get("/tools/status")
def provider_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    settings = get_settings()
    prefs = preferences(db, user.id)
    configured = bool(settings.tinyfish_api_key.get_secret_value())
    return {
        "provider": "TinyFish",
        "configured": configured,
        "search_fetch_free": settings.tinyfish_search_fetch_free,
        "agent_step_price": settings.tinyfish_agent_step_price,
        "browser_minute_price": settings.tinyfish_browser_minute_price,
        "agent_max_steps_supported": settings.tinyfish_agent_max_steps_supported,
        "agent_read_only_enforced": False,
        "agent_preaction_approval": bool(settings.tinyfish_agent_preaction_approval),
        "agent_side_effect_blocked_before_run": True,
        "agent_budget_enforcement": "provider_steps_and_local"
        if settings.tinyfish_agent_max_steps_supported
        else "local_soft",
        "browser_delete_supported": settings.tinyfish_browser_delete_supported,
        "pricing_checked_at": "2026-09-19",
        "vault_enabled": False,
        "profile_enabled": False,
        "capabilities": {
            "tinyfish_search": {
                "configured": configured,
                "enabled": prefs.search_enabled,
                "available": configured and prefs.search_enabled,
                "paid": False,
                "risk": "READ",
            },
            "tinyfish_fetch": {
                "configured": configured,
                "enabled": prefs.fetch_enabled,
                "available": configured and prefs.fetch_enabled,
                "paid": False,
                "risk": "READ",
            },
            "tinyfish_agent": {
                "configured": configured,
                "enabled": prefs.agent_mode != "off",
                "available": configured and prefs.agent_mode != "off",
                "paid": True,
                "risk": "READ",
                "mode": "READ_ONLY",
            },
            "tinyfish_browser": {
                "configured": configured,
                "enabled": prefs.browser_mode != "off",
                "available": configured and prefs.browser_mode != "off",
                "paid": True,
                "risk": "READ",
            },
        },
        "task_limits": {
            "paid_budget": prefs.tinyfish_paid_task_budget_usd,
            "agent_steps": prefs.agent_max_steps,
            "browser_minutes": prefs.browser_max_minutes,
            "agent_mode": prefs.agent_mode,
            "browser_mode": prefs.browser_mode,
        },
        "tor_search_configured": bool(settings.tor_search_providers),
        "tor_status": "Connected" if _tor_connected(settings) else "Unavailable",
        "limits": {
            "calls": settings.tools_max_calls,
            "searches": settings.tools_max_search,
            "fetches": settings.tools_max_fetch,
            "pages": settings.tools_max_pages,
            "chars": settings.tools_max_chars,
            "seconds": settings.tools_max_seconds,
            "coding_calls": settings.tools_max_coding_calls,
            "hard_calls": settings.tools_hard_max_calls,
            "local_calls": settings.tools_max_local_calls,
            "files_changed": settings.tools_max_files_changed,
            "file_bytes": settings.tools_max_file_bytes,
            "process_seconds": settings.tools_max_process_seconds,
            "tor_search": settings.tools_max_tor_search,
            "tor_fetch": settings.tools_max_tor_fetch,
            "tor_calls": settings.tools_max_tor_calls,
            "tor_follow": settings.tools_max_tor_follow,
            "tor_depth": settings.tools_max_tor_depth,
        },
        "tor_browser": browser_status(),
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
    digest: str | None = Field(default=None, max_length=64)


def _confirm_error(error: ToolError):
    status = 404 if error.code == "not_found" else 409
    raise HTTPException(status, detail={"code": error.code, "message": error.code}) from error


@router.post("/tools/runs/{key}/confirm")
def confirm(key: str, body: Confirmation, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .confirmation import confirm_allow, confirm_deny

    try:
        if body.allow:
            confirm_allow(db, run_id=key, user_id=user.id, digest_value=body.digest)
        else:
            confirm_deny(db, run_id=key, user_id=user.id)
    except ToolError as error:
        db.rollback()
        _confirm_error(error)
    db.commit()
    return {"status": "approved" if body.allow else "stopped"}


@router.post("/tools/devices/{key}/forget")
def forget_device(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.scalar(select(PairedDevice).where(PairedDevice.id == key, PairedDevice.user_id == user.id))
    if not row or row.revoked_at:
        raise HTTPException(404, "Устройство не найдено")
    row.revoked_at = now()
    db.commit()
    return {"revoked": True, "device_id": row.id}


@router.post("/tools/devices/{key}/rotate")
def rotate_device(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .local.devices import hash_credential, issue_credential, public_device

    row = db.scalar(select(PairedDevice).where(PairedDevice.id == key, PairedDevice.user_id == user.id))
    if not row or row.revoked_at:
        raise HTTPException(404, "Устройство не найдено")
    credential = issue_credential()
    row.credential_hash = hash_credential(credential)
    row.last_seen = now()
    db.commit()
    payload = public_device(row)
    payload["credential"] = credential
    return payload


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
                for field in (
                    "cwd",
                    "before_sha256",
                    "after_sha256",
                    "sha256",
                    "digest",
                    "path",
                    "pid",
                    "os_version",
                    "cpu_logical_processors",
                    "ram_total_mb",
                    "ram_avail_mb",
                    "system_disk_free_gb",
                    "desktop",
                    "documents",
                    "downloads",
                    "files_changed",
                    "conflict",
                    "error",
                    "reference",
                    "available",
                    "executed",
                    "armed",
                    "branch",
                    "dirty",
                    "status",
                    "started_by_alex",
                    "verified_dead",
                    "tool_run_id",
                )
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
