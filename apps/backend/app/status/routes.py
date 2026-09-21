"""Authenticated, read-only status surface. Nothing here can change compute state."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import User, now
from ..security import current_user
from .balance import RunPodBalanceService
from .snapshot import subsystem_status

router = APIRouter(prefix="/status", tags=["status"])


def balance_service(request: Request) -> RunPodBalanceService:
    service = getattr(request.app.state, "balance_override", None) or getattr(
        request.app.state, "balance", None
    )
    if service is None:
        compute = request.app.state.compute
        service = RunPodBalanceService(compute.api, compute.gpu_active, compute.active_session)
        request.app.state.balance = service
    return service


@router.get("")
async def status(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Five subsystems plus the shared RunPod account balance for any authenticated user."""
    settings = get_settings()
    return {
        "generated_at": now().isoformat(),
        "subsystems": subsystem_status(db, user, request.app.state.compute, settings),
        "balance": await balance_service(request).snapshot(),
    }
