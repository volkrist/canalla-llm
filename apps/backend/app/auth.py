import hmac
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_db
from .models import AuthSession, BootstrapClaim, User
from .schemas import (
    AuthStateOut,
    BootstrapRequest,
    Credentials,
    RefreshRequest,
    RevokeRequest,
    TokenOut,
    UserOut,
)
from .security import create_token, current_user, dummy_hash, password_hash, sync_role
from .session_auth import (
    SessionRejected,
    create_session,
    purge_stale,
    refresh_session,
    revoke_session,
)

router = APIRouter(prefix="/auth", tags=["auth"])

BOOTSTRAP_CLOSED = "Владелец этого компьютера уже создан"


def _device_id(request: Request) -> str | None:
    value = (request.headers.get("X-Alex-Device-Id") or "").strip()
    if not value or len(value) > 64 or not all(c.isalnum() or c in "._-" for c in value):
        return None
    return value


def _session_payload(user: User, db: Session, device_id: str | None) -> TokenOut:
    session_id, raw = create_session(db, user, device_id)
    return TokenOut(
        access_token=create_token(user.id),
        session_id=session_id,
        refresh_secret=raw,
        expires_at=db.get(AuthSession, session_id).expires_at,
        user=UserOut.model_validate(user),
    )


def _require_runtime_proof(request: Request) -> None:
    """First-owner bootstrap requires the local desktop runtime token.

    The token lives in `runtime/shutdown.token`, is handed to the owned backend
    process as `ALEX_RUNTIME_TOKEN` and is sent by the Desktop (not by generic
    web content). Arbitrary loopback/remote clients do not know it.
    """
    expected = os.environ.get("ALEX_RUNTIME_TOKEN") or ""
    got = request.headers.get("X-Alex-Runtime-Token") or ""
    if not expected or len(expected) != len(got) or not hmac.compare_digest(expected, got):
        raise HTTPException(403, "Создание владельца доступно только из установленного приложения")


def claim_first_owner(db: Session, email: str, password: str, display_name: str | None) -> User:
    """Race-safe first-owner claim. Exactly one caller can ever succeed.

    The singleton `bootstrap_claim` row (id=1) is the authoritative guard: a
    concurrent second caller commits after the winner and hits the primary-key
    constraint, rolling back its own user row as well. The pre-check on the
    users count is only an early exit.
    """
    if (db.scalar(select(func.count()).select_from(User)) or 0) > 0:
        raise HTTPException(409, BOOTSTRAP_CLOSED)
    user = User(
        email=email,
        password_hash=password_hash.hash(password),
        display_name=(display_name or "").strip()[:80] or "Пользователь",
        is_owner=True,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This email is already registered") from None
    db.add(BootstrapClaim(id=1, owner_user_id=user.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, BOOTSTRAP_CLOSED) from None
    return user


@router.post("/bootstrap", response_model=TokenOut, status_code=201)
def bootstrap(body: BootstrapRequest, request: Request, db: Session = Depends(get_db)):
    _require_runtime_proof(request)
    user = claim_first_owner(db, body.email, body.password, body.display_name)
    sync_role(user, db)
    return _session_payload(user, db, _device_id(request))


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: Credentials, request: Request, db: Session = Depends(get_db)):
    user = User(email=body.email, password_hash=password_hash.hash(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This email is already registered") from None
    sync_role(user, db)
    purge_stale(db)
    return _session_payload(user, db, _device_id(request))


@router.post("/login", response_model=TokenOut)
def login(body: Credentials, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email))
    valid = password_hash.verify(body.password, user.password_hash if user else dummy_hash)
    if not user or not valid:
        raise HTTPException(401, "Incorrect email or password")
    sync_role(user, db)
    purge_stale(db)
    return _session_payload(user, db, _device_id(request))


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    try:
        user, row, raw = refresh_session(db, body.session_id, body.refresh_secret, _device_id(request))
    except SessionRejected:
        raise HTTPException(401, "Сеанс недействителен. Войдите снова.") from None
    return TokenOut(
        access_token=create_token(user.id),
        session_id=row.id,
        refresh_secret=raw,
        expires_at=row.expires_at,
        user=UserOut.model_validate(user),
    )


@router.post("/revoke")
def revoke(body: RevokeRequest, db: Session = Depends(get_db)):
    """Revoke a persistent session by possession of its credential (idempotent)."""
    return {"revoked": revoke_session(db, body.session_id, body.refresh_secret)}


@router.get("/state", response_model=AuthStateOut)
def state(db: Session = Depends(get_db)):
    exists = bool(db.scalar(select(func.count()).select_from(User)) or 0)
    return AuthStateOut(users_exist=exists, state="auth_required" if exists else "first_run")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user
