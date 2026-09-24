"""Central Alex Gateway configuration.

The Gateway is the only place a RunPod master credential exists in production shared
mode. Everything provider-related is configured server-side here; clients can never widen
*a technical* limit, but a money policy (maximum $/hour, session budget) belongs to the
user who pays attention to it: see ``DEFAULT_*`` and ``ABSOLUTE_*`` below.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default policy for a request that carries none (new users start here). These are defaults,
# NOT product ceilings: an authenticated installation enforces the value it sent.
DEFAULT_MAX_HOURLY_PRICE = Decimal("0.52")
DEFAULT_SESSION_BUDGET = Decimal("3.00")

# Technical validity bounds only. They exist to reject nonsense (negative, zero, NaN,
# Infinity, parser overflow) and absurd abuse, not to cap a legitimate user's own policy.
ABSOLUTE_MAX_HOURLY_PRICE = Decimal("100")
ABSOLUTE_MAX_SESSION_BUDGET = Decimal("1000")


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    app_env: Literal["development", "production", "test"] = "development"
    gateway_protocol_version: int = 1
    product: str = "alex-llm-gateway"
    version: str = "1.2.0"

    database_url: str = "sqlite:///./gateway.db"

    # Signing key for short-lived installation access tokens. Required at startup: an empty
    # value fails validation, so a missing JWT_SECRET still cannot sign a token.
    jwt_secret: str = Field(default="", min_length=32)
    jwt_expire_minutes: int = Field(default=15, ge=1, le=120)
    jwt_issuer: str = "alex-gateway"
    jwt_audience: str = "alex-installation"

    activation_ttl_minutes: int = Field(default=60, ge=1, le=1440)
    # Sliding-window rate limits (per key per window). In-process; see docs for the
    # multi-worker note.
    rate_window_seconds: int = Field(default=60, ge=1, le=600)
    enrollment_rate_limit: int = Field(default=20, ge=1, le=1000)
    token_rate_limit: int = Field(default=120, ge=1, le=10000)
    compute_rate_limit: int = Field(default=30, ge=1, le=1000)
    inference_rate_limit: int = Field(default=60, ge=1, le=10000)

    # RunPod master credential: server-side only. Never returned, never stored in the DB.
    runpod_api_key: SecretStr = SecretStr("")
    runpod_graphql_url: str = "https://api.runpod.io/graphql"
    runpod_network_volume_id: str = "uwgeaie5b0"
    runpod_datacenter: str = "US-TX-3"
    runpod_min_vram_gb: int = Field(default=48, ge=1, le=1024)
    runpod_min_cuda_version: str = "12.8"
    runpod_image: str = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"
    runpod_llm_port: int = Field(default=8080, ge=1024, le=65535)
    runpod_gateway_port: int = Field(default=9000, ge=1024, le=65535)
    runpod_startup_timeout: int = Field(default=900, ge=60, le=3600)
    # D-9: a managed Pod that never becomes ready must not bill indefinitely. This is the
    # server-side watchdog deadline (default 5 minutes), *not* ``runpod_startup_timeout``
    # (how long a direct-mode client waits on the provider). It is measured from the persisted
    # create intent, so it survives a client or Gateway restart, and it is server-side only:
    # no client may set, widen or disable it.
    compute_startup_timeout_seconds: int = Field(default=300, ge=60, le=3600)
    # The 60-second capacity rule, server-side: a search that found no bookable GPU is a real
    # operation with an identity and this deadline — never a state that keeps answering
    # `searching` forever. The hard ceiling is 60 s on purpose: a client cannot widen it, and a
    # server operator cannot configure an unbounded "searching" (that is the defect this fixes).
    compute_search_timeout_seconds: int = Field(default=60, ge=5, le=60)
    llm_provider: Literal["mock", "llamacpp"] = "llamacpp"
    llm_model: str = "orcarouter-qwen38-27b-q5km"
    llm_api_key: str = ""

    # Policy defaults for a request without caps (NOT ceilings; see the module docstring).
    max_hourly_price: Decimal = Field(default=DEFAULT_MAX_HOURLY_PRICE, gt=0, le=100)
    max_session_budget: Decimal = Field(default=DEFAULT_SESSION_BUDGET, gt=0, le=1000)
    compute_idle_minutes: int = Field(default=10, ge=1, le=240)
    compute_poll_seconds: float = Field(default=5, ge=1, le=60)
    balance_active_seconds: float = Field(default=5, ge=1, le=300)
    balance_idle_seconds: float = Field(default=15, ge=1, le=600)

    # Inference policy: llama.cpp runs with --parallel 1, so the Gateway serializes.
    inference_queue_size: int = Field(default=8, ge=0, le=64)
    inference_queue_timeout_seconds: float = Field(default=120, ge=1, le=1800)
    inference_timeout_seconds: float = Field(default=900, ge=10, le=3600)
    inference_upstream_timeout_seconds: float = Field(default=120, ge=5, le=1800)

    # Desktop updates: a published manifest file (or inline JSON for tests). Public data only -
    # the signature lives in the manifest, the signing key never reaches this service.
    updates_manifest_path: str = ""
    updates_manifest_json: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def validate_policy(self):
        if self.app_env == "production":
            if len(self.jwt_secret) < 48:
                raise ValueError("Production JWT_SECRET must be at least 48 characters")
            if self.jwt_secret.startswith("replace-"):
                raise ValueError("Generate JWT_SECRET; the placeholder is not a secret")
        if self.max_hourly_price > ABSOLUTE_MAX_HOURLY_PRICE:
            raise ValueError("MAX_HOURLY_PRICE above the technical bound")
        if self.max_session_budget > ABSOLUTE_MAX_SESSION_BUDGET:
            raise ValueError("MAX_SESSION_BUDGET above the technical bound")
        return self

    @property
    def runpod_configured(self) -> bool:
        return bool(self.runpod_api_key.get_secret_value())


@lru_cache
def get_settings() -> GatewaySettings:
    return GatewaySettings()
