"""Persistent GPU monitoring for the one authorised LIVE Qwen run.

Design in one paragraph
-----------------------
Windows Task Scheduler runs this file once a minute, and every pass performs two checks 30 seconds
apart - a 30-second cadence built from the scheduler's own one-minute minimum. Each run takes an
exclusive file lock, reads a lightweight snapshot of the provider (pods, both regional volumes, the
pinned SKU's availability in both datacenters and its price), updates a durable state and status
file, and exits. There is no resident process and no dependency on an editor window. When the
conditions are true *and* a stored, separate owner authorization exists, the same run starts the
prepared LIVE acceptance and then records its outcome. With no authorization it keeps monitoring
and never creates a Pod.

Two placements, one permission
------------------------------
Both paid volumes are equal candidates: ``uwgeaie5b0`` (US-TX-3) and ``y92bt25ru8`` (US-MO-1). Their
records do not prove the same thing - MO-1 carries a verified answer from the real model, TX-3 carries
a manifest whose recorded self-test was malformed - so the model's state is reported per placement and
a candidate that is not VERIFIED runs with ``--self-test-mode``: the same Pod proves the model with a
real answer before any TaskDesk work, and a failed proof stops that Pod instead of trying a second
one. Availability is never widened beyond the authorization's own placement list.

Why the authorization is a file and not a flag
----------------------------------------------
The task's own rule is that prose is not permission. So permission is a small record written by
``authorize.py`` only after the exact phrase was supplied; it names the budget, the Pod count and
an expiry, it is consumed write-ahead (before the Pod call) so a crash cannot buy a second Pod, and
it is restored if the run provably created nothing. Prose, a GPU sighting or a monitor restart can
never widen it.

Guarantees this file is responsible for
---------------------------------------
* one instance: an exclusive lock plus Task Scheduler's ``IgnoreNew`` policy;
* one Pod: the grant says one, the harness says one, and the state is written before the call;
* no spend without permission: without a valid grant the monitor stops at ``WAITING_AUTHORIZATION``;
* a fast run: one catalogue read and one volume read, no capacity queue, no 20-minute wait;
* honest state: an unreachable provider is ``ERROR``/``UNKNOWN``, never "no Pods";
* notification hygiene: the journal is written only when the observed key changes.

    python artifacts/acceptance-127/live-qwen/monitor/monitor.py --once
    python artifacts/acceptance-127/live-qwen/monitor/monitor.py --status
    python artifacts/acceptance-127/live-qwen/monitor/monitor.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
LIVE_DIR = REPO / "artifacts" / "acceptance-127" / "live-qwen"
LIVE = LIVE_DIR / "live_acceptance.py"
PREFLIGHT = LIVE_DIR / "preflight.py"
EVIDENCE = LIVE_DIR / "final-live"
BACKEND = REPO / "apps" / "backend"
SCRIPTS = REPO / "scripts"

VOLUME_IDS = ()
BUDGET_USD = 2.00
CHECK_GAP_SECONDS = 30
PASS_INTERVAL_SECONDS = 60
INTERVAL_MINUTES = 1
STALE_AFTER_SECONDS = 5 * 60
PHRASE = "I AUTHORIZE ONE PAID POD"
SCHEMA = 1
GRANT_SCHEMA = 2
USABLE = {"LOW", "MEDIUM", "HIGH"}
TERMINAL_STATES = {
    "COMPLETED",
    "FAILED",
    "LIVE_RETEST_REQUIRED",
    "CRITICAL_TERMINATION_UNCONFIRMED",
    "CRITICAL_MULTIPLE_PODS",
}
MAX_CHECK_ROWS = 3000
LIVE_TIMEOUT_SECONDS = 2 * 60 * 60

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(LIVE_DIR))

from placement_records import (  # noqa: E402
    GPU_ID,
    MODEL_ID,
    PLACEMENTS,
    VERIFIED,
    is_usable,
    model_status,
    rank,
)

VOLUME_IDS = tuple(item["volume_id"] for item in PLACEMENTS)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def runtime_dir(override: str | None = None) -> Path:
    if override:
        return Path(override)
    configured = os.environ.get("CANALLA_MONITOR_DIR")
    if configured:
        return Path(configured)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "CanallaMonitor"


# --------------------------------------------------------------------------------------------- state
def initial_state() -> dict:
    return {
        "schema": SCHEMA,
        "monitoring_enabled": True,
        "created_at": utcnow(),
        "updated_at": None,
        "checks": 0,
        "last_check_time": None,
        "last_successful_check": None,
        "next_scheduled_check": None,
        "last_gpu_status": "UNKNOWN",
        "last_gpu_price": None,
        "last_error": None,
        "consecutive_failures": 0,
        "selected_datacenter": None,
        "selected_volume": None,
        "allowed_placements": [],
        "placements": [],
        "selection_mode": None,
        "requires_selftest": False,
        "live_execution_state": "WAITING_GPU",
        "paid_run_authorized": False,
        "grant_reason": None,
        "paid_run_already_attempted": False,
        "active_pod_id": None,
        "pod_count": None,
        "final_result": None,
        "last_notice_key": None,
        "last_notice_at": None,
        "scheduler_task": None,
        "authorization_grant_id": None,
    }


def load_state(rdir: Path) -> dict:
    path = rdir / "state.json"
    state = initial_state()
    if path.is_file():
        try:
            # utf-8-sig: a marker written by Windows PowerShell may carry a BOM, and a BOM must not
            # turn a readable state into a fresh one.
            stored = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            stored = {}
        if isinstance(stored, dict):
            state.update({key: stored[key] for key in stored if key in state})
    return state


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    os.replace(temporary, path)


# ---------------------------------------------------------------------------------------------- lock
class Busy(Exception):
    pass


class FileLock:
    """One exclusive holder. A second process exits without writing anything.

    Windows byte-range locks are per file handle, so a second handle in the same or another process
    is refused; POSIX uses ``flock`` for the same contract in the free tests.
    """

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise Busy(str(error)) from None
        self.handle = handle

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


# ------------------------------------------------------------------------------------------ provider
class ProviderUnavailable(Exception):
    """The provider answered badly or not at all - never a "no GPU" verdict."""


class ProviderRateLimited(Exception):
    """HTTP 429. The provider told us when it is polite to come back."""

    def __init__(self, retry_after: int = 5):
        self.retry_after = max(1, min(60, int(retry_after or 5)))
        super().__init__(f"429 retry_after={self.retry_after}s")


class RealProvider:
    BASE = "https://api.runpod.io"

    def __init__(self) -> None:
        import runpod_api_tools

        self.key = runpod_api_tools.read_provider_secret()
        if not self.key:
            raise ProviderUnavailable("credential_not_configured")

    def call(self, path: str, *, method: str = "GET", payload: dict | None = None):
        """One bounded call. 429 is its own signal so the caller can honour Retry-After."""
        import urllib.error
        import urllib.request

        body = json.dumps(payload).encode() if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self.key}",
            "User-Agent": "canalla-llm-monitor/1.2.7",
        }
        if body:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.BASE + path, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status, response_headers, raw = (
                    response.status,
                    response.headers,
                    response.read(),
                )
        except urllib.error.HTTPError as error:
            status, response_headers, raw = error.code, error.headers, error.read()
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise ProviderUnavailable(str(error)[:200]) from None
        if status == 429:
            raise ProviderRateLimited((response_headers or {}).get("Retry-After") or 5)
        if status in {401, 403}:
            raise ProviderUnavailable(f"auth_failed: HTTP {status}")
        try:
            return status, json.loads(raw or b"{}")
        except ValueError:
            return status, raw.decode("utf-8", "replace")[:400]

    def pods(self) -> list[dict]:
        status, body = self.call("/v2/pods")
        rows = body.get("pods") if isinstance(body, dict) else None
        if status != 200 or not isinstance(rows, list):
            raise ProviderUnavailable(f"pods_read_failed: HTTP {status}")
        return [row for row in rows if isinstance(row, dict)]

    def volumes(self) -> dict[str, dict]:
        """Both regional volumes from one read, so a cycle cannot disagree with itself."""
        status, body = self.call("/v2/network-volumes")
        rows = body if isinstance(body, list) else None
        if rows is None and isinstance(body, dict):
            for key in ("networkVolumes", "volumes", "data"):
                if isinstance(body.get(key), list):
                    rows = body[key]
                    break
        if status != 200 or not isinstance(rows, list):
            raise ProviderUnavailable(f"volumes_read_failed: HTTP {status}")
        result = {}
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            result[str(row["id"])] = {
                "id": str(row["id"]),
                "name": row.get("name"),
                "datacenter": str(
                    row.get("dataCenterId") or row.get("dataCenter") or ""
                ),
                "type": row.get("type"),
                "size_gb": row.get("size"),
            }
        return result

    def catalog(self) -> list[dict]:
        import urllib.parse

        query = urllib.parse.urlencode(
            {
                "include": "AVAILABILITY",
                "product": "POD",
                "count": 1,
                "minCudaVersion": "12.1",
            }
        )
        status, body = self.call("/v2/catalog/gpus?" + query)
        rows = body.get("gpus") if isinstance(body, dict) else None
        if status != 200 or not isinstance(rows, list):
            raise ProviderUnavailable(f"catalogue_read_failed: HTTP {status}")
        return [row for row in rows if isinstance(row, dict)]


def inspect(provider, *, allowed_pairs=()) -> dict:
    """One snapshot of both placements, then the one this pass would use.

    Three GETs: the Pod list, one volume list (both volumes), and one catalogue read (both
    datacenters are in the same payload, so checking two regions costs no extra request).

    ``allowed_pairs`` comes from the stored authorization. A placement can be technically perfect
    and still not be selectable, which is why technical eligibility and authorization are reported
    as separate facts.
    """
    allowed = {(str(dc), str(vid)) for dc, vid in allowed_pairs}
    pods = provider.pods()
    volumes = provider.volumes()
    rows = provider.catalog()
    row = next((item for item in rows if item.get("id") == GPU_ID), None)
    centers = {
        str(center.get("id")): str(center.get("availability") or "NONE")
        for center in (row or {}).get("dataCenters") or []
        if isinstance(center, dict) and center.get("id")
    }
    try:
        price = float(((row or {}).get("price") or {}).get("secure") or 0)
    except (TypeError, ValueError):
        price = 0.0

    from app.compute import cost_policy

    policy = None
    if price > 0:
        policy = cost_policy.plan(
            BUDGET_USD,
            price,
            max_billable_seconds=cost_policy.MAX_BILLABLE_LIFETIME_SECONDS,
            target_work_seconds=cost_policy.DEFAULT_TARGET_WORK_SECONDS,
            min_work_seconds=2700,
        ).to_dict()
    budget_ok = bool(policy and policy["feasible"] and policy["sufficient"])

    placements = []
    for spec in PLACEMENTS:
        volume = volumes.get(spec["volume_id"]) or {}
        present = bool(volume)
        actual_dc = str(volume.get("datacenter") or "")
        availability = centers.get(spec["datacenter"], "NONE")
        model, model_reason = model_status(spec["volume_id"])
        authorized = (spec["datacenter"], spec["volume_id"]) in allowed
        reasons = []
        if not present:
            reasons.append("volume_missing")
        elif actual_dc != spec["datacenter"]:
            reasons.append(f"volume_in_{actual_dc or 'unknown'}")
        if availability not in USABLE:
            reasons.append(f"availability_{availability.lower()}")
        if not is_usable(model):
            reasons.append(f"model_{model.lower()}")
        if not budget_ok:
            reasons.append("no_meaningful_work_window")
        if pods:
            reasons.append("existing_pod")
        if not authorized:
            reasons.append("placement_not_authorized")
        technical = bool(
            present
            and actual_dc == spec["datacenter"]
            and availability in USABLE
            and is_usable(model)
            and budget_ok
            and not pods
        )
        placements.append(
            {
                "volume_id": spec["volume_id"],
                "name": spec["name"],
                "datacenter": spec["datacenter"],
                "present": present,
                "volume_datacenter": actual_dc or None,
                "gpu_available": availability in USABLE,
                "gpu_availability": availability,
                "model": model,
                "model_status": model,
                "model_reason": model_reason,
                "requires_selftest": model != VERIFIED,
                "budget_allowed": budget_ok,
                "live_authorized": authorized,
                "technically_eligible": technical,
                "live_eligible": technical and authorized,
                "rejection_reason": None
                if technical and authorized
                else ",".join(reasons) or "unavailable",
                "reasons": reasons,
            }
        )

    # Prefer the proven copy when both can book the same card at the same price; otherwise the
    # only available one wins. Availability is never widened - this only orders what exists.
    candidates = [item for item in placements if item["live_eligible"]]
    chosen = min(candidates, key=lambda item: (rank(item["model"]), item["volume_id"])) if candidates else None
    technically = [item for item in placements if item["technically_eligible"]]
    reference = chosen or (min(technically, key=lambda item: rank(item["model"])) if technically else placements[0])

    return {
        "ok": True,
        "observed_at": utcnow(),
        "pods": [
            {
                "id": pod.get("id"),
                "status": pod.get("status"),
                "dataCenterId": pod.get("dataCenterId"),
                "createdAt": pod.get("createdAt"),
            }
            for pod in pods
        ],
        "pod_count": len(pods),
        "placements": placements,
        "chosen": chosen,
        "technically_available": bool(technically),
        "volume": {
            "id": reference["volume_id"],
            "datacenter": reference["volume_datacenter"],
            "present": reference["present"],
            "type": (volumes.get(reference["volume_id"]) or {}).get("type"),
            "size_gb": (volumes.get(reference["volume_id"]) or {}).get("size_gb"),
        },
        "gpu": {
            "id": GPU_ID,
            "price_usd_per_hour": price or None,
            "availability": reference["gpu_availability"],
            "availability_by_datacenter": {
                item["datacenter"]: item["gpu_availability"] for item in placements
            },
            "bookable_datacenters": sorted(
                name for name, value in centers.items() if value in USABLE
            ),
        },
        "policy": policy,
        "available": chosen is not None,
        "reason": None if chosen else (",".join(reference["reasons"]) or "unavailable"),
    }


# ---------------------------------------------------------------------------------------------- grant
def grant_path(rdir: Path) -> Path:
    return rdir / "authorization-grant.json"


def read_grant(rdir: Path, now: datetime | None = None) -> dict:
    """The stored permission, validated. Silence, prose or a GPU sighting never substitutes."""
    path = grant_path(rdir)
    if not path.is_file():
        return {"valid": False, "reason": "not_granted", "grant": None}
    try:
        grant = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"valid": False, "reason": "malformed", "grant": None}
    return validate_grant(grant, now)


def validate_grant(grant, now: datetime | None = None) -> dict:
    """Validate one payload without touching the disk.

    Separate from the read so a scope upgrade can be proven valid *before* it atomically replaces
    the stored file: a bad rewrite must never become the live permission.
    """
    now = now or datetime.now(timezone.utc)
    if not isinstance(grant, dict):
        return {"valid": False, "reason": "malformed", "grant": None}
    if grant.get("phrase") != PHRASE:
        return {"valid": False, "reason": "wrong_phrase", "grant": grant}
    try:
        budget = float(grant.get("budget_usd"))
    except (TypeError, ValueError):
        return {"valid": False, "reason": "malformed_budget", "grant": grant}
    if budget <= 0 or budget > BUDGET_USD + 1e-9:
        return {"valid": False, "reason": "budget_out_of_range", "grant": grant}
    if int(grant.get("max_pods") or 0) != 1:
        return {"valid": False, "reason": "pod_count_not_one", "grant": grant}
    # The grant names the exact run it permits: one model, one card, and an explicit list of
    # placements. A list is used from schema 2 on; a legacy single placement is still honoured.
    if grant.get("allowed_model") != MODEL_ID or grant.get("allowed_gpu") != GPU_ID:
        return {"valid": False, "reason": "scope_mismatch", "grant": grant}
    placements = grant.get("allowed_placements")
    if placements is None:
        if grant.get("allowed_volume") != "y92bt25ru8" or grant.get("allowed_datacenter") != "US-MO-1":
            return {"valid": False, "reason": "scope_mismatch", "grant": grant}
        pairs = [("US-MO-1", "y92bt25ru8")]
    else:
        if not isinstance(placements, list) or not placements:
            return {"valid": False, "reason": "scope_mismatch", "grant": grant}
        pairs = []
        known = {(item["datacenter"], item["volume_id"]) for item in PLACEMENTS}
        for entry in placements:
            if not isinstance(entry, dict):
                return {"valid": False, "reason": "scope_mismatch", "grant": grant}
            pair = (str(entry.get("datacenter") or ""), str(entry.get("volume_id") or ""))
            if pair not in known or str(entry.get("gpu") or GPU_ID) != GPU_ID:
                return {"valid": False, "reason": "scope_mismatch", "grant": grant}
            pairs.append(pair)
        if len(set(pairs)) != len(pairs):
            return {"valid": False, "reason": "scope_mismatch", "grant": grant}
    if grant.get("used"):
        return {"valid": False, "reason": "already_used", "grant": grant}
    expires = parse(grant.get("expires_at"))
    if expires is None:
        return {"valid": False, "reason": "missing_expiry", "grant": grant}
    if expires <= now:
        return {"valid": False, "reason": "expired", "grant": grant}
    if grant.get("revoked"):
        return {"valid": False, "reason": "revoked", "grant": grant}
    return {"valid": True, "reason": "ok", "grant": grant, "placements": pairs}


def mark_grant(rdir: Path, *, used: bool, run_id: str | None = None) -> None:
    path = grant_path(rdir)
    if not path.is_file():
        return
    try:
        grant = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    grant["used"] = used
    grant["used_at"] = utcnow() if used else None
    grant["run_id"] = run_id if used else None
    save_json(path, grant)


# -------------------------------------------------------------------------------------------- checks
def run_check(
    *,
    provider,
    rdir: Path,
    now: datetime | None = None,
    dry_run: bool = False,
    launch=None,
    remaining_reader=None,
    state_override: dict | None = None,
    next_delay: int = CHECK_GAP_SECONDS,
) -> dict:
    """One check. Pure enough to drive from tests: the provider and launcher are injected."""
    now = now or datetime.now(timezone.utc)
    rdir.mkdir(parents=True, exist_ok=True)
    scheduler = _scheduler_info(rdir)
    state = state_override or load_state(rdir)
    if scheduler.get("task_name"):
        state["scheduler_task"] = scheduler["task_name"]
    if not state.get("monitoring_enabled", True):
        state["updated_at"] = utcnow()
        save_json(rdir / "state.json", state)
        return {"skipped": "monitoring_disabled", "state": state}

    grant = read_grant(rdir, now)
    state["paid_run_authorized"] = bool(grant["valid"])
    state["grant_reason"] = grant["reason"]
    state["allowed_placements"] = [
        {"datacenter": dc, "volume_id": vid}
        for dc, vid in (grant.get("placements") or [])
    ]
    state["checks"] = int(state.get("checks") or 0) + 1
    state["last_check_time"] = now.isoformat(timespec="seconds")
    state["next_scheduled_check"] = (now + timedelta(seconds=next_delay)).isoformat(
        timespec="seconds"
    )

    try:
        observation = inspect(provider, allowed_pairs=grant.get("placements") or ())
        state["consecutive_failures"] = 0
        state["last_successful_check"] = state["last_check_time"]
        state["last_error"] = None
        state.pop("retry_after_seconds", None)
    except ProviderRateLimited as error:
        observation = None
        state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
        state["last_error"] = f"rate_limited: retry_after={error.retry_after}s"
        state["retry_after_seconds"] = error.retry_after
        state["last_gpu_status"] = "UNKNOWN"
    except ProviderUnavailable as error:
        observation = None
        state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
        state["last_error"] = f"provider_unreachable: {error}"[:300]
        state["last_gpu_status"] = "UNKNOWN"

    result = {"observation": observation, "grant": grant, "run": None}

    if observation is None:
        if not _is_terminal(state):
            state["live_execution_state"] = "ERROR"
        _finish(rdir, state, observation, grant, result)
        return result

    state["pod_count"] = observation["pod_count"]
    state["placements"] = observation["placements"]
    chosen = observation.get("chosen")
    state["selected_datacenter"] = chosen["datacenter"] if chosen else None
    state["selected_volume"] = chosen["volume_id"] if chosen else None
    state["requires_selftest"] = bool(chosen and chosen["requires_selftest"])
    state["selection_mode"] = (
        ("AUTO_LIVE_WITH_MODEL_SELFTEST" if chosen["requires_selftest"] else "AUTO_LIVE")
        if chosen
        else None
    )
    price = observation["gpu"]["price_usd_per_hour"]
    state["last_gpu_price"] = price
    # Availability is a fact about the catalogue, not about permission: a placement the grant
    # does not cover can still be bookable, and saying UNAVAILABLE would hide that.
    state["last_gpu_status"] = (
        "AVAILABLE" if observation["technically_available"] else "UNAVAILABLE"
    )

    if _is_terminal(state):
        _finish(rdir, state, observation, grant, result)
        return result

    if not chosen:
        # A technically usable placement that the grant does not cover is an authorization
        # question, not a capacity question; both are reported honestly.
        state["live_execution_state"] = (
            "WAITING_AUTHORIZATION" if observation["technically_available"] else "WAITING_GPU"
        )
        _finish(rdir, state, observation, grant, result)
        return result

    if not grant["valid"]:
        state["live_execution_state"] = "WAITING_AUTHORIZATION"
        _finish(rdir, state, observation, grant, result)
        return result

    if state.get("paid_run_already_attempted"):
        # A previous pass already started a paid attempt; only a human decision may start another.
        state["live_execution_state"] = "LIVE_RETEST_REQUIRED"
        _finish(rdir, state, observation, grant, result)
        return result

    if dry_run:
        state["live_execution_state"] = "DRY_RUN_OK"
        result["run"] = {
            "dry_run": True,
            "would_launch": _live_command(grant["grant"], chosen),
        }
        _finish(rdir, state, observation, grant, result)
        return result

    launcher = launch or launch_live
    payload = launcher(
        rdir=rdir, state=state, grant=grant["grant"], observation=observation
    )
    if isinstance(payload, dict):
        _apply_run_payload(rdir, state, payload, remaining_reader=remaining_reader)
    result["run"] = payload
    refreshed = read_grant(rdir, now)
    state["paid_run_authorized"] = bool(refreshed["valid"])
    state["grant_reason"] = refreshed["reason"]
    _finish(rdir, state, observation, refreshed, result)
    return result


def run_pass(
    *,
    provider_factory,
    rdir: Path,
    checks: int = 2,
    gap: int = CHECK_GAP_SECONDS,
    dry_run: bool = False,
    launch=None,
    sleep=None,
    warm: bool = True,
) -> list[dict]:
    """One scheduled pass: two checks whose *starts* are ``gap`` seconds apart, under one lock.

    Task Scheduler's smallest repetition is one minute, so a 30-second cadence is two checks per
    pass. The gap is measured from the start of one check to the start of the next: sleeping a full
    ``gap`` after the check finished made the real cadence ~44 s, because three provider reads are
    not instant. A 429 ends the pass early: the scheduler will come back in a minute, which is
    already at or beyond the Retry-After bound we accept.
    """
    sleeper = sleep or time.sleep
    results: list[dict] = []
    if warm and load_state(rdir).get("last_gpu_status") == "AVAILABLE":
        # A card was seen recently, so the next sighting may start a paid run. Refresh the free
        # preflight cache now, while nothing is being raced, instead of paying ~3 s for it between
        # "the card is free" and "ask for the Pod". It is a no-op while the cache is fresh.
        try:
            cached_preflight(rdir)
        except Exception:  # noqa: BLE001 - a failed warm-up must not break the pass
            pass
    previous_start: float | None = None
    for index in range(max(1, int(checks))):
        if previous_start is not None:
            sleeper(max(0.0, float(gap) - (time.monotonic() - previous_start)))
        started = time.monotonic()
        provider = provider_factory()
        next_delay = gap if index + 1 < checks else PASS_INTERVAL_SECONDS
        result = run_check(
            provider=provider,
            rdir=rdir,
            dry_run=dry_run,
            launch=launch,
            next_delay=next_delay,
        )
        previous_start = started
        results.append(result)
        state = result.get("state") or {}
        if state.get("retry_after_seconds"):
            break
        if _is_terminal(state):
            break
    return results


def _live_command(
    grant: dict,
    chosen: dict,
    prepared_path: Path | None = None,
    run_id: str | None = None,
) -> list[str]:
    """The prepared harness, bound to the placement this pass proved and to its own proof level.

    ``--self-test-mode`` is not a weaker run: it is the same single Pod proving the model with a
    real answer before TaskDesk starts. It is added exactly when the chosen copy's record is not a
    verified real-model answer.

    ``--prepared`` carries the observation this pass just made, so the harness does not repeat the
    free checks or re-read the same catalogue while the card is still bookable.
    """
    command = [
        sys.executable,
        str(LIVE),
        "--live",
        "--budget-usd",
        f"{float(grant['budget_usd']):.2f}",
        "--confirm",
        str(grant["phrase"]),
        "--volume",
        str(chosen["volume_id"]),
    ]
    if prepared_path is not None:
        command += ["--prepared", str(prepared_path)]
    if run_id:
        command += ["--run-id", run_id]
    if chosen.get("requires_selftest"):
        command.append("--self-test-mode")
    return command


PREFLIGHT_TTL_SECONDS = 900


def cached_preflight(rdir: Path, now: datetime | None = None) -> tuple[dict, bool]:
    """The free preflight, reused while its facts can still be true.

    Every item it checks changes on a human scale (installed build, production database, disk,
    watchdog, provenance, account pod count). Re-running it between "a card is free" and "ask for
    the Pod" was the largest avoidable delay in the previous attempts. It is never trusted past its
    TTL, and a non-READY answer is never cached over a READY one.
    """
    now = now or datetime.now(timezone.utc)
    path = rdir / "preflight-cache.json"
    cached = _read_json(path)
    checked = parse(cached.get("checked_at"))
    report = cached.get("report") if isinstance(cached.get("report"), dict) else {}
    if (
        report.get("verdict") == "READY"
        and checked is not None
        and (now - checked).total_seconds() <= PREFLIGHT_TTL_SECONDS
    ):
        return report, True
    report = _run_json([sys.executable, str(PREFLIGHT)], timeout=300)
    if report.get("verdict") == "READY":
        save_json(path, {"checked_at": utcnow(), "report": report})
    return report, False


def prepared_payload(
    observation: dict, chosen: dict, grant: dict, preflight: dict, run_id: str
) -> dict:
    """What the harness needs, taken from this pass's own provider read."""
    return {
        "schema": 1,
        "run_id": run_id,
        "generated_at": utcnow(),
        "observed_at": observation.get("observed_at"),
        "pod_count": observation.get("pod_count"),
        "placement": {
            "datacenter": chosen.get("datacenter"),
            "volume_id": chosen.get("volume_id"),
            "storage_mode": "regional_network_volume",
        },
        "rate_usd_per_hour": observation.get("gpu", {}).get("price_usd_per_hour"),
        "budget_usd": grant.get("budget_usd"),
        "model_state": chosen.get("model"),
        "requires_selftest": bool(chosen.get("requires_selftest")),
        "preflight": {
            "verdict": preflight.get("verdict"),
            "checked_at": preflight.get("checked_at") or utcnow(),
            "blockers": preflight.get("blockers") or [],
            "report": preflight,
        },
    }


def resolve_run_accounting(
    *,
    run_id: str,
    record: dict,
    final: dict,
    billing: dict,
    rate: float,
    pod_id: str | None,
) -> dict:
    """What a finished attempt actually bought, from evidence that can be attributed to it.

    Three independent signals, strongest first: the run-scoped record written while the isolated
    database still existed, a Pod id in the lifecycle evidence, and the account balance movement.
    A shared ``final-summary.json`` is only used when it names this run; a summary left by another
    process must never restore a permission that a real Pod has consumed.
    """
    trusted = bool(record.get("run_id")) and record.get("run_id") == run_id
    same_summary = bool(final.get("run_id")) and final.get("run_id") == run_id

    pods_created = 0
    spend = 0.0
    verdict = "UNKNOWN"
    if trusted:
        raw = record.get("pods_created")
        pods_created = int(raw) if isinstance(raw, int) else 0
        spend = float(record.get("spend_usd") or 0.0)
        verdict = str(record.get("verdict") or "INCOMPLETE")
    elif same_summary:
        pods_created = int(final.get("pods_created") or 0)
        spend = float(final.get("spend_usd") or 0.0)
        verdict = str(final.get("verdict") or "UNKNOWN")

    if pod_id:
        pods_created = max(pods_created, 1)

    before, after = billing.get("balance_before_usd"), billing.get("balance_after_usd")
    billed: float | None = None
    if (
        isinstance(before, (int, float))
        and isinstance(after, (int, float))
        and rate > 0
    ):
        billed = round(float(before) - float(after), 4)
        if billed >= rate / 60:  # one billing minute is the smallest amount a Pod can cost
            pods_created = max(pods_created, 1)

    if pods_created == 0 and billed is None:
        # No money truth, so only an accounting record that positively counts zero Pods is clean.
        recorded_zero = trusted and isinstance(record.get("pods_created"), int)
        summary_zero = same_summary and int(final.get("pods_created") or 0) == 0
        if not (recorded_zero or summary_zero):
            # Nothing attributable and no money truth: the run is unknown, not clean. A second Pod
            # needs a human decision, so this is treated as an attempt.
            pods_created = 1
            verdict = "UNKNOWN"

    return {
        "trusted": trusted or same_summary,
        "pods_created": pods_created,
        "spend_usd": spend,
        "verdict": verdict,
        "billed_usd": billed,
    }


def launch_live(*, rdir: Path, state: dict, grant: dict, observation: dict) -> dict:
    """Run the prepared acceptance exactly once, after the free checks are satisfied.

    The state and the grant are written *before* the harness starts: if this process dies mid-run,
    the next scheduled pass sees an attempted run and a consumed grant and refuses to buy a second
    Pod. If the harness proves that nothing was created, both are restored so a genuine capacity
    race does not burn the owner's permission.
    """
    run_id = uuid.uuid4().hex
    detected_at = observation.get("observed_at")
    preflight, reused = cached_preflight(rdir)
    preflight_at = utcnow()
    if preflight.get("verdict") != "READY":
        payload = {
            "generated_at": utcnow(),
            "verdict": "PREFLIGHT_BLOCKED",
            "blocked_by": preflight.get("blockers"),
            "pods_created": 0,
            "spend_usd": 0.0,
        }
        save_json(rdir / "last-run.json", payload)
        state["last_error"] = (
            "preflight_blocked: " + ",".join(preflight.get("blockers") or [])[:200]
        )
        return payload

    chosen = observation.get("chosen") or {}
    volume_id = str(chosen.get("volume_id") or "")
    if not volume_id:
        raise RuntimeError("no placement was chosen for the paid run")
    prepared_path = rdir / "live-prepared.json"
    save_json(prepared_path, prepared_payload(observation, chosen, grant, preflight, run_id))
    state["paid_run_already_attempted"] = True
    state["live_execution_state"] = (
        "AUTO_LIVE_WITH_MODEL_SELFTEST"
        if chosen.get("requires_selftest")
        else "AUTO_LIVE"
    )
    state["run_started_at"] = utcnow()
    state["authorization_grant_id"] = grant.get("grant_id")
    save_json(rdir / "state.json", state)
    mark_grant(rdir, used=True, run_id=run_id)
    claimed_at = utcnow()

    log_path = rdir / "live-run.log"
    launched_at = utcnow()
    started = time.monotonic()
    try:
        completed = subprocess.run(
            _live_command(grant, chosen, prepared_path, run_id),
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=LIVE_TIMEOUT_SECONDS,
            check=False,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        stdout, stderr, code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as error:
        stdout = (
            (error.stdout or b"").decode("utf-8", "replace") if error.stdout else ""
        )
        stderr = (
            (error.stderr or b"").decode("utf-8", "replace") if error.stderr else ""
        )
        code = -9
    elapsed = round(time.monotonic() - started, 1)
    detected_clock, launched_clock = parse(detected_at), parse(launched_at)
    detection_to_launch = (
        round((launched_clock - detected_clock).total_seconds(), 3)
        if detected_clock and launched_clock
        else None
    )
    timings = {
        "detected_at": detected_at,
        "preflight_at": preflight_at,
        "preflight_reused": reused,
        "claimed_at": claimed_at,
        "launched_at": launched_at,
        "detection_to_launch_seconds": detection_to_launch,
        "harness_elapsed_seconds": elapsed,
        "finished_at": utcnow(),
    }
    log_path.write_text(
        f"# run {run_id} exit={code} elapsed_s={elapsed}\n"
        f"# detected={detected_at} claimed={claimed_at} preflight_reused={reused}\n"
        f"## stdout\n{stdout}\n## stderr\n{stderr}\n",
        encoding="utf-8",
    )

    final = _read_json(EVIDENCE / "final-summary.json") or {}
    cleanup = _read_json(EVIDENCE / "cleanup.json") or {}
    harness_timings = _read_json(EVIDENCE / "startup-timings.json") or {}
    record = _read_json(EVIDENCE / f"run-{run_id}.json") or {}
    billing = _read_json(EVIDENCE / "billing-summary.json") or {}
    rate = float((observation.get("gpu") or {}).get("price_usd_per_hour") or 0)
    accounting = resolve_run_accounting(
        run_id=run_id,
        record=record,
        final=final,
        billing=billing,
        rate=rate,
        pod_id=_pod_id_from_evidence(),
    )
    pods_created = accounting["pods_created"]
    spend = accounting["spend_usd"]

    payload = {
        "generated_at": utcnow(),
        "run_id": run_id,
        "exit_code": code,
        "elapsed_seconds": elapsed,
        "verdict": accounting["verdict"],
        "pods_created": pods_created,
        "spend_usd": spend,
        "billed_usd": accounting["billed_usd"],
        "evidence_trusted": accounting["trusted"],
        "path": final.get("path") or ("prepared" if harness_timings else None),
        "prepared_observed_at": final.get("prepared_observed_at"),
        "timings": timings,
        "harness_timings": harness_timings,
        "cleanup": cleanup,
        "final_result": final,
    }
    save_json(rdir / "last-run.json", payload)
    return payload


def _apply_run_payload(
    rdir: Path, state: dict, payload: dict, *, remaining_reader=None
) -> None:
    """Reconcile the durable state with what the attempt actually did.

    Kept separate from the launcher so the free tests can drive the same decision with a fake run.
    Nothing created means nothing was bought: the permission is restored and the monitor returns to
    waiting. Anything else is terminal - a second Pod needs a new, separate authorization.
    """
    verdict = str(payload.get("verdict") or "FAILED")
    pods_created = int(payload.get("pods_created") or 0)
    spend = float(payload.get("spend_usd") or 0.0)
    state["final_result"] = {
        "verdict": verdict,
        "spend_usd": spend,
        "pods_created": pods_created,
        "exit_code": payload.get("exit_code"),
    }
    state["last_run_path"] = payload.get("path")
    state["last_run_timings"] = payload.get("timings")
    if verdict == "PREFLIGHT_BLOCKED":
        state["paid_run_already_attempted"] = False
        state["live_execution_state"] = "PREFLIGHT_BLOCKED"
        state["last_error"] = (
            "preflight_blocked: " + ",".join(payload.get("blocked_by") or [])[:200]
        )
        mark_grant(rdir, used=False)
        return
    if pods_created == 0 and spend == 0.0:
        state["paid_run_already_attempted"] = False
        state["live_execution_state"] = "WAITING_GPU"
        state["active_pod_id"] = None
        mark_grant(rdir, used=False)
        return
    state["paid_run_already_attempted"] = True
    state["active_pod_id"] = _pod_id_from_evidence()
    # A run that bought a Pod spends the permission, whichever launcher produced it.
    mark_grant(rdir, used=True, run_id=payload.get("run_id"))
    remaining = (remaining_reader or _remaining_pods)()
    if remaining is None:
        state["live_execution_state"] = "CRITICAL_TERMINATION_UNCONFIRMED"
        return
    if not remaining:
        state["live_execution_state"] = (
            "COMPLETED" if verdict == "COMPLETED" else "FAILED"
        )
        state["pod_count"] = 0
    elif len(remaining) == 1:
        state["live_execution_state"] = "CRITICAL_TERMINATION_UNCONFIRMED"
        state["pod_count"] = 1
    else:
        state["live_execution_state"] = "CRITICAL_MULTIPLE_PODS"
        state["pod_count"] = len(remaining)


def _pod_id_from_evidence() -> str | None:
    for name in ("pod-lifecycle.json", "watchdog-events.json", "final-summary.json"):
        payload = _read_json(EVIDENCE / name) or {}
        if payload.get("pod_id"):
            return str(payload["pod_id"])
        pod_ids = payload.get("pod_ids")
        if isinstance(pod_ids, list) and pod_ids:
            return str(pod_ids[0])
        for event in payload.get("events") or []:
            if isinstance(event, dict) and event.get("pod_id"):
                return str(event["pod_id"])
    return None


def _remaining_pods() -> list[dict] | None:
    """A read-only confirmation after the run. ``None`` means the answer is unknown, not clean.

    Termination itself belongs to the harness and its external watchdog; this monitor never kills a
    Pod it cannot attribute, because the provider account is shared with other installations.
    """
    try:
        provider = RealProvider()
        return provider.pods()
    except ProviderUnavailable:
        return None
    except OSError:
        return None


def _run_json(command: list[str], *, timeout: float) -> dict:
    try:
        completed = subprocess.run(
            command,
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    for line in reversed((completed.stdout or "").strip().splitlines()):
        candidate = line.strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            try:
                return json.loads(candidate)
            except ValueError:
                continue
    try:
        return json.loads(completed.stdout or "{}")
    except ValueError:
        return {}


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_terminal(state: dict) -> bool:
    return str(state.get("live_execution_state")) in TERMINAL_STATES


def _finish(rdir: Path, state: dict, observation, grant, result) -> None:
    state["updated_at"] = utcnow()
    # Notify first: it records the observed key on the state, and a key that is not persisted turns
    # every scheduled pass into a duplicate notification.
    notify(rdir, state, observation, grant)
    save_json(rdir / "state.json", state)
    save_json(rdir / "status.json", build_status(state, observation, grant))
    append_check(rdir, state, observation, grant)
    result["state"] = state


def build_status(state: dict, observation, grant) -> dict:
    last = parse(state.get("last_check_time"))
    enabled = bool(state.get("monitoring_enabled", True))
    failures = int(state.get("consecutive_failures") or 0)
    if not enabled:
        monitor = "STOPPED"
    elif failures >= 3:
        monitor = "ERROR"
    elif (
        last is None
        or (datetime.now(timezone.utc) - last).total_seconds() <= STALE_AFTER_SECONDS
    ):
        monitor = "RUNNING"
    else:
        monitor = "ERROR"
    availability = (
        "UNKNOWN"
        if observation is None
        else ("AVAILABLE" if observation["technically_available"] else "UNAVAILABLE")
    )
    return {
        "gpu_monitor": monitor,
        "gpu_availability": availability,
        "live": state.get("live_execution_state"),
        "automatic_live": automatic_live(state),
        "selection_mode": state.get("selection_mode"),
        "requires_selftest": bool(state.get("requires_selftest")),
        "allowed_placements": state.get("allowed_placements") or [],
        "interval_seconds": CHECK_GAP_SECONDS,
        "pending_interval_seconds": PASS_INTERVAL_SECONDS,
        "placements": state.get("placements")
        or (observation or {}).get("placements")
        or [],
        "last_check": state.get("last_check_time"),
        "next_check": state.get("next_scheduled_check"),
        "current_gpu_price": state.get("last_gpu_price"),
        "retry_after_seconds": state.get("retry_after_seconds"),
        "pod_count": state.get("pod_count"),
        "paid_run_authorized": bool(state.get("paid_run_authorized")),
        "grant_reason": state.get("grant_reason"),
        "monitoring_enabled": enabled,
        "consecutive_failures": failures,
        "last_error": state.get("last_error"),
        "selected_datacenter": state.get("selected_datacenter"),
        "selected_volume": state.get("selected_volume"),
        "scheduler_task": state.get("scheduler_task"),
        "final_result": state.get("final_result"),
        "last_run_path": state.get("last_run_path"),
        "last_run_timings": state.get("last_run_timings"),
        "updated_at": state.get("updated_at"),
        "gpu": (observation or {}).get("gpu"),
        "volume": (observation or {}).get("volume"),
        "policy": (observation or {}).get("policy"),
    }


def automatic_live(state: dict) -> str:
    """Whether a paid run may start without another human decision.

    ARMED means: a valid, unused grant exists, no paid attempt has been made and the task is not in
    a terminal state. GPU availability is deliberately not part of it - the monitor keeps checking
    and starts the run the moment the card appears.
    """
    if _is_terminal(state) or state.get("paid_run_already_attempted"):
        return "DISARMED"
    if not state.get("monitoring_enabled", True):
        return "DISARMED"
    return "ARMED" if state.get("paid_run_authorized") else "WAITING_AUTHORIZATION"


def append_check(rdir: Path, state: dict, observation, grant) -> None:
    row = {
        "at": state.get("last_check_time"),
        "availability": "UNKNOWN"
        if observation is None
        else ("AVAILABLE" if observation["technically_available"] else "UNAVAILABLE"),
        "price": state.get("last_gpu_price"),
        "pod_count": state.get("pod_count"),
        "live": state.get("live_execution_state"),
        "authorized": bool(state.get("paid_run_authorized")),
        "reason": (observation or {}).get("reason") or state.get("last_error"),
    }
    path = rdir / "checks.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    _trim(path)


def _trim(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= MAX_CHECK_ROWS:
        return
    keep = lines[-MAX_CHECK_ROWS:]
    path.write_text("\n".join(keep) + "\n", encoding="utf-8")


def notify(rdir: Path, state: dict, observation, grant) -> None:
    """One journal line per distinct observation, never the same line every ten minutes."""
    key = "|".join(
        [
            "UNKNOWN"
            if observation is None
            else ("AVAILABLE" if observation["technically_available"] else "UNAVAILABLE"),
            str(state.get("last_gpu_price")),
            str(state.get("live_execution_state")),
            str(state.get("pod_count")),
            str(bool(state.get("paid_run_authorized"))),
        ]
    )
    if key == state.get("last_notice_key"):
        return
    state["last_notice_key"] = key
    state["last_notice_at"] = utcnow()
    row = {
        "at": state["last_notice_at"],
        "availability": key.split("|")[0],
        "price": state.get("last_gpu_price"),
        "live": state.get("live_execution_state"),
        "pod_count": state.get("pod_count"),
        "authorized": bool(state.get("paid_run_authorized")),
        "grant_reason": grant.get("reason"),
        "error": state.get("last_error"),
    }
    with (rdir / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    _trim(rdir / "events.jsonl")


def _scheduler_info(rdir: Path) -> dict:
    return _read_json(rdir / "scheduler.json")


# ---------------------------------------------------------------------------------------------- entry
def status_block(rdir: Path) -> dict:
    status = _read_json(rdir / "status.json")
    if not status:
        state = load_state(rdir)
        status = build_status(state, None, {"valid": False, "reason": "not_granted"})
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Canalla LIVE GPU monitor (one scheduled pass)."
    )
    parser.add_argument(
        "--once", action="store_true", help="run a single check instead of a full pass"
    )
    parser.add_argument(
        "--pass",
        action="store_true",
        help="run the scheduled pass: two checks, 30 seconds apart (default)",
    )
    parser.add_argument("--checks", type=int, default=2, help=argparse.SUPPRESS)
    parser.add_argument(
        "--gap", type=float, default=CHECK_GAP_SECONDS, help=argparse.SUPPRESS
    )
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--dry-run", action="store_true", help="prove the paid path without a Pod"
    )
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--disable", action="store_true")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument(
        "--fixture", default=None, help="observation JSON instead of the provider"
    )
    parser.add_argument(
        "--debug-hold-seconds", type=float, default=0.0, help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)
    rdir = runtime_dir(args.state_dir)

    if args.self_test:
        return self_test()
    if args.status:
        print(json.dumps(status_block(rdir), ensure_ascii=False, indent=1))
        return 0
    if args.enable or args.disable:
        state = load_state(rdir)
        state["monitoring_enabled"] = bool(args.enable)
        state["updated_at"] = utcnow()
        save_json(rdir / "state.json", state)
        save_json(
            rdir / "status.json",
            build_status(state, None, {"valid": False, "reason": "n/a"}),
        )
        print(
            json.dumps(
                {"monitoring_enabled": state["monitoring_enabled"]}, ensure_ascii=False
            )
        )
        return 0

    try:
        lock = FileLock(rdir / "monitor.lock")
        lock.acquire()
    except Busy:
        print(json.dumps({"skipped": "another_instance_running"}, ensure_ascii=False))
        return 0
    try:
        if args.debug_hold_seconds:
            time.sleep(args.debug_hold_seconds)
        fixture = _provider_from_fixture(args.fixture) if args.fixture else None
        results = run_pass(
            provider_factory=lambda: fixture if fixture is not None else RealProvider(),
            rdir=rdir,
            checks=1 if args.once else max(1, args.checks),
            gap=0 if args.once else max(0.0, args.gap),
            dry_run=args.dry_run,
        )
        for result in results:
            observation = result.get("observation")
            print(
                json.dumps(
                    {
                        "at": (result.get("state") or {}).get("last_check_time"),
                        "gpu_availability": "UNKNOWN"
                        if observation is None
                        else (
                            "AVAILABLE" if observation["technically_available"] else "UNAVAILABLE"
                        ),
                        "price": (result.get("state") or {}).get("last_gpu_price"),
                        "live": (result.get("state") or {}).get("live_execution_state"),
                        "authorized": bool(
                            (result.get("state") or {}).get("paid_run_authorized")
                        ),
                        "pod_count": (result.get("state") or {}).get("pod_count"),
                        "placements": [
                            {
                                "datacenter": item["datacenter"],
                                "gpu": item["gpu_availability"],
                                "model": item["model"],
                                "eligible": item["live_eligible"],
                            }
                            for item in (observation or {}).get("placements") or []
                        ],
                        "reason": (observation or {}).get("reason")
                        or (result.get("state") or {}).get("last_error"),
                    },
                    ensure_ascii=False,
                )
            )
        return 0
    finally:
        lock.release()


class FixtureProvider:
    """Offline observations for the free tests: a list or a single dict, one per pass."""

    def __init__(self, payload):
        if isinstance(payload, dict) and "passes" in payload:
            self.passes = list(payload["passes"])
        elif isinstance(payload, list):
            self.passes = payload
        else:
            self.passes = [payload]

    def _next(self):
        if not self.passes:
            return self.passes[-1] if self.passes else {"ok": False}
        if len(self.passes) > 1:
            return self.passes.pop(0)
        return self.passes[0]

    def pods(self):
        data = self._next()
        if data.get("rate_limited"):
            raise ProviderRateLimited(int(data.get("retry_after") or 5))
        if data.get("error"):
            raise ProviderUnavailable(str(data["error"]))
        return data.get("pods") or []

    def volumes(self):
        """Both volumes, each in its declared datacenter; a fixture may omit either id."""
        data = self._next()
        if data.get("rate_limited"):
            raise ProviderRateLimited(int(data.get("retry_after") or 5))
        if data.get("error"):
            raise ProviderUnavailable(str(data["error"]))
        supplied = data.get("volumes") if isinstance(data.get("volumes"), dict) else {}
        result = {}
        for spec in PLACEMENTS:
            override = supplied.get(spec["volume_id"])
            if override is False:
                continue
            override = override if isinstance(override, dict) else {}
            result[spec["volume_id"]] = {
                "id": spec["volume_id"],
                "name": spec["name"],
                "datacenter": override.get("datacenter", spec["datacenter"]),
                "type": override.get("type", "STANDARD"),
                "size_gb": override.get("size_gb", 50),
            }
        return result

    def catalog(self):
        data = self._next()
        if data.get("rate_limited"):
            raise ProviderRateLimited(int(data.get("retry_after") or 5))
        if data.get("error"):
            raise ProviderUnavailable(str(data["error"]))
        gpu = data.get("gpu") or {}
        by_dc = gpu.get("availability_by_dc")
        if not isinstance(by_dc, dict):
            # Backward compatible: one availability names the placement the caller had in mind; the
            # other is reported as NONE rather than being invented. ``datacenter`` says which one.
            single = gpu.get("availability", "NONE")
            named = gpu.get("datacenter") or PLACEMENTS[0]["datacenter"]
            by_dc = {spec["datacenter"]: (single if spec["datacenter"] == named else "NONE") for spec in PLACEMENTS}
        return [
            {
                "id": GPU_ID,
                "manufacturer": "NVIDIA",
                "memory": 48,
                "price": {"secure": gpu.get("price_usd_per_hour", 1.09)},
                "dataCenters": [
                    {"id": dc, "availability": value} for dc, value in by_dc.items()
                ],
            }
        ]


def _provider_from_fixture(path: str):
    return FixtureProvider(_read_json(Path(path)))


# ------------------------------------------------------------------------------------------ self-test
def self_test() -> int:
    """The free lifecycle matrix lives next to the monitor; see ``selftest.py``."""
    import selftest

    return selftest.run_self_test()


def _write_fixture(root: Path, payload: dict) -> Path:
    path = root / "fixture.json"
    save_json(path, payload)
    return path


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
