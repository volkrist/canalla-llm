"""Gateway HTTP surface.

Typed operations only. There is deliberately no ``/runpod/*``, ``/provider/raw`` or any
other passthrough: a client can never ask the Gateway to execute an arbitrary provider
command, and no endpoint ever returns the RunPod master key, the installation secret, a
token-minting secret or a direct llama.cpp endpoint.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from .config import GatewaySettings
from .errors import GatewayError
from .models import AuditEvent, Installation
from .security import (
    hash_secret,
    installation_for_token,
    issue_token,
    new_installation_secret,
    now,
    redeem_code,
    utc,
)

router = APIRouter()

REQUIRED_TABLES = {"installations", "gateway_compute", "gateway_sessions", "audit_events"}


def settings_of(request: Request) -> GatewaySettings:
    return request.app.state.settings


def db_dependency(request: Request):
    with request.app.state.session_factory() as session:
        yield session


async def current_installation(
    request: Request,
    db: Annotated[Session, Depends(db_dependency)],
    authorization: Annotated[str, Header()] = "",
    x_alex_protocol_version: Annotated[str, Header()] = "",
) -> Installation:
    settings = settings_of(request)
    if x_alex_protocol_version and x_alex_protocol_version.strip() != str(settings.gateway_protocol_version):
        raise GatewayError("gateway_protocol_mismatch")
    if not authorization.lower().startswith("bearer "):
        raise GatewayError("gateway_auth_failed")
    installation = installation_for_token(db, settings, authorization.split(" ", 1)[1].strip())
    installation.last_seen_at = now()
    db.commit()
    request.state.installation = installation
    return installation


InstallationDep = Annotated[Installation, Depends(current_installation)]


class EnrollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activation_code: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=120)
    platform: str = Field(default="", max_length=40)
    client_version: str = Field(default="", max_length=40)
    gateway_protocol_version: int | None = None


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    installation_id: str = Field(min_length=8, max_length=64)
    installation_secret: str = Field(min_length=16, max_length=200)
    gateway_protocol_version: int | None = None


class EnsureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    max_hourly_price: float | None = Field(default=None, gt=0, le=1000)
    session_budget: float | None = Field(default=None, gt=0, le=10000)
    auto_stop_minutes: int | None = Field(default=None, ge=0, le=240)
    min_vram_gb: int | None = Field(default=None, ge=1, le=1024)
    selection: Literal["automatic", "manual"] | None = Field(default=None)
    gpu_id: str | None = Field(default=None, max_length=160)


class StopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=64)


class RevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="self_revoked", max_length=200)


def database_state(request: Request) -> Literal["ok", "missing_schema", "error"]:
    try:
        with request.app.state.session_factory() as db:
            connection = db.connection()
            present = set(inspect(connection).get_table_names())
    except Exception:
        return "error"
    return "ok" if REQUIRED_TABLES <= present else "missing_schema"


@router.get("/health")
async def health(request: Request) -> dict:
    settings = settings_of(request)
    state = database_state(request)
    return {
        "product": settings.product,
        "version": settings.version,
        "gateway_protocol_version": settings.gateway_protocol_version,
        "ready": state == "ok",
        # Coarse readiness only: a probe learns nothing about accounts or credentials.
        "database": state,
        "provider_configured": settings.runpod_configured,
        "time": now().isoformat(),
    }


@router.post("/enroll")
async def enroll(
    request: Request,
    payload: EnrollRequest,
    db: Annotated[Session, Depends(db_dependency)],
    x_forwarded_for: Annotated[str, Header()] = "",
) -> dict:
    settings = settings_of(request)
    if payload.gateway_protocol_version not in (None, settings.gateway_protocol_version):
        raise GatewayError("gateway_protocol_mismatch")
    client = (x_forwarded_for.split(",")[0].strip() if x_forwarded_for else "") or _peer(request)
    request.app.state.limiter.check("enroll", hash_secret(client)[:16])

    secret = new_installation_secret()
    installation = Installation(
        name=payload.name.strip(),
        platform=payload.platform.strip(),
        client_version=payload.client_version.strip(),
        secret_hash=hash_secret(secret),
        created_at=now(),
        last_seen_at=now(),
        meta={},
    )
    db.add(installation)
    try:
        db.flush()
        redeem_code(db, payload.activation_code, installation)
        db.add(AuditEvent(operation="enroll", result="ok", installation_id=installation.id, created_at=now()))
        db.commit()
    except GatewayError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise GatewayError("activation_code_rejected") from None
    return {
        "installation_id": installation.id,
        "installation_secret": secret,
        "gateway_protocol_version": settings.gateway_protocol_version,
        "product": settings.product,
        "version": settings.version,
        # The client stores this in the OS credential store; this is the only emission.
        "issued_at": now().isoformat(),
    }


@router.post("/auth/token")
async def token(
    request: Request,
    payload: TokenRequest,
    db: Annotated[Session, Depends(db_dependency)],
) -> dict:
    settings = settings_of(request)
    if payload.gateway_protocol_version not in (None, settings.gateway_protocol_version):
        raise GatewayError("gateway_protocol_mismatch")
    request.app.state.limiter.check("token", payload.installation_id)
    installation = db.get(Installation, payload.installation_id)
    if installation is None:
        raise GatewayError("installation_unknown", 401)
    if installation.revoked_at is not None:
        raise GatewayError("installation_revoked")
    if installation.secret_hash != hash_secret(payload.installation_secret):
        raise GatewayError("gateway_auth_failed")
    installation.last_seen_at = now()
    db.commit()
    result = issue_token(settings, installation.id)
    result["installation_id"] = installation.id
    return result


@router.post("/auth/revoke")
async def revoke(
    request: Request,
    payload: RevokeRequest,
    installation: InstallationDep,
    db: Annotated[Session, Depends(db_dependency)],
) -> dict:
    installation.revoked_at = now()
    installation.revoked_reason = payload.reason[:200]
    db.add(AuditEvent(operation="revoke", result="ok", installation_id=installation.id, created_at=now()))
    db.commit()
    return {
        "revoked": True,
        "installation_id": installation.id,
        "revoked_at": utc(installation.revoked_at).isoformat(),
    }


@router.get("/compute/status")
async def compute_status(request: Request, installation: InstallationDep) -> dict:
    request.app.state.limiter.check("compute", installation.id)
    return await request.app.state.authority.status()


@router.post("/compute/ensure")
async def compute_ensure(request: Request, payload: EnsureRequest, installation: InstallationDep) -> dict:
    request.app.state.limiter.check("compute", installation.id)
    caps = {
        "max_hourly_price": payload.max_hourly_price,
        "session_budget": payload.session_budget,
        "auto_stop_minutes": payload.auto_stop_minutes,
        "min_vram_gb": payload.min_vram_gb,
        "selection": payload.selection,
        "gpu_id": payload.gpu_id,
    }
    return await request.app.state.authority.ensure(
        installation.id,
        operation_id=payload.operation_id,
        task_id=payload.task_id,
        caps={key: value for key, value in caps.items() if value is not None},
    )


@router.post("/compute/stop")
async def compute_stop(request: Request, payload: StopRequest, installation: InstallationDep) -> dict:
    request.app.state.limiter.check("compute", installation.id)
    return await request.app.state.authority.stop(installation.id, operation_id=payload.operation_id)


@router.get("/balance")
async def balance(request: Request, installation: InstallationDep) -> dict:
    request.app.state.limiter.check("compute", installation.id)
    return await request.app.state.authority.balance.snapshot()


@router.get("/v1/models")
async def models(request: Request, installation: InstallationDep) -> dict:
    request.app.state.limiter.check("inference", installation.id)
    return await request.app.state.proxy.models()


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    installation: InstallationDep,
    x_alex_task_id: Annotated[str, Header()] = "",
    x_alex_request_id: Annotated[str, Header()] = "",
):
    request.app.state.limiter.check("inference", installation.id)
    try:
        body = await request.json()
    except Exception:
        raise GatewayError("gateway_invalid_request", detail="Тело запроса не является JSON.") from None
    if not isinstance(body, dict):
        raise GatewayError("gateway_invalid_request", detail="Тело запроса должно быть объектом.")
    return await request.app.state.proxy.completions(
        body,
        installation_id=installation.id,
        task_id=x_alex_task_id or None,
        request_id=x_alex_request_id or None,
    )


def _peer(request: Request) -> str:
    return request.client.host if request.client else "unknown"
