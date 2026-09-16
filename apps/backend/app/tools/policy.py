import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import RiskLevel, ToolError
from .local.paths import inside_trusted
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


WEB_CAPABILITIES = {"search", "fetch", "agent", "browser"}
TOR_CAPABILITIES = {"tor_search", "tor_fetch"}
LOCAL_CAPABILITIES = {
    "local_fs",
    "local_process",
    "local_info",
    "local_registry",
    "local_service",
    "local_install",
    "local_system",
    "local_credential",
    "local_git",
}
NETWORK_DIRECT = WEB_CAPABILITIES
NETWORK_TOR = TOR_CAPABILITIES
PROTECTED_BRANCHES = {"main", "master", "develop", "production"}


@dataclass
class ToolLimits:
    max_calls: int = 8
    max_search: int = 3
    max_fetch: int = 3
    max_pages: int = 10
    max_chars: int = 20000
    max_seconds: float = 180
    max_local_calls: int = 24
    max_files_changed: int = 20
    max_file_bytes: int = 2_000_000
    max_process_seconds: int = 120
    hard_max_calls: int = 32
    calls: int = 0
    searches: int = 0
    fetches: int = 0
    pages: int = 0
    chars: int = 0
    local_calls: int = 0
    started: float = field(default_factory=time.monotonic)

    @property
    def remaining(self):
        return max(0, self.max_seconds - (time.monotonic() - self.started))

    def consume(self, definition, args):
        ceiling = min(self.max_calls, self.hard_max_calls)
        if self.remaining <= 0 or self.calls >= ceiling:
            raise ToolError("tool_limit")
        pages = len(getattr(args, "urls", []))
        searching = definition.capability in {"search", "tor_search"}
        fetching = definition.capability in {"fetch", "tor_fetch"}
        local = definition.capability in LOCAL_CAPABILITIES
        if searching and self.searches >= self.max_search:
            raise ToolError("search_limit")
        if fetching and (self.fetches >= self.max_fetch or self.pages + pages > self.max_pages):
            raise ToolError("page_limit")
        if local and self.local_calls >= self.max_local_calls:
            raise ToolError("tool_limit")
        content = getattr(args, "content", None) or getattr(args, "new_text", None) or ""
        if content and len(str(content).encode("utf-8")) > self.max_file_bytes:
            raise ToolError("file_size_limit")
        timeout = getattr(args, "timeout_seconds", None)
        if timeout is not None and int(timeout) > self.max_process_seconds:
            raise ToolError("process_timeout_limit")
        self.calls += 1
        self.searches += searching
        self.fetches += fetching
        self.pages += pages
        self.local_calls += local


def network_channel(definition) -> str | None:
    if definition.capability in NETWORK_TOR:
        return "tor"
    if definition.capability in NETWORK_DIRECT:
        return "direct"
    return None


def effective_risk(definition, args=None):
    risk = definition.risk_level
    elevate = bool(getattr(args, "elevate", False)) if args is not None else False
    if getattr(args, "hive", None) == "HKLM":
        elevate = True
    name = getattr(definition, "name", "")
    if name == "git_push" and getattr(args, "force", False):
        return RiskLevel.CRITICAL
    if name == "git_reset" and getattr(args, "mode", None) == "hard":
        return RiskLevel.CRITICAL
    if name == "git_branch" and getattr(args, "delete", False):
        branch = (getattr(args, "name", None) or "").lower()
        return RiskLevel.CRITICAL if branch in PROTECTED_BRANCHES else RiskLevel.SENSITIVE
    if elevate and risk in {RiskLevel.READ, RiskLevel.NORMAL_CHANGE}:
        return RiskLevel.SENSITIVE
    return risk


def _scope_paths(definition, args):
    if args is None:
        return None
    if definition.capability == "local_process":
        cwd = getattr(args, "cwd", None)
        return [cwd] if cwd else []
    values = []
    for key in ("path", "source", "destination", "root"):
        value = getattr(args, key, None)
        if value:
            values.append(value)
    for item in getattr(args, "paths", None) or []:
        values.append(item)
    return values


def inside_trusted_scope(definition, args, roots) -> bool:
    if definition.risk_level in {RiskLevel.SENSITIVE, RiskLevel.CRITICAL}:
        return False
    paths = _scope_paths(definition, args)
    if paths is None:
        return False
    if definition.capability == "local_process":
        return bool(paths) and all(inside_trusted(path, roots) for path in paths)
    if not paths:
        return True
    return all(inside_trusted(path, roots) for path in paths)


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
        args=None,
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
        risk = effective_risk(definition, args)
        if capability in LOCAL_CAPABILITIES:
            if risk in {RiskLevel.SENSITIVE, RiskLevel.CRITICAL}:
                return "allowed" if confirmed else "confirmation_required"
            if computer_mode == "ask":
                return "allowed" if confirmed else "confirmation_required"
            if risk == RiskLevel.READ:
                return "allowed"
            if risk == RiskLevel.NORMAL_CHANGE and inside_trusted_scope(
                definition, args, settings.workspace_roots
            ):
                return "allowed"
            return "allowed" if confirmed else "confirmation_required"
        if risk == RiskLevel.READ:
            return "allowed"
        return "allowed" if confirmed else "confirmation_required"
