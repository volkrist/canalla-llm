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


def ai_source(request: Request):
    """Direct mode: the RunPod controller. Shared mode: the Alex Cloud adapter."""
    return getattr(request.app.state, "ai_status_source", None) or request.app.state.compute


@router.get("")
async def status(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Five subsystems plus the shared RunPod account balance for any authenticated user."""
    settings = get_settings()
    cloud = getattr(request.app.state, "cloud", None)
    if cloud is not None and cloud.shared:
        # Read-only refresh so the chips describe the last known Gateway state, and never
        # make the user wait for a provider round trip on every poll.
        await cloud.refresh()
    return {
        "generated_at": now().isoformat(),
        "subsystems": subsystem_status(db, user, ai_source(request), settings),
        "balance": await balance_service(request).snapshot(),
    }
