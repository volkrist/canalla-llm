"""Alex Cloud status and Gateway-owned compute operations.

Read-only by default: ``GET /cloud/status`` never starts anything. The two explicit
operations are typed Gateway calls (``ensure``/``stop``) with a local operation id, and
the Gateway stays the final authority for money, ownership and the global lease.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import User
from ..security import current_user
from .client import CloudError
from .state import CloudState

router = APIRouter(prefix="/cloud", tags=["cloud"])


class ComputeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str | None = Field(default=None, max_length=64)
    auto_stop_minutes: int | None = Field(default=None, ge=0, le=240)


def cloud_of(request: Request) -> CloudState:
    cloud = getattr(request.app.state, "cloud", None)
    if cloud is None:
        raise HTTPException(503, "Canalla Cloud недоступен в этой конфигурации")
    return cloud


def raw(request: Request):
    return request


@router.get("/status")
async def status(request: Request, user: User = Depends(current_user)) -> dict:
    cloud = cloud_of(request)
    await cloud.refresh()
    return cloud.snapshot()


@router.post("/compute/ensure")
async def ensure_compute(
    request: Request,
    body: ComputeRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Ask the Gateway for shared compute. The Gateway owns whether a Pod is created."""
    cloud = cloud_of(request)
    controller = getattr(request.app.state, "compute", None)
    prefs = controller.preferences(user.id) if controller is not None else None
    caps = {}
    if prefs is not None:
        caps = {
            "max_hourly_price": float(prefs.max_hourly_price),
            "session_budget": float(prefs.session_budget),
            "min_vram_gb": int(prefs.min_vram_gb),
            "selection": prefs.selection,
            "gpu_id": prefs.gpu_id,
        }
    if body.auto_stop_minutes is not None:
        caps["auto_stop_minutes"] = body.auto_stop_minutes
    try:
        payload = await cloud.client.ensure_compute(
            operation_id="local-" + str(uuid4()), task_id=body.task_id, caps=caps
        )
    except CloudError as error:
        raise _http(error) from None
    cloud.compute = payload
    cloud.fetched_at = cloud.clock()
    result = cloud.snapshot()
    result["compute"] = payload
    return result


@router.post("/compute/stop")
async def stop_compute(request: Request, user: User = Depends(current_user)) -> dict:
    cloud = cloud_of(request)
    try:
        payload = await cloud.client.stop_compute(operation_id="local-" + str(uuid4()))
    except CloudError as error:
        raise _http(error) from None
    cloud.compute = payload
    cloud.fetched_at = cloud.clock()
    result = cloud.snapshot()
    result["compute"] = payload
    return result


def _http(error: CloudError) -> HTTPException:
    status_code = {
        "gateway_not_connected": 503,
        "gateway_unavailable": 503,
        "gateway_busy": 429,
        "gateway_queue_full": 429,
        "installation_revoked": 409,
        "gateway_auth_failed": 409,
        "gateway_protocol_mismatch": 409,
        "gateway_budget_denied": 409,
    }.get(error.code, 502)
    return HTTPException(status_code, str(error) or error.code)


def settings_of(request: Request):
    return get_settings()
