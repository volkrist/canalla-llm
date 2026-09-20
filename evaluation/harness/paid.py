"""Paid-resource authorization for --mode real.

Real mode must never silently start RunPod or TinyFish Agent.
TinyFish Browser is opt-in and budget-capped.
"""

from __future__ import annotations

from dataclasses import dataclass

HARD_RUNPOD_USD = 1.20
TARGET_RUNPOD_USD = 0.80
SOFT_STOP_FRACTION = 0.75
HARD_TINYFISH_BROWSER_USD = 0.05
HARD_TINYFISH_AGENT_USD = 0.0
MAX_HOURLY_USD = 1.20
VOLUME_ID = "uwgeaie5b0"
PREFERRED_GPU = "NVIDIA L40S"
PREFERRED_DC = "US-TX-3"
MODEL_ALIAS = "orcarouter-qwen38-27b-q5km"
PROVIDER = "llamacpp"

BROWSER_CASES = frozenset({"WM-07", "WB-05", "TF-02", "TF-08"})
AGENT_CASES = frozenset({"WB-06", "TF-03"})
SEARCH_CASES = frozenset({"WM-10", "WB-01", "WB-02", "WB-04", "WB-07", "WB-08", "TF-01"})


class PaidRefused(Exception):
    """Real mode refused to start a paid resource."""


@dataclass
class PaidConfig:
    allow_runpod: bool = False
    runpod_budget_usd: float = 0.0
    allow_tinyfish_browser: bool = False
    tinyfish_budget_usd: float = 0.0
    allow_tinyfish_agent: bool = False
    allow_tinyfish_search: bool = False
    spent_runpod_usd: float = 0.0
    spent_tinyfish_browser_usd: float = 0.0
    spent_tinyfish_agent_usd: float = 0.0
    tinyfish_agent_calls: int = 0
    tinyfish_browser_calls: int = 0
    runpod_calls: int = 0

    @property
    def hard_runpod(self) -> float:
        return min(self.runpod_budget_usd or 0.0, HARD_RUNPOD_USD)

    @property
    def soft_runpod(self) -> float:
        return min(TARGET_RUNPOD_USD, self.hard_runpod * SOFT_STOP_FRACTION)


def validate_real_start(config: PaidConfig) -> None:
    """Refuse before any Pod is created."""
    if not config.allow_runpod:
        raise PaidRefused(
            "REFUSED: real mode will not start RunPod without --allow-runpod and --runpod-budget-usd"
        )
    if config.runpod_budget_usd <= 0:
        raise PaidRefused("REFUSED: --runpod-budget-usd must be > 0")
    if config.runpod_budget_usd > HARD_RUNPOD_USD + 1e-9:
        raise PaidRefused(f"REFUSED: RunPod budget {config.runpod_budget_usd} exceeds hard ${HARD_RUNPOD_USD:.2f}")
    if config.allow_tinyfish_agent:
        raise PaidRefused("REFUSED: TinyFish Agent new runs are not authorized in this stage")
    if config.allow_tinyfish_browser:
        if config.tinyfish_budget_usd <= 0:
            raise PaidRefused("REFUSED: --tinyfish-budget-usd required with --allow-tinyfish-browser")
        if config.tinyfish_budget_usd > HARD_TINYFISH_BROWSER_USD + 1e-9:
            raise PaidRefused(
                f"REFUSED: TinyFish Browser budget {config.tinyfish_budget_usd} exceeds hard ${HARD_TINYFISH_BROWSER_USD:.2f}"
            )


def checkpoint(config: PaidConfig) -> str:
    """ok | soft_stop | hard_stop. soft_stop: do not start non-critical suites."""
    spent = config.spent_runpod_usd
    hard = config.hard_runpod if config.hard_runpod > 0 else HARD_RUNPOD_USD
    if spent >= hard - 1e-9:
        return "hard_stop"
    if spent >= hard * SOFT_STOP_FRACTION - 1e-9:
        return "soft_stop"
    if spent >= TARGET_RUNPOD_USD - 1e-9 and spent >= config.soft_runpod:
        return "soft_stop"
    if config.spent_tinyfish_agent_usd > HARD_TINYFISH_AGENT_USD + 1e-9:
        return "hard_stop"
    if config.spent_tinyfish_browser_usd > HARD_TINYFISH_BROWSER_USD + 1e-9:
        return "hard_stop"
    return "ok"


def case_needs_browser(task_id: str) -> bool:
    return task_id in BROWSER_CASES


def case_needs_agent(task_id: str) -> bool:
    return task_id in AGENT_CASES


def case_allows_search(task_id: str) -> bool:
    return task_id in SEARCH_CASES


def authorize_case(config: PaidConfig, task_id: str) -> str | None:
    """Return skip reason or None if the case may run."""
    if case_needs_agent(task_id):
        return "SKIPPED: TinyFish Agent new runs forbidden this stage"
    if case_needs_browser(task_id) and not config.allow_tinyfish_browser:
        return "SKIPPED: TinyFish Browser not authorized (--allow-tinyfish-browser)"
    if case_needs_browser(task_id) and config.spent_tinyfish_browser_usd >= HARD_TINYFISH_BROWSER_USD:
        return "SKIPPED: TinyFish Browser hard budget exhausted"
    gate = checkpoint(config)
    if gate == "hard_stop":
        return "SKIPPED: RunPod hard budget reached"
    return None
