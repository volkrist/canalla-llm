from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .schemas import Credentials, TokenOut, UserOut
from .security import create_token, current_user, dummy_hash, password_hash, sync_role

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: Credentials, db: Session = Depends(get_db)):
    user = User(email=body.email, password_hash=password_hash.hash(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This email is already registered") from None
    sync_role(user, db)
    return TokenOut(access_token=create_token(user.id))


@router.post("/login", response_model=TokenOut)
def login(body: Credentials, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email))
    valid = password_hash.verify(body.password, user.password_hash if user else dummy_hash)
    if not user or not valid:
        raise HTTPException(401, "Incorrect email or password")
    return TokenOut(access_token=create_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user
