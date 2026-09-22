"""Local TinyFish spend accounting. Not a Wallet balance API."""

from dataclasses import asdict, dataclass
from typing import TypeVar

from ..contracts import ToolError

NumberT = TypeVar("NumberT", int, float)


def _num(value, default: NumberT) -> NumberT:
    try:
        return type(default)(value)
    except (TypeError, ValueError):
        return default


@dataclass
class TinyFishBudget:
    paid_spent: float = 0.0
    agent_runs: int = 0
    agent_steps: int = 0
    browser_sessions: int = 0
    browser_seconds: float = 0.0
    active_browser_session_id: str = ""
    active_agent_run_id: str = ""

    def dump(self):
        return asdict(self)


def load_budget(checkpoint) -> TinyFishBudget:
    raw = ((checkpoint or {}).get("tinyfish") or {}) if isinstance(checkpoint, dict) else {}
    return TinyFishBudget(
        paid_spent=_num(raw.get("paid_spent"), 0.0),
        agent_runs=_num(raw.get("agent_runs"), 0),
        agent_steps=_num(raw.get("agent_steps"), 0),
        browser_sessions=_num(raw.get("browser_sessions"), 0),
        browser_seconds=_num(raw.get("browser_seconds"), 0.0),
        active_browser_session_id=str(raw.get("active_browser_session_id") or "")[:160],
        active_agent_run_id=str(raw.get("active_agent_run_id") or "")[:160],
    )


def store_budget(checkpoint, budget: TinyFishBudget):
    data = dict(checkpoint or {})
    data["tinyfish"] = budget.dump()
    return data


def ceilings(settings, prefs):
    hard_usd = float(getattr(settings, "tinyfish_paid_hard_usd", 2.0) or 2.0)
    hard_steps = int(getattr(settings, "tinyfish_agent_hard_steps", 50) or 50)
    hard_minutes = float(getattr(settings, "tinyfish_browser_hard_minutes", 30) or 30)
    usd = min(float(getattr(prefs, "tinyfish_paid_task_budget_usd", 1.0) or 1.0), hard_usd)
    steps = min(int(getattr(prefs, "agent_max_steps", 20) or 20), hard_steps)
    minutes = min(float(getattr(prefs, "browser_max_minutes", 10) or 10), hard_minutes)
    runs = min(
        int(getattr(prefs, "agent_max_runs", 2) or 2),
        int(getattr(settings, "tinyfish_agent_max_runs", 2) or 2),
    )
    sessions = min(
        int(getattr(prefs, "browser_max_sessions", 2) or 2),
        int(getattr(settings, "tinyfish_browser_max_sessions", 2) or 2),
    )
    return {
        "usd": usd,
        "steps": steps,
        "minutes": minutes,
        "runs": runs,
        "sessions": sessions,
        "step_price": float(getattr(settings, "tinyfish_agent_step_price", 0.016) or 0.016),
        "minute_price": float(getattr(settings, "tinyfish_browser_minute_price", 0.002) or 0.002),
    }


def remaining_usd(budget: TinyFishBudget, settings, prefs) -> float:
    return max(0.0, ceilings(settings, prefs)["usd"] - budget.paid_spent)


def preflight_agent(budget: TinyFishBudget, settings, prefs, requested_steps=1):
    limits = ceilings(settings, prefs)
    if budget.agent_runs >= limits["runs"]:
        raise ToolError("run_budget")
    if budget.agent_steps + requested_steps > limits["steps"]:
        raise ToolError("run_budget")
    cost = requested_steps * limits["step_price"]
    if budget.paid_spent + cost > limits["usd"] + 1e-9:
        raise ToolError("run_budget")


def preflight_browser(budget: TinyFishBudget, settings, prefs, requested_minutes=1):
    limits = ceilings(settings, prefs)
    if budget.browser_sessions >= limits["sessions"] and not budget.active_browser_session_id:
        raise ToolError("run_budget")
    used_minutes = budget.browser_seconds / 60
    if used_minutes + requested_minutes > limits["minutes"] + 1e-9:
        raise ToolError("run_budget")
    cost = requested_minutes * limits["minute_price"]
    if budget.paid_spent + cost > limits["usd"] + 1e-9:
        raise ToolError("run_budget")


def exhausted(checkpoint, settings, prefs) -> bool:
    budget = load_budget(checkpoint)
    limits = ceilings(settings, prefs)
    return budget.paid_spent + 1e-9 >= limits["usd"]
