"""The arithmetic of a bounded paid run: a budget becomes a wall-clock deadline.

A budget of $0.15 at $1.09/hour is not "480 seconds of work". The provider bills the Pod until the
termination is confirmed, billing is rounded up to a granularity, and the stop itself takes time.
Writing `480 * 1.09 / 3600 = 0.1453` and calling it a ceiling ignores both, so a run that really
used 480 s could bill more than the budget. This module derives the numbers once, and every
consumer (the plan, the watchdog, the acceptance harness) reads them from here:

    budget -> max billed seconds (rounded DOWN to the billing granularity)
           -> minus the termination grace    = the hard wall the pipeline may use
           -> minus the watchdog margin      = when the external watchdog requests the stop

The result is conservative in both directions: the worst case includes the full grace and the
rounding, and the run is refused before a Pod exists when even that worst case exceeds the budget.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# RunPod bills a Pod per second but rounds the total to a minute; assuming the minute is the
# conservative choice and costs nothing when the real granularity is finer.
DEFAULT_BILLING_GRANULARITY_SECONDS = 60
# How long a terminated Pod may keep billing while removal is confirmed. Measured stops on the
# deployed Gateway completed well inside this; the full value is reserved anyway.
DEFAULT_TERMINATION_GRACE_SECONDS = 30
# The external watchdog asks for the stop this many seconds before the hard wall, so a poll that
# lands late still leaves the pipeline inside its own wall.
DEFAULT_WATCHDOG_MARGIN_SECONDS = 10
DEFAULT_WATCHDOG_POLL_SECONDS = 5
# The provider-level capacity rule: no run waits longer than this for hardware.
DEFAULT_CAPACITY_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class BudgetPlan:
    budget_usd: float
    hourly_rate_usd: float
    billing_granularity_seconds: int
    termination_grace_seconds: int
    watchdog_margin_seconds: int
    watchdog_poll_seconds: int
    capacity_timeout_seconds: int
    max_billed_seconds: int
    hard_wall_seconds: int
    stop_request_seconds: int
    worst_case_usd: float
    feasible: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "budget_usd": round(self.budget_usd, 6),
            "hourly_rate_usd": self.hourly_rate_usd,
            "billing_granularity_seconds": self.billing_granularity_seconds,
            "termination_grace_seconds": self.termination_grace_seconds,
            "watchdog_margin_seconds": self.watchdog_margin_seconds,
            "watchdog_poll_seconds": self.watchdog_poll_seconds,
            "capacity_timeout_seconds": self.capacity_timeout_seconds,
            "max_billed_seconds": self.max_billed_seconds,
            "hard_wall_seconds": self.hard_wall_seconds,
            "stop_request_seconds": self.stop_request_seconds,
            "worst_case_usd": round(self.worst_case_usd, 6),
            "budget_headroom_usd": round(self.budget_usd - self.worst_case_usd, 6),
            "feasible": self.feasible,
            "reasons": self.reasons,
        }


def rate_per_second(hourly_rate_usd: float) -> float:
    if hourly_rate_usd <= 0:
        raise ValueError("hourly_rate_usd must be positive")
    return float(hourly_rate_usd) / 3600.0


def plan(
    budget_usd: float,
    hourly_rate_usd: float,
    *,
    billing_granularity_seconds: int = DEFAULT_BILLING_GRANULARITY_SECONDS,
    termination_grace_seconds: int = DEFAULT_TERMINATION_GRACE_SECONDS,
    watchdog_margin_seconds: int = DEFAULT_WATCHDOG_MARGIN_SECONDS,
    watchdog_poll_seconds: int = DEFAULT_WATCHDOG_POLL_SECONDS,
    capacity_timeout_seconds: int = DEFAULT_CAPACITY_TIMEOUT_SECONDS,
) -> BudgetPlan:
    """Derive the deadlines for one Pod under one budget, or explain why it is not allowed."""
    if budget_usd <= 0:
        raise ValueError("budget_usd must be positive")
    if hourly_rate_usd <= 0:
        raise ValueError("hourly_rate_usd must be positive")
    granularity = max(1, int(billing_granularity_seconds))
    reason: list[str] = []

    affordable = budget_usd / rate_per_second(hourly_rate_usd)
    max_billed = math.floor(affordable / granularity) * granularity
    if max_billed <= 0:
        max_billed = 0
        reason.append("budget buys less than one billing interval")
    hard_wall = max_billed - int(termination_grace_seconds)
    if hard_wall <= 0:
        hard_wall = 0
        reason.append("termination grace alone consumes the budget")
    stop_request = max(0, hard_wall - int(watchdog_margin_seconds))
    worst_case = (max_billed / 3600.0) * hourly_rate_usd
    feasible = max_billed > 0 and hard_wall > 0 and worst_case <= budget_usd + 1e-12
    if feasible and stop_request <= 0:
        feasible = False
        reason.append("no usable work window before the stop")
    return BudgetPlan(
        budget_usd=float(budget_usd),
        hourly_rate_usd=float(hourly_rate_usd),
        billing_granularity_seconds=granularity,
        termination_grace_seconds=int(termination_grace_seconds),
        watchdog_margin_seconds=int(watchdog_margin_seconds),
        watchdog_poll_seconds=max(1, int(watchdog_poll_seconds)),
        capacity_timeout_seconds=max(1, int(capacity_timeout_seconds)),
        max_billed_seconds=max_billed,
        hard_wall_seconds=hard_wall,
        stop_request_seconds=stop_request,
        worst_case_usd=worst_case,
        feasible=feasible,
        reasons=reason,
    )


def check_rate(plan_value: BudgetPlan, actual_hourly_usd: float) -> tuple[bool, str]:
    """Re-check the plan against the price the provider actually quoted for the selected Pod.

    A catalogue can change between planning and creation, so the number that matters is the one
    on the Pod. A rate above the planning rate makes the worst case unaffordable and the run must
    be refused *before* creation.
    """
    if actual_hourly_usd <= 0:
        return False, "selected Pod reports no hourly rate"
    if actual_hourly_usd > plan_value.hourly_rate_usd + 1e-9:
        worst = (plan_value.max_billed_seconds / 3600.0) * actual_hourly_usd
        if worst > plan_value.budget_usd + 1e-12:
            return False, (
                f"selected rate ${actual_hourly_usd:.4f}/h makes the worst case "
                f"${worst:.4f} exceed the ${plan_value.budget_usd:.2f} budget"
            )
    return True, ""


def remaining_seconds(plan_value: BudgetPlan, elapsed_seconds: float) -> float:
    return max(0.0, float(plan_value.hard_wall_seconds) - float(elapsed_seconds))
