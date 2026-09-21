"""Typed backup API: create, list, verify and diagnose. Restore is never HTTP.

A restore has to stop the owned backend and swap the live database, so it is a Desktop
operation (``restore_backup`` in ``apps/desktop/src-tauri/src/backup.rs``) that runs the
sidecar in its one-shot ``--restore-backup`` mode. Nothing on this router can overwrite
the running database.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import BackupEvent, User
from ..security import current_user
from .archive import BackupService, verify_backup
from .format import BACKUP_ID_PATTERN, BackupError, sqlite_revision
from .restore import read_result

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backup", tags=["backup"])


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="", max_length=120)


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backup_id: str = Field(min_length=1, max_length=80)


def service_of(request: Request) -> BackupService:
    service = getattr(request.app.state, "backup", None)
    if service is None:
        settings = get_settings()
        from ..data_paths import application_data

        service = BackupService(
            application_data(settings),
            keep_automatic=settings.backup_keep_automatic,
            keep_manual=settings.backup_keep_manual,
        )
        request.app.state.backup = service
    return service


def _head_revision() -> str | None:
    from .restore import _alembic_chain

    chain = _alembic_chain()
    return chain[-1] if chain else None


def _record(
    db: Session,
    *,
    kind: str,
    status: str,
    backup_id: str,
    result: dict | None = None,
    code: str = "",
    detail: str = "",
) -> None:
    payload = result or {}
    db.add(
        BackupEvent(
            kind=kind[:32],
            status=status[:32],
            backup_id=str(backup_id)[:96],
            code=code[:64],
            detail=detail[:400],
            app_version=str(payload.get("app_version") or "")[:32],
            schema_revision=str(payload.get("schema_revision") or "")[:32],
            size_bytes=int(payload.get("bytes") or 0),
        )
    )
    db.commit()


def _failure(error: BackupError, status_code: int = 400) -> HTTPException:
    logger.info("backup_api_error code=%s", error.code)
    return HTTPException(status_code, error.message)


@router.get("")
async def list_backups(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    service = service_of(request)
    return {
        "state": service.state(),
        "backups": service.list_backups(),
        "last_restore": read_result(service.root),
        "keep": {"automatic": service.keep_automatic, "manual": service.keep_manual},
    }


@router.post("")
async def create_backup(
    request: Request,
    body: CreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    service = service_of(request)
    try:
        result = await service.create(label=body.label)
    except BackupError as error:
        _record(db, kind="manual", status="failed", backup_id="", code=error.code, detail=error.message)
        raise _failure(error, 409 if error.code == "backup_busy" else 400) from None
    _record(db, kind="manual", status="created", backup_id=result.get("id", ""), result=result)
    return result


@router.post("/verify")
async def verify(
    request: Request,
    body: VerifyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    service = service_of(request)
    try:
        result = await service.verify(body.backup_id)
    except BackupError as error:
        _record(db, kind="verify", status="failed", backup_id=body.backup_id, code=error.code)
        raise _failure(error, 409 if error.code == "backup_busy" else 400) from None
    _record(db, kind="verify", status="verified", backup_id=body.backup_id, result=result)
    return result


@router.get("/diagnostic")
async def diagnostic(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    service = service_of(request)
    settings = get_settings()
    payload = service.diagnostic(
        schema_revision=sqlite_revision(service.live_database),
        head_revision=_head_revision(),
        last_migration=None,
    )
    payload["mode"] = settings.alex_ai_mode
    payload["restore_supported"] = True
    return payload


def verify_directory_for_tests(path, *, write_marker: bool = False) -> dict:
    """Small indirection so tests can verify an imported directory without the service."""
    return verify_backup(path, write_marker=write_marker)


def valid_backup_id(value: str) -> bool:
    return bool(BACKUP_ID_PATTERN.fullmatch(value or ""))
