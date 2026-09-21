from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Credentials(BaseModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        return str(value).lower()


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    display_name: str
    role: Literal["user", "admin"]
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    session_id: str | None = None
    refresh_secret: str | None = None
    expires_at: datetime | None = None
    user: UserOut | None = None


class BootstrapRequest(Credentials):
    display_name: str | None = Field(default=None, max_length=80)


class RefreshRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    refresh_secret: str = Field(min_length=1, max_length=256)


class RevokeRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    refresh_secret: str = Field(min_length=1, max_length=256)


class AuthStateOut(BaseModel):
    users_exist: bool
    state: Literal["first_run", "auth_required"]


class ChatCreate(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=120)


class ChatOut(ChatCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime
    updated_at: datetime
    pinned: bool
    project_id: str | None


class ChatUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    pinned: bool | None = None
    project_id: str | None = None


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=32000)
    web_mode: Literal["off", "auto", "on"] | None = None
    computer_mode: Literal["off", "ask", "trusted"] | None = None
    tor_mode: Literal["off", "auto", "on"] | None = None

    @field_validator("content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Message cannot be blank")
        return value.strip()


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    chat_id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    status: str
    edited_at: datetime | None
    generation_started_at: datetime | None = None
    first_token_at: datetime | None = None
    completed_at: datetime | None = None
    ttft_ms: int | None = None
    cancellation: dict | None = None
