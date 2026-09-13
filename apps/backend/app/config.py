from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: Literal["development", "production", "test"] = "development"
    database_url: str = "sqlite:///./alex.db"
    jwt_secret: str = Field(min_length=32)
    jwt_expire_minutes: int = Field(default=60, ge=1, le=1440)
    cors_origins: list[str] = [
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    ]
    llm_provider: Literal["mock", "llamacpp"] = "mock"
    llm_base_url: str = "http://127.0.0.1:8080"
    llm_model: str = "orcarouter-qwen38-27b-q5km"
    llm_api_key: str = ""
    mock_delay: float = Field(default=0.035, ge=0, le=1)

    @model_validator(mode="after")
    def validate_security(self):
        if self.jwt_secret.startswith("replace-"):
            raise ValueError("Generate JWT_SECRET; the example placeholder is not a secret")
        if any("*" in origin for origin in self.cors_origins):
            raise ValueError("CORS origins must be explicit")
        if self.app_env == "production":
            if any(not origin.startswith(("https://", "tauri://")) for origin in self.cors_origins):
                raise ValueError("Production CORS must use HTTPS or tauri://localhost")
            if len(self.jwt_secret) < 48:
                raise ValueError("Production JWT_SECRET must be at least 48 characters")
        if urlparse(self.llm_base_url).scheme not in {"http", "https"}:
            raise ValueError("LLM_BASE_URL must be an HTTP(S) URL")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
