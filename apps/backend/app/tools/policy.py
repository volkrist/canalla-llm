import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import RiskLevel, ToolError
from .models import ToolPreferences


class WebSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search_enabled: bool = True
    fetch_enabled: bool = True
    default_mode: Literal["off", "auto", "on"] = "auto"
    agent_enabled: bool = False
    agent_run_budget: float = Field(default=0.25, gt=0, le=10)
    agent_daily_budget: float = Field(default=1, gt=0, le=50)
    agent_max_runtime: int = Field(default=120, ge=10, le=600)
    browser_enabled: bool = False
    tor_enabled: bool = False
    computer_mode: Literal["off", "ask", "trusted"] = "ask"
    workspace_roots: list[str] = Field(default_factory=list, max_length=8)
    device_display_name: str = Field(default="", max_length=80)


def preferences(db, user_id):
    row = db.get(ToolPreferences, user_id)
    return WebSettings.model_validate(row.values if row else {})


@dataclass
class ToolLimits:
    max_calls: int = 8
    max_search: int = 3
    max_fetch: int = 3
    max_pages: int = 10
    max_chars: int = 20000
    max_seconds: float = 180
    calls: int = 0
    searches: int = 0
    fetches: int = 0
    pages: int = 0
    chars: int = 0
    started: float = field(default_factory=time.monotonic)

    @property
    def remaining(self):
        return max(0, self.max_seconds - (time.monotonic() - self.started))

    def consume(self, definition, args):
        if self.remaining <= 0 or self.calls >= self.max_calls:
            raise ToolError("tool_limit")
        pages = len(getattr(args, "urls", []))
        searching = definition.capability in {"search", "tor_search"}
        fetching = definition.capability in {"fetch", "tor_fetch"}
        if searching and self.searches >= self.max_search:
            raise ToolError("search_limit")
        if fetching and (self.fetches >= self.max_fetch or self.pages + pages > self.max_pages):
            raise ToolError("page_limit")
        self.calls += 1
        self.searches += searching
        self.fetches += fetching
        self.pages += pages


WEB_CAPABILITIES = {"search", "fetch", "agent", "browser"}
TOR_CAPABILITIES = {"tor_search", "tor_fetch"}
LOCAL_CAPABILITIES = {"local_fs", "local_process", "local_info"}
TRUSTED_LOCAL = {
    "list_directory",
    "read_file",
    "search_files",
    "create_directory",
    "write_file",
    "copy_file",
    "move_file",
}


class ToolPolicy:
    def validate(
        self,
        definition,
        settings,
        *,
        mode,
        computer_mode="off",
        tor_enabled=False,
        explicit=False,
        confirmed=False,
    ):
        capability = definition.capability
        if capability in WEB_CAPABILITIES and mode == "off":
            raise ToolError("web_disabled")
        if capability in TOR_CAPABILITIES and not (tor_enabled or settings.tor_enabled):
            raise ToolError("tor_disabled")
        if capability in LOCAL_CAPABILITIES and computer_mode == "off":
            raise ToolError("computer_disabled")
        if not definition.auto_route and not explicit:
            raise ToolError("explicit_action_required")
        enabled = {
            "search": settings.search_enabled,
            "fetch": settings.fetch_enabled,
            "agent": settings.agent_enabled,
            "browser": settings.browser_enabled,
        }
        if capability in enabled and not enabled[capability]:
            raise ToolError("tool_disabled")
        if capability in LOCAL_CAPABILITIES:
            if computer_mode == "trusted" and definition.name in TRUSTED_LOCAL:
                return "allowed"
            if not confirmed:
                return "confirmation_required"
            return "allowed"
        if definition.risk_level != RiskLevel.READ_ONLY and not confirmed:
            return "confirmation_required"
        return "allowed"
