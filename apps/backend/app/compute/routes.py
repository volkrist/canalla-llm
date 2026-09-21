from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select

from ..config import get_settings
from ..database import SessionLocal
from ..models import User
from ..security import admin_user, compute_user, current_user
from .controller import utc
from .models import ComputeEvent, ComputeSession, GenerationUsage
from .runpod_api import ERROR_MESSAGES
from .schemas import ComputePreferences, StartRequest, StopRequest

router = APIRouter(prefix="/compute", tags=["compute"])
admin = APIRouter(prefix="/admin", tags=["admin"])


def controller(request: Request):
    return request.app.state.compute


def local_compute_required(request: Request) -> None:
    """Shared mode owns compute on the Gateway, so the local lifecycle is refused.

    The provider credential and the global lease are server-side in that mode: letting a
    local route start or stop provider compute would bypass the account-wide limits.
    Inference, status and balance keep working through Alex Cloud.
    """
    settings = getattr(request.app.state, "settings", None) or get_settings()
    if getattr(settings, "alex_ai_mode", "direct") == "shared":
        raise HTTPException(409, ERROR_MESSAGES["gateway_managed_compute"])


@router.get("/status")
def status(request: Request, user: User = Depends(current_user)):
    return controller(request).get_compute_status(user)


@router.get("/preferences")
def preferences(request: Request, user: User = Depends(current_user)):
    return controller(request).preferences(user.id)


@router.put("/preferences")
async def update_preferences(
    body: ComputePreferences,
    request: Request,
    user: User = Depends(compute_user),
    _: None = Depends(local_compute_required),
):
    try:
        return await controller(request).update_preferences(user, body)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.get("/options")
async def options(request: Request, user: User = Depends(current_user)):
    return {"options": await controller(request).list_gpu_options(user.id)}


@router.post("/search")
async def search(
    body: ComputePreferences,
    request: Request,
    user: User = Depends(compute_user),
    _: None = Depends(local_compute_required),
):
    try:
        return await controller(request).search_gpu(user, body)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.get("/quotes/{quote_id}")
def quote(quote_id: str, request: Request, user: User = Depends(current_user)):
    return controller(request).quote(user, quote_id)


@router.post("/start")
async def start(
    body: StartRequest,
    request: Request,
    user: User = Depends(compute_user),
    _: None = Depends(local_compute_required),
):
    try:
        return await controller(request).start_compute(user, body)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.post("/stop")
async def stop(
    body: StopRequest,
    request: Request,
    user: User = Depends(compute_user),
    _: None = Depends(local_compute_required),
):
    return await controller(request).stop_compute(user, body)


@router.post("/search/cancel")
async def cancel(
    request: Request,
    user: User = Depends(compute_user),
    _: None = Depends(local_compute_required),
):
    return await controller(request).cancel_gpu_search(user)


def usage_for(user_id, at):
    today = at.replace(hour=0, minute=0, second=0, microsecond=0)
    starts = {
        "today": today,
        "week": today - timedelta(days=today.weekday()),
        "month": today.replace(day=1),
        "all": datetime.min.replace(tzinfo=timezone.utc),
    }
    with SessionLocal() as db:
        sessions = db.scalars(
            select(ComputeSession).where(
                ComputeSession.started_by_user_id == user_id,
                ComputeSession.managed.is_(True),
                ComputeSession.started_at.is_not(None),
            )
        ).all()
        periods = {}
        for name, since in starts.items():
            seconds = 0
            cost = Decimal(0)
            for row in sessions:
                left, right = max(utc(row.started_at), since), min(utc(row.stopped_at or at), at)
                duration = max(0, int((right - left).total_seconds()))
                seconds += duration
                cost += Decimal(duration) / 3600 * row.hourly_rate
            requests = db.scalar(
                select(func.count())
                .select_from(GenerationUsage)
                .where(
                    GenerationUsage.user_id == user_id,
                    GenerationUsage.created_at >= since,
                    GenerationUsage.created_at <= at,
                )
            )
            periods[name] = {
                "gpu_seconds": seconds,
                "estimated_cost": float(cost.quantize(Decimal("0.000001"))),
                "requests": requests,
            }
            token_sums = db.execute(
                select(
                    func.sum(GenerationUsage.input_tokens),
                    func.sum(GenerationUsage.output_tokens),
                    func.sum(GenerationUsage.total_tokens),
                ).where(
                    GenerationUsage.user_id == user_id,
                    GenerationUsage.created_at >= since,
                    GenerationUsage.created_at <= at,
                )
            ).one()
            periods[name].update(zip(("input_tokens", "output_tokens", "total_tokens"), token_sums))
        return {
            "periods": periods,
            "timezone": "UTC",
            "attribution": "session_initiator",
            "note": "Estimated compute cost, не счёт RunPod. Mock-запросы считаются отдельно от GPU.",
        }


@router.get("/usage/me")
def usage(request: Request, user: User = Depends(current_user)):
    return usage_for(user.id, controller(request).clock())


@router.get("/sessions/me")
def sessions_me(
    request: Request,
    user: User = Depends(current_user),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    with SessionLocal() as db:
        rows = db.scalars(
            select(ComputeSession)
            .where(ComputeSession.started_by_user_id == user.id, ComputeSession.managed.is_(True))
            .order_by(ComputeSession.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return [controller(request).session_out(row) for row in rows]


@admin.get("/compute/current")
def admin_current(request: Request, user: User = Depends(admin_user)):
    return controller(request).get_compute_status(user)


@admin.get("/users")
def users(
    request: Request,
    user: User = Depends(admin_user),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    admins = {email.lower() for email in controller(request).settings.admin_emails}
    with SessionLocal() as db:
        return [
            {
                "id": row.id,
                "email": row.email,
                "role": "admin" if row.email in admins else "user",
                "created_at": row.created_at,
            }
            for row in db.scalars(select(User).order_by(User.created_at).offset(offset).limit(limit))
        ]


@admin.get("/compute/sessions")
def all_sessions(
    request: Request,
    user: User = Depends(admin_user),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    with SessionLocal() as db:
        return [
            controller(request).session_out(row, True)
            for row in db.scalars(
                select(ComputeSession).order_by(ComputeSession.created_at.desc()).offset(offset).limit(limit)
            )
        ]


@admin.get("/compute/usage")
def all_usage(
    request: Request,
    user: User = Depends(admin_user),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    with SessionLocal() as db:
        rows = db.scalars(select(User).order_by(User.created_at).offset(offset).limit(limit)).all()
    return [
        {"user_id": row.id, "email": row.email, **usage_for(row.id, controller(request).clock())}
        for row in rows
    ]


@admin.get("/compute/events")
def events(request: Request, user: User = Depends(admin_user), limit: int = Query(100, ge=1, le=200)):
    with SessionLocal() as db:
        return [
            {
                "id": row.id,
                "session_id": row.session_id,
                "kind": row.kind,
                "code": row.code,
                "created_at": row.created_at,
            }
            for row in db.scalars(select(ComputeEvent).order_by(ComputeEvent.created_at.desc()).limit(limit))
        ]


@admin.post("/compute/reconcile")
async def reconcile(request: Request, user: User = Depends(admin_user)):
    await controller(request).tick()
    return controller(request).get_compute_status(user)


@admin.post("/compute/billing/refresh")
async def billing(request: Request, user: User = Depends(admin_user)):
    return await controller(request).refresh_billing(user)
