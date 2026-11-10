"""Bounded, autonomous supervisor for the remaining 1.2.6 live gates.

The previous stage ended with "capacity checked five times, nothing, done". That is not a
supervisor, it is a sample. This process owns the whole bounded wait instead:

* it re-reads *free* provider availability every 15 minutes for at most 48 hours (<= 192 checks);
* it never creates a GPU to find out whether a GPU is free;
* it refuses to start anything unless the independent watchdog is reachable, the manual-off latch
  is clear, no other Pod exists, the budget allows the whole worst case, and the free readiness
  file is current;
* when a placeable card appears it runs one bounded live scenario per Pod (the hard 600 s Pod cap
  makes "all three scenarios in one Pod" impossible: T3L 480 s + T4L 420 s + LA 480 s is 1380 s),
  records machine-readable evidence, verifies the Pod really stopped, and moves to the next
  scenario on the next window of capacity;
* it detects a Pod that survived a crashed coordinator and fails closed instead of creating a
  second one;
* it can be stopped by the owner at any time (`cancel`), and it stops itself on TTL.

It is a plain foreground process: no Windows service, no scheduled task, no elevation. State is a
lock file plus atomically replaced JSON, the log is redacted, and `status` reads them without
touching the provider.

Usage:
    python artifacts/acceptance-126/coordinator.py preflight      # free checks -> readiness JSON
    python artifacts/acceptance-126/coordinator.py inventory      # one dated availability statement
    python artifacts/acceptance-126/coordinator.py monitor        # the bounded autonomous wait
    python artifacts/acceptance-126/coordinator.py monitor --dry-run   # plan only, no side effects
    python artifacts/acceptance-126/coordinator.py status
    python artifacts/acceptance-126/coordinator.py cancel         # owner stop; no new Pod after this
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ACC = REPO / "artifacts" / "acceptance-123"
STATE_DIR = HERE / "coordinator"
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "coordinator.lock"
EVENT_LOG = STATE_DIR / "events.log"
CANCEL_FILE = STATE_DIR / "cancel.flag"
READINESS = HERE / "final-readiness.json"

sys.path.insert(0, str(ACC))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

SCENARIO_ORDER = ("T3L", "T4L", "LA")
SCENARIO_LABEL = {
    "T3L": "restart the installed window during a live task",
    "T4L": "lose and restore the Computer host transport",
    "LA": "full autonomous research -> build -> run -> verify",
}

SCHEMA = 2
QUIET = False


# ------------------------------------------------------------------------------------------ config


@dataclass
class Config:
    ttl_hours: float = 48.0
    interval_seconds: int = 900
    max_checks: int = 192
    max_pods: int = 3
    budget_usd: float = 0.85
    max_pod_seconds: int = 600
    watchdog_reserve_seconds: int = 90
    max_hourly_usd: float = 1.79
    min_vram_gb: int = 48
    allow_community: bool = False
    # The runner needs its own window, and the watchdog must fire with >= 90 s of margin.
    session_seconds: dict = field(
        default_factory=lambda: {"T3L": 480, "T4L": 450, "LA": 480}
    )
    # A stop has to be confirmed before the Pod's 600 s cap, so the watchdog is armed below it.
    stop_margin_seconds: int = 20

    def watchdog_seconds(self) -> int:
        return self.max_pod_seconds - self.watchdog_reserve_seconds

    def worst_case_pod_usd(self, hourly: float) -> float:
        return round(self.max_pod_seconds / 3600.0 * hourly, 4)


# ------------------------------------------------------------------------------------- pure planner


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def budget_used(state: dict) -> float:
    return round(sum(float(item.get("cost_usd") or 0.0) for item in state.get("attempts", [])), 4)


def outstanding(state: dict) -> list[str]:
    verdicts = state.get("scenario_verdicts") or {}
    return [name for name in SCENARIO_ORDER if verdicts.get(name) != "PASS"]


def plan(snapshot: dict, state: dict, cfg: Config) -> dict:
    """The whole decision, as a pure function of what the world says and what was recorded.

    Every branch returns `{"action": …}`. Only `create`, `reconcile_session`, `stop_orphans` and
    `finish` have side effects; the caller decides whether to perform them.
    """
    if state.get("finished"):
        return {"action": "noop", "reason": "finished", "outcome": state.get("outcome")}
    if state.get("cancelled") or snapshot.get("cancel"):
        return {"action": "finish", "outcome": "CANCELLED_BY_OWNER",
                "reason": "the owner cancelled the monitor"}
    if cfg.ttl_hours and snapshot["now"] >= _parse(state["expires_at"]):
        return {"action": "finish", "outcome": "EXPIRED_NO_CAPACITY",
                "reason": "the 48-hour permission window closed"}

    pods = snapshot.get("pods") or []
    live_pods = [pod for pod in pods if str(pod.get("status", "")).upper() in
                 {"RUNNING", "STARTING", "PROVISIONING", "PENDING"}]

    if state.get("in_session"):
        # The previous process died (or is still running) mid-session. Reconcile before anything
        # else: a second Pod must never be created to replace an unaccounted one.
        return {
            "action": "reconcile_session",
            "pods": [pod.get("id") for pod in live_pods],
            "reason": "a previous live session has no recorded verdict",
        }

    orphan = [pod for pod in live_pods]
    if orphan:
        # Outside a session nothing of ours may be running. Fail closed and stop it.
        return {
            "action": "stop_orphans",
            "pods": [pod.get("id") for pod in orphan],
            "outcome": "ORPHAN_POD_STOPPED_REVIEW",
            "reason": "a running Pod has no attempt in this coordinator's state",
        }

    todo = outstanding(state)
    if not todo:
        return {"action": "finish", "outcome": "READY_FOR_RELEASE",
                "reason": "every live scenario recorded PASS"}

    readiness = state.get("readiness") or {}
    missing = [flag for flag in readiness.get("required_flags", [])
               if not readiness.get("flags", {}).get(flag)]
    if not readiness.get("ok") or missing:
        return {"action": "finish", "outcome": "NOT_READY",
                "reason": "free readiness is not satisfied: " + ", ".join(missing or ["no readiness"])}

    if snapshot.get("manual_off"):
        return {"action": "finish", "outcome": "MANUAL_OFF",
                "reason": "the owner's manual AI-off latch is set; no automatic start"}

    if snapshot.get("pods_unreadable"):
        return {"action": "wait", "reason": "the Pod list could not be read; not starting blind",
                "next_check_in": cfg.interval_seconds}

    if int(state.get("pods_created", 0)) >= cfg.max_pods:
        return {"action": "finish", "outcome": "POD_LIMIT",
                "reason": f"{cfg.max_pods} Pods were already created in this task"}
    if int(state.get("checks", 0)) >= cfg.max_checks:
        # The permitted check budget *is* the permitted window: 192 checks at 15 minutes is 48
        # hours. Reporting it as a capacity outcome keeps the report honest instead of implying
        # that a limit was hit while there was still time left to look.
        return {"action": "finish", "outcome": "EXPIRED_NO_CAPACITY",
                "reason": f"{cfg.max_checks} free availability checks used; "
                          f"no placeable card appeared in the window"}

    watchdog = snapshot.get("watchdog") or {}
    if not watchdog.get("reachable") or watchdog.get("armed"):
        return {"action": "wait",
                "reason": "the independent watchdog is not reachable and disarmed yet",
                "next_check_in": 60}
    if not snapshot.get("app_healthy"):
        return {"action": "wait", "reason": "the local backend is not healthy",
                "next_check_in": 120}

    found = snapshot.get("candidates") or []
    if not found:
        remaining = [item for item in state.get("attempts", []) if item.get("verdict") != "PASS"]
        return {
            "action": "wait",
            "reason": "no compatible card is placeable on an existing volume right now",
            "next_check_in": cfg.interval_seconds,
            "checked_datacenters": snapshot.get("checked_datacenters", []),
            "failed_attempts": len(remaining),
        }

    scenario = todo[0]
    cheapest = min(found, key=lambda item: item["selected_hourly_usd"])
    spent = budget_used(state)
    rate = max(cheapest["selected_hourly_usd"], float(state.get("last_rate_usd") or 0))
    worst = cfg.worst_case_pod_usd(rate)
    if spent + worst > cfg.budget_usd + 1e-9:
        return {"action": "finish", "outcome": "BUDGET_LIMIT",
                "reason": f"${spent:.4f} spent, worst case ${worst:.4f}, "
                          f"cap ${cfg.budget_usd:.2f}"}

    return {
        "action": "create",
        "scenario": scenario,
        "candidate": cheapest,
        "max_session_seconds": min(cfg.session_seconds.get(scenario, 480),
                                   cfg.max_pod_seconds - cfg.watchdog_reserve_seconds - 10),
        "watchdog_seconds": cfg.watchdog_seconds(),
        "budget_usd": round(cfg.budget_usd - spent, 4),
        "planned_seconds": cfg.max_pod_seconds,
    }


# ------------------------------------------------------------------------------------------ state IO


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    state["updated_at"] = _now().isoformat()
    atomic_write(STATE_FILE, state)


def log_event(state: dict, message: str, **extra) -> None:
    """One redacted line for the owner: a clock, a fact, never a credential or a Pod id."""
    stamp = _now().strftime("%H:%M:%S")
    line = f"{stamp} — {message}"
    if extra:
        line += " " + json.dumps(extra, ensure_ascii=False, sort_keys=True)
    if not QUIET:
        print(line, flush=True)
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with EVENT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{_now().isoformat()} {message} "
                     f"{json.dumps(extra, ensure_ascii=False, sort_keys=True)}\n")
    state.setdefault("events", []).append(
        {"at": _now().isoformat(), "message": message, "detail": extra}
    )
    state["events"] = state["events"][-400:]


class Lock:
    """One instance, atomically, with stale-lock recovery for a killed predecessor."""

    def __init__(self, path: Path):
        self.path = path
        self.held = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                meta = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            if self._alive(int(meta.get("pid") or 0)):
                raise SystemExit(
                    f"another coordinator is running (pid {meta.get('pid')}); use `status` or `cancel`"
                )
            self.path.unlink(missing_ok=True)
            handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(handle, json.dumps({"pid": os.getpid(), "at": _now().isoformat()}).encode())
        os.close(handle)
        self.held = True
        return self

    @staticmethod
    def _alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            # A localised Windows answers in cp866/cp1251; `text=True` then raises inside the
            # pipe reader thread and the stale-lock check dies on a byte it cannot decode.
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, timeout=30,
            )
            output = (result.stdout or b"").decode("utf-8", "replace")
            return str(pid) in output
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def __exit__(self, *exc):
        if self.held:
            self.path.unlink(missing_ok=True)
        return False


# -------------------------------------------------------------------------------------- real world


class RealWorld:
    """Every side effect the planner may ask for, in one place, so tests can replace it."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._key: str | None = None
        self._placements = None

    # -- reads -------------------------------------------------------------------------------
    @property
    def key(self) -> str:
        if self._key is None:
            import runpod_api_tools

            self._key = runpod_api_tools.read_provider_secret()
        return self._key

    def now(self) -> datetime:
        return _now()

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))

    def availability(self) -> dict:
        import placements

        result = placements.snapshot(
            self.key,
            max_hourly_usd=self.cfg.max_hourly_usd,
            min_vram_gb=self.cfg.min_vram_gb,
            allow_community=self.cfg.allow_community,
        )
        self._placements = result
        result["checked_datacenters"] = sorted(
            {item["datacenter"] for item in result.get("scanned", [])}
        )
        return result

    def pods(self) -> dict:
        import runpod_api_tools

        status, payload = runpod_api_tools.call("/v2/pods", self.key)
        if status != 200 or not isinstance(payload, dict):
            return {"ok": False, "pods": []}
        rows = payload.get("pods")
        if not isinstance(rows, list):
            return {"ok": False, "pods": []}
        return {
            "ok": True,
            "pods": [
                {
                    "id": row.get("id"),
                    "status": row.get("status"),
                    "name": row.get("name"),
                    "datacenter": row.get("dataCenterId"),
                    "created_at": row.get("createdAt"),
                }
                for row in rows
            ],
        }

    def balance(self) -> float | None:
        import runpod_api_tools

        status, payload = runpod_api_tools.call(
            "https://api.runpod.io/graphql",
            self.key,
            method="POST",
            payload={"query": "query { myself { clientBalance currentSpendPerHr } }"},
        )
        if status != 200 or not isinstance(payload, dict):
            return None
        myself = (payload.get("data") or {}).get("myself") or {}
        try:
            return float(myself.get("clientBalance"))
        except (TypeError, ValueError):
            return None

    def watchdog(self) -> dict:
        command = (
            "/opt/alex-gateway/venv/bin/python /usr/local/sbin/canalla-gpu-test-watchdog.py --status"
        )
        try:
            result = subprocess.run(
                ["ssh", "distance", "sudo -n " + command],
                capture_output=True, timeout=90,
            )
        except (subprocess.SubprocessError, OSError) as error:
            return {"reachable": False, "armed": False, "detail": str(error)[:200]}
        text = ((result.stdout or b"") + (result.stderr or b"")).decode("utf-8", "replace")
        if result.returncode != 0:
            return {"reachable": False, "armed": False, "detail": text[-200:]}
        lower = text.lower()
        return {
            "reachable": True,
            "armed": ("armed" in lower and "disarmed" not in lower)
                     or '"active": true' in lower
                     or '"armed": true' in lower,
            "detail": text[-300:],
        }

    def manual_off(self) -> bool:
        import os

        root = Path(os.environ.get("LOCALAPPDATA", "")) / "Alex LLM" / "runtime"
        return (root / "compute.manual_off").is_file()

    def clear_manual_off(self) -> bool:
        """Only ever called for a latch this coordinator's own stop created."""
        import os

        root = Path(os.environ.get("LOCALAPPDATA", "")) / "Alex LLM" / "runtime"
        path = root / "compute.manual_off"
        if not path.exists():
            return False
        path.unlink(missing_ok=True)
        return True

    def app_healthy(self) -> bool:
        try:
            import common

            status, _ = common.call("GET", "/health", token=common.token(), timeout=10)
            return status == 200
        except BaseException:  # noqa: BLE001 - a free probe must never abort the monitor
            return False

    # -- writes ------------------------------------------------------------------------------
    def run_scenario(self, scenario: str, *, max_session_seconds: int,
                     watchdog_seconds: int, budget_usd: float) -> dict:
        """Run exactly one bounded live scenario through the existing acceptance runner."""
        started = time.time()
        command = [
            str(REPO / "apps" / "backend" / ".venv" / "Scripts" / "python.exe"),
            str(ACC / "runner.py"),
            "--live",
            "--confirm-paid",
            "--scenario", scenario,
            "--max-session-seconds", str(max_session_seconds),
            "--watchdog-seconds", str(watchdog_seconds),
            "--budget-usd", f"{budget_usd:.4f}",
            "--max-ready-seconds", "360",
        ]
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        try:
            result = subprocess.run(
                command, cwd=str(ACC), capture_output=True, timeout=max_session_seconds + 420,
                env=env,
            )
            output = ((result.stdout or b"") + (result.stderr or b"")).decode("utf-8", "replace")
            code = result.returncode
        except subprocess.TimeoutExpired as error:
            output = ((error.stdout or b"") + (error.stderr or b"")).decode("utf-8", "replace")
            code = -1
            output += "\n[coordinator] the runner exceeded its own timeout"

        summary_path, summary = self._latest_summary(started)
        verdict = "ERROR"
        reasons = []
        pod_created = False
        cost = 0.0
        pod_seconds = 0.0
        if summary:
            entry = next(
                (item for item in summary.get("results", [])
                 if item.get("scenario") == scenario), None
            )
            if entry:
                verdict = str(entry.get("verdict") or "ERROR")
                reasons = list(entry.get("reasons") or [])[:3]
            elif "capacity" in output or "waiting_capacity" in output or "not ready" in output:
                verdict = "NO_CAPACITY"
            else:
                verdict = "FAIL"
            end = summary.get("session_end") or {}
            start = summary.get("session_start") or {}
            elapsed = float(summary.get("elapsed_seconds") or 0)
            # The final session read can come back empty (the stop already answered), so the Pod
            # lifetime is the conservative billable bound: startup and stop are inside it.
            pod_seconds = float(
                end.get("billable_seconds") or start.get("billable_seconds") or elapsed or 0
            )
            cost = float(end.get("estimated_usd") or 0)
            rate = float(end.get("hourly_rate_usd") or start.get("hourly_rate_usd") or 0)
            pod_created = bool(pod_seconds) or start.get("state") in {"ready", "generating"}
            if not cost and pod_seconds and rate:
                cost = round(pod_seconds / 3600.0 * rate, 4)
        elif code == 5:
            verdict = "NO_CAPACITY"
        return {
            "scenario": scenario,
            "verdict": verdict,
            "reasons": reasons,
            "pod_created": pod_created,
            "pod_seconds": round(pod_seconds, 1),
            "cost_usd": round(cost, 4),
            "rate_usd": rate,
            "elapsed_seconds": round(elapsed, 1),
            "evidence": str(summary_path) if summary_path else None,
            "runner_exit": code,
            "output_tail": output[-1200:],
        }

    @staticmethod
    def _latest_summary(started: float) -> tuple[Path | None, dict]:
        runs = ACC / "runs"
        if not runs.is_dir():
            return None, {}
        candidates = [
            path for path in runs.glob("live-*/summary.json")
            if path.stat().st_mtime >= started - 5
        ]
        if not candidates:
            return None, {}
        newest = max(candidates, key=lambda path: path.stat().st_mtime)
        try:
            return newest, json.loads(newest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return newest, {}

    def stop_compute(self) -> dict:
        try:
            import common

            status, text = common.call(
                "POST", "/cloud/compute/stop", token=common.token(), body={}, timeout=90
            )
            return {"http": status, "body": text[:300]}
        except Exception as error:  # noqa: BLE001
            return {"http": 0, "body": type(error).__name__}

    def disarm_watchdog(self) -> dict:
        command = (
            "/opt/alex-gateway/venv/bin/python /usr/local/sbin/canalla-gpu-test-watchdog.py --disarm"
        )
        try:
            result = subprocess.run(
                ["ssh", "distance", "sudo -n " + command],
                capture_output=True, timeout=90,
            )
            text = ((result.stdout or b"") + (result.stderr or b"")).decode("utf-8", "replace")
            return {"exit": result.returncode, "detail": text[-200:]}
        except (subprocess.SubprocessError, OSError) as error:
            return {"exit": -1, "detail": str(error)[:200]}

    def terminate_pod(self, pod_id: str) -> dict:
        import runpod_api_tools

        return runpod_api_tools.terminate(self.key, pod_id)


# ------------------------------------------------------------------------------------ execution


def refresh_snapshot(world: RealWorld, cfg: Config, *, with_availability: bool) -> dict:
    pods_state = world.pods()
    snapshot = {
        "now": world.now(),
        "pods": pods_state.get("pods", []),
        "pods_unreadable": not pods_state.get("ok", False),
        "watchdog": world.watchdog(),
        "manual_off": world.manual_off(),
        "app_healthy": world.app_healthy(),
        "cancel": CANCEL_FILE.exists(),
        "candidates": [],
        "checked_datacenters": [],
    }
    if with_availability:
        availability = world.availability()
        snapshot["candidates"] = availability.get("candidates", [])
        snapshot["checked_datacenters"] = availability.get("checked_datacenters", [])
        snapshot["availability"] = availability
    return snapshot


def execute(world: RealWorld, cfg: Config, state: dict, decision: dict) -> None:
    action = decision["action"]
    if action == "create":
        run_one_session(world, cfg, state, decision)
        return
    if action == "reconcile_session":
        reconcile(world, state, decision)
        return
    if action == "stop_orphans":
        for pod_id in decision.get("pods", []):
            result = world.terminate_pod(pod_id)
            log_event(state, "stopped a Pod this run did not open", pod=str(pod_id)[:6],
                      result=str(result.get("status")))
        world.disarm_watchdog()
        state["finished"] = True
        state["outcome"] = decision["outcome"]
        state["current_step"] = "orphan_pod_stopped"
        save_state(state)
        return
    if action == "finish":
        world.disarm_watchdog()
        state["finished"] = True
        state["outcome"] = decision["outcome"]
        state["reason"] = decision.get("reason")
        state["current_step"] = "finished"
        state["next_step"] = {
            "READY_FOR_RELEASE": "run release_coordinator.py",
            "EXPIRED_NO_CAPACITY": "nothing to do; capacity never appeared in the window",
        }.get(decision["outcome"], "review the evidence before any further paid attempt")
        save_state(state)
        log_event(state, f"monitor finished: {decision['outcome']}", reason=decision.get("reason"))
        return


def reconcile(world: RealWorld, state: dict, decision: dict) -> None:
    """A crashed session: stop what is running, record the truth, never guess a verdict.

    The independent watchdog is the outer bound (it fires 90 s before the Pod cap). This function
    is the inner one: it asks the product to stop, waits for the account to be clean, and only
    then lets the monitor continue.
    """
    log_event(state, "reconciling a session the previous process did not finish",
              pods=len(decision.get("pods") or []))
    world.stop_compute()
    attempts = state.get("attempts") or []
    if attempts and attempts[-1].get("latch_before") is False and world.manual_off():
        world.clear_manual_off()
        log_event(state, "cleared the latch this session's own stop created")
    live = decision.get("pods") or []
    for _ in range(6):
        world.sleep(15)
        remaining = [
            pod for pod in (world.pods().get("pods") or [])
            if str(pod.get("status", "")).upper()
            in {"RUNNING", "STARTING", "PROVISIONING", "PENDING"}
        ]
        live = [pod.get("id") for pod in remaining]
        if not live:
            break
    if live:
        for pod_id in live:
            world.terminate_pod(pod_id)
        world.disarm_watchdog()
        state["finished"] = True
        state["outcome"] = "ORPHAN_POD_STOPPED_REVIEW"
        state["current_step"] = "orphan_pod_stopped"
        save_state(state)
        return

    attempts = state.get("attempts") or []
    if attempts and attempts[-1].get("verdict") == "RUNNING":
        attempts[-1].update(
            {
                "verdict": "INTERRUPTED_BY_CRASH",
                "finished_at": world.now().isoformat(),
                "stop_confirmed": True,
            }
        )
    world.disarm_watchdog()
    state["in_session"] = False
    state["checks"] = int(state.get("checks", 0)) + 1
    state["current_step"] = "idle"
    state["last_reconcile"] = world.now().isoformat()
    save_state(state)
    log_event(state, "the account is clean again; monitoring continues")


def run_one_session(world: RealWorld, cfg: Config, state: dict, decision: dict) -> None:
    scenario = decision["scenario"]
    candidate = decision["candidate"]
    attempt = {
        "index": int(state.get("pods_created", 0)) + 1,
        "scenario": scenario,
        "gpu": candidate["id"],
        "datacenter": candidate["datacenter"],
        "volume_id": candidate["volume_id"],
        "hourly_usd": candidate["selected_hourly_usd"],
        "started_at": world.now().isoformat(),
        "watchdog_seconds": decision["watchdog_seconds"],
        "max_session_seconds": decision["max_session_seconds"],
        "verdict": "RUNNING",
    }
    attempt["latch_before"] = world.manual_off()
    state.setdefault("attempts", []).append(attempt)
    state["in_session"] = True
    state["current_step"] = f"{scenario}_live"
    save_state(state)
    log_event(
        state,
        f"{scenario}: starting one Pod on {candidate['id']} "
        f"({candidate['vram_gb']} GB, ${candidate['selected_hourly_usd']}/h, "
        f"{candidate['datacenter']})",
        session_seconds=decision["max_session_seconds"],
        watchdog_seconds=decision["watchdog_seconds"],
    )

    result = world.run_scenario(
        scenario,
        max_session_seconds=decision["max_session_seconds"],
        watchdog_seconds=decision["watchdog_seconds"],
        budget_usd=decision["budget_usd"],
    )

    attempt.update(
        {
            "finished_at": world.now().isoformat(),
            "verdict": result["verdict"],
            "reasons": result["reasons"],
            "pod_created": result["pod_created"],
            "pod_seconds": result["pod_seconds"],
            "cost_usd": result["cost_usd"],
            "evidence": result["evidence"],
        }
    )
    if result["pod_created"]:
        state["pods_created"] = int(state.get("pods_created", 0)) + 1
    if result.get("rate_usd"):
        # The provider charged more than the catalogue row promised before; keep the guard honest.
        state["last_rate_usd"] = result["rate_usd"]

    # The Pod's stop must be seen, not assumed. The independent watchdog bounds the worst case.
    confirmed = False
    for _ in range(4):
        pods_state = world.pods()
        live = [
            pod for pod in pods_state.get("pods", [])
            if str(pod.get("status", "")).upper()
            in {"RUNNING", "STARTING", "PROVISIONING", "PENDING"}
        ]
        if pods_state.get("ok") and not live:
            confirmed = True
            break
        world.sleep(15)
    attempt["stop_confirmed"] = confirmed
    state["in_session"] = False

    if result["pod_created"] and not confirmed:
        world.stop_compute()
        log_event(state, f"{scenario}: the Pod stop is not confirmed; no new Pod will be created")
        state["finished"] = True
        state["outcome"] = "POD_STOP_UNCONFIRMED"
        state["current_step"] = "fail_closed"
        save_state(state)
        return

    if result["verdict"] == "PASS":
        state.setdefault("scenario_verdicts", {})[scenario] = "PASS"
        log_event(state, f"{scenario}: PASS", cost_usd=result["cost_usd"],
                  pod_seconds=result["pod_seconds"])
    elif result["verdict"] == "NO_CAPACITY":
        log_event(state, f"{scenario}: no capacity for the product's own ensure; "
                         f"no Pod, no spend")
    else:
        state.setdefault("scenario_verdicts", {})[scenario] = result["verdict"]
        log_event(state, f"{scenario}: {result['verdict']}", reasons=result["reasons"])
        if result["verdict"] in {"FAIL", "ERROR"}:
            # A product or harness failure needs a fix, not a second identical paid attempt.
            state["finished"] = True
            state["outcome"] = "LIVE_FAILED_" + scenario
            state["current_step"] = "awaiting_fix"
            save_state(state)
            return

    world.disarm_watchdog()
    state["current_step"] = "idle"
    save_state(state)


# ----------------------------------------------------------------------------------- preflight



APP_CONTROL = REPO / "artifacts" / "ensure-app-cdp.ps1"


def app_control(*, stop: bool = False, start: bool = False) -> None:
    """The documented window helper. Never kills by name; it stops the app the product owns."""
    if not APP_CONTROL.is_file():
        return
    arguments = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(APP_CONTROL)]
    if stop:
        arguments.append("-Stop")
    subprocess.run(arguments, capture_output=True, timeout=300)


def wait_app(seconds: int) -> tuple[bool, str]:
    """Poll the liveness endpoint directly: `/auth/refresh` raises SystemExit before warm-up."""
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + seconds
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5) as response:
                text = response.read().decode("utf-8", "replace")
                last = f"{response.status} {text[:120]}"
                if response.status == 200:
                    return True, last
        except (urllib.error.URLError, OSError, ValueError) as error:
            last = f"{type(error).__name__}: {error}"
        time.sleep(3)
    return False, last


def tor_state(seconds: int = 240) -> tuple[bool, str]:
    """`ready` is a persisted SOCKS5h proof, not a configured port.

    The service comes up with the app (1.2.6), and a fresh proof takes a moment, so this waits
    inside a bounded window instead of sampling once and calling a warming service broken.
    """
    import common

    deadline = time.monotonic() + seconds
    detail = "not polled"
    while time.monotonic() < deadline:
        try:
            status, text = common.call("GET", "/tools/tor", token=common.token(), timeout=15)
            if status != 200:
                detail = f"http {status}"
            else:
                payload = json.loads(text)
                binary = payload.get("binary") or {}
                version = str(payload.get("runtime_version") or "")
                state = str(payload.get("state") or "")
                verified = bool(payload.get("verified_chain"))
                detail = (f"state {state}, chain {verified}, runtime {version}, "
                          f"source {(binary.get('source') or '-')}")
                if state == "ready" and verified and version.startswith("0.4."):
                    return True, detail
        except BaseException as error:  # noqa: BLE001 - the app may be mid-restart
            detail = f"{type(error).__name__}: {error}"
        time.sleep(10)
    return False, detail


REQUIRED_FLAGS = (
    "t3_free_ready",
    "t4_free_ready",
    "candidate_verified",
    "data_safe",
    "device_stable",
    "gpu_watchdog_ready",
    "release_pipeline_ready",
    "budget_available",
)


def installed_version() -> str:
    """The backend's own product version, read from the packaged sidecar it is running."""
    try:
        import common

        status, text = common.call("GET", "/health", token=common.token(), timeout=10)
        if status != 200:
            return ""
        payload = json.loads(text)
        return str(payload.get("product_version") or payload.get("version") or "")
    except BaseException:  # noqa: BLE001
        return ""


def run_preflight(cfg: Config) -> dict:
    """Free checks only: no Pod, no paid call. Writes `final-readiness.json`."""
    flags: dict[str, bool] = {}
    detail: dict[str, str] = {}

    def note(name: str, ok: bool, why: str) -> None:
        flags[name] = bool(ok)
        detail[name] = why
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {why}")

    world = RealWorld(cfg)

    # The free mechanics harness starts its own window and refuses to share one, so the installed
    # app is closed for it and reopened afterwards. Nothing here touches a Pod or a paid call.
    app_control(stop=True)
    for name, target in (("t3_free_ready", "t3"), ("t4_free_ready", "t4")):
        script = HERE / "free_restart_selftest.py"
        result = subprocess.run(
            [sys.executable, str(script), target],
            capture_output=True, timeout=2400,
            env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"),
        )
        text = ((result.stdout or b"") + (result.stderr or b"")).decode("utf-8", "replace")
        ok = result.returncode == 0 and '"verdict": "PASS"' in text
        note(name, ok, (text.strip().splitlines() or ["no output"])[-1][:200])
    app_control(start=True)
    app_ok, app_detail = wait_app(180)
    note("app_running", app_ok, app_detail)

    # The candidate artifact, verified against the committed production public key.
    candidates = sorted(
        (REPO / "apps" / "desktop" / "src-tauri" / "target" / "release" / "bundle" / "nsis").glob(
            "Canalla LLM_1.2.6_x64-setup.exe"
        )
    )
    if not candidates:
        note("candidate_verified", False, "no 1.2.6 installer in the bundle directory")
    else:
        installer = candidates[-1]
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "release-gate.py"), "verify-artifact",
             "--installer", str(installer), "--version", "1.2.6"],
            capture_output=True, timeout=900,
            env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"),
        )
        text = ((result.stdout or b"") + (result.stderr or b"")).decode("utf-8", "replace")
        note("candidate_verified", result.returncode == 0,
             text.strip().splitlines()[-1][:200] if text.strip() else "no output")

    # Data: integrity, counts and the device identity the live runs must preserve.
    import sqlite3

    db = Path(os.environ.get("LOCALAPPDATA", "")) / "Alex LLM" / "data" / "alex.db"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        integrity = con.execute("pragma integrity_check").fetchone()[0]
        users = con.execute("select count(*) from users").fetchone()[0]
        chats = con.execute("select count(*) from chats").fetchone()[0]
        messages = con.execute("select count(*) from messages").fetchone()[0]
        devices = con.execute("select count(*) from paired_devices").fetchone()[0]
        con.close()
        note("data_safe", integrity == "ok",
             f"integrity {integrity}, users {users}, chats {chats}, messages {messages}, "
             f"devices {devices}")
    except Exception as error:  # noqa: BLE001
        note("data_safe", False, f"{type(error).__name__}: {error}")

    device_file = Path(os.environ.get("LOCALAPPDATA", "")) / "Alex LLM" / "device.json"
    device_id = ""
    try:
        device_id = json.loads(device_file.read_text(encoding="utf-8")).get("device_id", "")
    except (OSError, ValueError):
        device_id = ""
    note("device_stable", bool(device_id), f"device_id {device_id or 'missing'}")

    tor_ok, tor_detail = tor_state()
    note("tor_ready", tor_ok, tor_detail)

    watchdog = world.watchdog()
    note("gpu_watchdog_ready", bool(watchdog.get("reachable")),
         (watchdog.get("detail") or "")[-160:].replace("\n", " "))

    pods_state = world.pods()
    balance = world.balance()
    note(
        "budget_available",
        bool(pods_state.get("ok")) and not pods_state.get("pods") and balance is not None
        and balance > cfg.budget_usd,
        f"pods {len(pods_state.get('pods', []))}, balance ${balance}",
    )

    pipeline = (REPO / "artifacts" / "acceptance-126" / "release_coordinator.py").is_file()
    note("release_pipeline_ready", pipeline,
         "release_coordinator.py present" if pipeline else "release_coordinator.py missing")

    availability = world.availability()
    report = {
        "generated_at": _now().isoformat(),
        "git_sha": subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO),
            capture_output=True, timeout=60,
        ).stdout.decode("utf-8", "replace").strip(),
        "installed_version": installed_version(),
        "flags": flags,
        "detail": detail,
        "required_flags": list(REQUIRED_FLAGS),
        "ok": all(flags.get(name) for name in REQUIRED_FLAGS),
        "device_id": device_id,
        "balance_usd": balance,
        "pods": pods_state.get("pods", []),
        "availability": {
            "verdict": availability.get("verdict"),
            "tested_at": availability.get("tested_at"),
            "candidates": availability.get("candidates", []),
            "scanned": availability.get("scanned", []),
        },
    }
    atomic_write(READINESS, report)
    print(json.dumps({"ok": report["ok"], "flags": flags}, ensure_ascii=False, indent=2))
    return report


# ------------------------------------------------------------------------------------------- loop


def initial_state(cfg: Config, readiness: dict) -> dict:
    started = _now()
    return {
        "schema": SCHEMA,
        "started_at": started.isoformat(),
        "expires_at": (started + timedelta(hours=cfg.ttl_hours)).isoformat(),
        "checks": 0,
        "pods_created": 0,
        "attempts": [],
        "scenario_verdicts": {},
        "in_session": False,
        "finished": False,
        "cancelled": False,
        "outcome": None,
        "current_step": "monitoring",
        "next_step": "wait for a placeable card",
        "budget_usd": cfg.budget_usd,
        "max_pods": cfg.max_pods,
        "readiness": {
            "ok": bool(readiness.get("ok")),
            "flags": readiness.get("flags", {}),
            "required_flags": readiness.get("required_flags", list(REQUIRED_FLAGS)),
            "generated_at": readiness.get("generated_at"),
            "installed_version": readiness.get("installed_version"),
        },
    }


def monitor(cfg: Config, *, dry_run: bool = False) -> int:
    readiness = json.loads(READINESS.read_text(encoding="utf-8")) if READINESS.exists() else {}
    if not readiness.get("ok"):
        print("ABORT readiness is not satisfied; run `preflight` first")
        return 2
    if not dry_run:
        with Lock(LOCK_FILE):
            return _monitor_locked(cfg, readiness)
    return _monitor_locked(cfg, readiness, dry_run=True)


def _monitor_locked(cfg: Config, readiness: dict, dry_run: bool = False) -> int:
    world = RealWorld(cfg)
    state = load_state()
    if not state or state.get("schema") != SCHEMA:
        state = initial_state(cfg, readiness)
    else:
        state["readiness"] = {
            "ok": bool(readiness.get("ok")),
            "flags": readiness.get("flags", {}),
            "required_flags": readiness.get("required_flags", list(REQUIRED_FLAGS)),
            "generated_at": readiness.get("generated_at"),
            "installed_version": readiness.get("installed_version"),
        }
    # The installed build must be the candidate the live runs certify.
    installed = readiness.get("installed_version") or ""
    if installed and not installed.startswith("1.2.6"):
        print(f"ABORT the installed build is {installed}, not 1.2.6; live evidence would "
              f"belong to another artifact")
        return 3
    save_state(state)
    log_event(state, "monitor started", expires_at=state["expires_at"],
              interval_seconds=cfg.interval_seconds, dry_run=dry_run)
    return run_loop(world, cfg, state, dry_run=dry_run)


def run_loop(world: RealWorld, cfg: Config, state: dict, *, dry_run: bool = False,
             max_iterations: int | None = None) -> int:
    """The bounded loop, driven by any object implementing the same read/write ports."""
    iterations = 0
    try:
        while True:
            if max_iterations is not None and iterations >= max_iterations:
                return 0
            iterations += 1
            snapshot = refresh_snapshot(world, cfg, with_availability=True)
            state["checks"] = int(state.get("checks", 0)) + 1
            decision = plan(snapshot, state, cfg)
            state["last_decision"] = {key: value for key, value in decision.items()
                                      if key != "candidate"}
            state["availability"] = {
                "tested_at": (snapshot.get("availability") or {}).get("tested_at"),
                "verdict": (snapshot.get("availability") or {}).get("verdict"),
                "checked_datacenters": snapshot.get("checked_datacenters", []),
                "candidates": snapshot.get("candidates", []),
            }
            log_event(
                state,
                f"check #{state['checks']}: {decision['action']}",
                reason=decision.get("reason"),
                candidates=len(snapshot.get("candidates") or []),
                datacenters=snapshot.get("checked_datacenters"),
            )
            save_state(state)
            if decision["action"] == "noop":
                return 0
            if decision["action"] == "wait":
                if dry_run:
                    print(json.dumps(state["last_decision"], ensure_ascii=False, indent=2))
                    return 0
                world.sleep(decision.get("next_check_in", cfg.interval_seconds))
                continue
            if dry_run:
                print(json.dumps({"would": decision}, ensure_ascii=False, indent=2, default=str))
                return 0
            execute(world, cfg, state, decision)
            if state.get("finished"):
                return 0
            # A finished scenario needs no sleep before the next capacity window.
    except KeyboardInterrupt:
        log_event(state, "interrupted by the owner's console; no new Pod will be created")
        state["finished"] = True
        state["outcome"] = "INTERRUPTED"
        save_state(state)
        return 130
    except Exception:  # noqa: BLE001 - record, stop, never leave billing running
        log_event(state, "monitor crashed", trace=traceback.format_exc()[-800:])
        world.disarm_watchdog()
        state["last_crash"] = traceback.format_exc()[-2000:]
        state["current_step"] = "crashed"
        save_state(state)
        return 1


# ------------------------------------------------------------------------------------------- CLI


def status() -> int:
    state = load_state()
    if not state:
        print(json.dumps({"coordinator": "not started"}))
        return 0
    pods = RealWorld(Config()).pods()
    payload = {
        "started_at": state.get("started_at"),
        "expires_at": state.get("expires_at"),
        "checks": state.get("checks"),
        "pods_created": state.get("pods_created"),
        "budget_usd": state.get("budget_usd"),
        "spent_usd": budget_used(state),
        "scenario_verdicts": state.get("scenario_verdicts"),
        "current_step": state.get("current_step"),
        "next_step": state.get("next_step"),
        "finished": state.get("finished"),
        "outcome": state.get("outcome"),
        "cancelled": CANCEL_FILE.exists(),
        "pods_now": pods.get("pods", []),
        "last_attempt": (state.get("attempts") or [None])[-1],
        "events": (state.get("events") or [])[-5:],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cancel() -> int:
    CANCEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    CANCEL_FILE.write_text(_now().isoformat(), encoding="utf-8")
    state = load_state()
    if state:
        state["cancelled"] = True
        save_state(state)
    print("cancel requested: the monitor will create no further Pod")
    return 0


def inventory(cfg: Config) -> int:
    world = RealWorld(cfg)
    snapshot = world.availability()
    atomic_write(HERE / "gpu-inventory.json", snapshot)
    print(json.dumps(
        {
            "verdict": snapshot.get("verdict"),
            "tested_at": snapshot.get("tested_at"),
            "checked_datacenters": snapshot.get("checked_datacenters"),
            "candidates": snapshot.get("candidates"),
        },
        ensure_ascii=False, indent=2,
    ))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("preflight", "inventory", "monitor", "status", "cancel"),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ttl-hours", type=float, default=Config.ttl_hours)
    parser.add_argument("--interval-seconds", type=int, default=Config.interval_seconds)
    parser.add_argument("--budget-usd", type=float, default=Config.budget_usd)
    parser.add_argument("--max-pods", type=int, default=Config.max_pods)
    args = parser.parse_args(argv)

    cfg = Config(
        ttl_hours=args.ttl_hours,
        interval_seconds=args.interval_seconds,
        budget_usd=args.budget_usd,
        max_pods=args.max_pods,
    )
    if args.command == "preflight":
        report = run_preflight(cfg)
        return 0 if report["ok"] else 1
    if args.command == "inventory":
        return inventory(cfg)
    if args.command == "monitor":
        return monitor(cfg, dry_run=args.dry_run)
    if args.command == "status":
        return status()
    return cancel()


if __name__ == "__main__":
    raise SystemExit(main())
