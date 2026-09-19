import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import RiskLevel, ToolError
from .local.paths import inside_trusted
from .models import ToolPreferences


class WebSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search_enabled: bool = True
    fetch_enabled: bool = True
    default_mode: Literal["off", "auto", "on"] = "auto"
    agent_mode: Literal["off", "auto", "on"] = "auto"
    agent_enabled: bool = True
    agent_run_budget: float = Field(default=0.25, gt=0, le=10)
    agent_daily_budget: float = Field(default=1, gt=0, le=50)
    agent_max_runtime: int = Field(default=120, ge=10, le=600)
    agent_max_runs: int = Field(default=2, ge=1, le=8)
    agent_max_steps: int = Field(default=20, ge=1, le=50)
    browser_mode: Literal["off", "auto", "on"] = "auto"
    browser_enabled: bool = True
    tinyfish_paid_task_budget_usd: float = Field(default=1.0, gt=0, le=2)
    browser_max_sessions: int = Field(default=2, ge=1, le=8)
    browser_max_minutes: float = Field(default=10, ge=1, le=30)
    tor_mode: Literal["off", "auto", "on"] = "auto"
    tor_enabled: bool = True
    tor_browser_mode: Literal["off", "auto", "on"] = "auto"
    computer_mode: Literal["off", "ask", "trusted"] = "ask"
    workspace_roots: list[str] = Field(default_factory=list, max_length=8)
    device_display_name: str = Field(default="", max_length=80)
    auto_commit: bool = False
    allow_push: bool = False

    @model_validator(mode="before")
    @classmethod
    def migrate_tor_mode(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        mode = data.get("tor_mode")
        if mode not in {"off", "auto", "on"}:
            if "tor_enabled" in data:
                data["tor_mode"] = "auto" if data.get("tor_enabled") else "off"
            else:
                data["tor_mode"] = "auto"
        data["tor_enabled"] = data["tor_mode"] != "off"
        if data.get("tor_browser_mode") not in {"off", "auto", "on"}:
            data["tor_browser_mode"] = "auto"
        if data.get("agent_mode") not in {"off", "auto", "on"}:
            data["agent_mode"] = "auto"
        data["agent_enabled"] = data["agent_mode"] != "off"
        if data.get("browser_mode") not in {"off", "auto", "on"}:
            data["browser_mode"] = "auto"
        data["browser_enabled"] = data["browser_mode"] != "off"
        return data


def preferences(db, user_id):
    row = db.get(ToolPreferences, user_id)
    return WebSettings.model_validate(row.values if row else {})


WEB_CAPABILITIES = {"search", "fetch", "agent", "browser"}
TOR_CAPABILITIES = {"tor_search", "tor_fetch", "tor_browser"}
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
EXTERNAL_CAPABILITIES = {
    "external_form",
    "external_message",
    "external_purchase",
    "external_email",
}
CODING_PLANNER_TOOLS = frozenset(
    {
        "list_directory",
        "read_file",
        "write_file",
        "patch_file",
        "search_code",
        "search_files",
        "run_python",
        "git_status",
        "git_diff",
        "git_log",
        "process_status",
        "stop_process",
    }
)
NETWORK_DIRECT = WEB_CAPABILITIES
NETWORK_TOR = TOR_CAPABILITIES
PROTECTED_BRANCHES = {"main", "master", "develop", "production"}
BLANKET_GIT_ADD = {"-a", "-A", "--all", ".", "*", "**", "/", "\\"}


def assert_explicit_git_add(paths):
    for item in paths or []:
        value = str(item).strip()
        name = value.replace("/", "\\").rsplit("\\", 1)[-1]
        if not value or value in BLANKET_GIT_ADD or name in BLANKET_GIT_ADD or value.startswith("-"):
            raise ToolError("invalid_arguments")


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
    max_tor_search: int = 3
    max_tor_fetch: int = 8
    max_tor_pages: int = 8
    max_tor_calls: int = 12
    max_tor_follow: int = 8
    max_tor_depth: int = 3
    max_tor_candidates: int = 50
    max_tor_seconds: float = 180
    max_tor_browser: int = 8
    hard_tor_search: int = 3
    hard_tor_fetch: int = 8
    hard_tor_calls: int = 12
    hard_tor_seconds: float = 300
    hard_tor_browser: int = 8
    calls: int = 0
    searches: int = 0
    fetches: int = 0
    pages: int = 0
    chars: int = 0
    local_calls: int = 0
    tor_searches: int = 0
    tor_fetches: int = 0
    tor_pages: int = 0
    tor_calls: int = 0
    tor_follows: int = 0
    tor_browser: int = 0
    started: float = field(default_factory=time.monotonic)

    @property
    def remaining(self):
        return max(0, self.max_seconds - (time.monotonic() - self.started))

    def consume(self, definition, args):
        ceiling = min(self.max_calls, self.hard_max_calls)
        if self.remaining <= 0 or self.calls >= ceiling:
            raise ToolError("tool_limit")
        pages = len(getattr(args, "urls", []))
        searching = definition.capability == "search"
        fetching = definition.capability == "fetch"
        tor_searching = definition.capability == "tor_search"
        tor_fetching = definition.capability == "tor_fetch"
        tor_browsing = definition.capability == "tor_browser"
        local = definition.capability in LOCAL_CAPABILITIES
        if searching and self.searches >= self.max_search:
            raise ToolError("search_limit")
        if fetching and (self.fetches >= self.max_fetch or self.pages + pages > self.max_pages):
            raise ToolError("page_limit")
        if tor_searching and self.tor_searches >= min(self.max_tor_search, self.hard_tor_search):
            raise ToolError("search_limit")
        if tor_fetching and (
            self.tor_fetches >= min(self.max_tor_fetch, self.hard_tor_fetch)
            or self.tor_pages + pages > self.max_tor_pages
        ):
            raise ToolError("page_limit")
        if tor_browsing and self.tor_browser >= min(self.max_tor_browser, self.hard_tor_browser):
            raise ToolError("tool_limit")
        if (tor_searching or tor_fetching or tor_browsing) and self.tor_calls >= min(
            self.max_tor_calls, self.hard_tor_calls
        ):
            raise ToolError("tool_limit")
        if (tor_searching or tor_fetching or tor_browsing) and (time.monotonic() - self.started) >= min(
            self.max_tor_seconds, self.hard_tor_seconds
        ):
            raise ToolError("timeout")
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
        self.tor_searches += tor_searching
        self.tor_fetches += tor_fetching
        self.tor_pages += pages if tor_fetching else 0
        self.tor_calls += tor_searching or tor_fetching or tor_browsing
        self.tor_browser += tor_browsing


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
    if name == "web_browser" and getattr(args, "operation", None) == "type":
        return RiskLevel.SENSITIVE
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
        tor_mode="off",
        explicit=False,
        confirmed=False,
        args=None,
    ):
        capability = definition.capability
        if capability in WEB_CAPABILITIES and mode == "off":
            raise ToolError("web_disabled")
        mode_tor = tor_mode if tor_mode in {"off", "auto", "on"} else ("auto" if tor_enabled else "off")
        if capability in TOR_CAPABILITIES and mode_tor == "off":
            raise ToolError("tor_disabled")
        if capability == "tor_browser" and getattr(settings, "tor_browser_mode", "auto") == "off":
            raise ToolError("tor_browser_disabled")
        if capability == "agent" and getattr(settings, "agent_mode", "off") == "off":
            raise ToolError("tool_disabled")
        if capability == "browser" and getattr(settings, "browser_mode", "off") == "off":
            raise ToolError("tool_disabled")
        if capability in LOCAL_CAPABILITIES and computer_mode == "off":
            raise ToolError("computer_disabled")
        if not definition.auto_route and not explicit:
            raise ToolError("explicit_action_required")
        if definition.name == "git_add" and args is not None:
            assert_explicit_git_add(getattr(args, "paths", None) or [])
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
            if risk == RiskLevel.NORMAL_CHANGE and (
                inside_trusted_scope(definition, args, settings.workspace_roots)
                or (
                    computer_mode == "trusted"
                    and definition.capability == "local_process"
                    and not getattr(args, "elevate", False)
                    and not getattr(args, "cwd", None)
                )
            ):
                return "allowed"
            return "allowed" if confirmed else "confirmation_required"
        if capability in EXTERNAL_CAPABILITIES:
            if risk == RiskLevel.READ or definition.name == "fill_form_field":
                return "allowed"
            return "allowed" if confirmed else "confirmation_required"
        if risk == RiskLevel.READ:
            return "allowed"
        return "allowed" if confirmed else "confirmation_required"
