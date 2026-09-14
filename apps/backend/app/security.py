from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import User

password_hash = PasswordHash.recommended()
dummy_hash = password_hash.hash("dummy-password-for-timing-only")
bearer = HTTPBearer(auto_error=False)


def create_token(user_id: str):
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "iat": now,
            "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
            "iss": "alex-llm",
            "aud": "alex-desktop",
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> User:
    error = HTTPException(401, "Session expired. Please sign in.", headers={"WWW-Authenticate": "Bearer"})
    if credentials is None:
        raise error
    try:
        payload = jwt.decode(
            credentials.credentials,
            get_settings().jwt_secret,
            algorithms=["HS256"],
            issuer="alex-llm",
            audience="alex-desktop",
            options={"require": ["sub", "exp", "iat"]},
        )
        user = db.get(User, payload["sub"])
    except (jwt.InvalidTokenError, TypeError, ValueError):
        raise error from None
    if user is None:
        raise error
    sync_role(user, db)
    return user


def sync_role(user: User, db: Session):
    role = (
        "admin" if user.email.lower() in {email.lower() for email in get_settings().admin_emails} else "user"
    )
    if user.role != role:
        user.role = role
        db.commit()


def admin_user(user: User = Depends(current_user)):
    if user.role != "admin":
        raise HTTPException(403, "Требуются права администратора")
    return user


def compute_user(user: User = Depends(current_user)):
    if user.role != "admin" and not get_settings().allow_user_compute_start:
        raise HTTPException(403, "Запуск и остановка GPU доступны только администратору")
    return user
