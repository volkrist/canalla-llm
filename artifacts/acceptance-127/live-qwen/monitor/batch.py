"""The owner's bounded series of paid attempts: one budget, one Pod at a time.

A single-use grant cannot express "keep trying until this works, but never spend more than $2.00".
This module owns that limit and nothing else: it never talks to the provider, never creates a grant
and never starts a run. The monitor asks it two questions - may another attempt start, and what did
the last one cost - and records the answer durably, so a restart, a crash or a new run id cannot
reset the money.

Rules that must not be broken:

* the total is a hard ceiling across every attempt (`spent + reserved + required <= budget`);
* at most one attempt is in flight (`active_run_id`);
* a previous attempt whose outcome is unknown blocks the next one until the provider read is clean;
* spend only ever grows - a failed attempt is never refunded out of the journal;
* running out of budget is a typed state (`PAID_BATCH_EXHAUSTED`), never a silent retry.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BATCH_PHRASE_TEMPLATE = "I AUTHORIZE A PAID LIVE BATCH WITH A TOTAL GPU BUDGET OF USD {amount:.2f}"
BATCH_PHRASE = BATCH_PHRASE_TEMPLATE.format(amount=2.00)
MAX_BATCH_BUDGET_USD = 5.00
BATCH_FILE = "batch-authorization.json"
BATCH_JOURNAL = "batch-spend.jsonl"
# The smallest window that can still produce a verified result: the Pod must start, the model must
# load, and the agent needs a real chance to create and check an application. It is a floor for
# *usefulness*, not the owner's fixed 45-minute target.
MIN_USEFUL_SECONDS = 20 * 60
SAFETY_MARGIN_USD = 0.05
# After a real attempt, the next one waits. A deterministic failure (a pre-step that times out, a
# prompt the product cannot satisfy) would otherwise burn the whole budget in four 30-second
# retries before anyone could read the evidence. A capacity race that created nothing is not
# throttled - nothing was spent and the card may be there on the next check.
BATCH_RETRY_COOLDOWN_SECONDS = 5 * 60


class BatchError(Exception):
    pass


def batch_path(rdir: Path) -> Path:
    return rdir / BATCH_FILE


def journal_path(rdir: Path) -> Path:
    return rdir / BATCH_JOURNAL


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(path)


def remaining_usd(batch: dict) -> float:
    try:
        budget = float(batch.get("maximum_total_gpu_spend_usd") or 0)
        spent = float(batch.get("spent_gpu_usd") or 0)
        reserved = float(batch.get("reserved_gpu_usd") or 0)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, budget - spent - reserved), 4)


def required_usd(rate: float, min_seconds: int = MIN_USEFUL_SECONDS) -> float:
    """The money an attempt must be able to spend to be worth starting."""
    if rate <= 0:
        return 0.0
    return round(rate * (min_seconds / 3600.0) + SAFETY_MARGIN_USD, 3)


def phrase_for(amount: float) -> str:
    """The owner's phrase must name the exact ceiling, so a bigger series cannot be smuggled in."""
    return BATCH_PHRASE_TEMPLATE.format(amount=float(amount))


def status(batch: dict, now: datetime | None = None) -> str:
    if not batch:
        return "absent"
    now = now or _now()
    if batch.get("revoked"):
        return "revoked"
    if batch.get("status") == "completed":
        return "completed"
    expires = _parse(batch.get("expires_at"))
    if expires is not None and expires <= now:
        return "expired"
    try:
        spent = float(batch.get("spent_gpu_usd") or 0)
        budget = float(batch.get("maximum_total_gpu_spend_usd") or 0)
        created = int(batch.get("pods_created") or 0)
        cap = int(batch.get("maximum_total_pods") or 0)
    except (TypeError, ValueError):
        return "exhausted"
    if budget - spent <= 1e-9:
        return "exhausted"
    if cap and created >= cap:
        return "exhausted"
    return "active"


def read(rdir: Path) -> dict:
    return _read(batch_path(rdir))


def create(
    rdir: Path,
    *,
    phrase: str,
    budget_usd: float,
    days: int,
    placements: list[dict],
    allowed_gpu: str,
    model_id: str,
    starting_balance_usd: float | None = None,
    minimum_balance_usd: float = 0.0,
    max_attempt_seconds: int | None = None,
    maximum_total_pods: int = 3,
) -> dict:
    """Record the owner's bounded series. An existing live batch is never replaced silently."""
    existing = read(rdir)
    if existing and status(existing) in {"active", "completed"}:
        raise BatchError("batch_already_exists")
    if phrase != phrase_for(budget_usd):
        raise BatchError("wrong_phrase")
    if not 0 < budget_usd <= MAX_BATCH_BUDGET_USD + 1e-9:
        raise BatchError("budget_out_of_range")
    if days < 1 or days > 365:
        raise BatchError("days_out_of_range")
    if not 0 < maximum_total_pods <= 10:
        raise BatchError("pod_limit_out_of_range")
    if minimum_balance_usd < 0:
        raise BatchError("reserve_out_of_range")
    now = _now()
    batch = {
        "schema": 2,
        "batch_id": uuid.uuid4().hex,
        "phrase": phrase,
        "owner_approval": True,
        "maximum_total_gpu_spend_usd": round(float(budget_usd), 2),
        "maximum_concurrent_pods": 1,
        "maximum_total_pods": int(maximum_total_pods),
        "spent_gpu_usd": 0.0,
        "reserved_gpu_usd": 0.0,
        "pods_created": 0,
        "active_pod_id": None,
        "active_run_id": None,
        "reconciliation_status": "clean",
        "allowed_placements": placements,
        "allowed_gpu": allowed_gpu,
        "allowed_model": model_id,
        "starting_balance_usd": None if starting_balance_usd is None else round(float(starting_balance_usd), 4),
        "minimum_balance_usd": round(float(minimum_balance_usd), 4),
        "max_attempt_seconds": None if max_attempt_seconds is None else int(max_attempt_seconds),
        "watchdog_status": "not_started",
        "status": "active",
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(days=days)).isoformat(timespec="seconds"),
    }
    _write(batch_path(rdir), batch)
    return batch


def can_start(batch: dict, rate: float, now: datetime | None = None) -> tuple[bool, str, float]:
    """May another attempt start, and how much must remain for it to be worth starting?"""
    now = now or _now()
    state = status(batch, now)
    if state in {"completed", "revoked", "expired"}:
        return False, f"batch_{state}", 0.0
    if batch.get("active_run_id"):
        return False, "attempt_in_flight", 0.0
    if batch.get("reconciliation_status") != "clean":
        return False, "previous_outcome_unknown", 0.0
    cooldown = _parse(batch.get("next_attempt_at"))
    if cooldown is not None and cooldown > now:
        return False, "batch_cooldown", 0.0
    need = required_usd(rate)
    left = remaining_usd(batch)
    if state == "exhausted" or left <= 0:
        return False, "PAID_BATCH_EXHAUSTED", need
    if left < need:
        return False, "PAID_BATCH_EXHAUSTED", need
    return True, "ok", need


def child_budget(batch: dict) -> float:
    """What this attempt may spend: the remaining budget, never more than the batch ceiling."""
    return round(min(remaining_usd(batch), float(batch.get("maximum_total_gpu_spend_usd") or 0.0)), 2)


def begin(batch: dict, run_id: str, reserved_usd: float) -> dict:
    if batch.get("active_run_id"):
        raise BatchError("attempt_in_flight")
    batch = dict(batch)
    batch["active_run_id"] = run_id
    batch["reserved_gpu_usd"] = round(float(reserved_usd), 4)
    batch["reconciliation_status"] = "in_flight"
    return batch


def record(
    batch: dict,
    *,
    run_id: str,
    spend_usd: float,
    pod_ids: list[str],
    confirmed_pod: bool,
) -> dict:
    """Settle an attempt. Spend only grows; a confirmed Pod is counted exactly once."""
    batch = dict(batch)
    if batch.get("active_run_id") not in {None, run_id}:
        # A different attempt owns the slot: never overwrite its accounting.
        return batch
    cost = max(0.0, float(spend_usd or 0.0))
    batch["spent_gpu_usd"] = round(float(batch.get("spent_gpu_usd") or 0.0) + cost, 4)
    batch["reserved_gpu_usd"] = 0.0
    batch["active_run_id"] = None
    batch["active_pod_id"] = (pod_ids or [None])[0]
    batch["reconciliation_status"] = "clean" if confirmed_pod else "unconfirmed"
    if confirmed_pod:
        batch["pods_created"] = int(batch.get("pods_created") or 0) + 1
        batch["next_attempt_at"] = (
            _now() + timedelta(seconds=BATCH_RETRY_COOLDOWN_SECONDS)
        ).isoformat(timespec="seconds")
    if remaining_usd(batch) <= 0:
        batch["status"] = "exhausted"
    return batch


def restore(batch: dict, run_id: str) -> dict:
    """An attempt that provably created nothing releases its reservation and its slot."""
    batch = dict(batch)
    if batch.get("active_run_id") not in {None, run_id}:
        return batch
    batch["active_run_id"] = None
    batch["reserved_gpu_usd"] = 0.0
    batch["reconciliation_status"] = "clean"
    return batch


def reconcile(batch: dict, pod_count: int | None) -> dict:
    """An unknown previous outcome clears only on a confirmed zero-Pod read."""
    if not batch or batch.get("reconciliation_status") != "unconfirmed":
        return batch
    if pod_count == 0:
        batch = dict(batch)
        batch["reconciliation_status"] = "clean"
        batch["active_pod_id"] = None
    return batch


def mark_completed(batch: dict) -> dict:
    batch = dict(batch)
    batch["status"] = "completed"
    batch["active_run_id"] = None
    batch["reserved_gpu_usd"] = 0.0
    return batch


def archive(rdir: Path) -> str:
    """Retire a finished series before a new owner decision. Never touches the spend journal.

    Refused while an attempt is in flight or while the previous outcome is unknown: renaming a
    batch file must not become a way to forget an unaccounted Pod.
    """
    batch = read(rdir)
    if not batch:
        return "absent"
    if batch.get("active_run_id"):
        raise BatchError("attempt_in_flight")
    if batch.get("reconciliation_status") != "clean":
        raise BatchError("previous_outcome_unknown")
    target = rdir / f"batch-authorization.archived-{batch.get('batch_id')}.json"
    batch_path(rdir).replace(target)
    return str(target)


def save(rdir: Path, batch: dict) -> None:
    _write(batch_path(rdir), batch)


def append_journal(rdir: Path, row: dict) -> None:
    path = journal_path(rdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
