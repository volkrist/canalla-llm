from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import Settings


class ComputePreferences(BaseModel):
    """One local user's own compute policy.

    These are **preferences, not product caps**: every authenticated user may raise or lower
    ``max_hourly_price`` and ``session_budget`` for themselves. The Gateway enforces the
    value the caller sent (within technical bounds only) and never silently clamps it to a
    product ceiling. Defaults (docs/release-1.0.md): automatic cheapest compatible GPU,
    48 GB VRAM floor, $0.52/hour and $3.00 per session.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    selection: Literal["automatic", "manual"] = "automatic"
    min_vram_gb: int = Field(default=48, ge=1, le=1024)
    max_hourly_price: Decimal = Field(default=Decimal("0.52"), gt=0, le=100, max_digits=8, decimal_places=4)
    session_budget: Decimal = Field(default=Decimal("3.00"), gt=0, le=1000, max_digits=9, decimal_places=4)
    auto_stop_minutes: Literal[0, 5, 10, 15, 30] = 10
    gpu_id: str | None = Field(default=None, min_length=1, max_length=160)
    auto_connect: bool = False
    auto_search: bool = True
    search_interval: int = Field(default=30, ge=15, le=300)
    # The second provider cloud tier. A *visible* choice, never a hidden behaviour change: the
    # default is off, and the allocator only admits the tier when the deployment's own policy
    # permits it as well (see docs/compute-preferences.md §11).
    allow_community: bool = False

    def enforce(self, settings: Settings):
        if self.min_vram_gb < settings.runpod_min_vram_gb:
            raise ValueError(f"Минимум VRAM на сервере: {settings.runpod_min_vram_gb} GB")
        if self.auto_connect and self.selection == "manual" and not self.gpu_id:
            raise ValueError("Для автоподключения выберите точный GPU или автоматический выбор")
        return self

    @classmethod
    def defaults(cls, settings: Settings):
        """New users start on the cheap automatic policy; they own it from then on."""
        return cls(
            selection="automatic",
            gpu_id=None,
            min_vram_gb=settings.runpod_min_vram_gb,
            max_hourly_price=settings.runpod_default_hourly_price,
            session_budget=settings.runpod_default_session_budget,
            auto_stop_minutes=settings.runpod_auto_stop_minutes,
            auto_search=True,
            search_interval=settings.runpod_search_interval,
        )


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote_id: str = Field(min_length=36, max_length=36)
    gpu_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    confirmed: Literal[True]


class StopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    after_generation: bool = False
    confirm_external: bool = False


class GpuOption(BaseModel):
    id: str
    name: str
    vram_gb: int
    hourly_rate: Decimal
    availability: str
    compatible: bool
    selectable: bool
    reason: str | None = None


class PodInfo(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False, populate_by_name=True)
    id: str = Field(min_length=1, max_length=64)
    name: str
    status: Literal["PROVISIONING", "STARTING", "RUNNING", "EXITED", "ERROR", "TERMINATED"]
    cost: Decimal = Field(ge=0)
    started_at: str | None = Field(default=None, alias="startedAt")
    data_center: str | None = Field(default=None, alias="dataCenterId")
    mounts: dict = Field(default_factory=dict)
    gpu: dict = Field(default_factory=dict)

    @field_validator("started_at")
    @classmethod
    def valid_start(cls, value):
        if value is not None:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Supplier timestamp must include a timezone")
        return value


class VolumeInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    size: int = Field(ge=1)
    dataCenter: str
    type: str
