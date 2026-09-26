#!/usr/bin/env python3
"""CANALLA LLM 1.2.0 — final acceptance watcher: read-only capacity watch, then ONE live run.

This is the last release gate and nothing else. It polls the **free** provider catalogue every two
minutes for a US-TX-3 Secure GPU with at least 48 GB and a price inside the operator's own ceiling.
While capacity is absent it creates nothing, posts nothing and spends nothing — it just waits, for as
long as it takes, at zero cost.

The moment a compatible card appears it acts **in the same poll cycle** and runs the live acceptance
through the product's own path (the installed Canalla, which ensures compute by itself through the
deployed Gateway). It never creates an inference Pod directly: the chat request does that.

Two hard rules it enforces from the outside, so a stuck pipeline cannot cost money:

* an independent **20-minute deadline** from each Pod's provider ``createdAt`` — any Pod older than
  that is terminated regardless of what any pipeline believes;
* a real acceptance failure terminates the Pod(s) and **stops** — it never spends on a second
  attempt. Only a capacity race *before any Pod exists* returns it to watching.

    python scripts/final-acceptance-watcher.py --self-test
    python scripts/final-acceptance-watcher.py --once     # one read-only scan and the verdict
    python scripts/final-acceptance-watcher.py            # the loop (detached)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from runpod_api_tools import LIVE_STATUSES, call, key_or_exit, terminate

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO / "artifacts" / "final-acceptance"
LOG = ARTIFACTS / "watcher.log"
STATE = ARTIFACTS / "state.json"
PIDFILE = ARTIFACTS / "watcher.pid"
LOCKFILE = ARTIFACTS / "watcher.lock"
RECORD = ARTIFACTS / "live-acceptance.json"
BLOCKER = ARTIFACTS / "blocker.json"

DC = "US-TX-3"
MIN_VRAM = 48
MAX_HOURLY = 2.00
POLL_SECONDS = 120
POD_DEADLINE_SECONDS = 20 * 60
BOOKABLE = {"LOW", "MEDIUM", "HIGH"}
HARNESS = REPO / "apps" / "desktop" / "e2e" / "live-ai-acceptance.mjs"
# The codes that mean "no capacity", not "the product refused us": only these keep the watch alive.
CAPACITY_CODES = (
    "provider_unavailable",
    "gpu_capacity_unavailable",
    "gpu_unavailable",
    "no_compatible_gpu",
    "price_limit",
    "snapshot_stale",
    "searching",
    "",
)

_LOCK_HANDLE = None


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message: str) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    line = f"[{now()}] {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def write_json(path: Path, payload: object) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )


def take_lock() -> bool:
    global _LOCK_HANDLE
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    handle = LOCKFILE.open("a+b")
    handle.seek(0)
    handle.write(b"0")
    handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _LOCK_HANDLE = handle
    return True


def age_seconds(stamp: object) -> float | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


# --------------------------------------------------------------------- capacity (read-only)


def capacity(key: str) -> list[dict]:
    """US-TX-3 Secure cards inside the operator's own policy. One free catalogue GET."""
    status, payload = call(
        "/v2/catalog/gpus?include=AVAILABILITY&product=POD&count=1", key
    )
    rows = (payload or {}).get("gpus") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(rows, list):
        raise RuntimeError(f"catalog/gpus → HTTP {status}: {str(payload)[:200]}")
    found = []
    for row in rows:
        if not row.get("secure") or int(row.get("memory") or 0) < MIN_VRAM:
            continue
        price = (row.get("price") or {}).get("secure") or 0
        if not price or float(price) > MAX_HOURLY:
            continue
        for center in row.get("dataCenters") or []:
            if str(center.get("id")) != DC:
                continue
            stock = str(center.get("availability") or "NONE").upper()
            if stock in BOOKABLE:
                found.append(
                    {
                        "gpu": row.get("id"),
                        "vram_gb": int(row.get("memory") or 0),
                        "price_per_hour": float(price),
                        "availability": stock,
                    }
                )
    found.sort(key=lambda item: item["price_per_hour"])
    return found


# --------------------------------------------------------------------- paid-Pod deadline


def pods(key: str) -> list[dict]:
    status, payload = call("/v2/pods", key)
    rows = (payload or {}).get("pods") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(rows, list):
        return []
    return [
        {
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or ""),
            "status": str(row.get("status") or "").upper(),
            "dataCenterId": row.get("dataCenterId"),
            "cost": row.get("cost"),
            "createdAt": row.get("createdAt"),
            "age_seconds": age_seconds(row.get("createdAt")),
        }
        for row in rows
        if str(row.get("status") or "").upper() in LIVE_STATUSES
    ]


def enforce_deadline(key: str, state: dict) -> list[str]:
    """Any live Pod past the 20-minute provider-time deadline is terminated, whatever it is."""
    released = []
    for pod in pods(key):
        age = pod["age_seconds"]
        if age is not None and age <= POD_DEADLINE_SECONDS:
            continue
        try:
            terminate(key, pod["id"])
            released.append(pod["id"])
            log(f"DEADLINE: terminated {pod['id']} ({pod['name']}) age={int(age or -1)}s")
        except (RuntimeError, OSError, ValueError) as error:
            log(f"DEADLINE: could not terminate {pod['id']}: {type(error).__name__}: {error}")
    if released:
        state["deadline_terminations"] = state.get("deadline_terminations", []) + released
    return released


# --------------------------------------------------------------------- the acceptance


def harness(phase: str, timeout: int) -> dict:
    """Run one live phase through the installed product; capture its whole output."""
    log(f"acceptance phase {phase}: starting")
    started = time.monotonic()
    try:
        result = subprocess.run(
            ["node", str(HARNESS), "--phase", phase],
            cwd=str(REPO / "apps" / "desktop"),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output, code = (result.stdout or "") + (result.stderr or ""), result.returncode
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or "") + (error.stderr or "") if error.stdout else ""
        output = output if isinstance(output, str) else output.decode("utf-8", "replace")
        code = 124
    return {
        "phase": phase,
        "exit_code": code,
        "seconds": round(time.monotonic() - started, 1),
        "output": output[-20000:],
        "pass": code == 0,
    }


def last_code(output: str) -> str:
    """The last AI code the badge reported, e.g. ``provider_unavailable``."""
    code = ""
    for line in output.splitlines():
        if "disconnected/" in line or "connecting/" in line or "connected/" in line:
            for token in line.replace("(", " ").replace(")", " ").replace("[", " ").split():
                if "/" in token and not token.startswith("http"):
                    code = token.rsplit("/", 1)[-1].strip(",;")
    return code


def run_acceptance(key: str, state: dict) -> dict:
    """One acceptance run: chat, then Tor only if the chat passed, then always stop."""
    chat = harness("chat", 1500)
    live = pods(key)
    record = {
        "started_at": now(),
        "chat": {k: v for k, v in chat.items() if k != "output"},
        "chat_output": chat["output"],
        "last_ai_code": last_code(chat["output"]),
        "pods_after_chat": live,
    }
    if not chat["pass"]:
        record["result"] = "chat_failed"
        state["last_attempt"] = {k: record[k] for k in ("started_at", "result", "last_ai_code")}
        return record

    tor = harness("tor", 900)
    record["tor"] = {k: v for k, v in tor.items() if k != "output"}
    record["tor_output"] = tor["output"]
    record["result"] = "pass" if tor["pass"] else "tor_failed"
    state["last_attempt"] = {k: record[k] for k in ("started_at", "result")}
    return record


def release_everything(key: str, state: dict) -> list[str]:
    released = []
    for pod in pods(key):
        try:
            terminate(key, pod["id"])
            released.append(pod["id"])
            log(f"released {pod['id']} ({pod['name']})")
        except (RuntimeError, OSError, ValueError) as error:
            log(f"could not release {pod['id']}: {type(error).__name__}: {error}")
    if released:
        state["released"] = state.get("released", []) + released
    return released


def watch(key: str) -> int:
    if not take_lock():
        log("another acceptance watcher holds the lock — refusing to start a second one")
        return 3
    PIDFILE.write_text(str(os.getpid()), encoding="utf-8")
    state: dict = {
        "pid": os.getpid(),
        "started_at": now(),
        "dc": DC,
        "min_vram_gb": MIN_VRAM,
        "max_hourly_price": MAX_HOURLY,
        "poll_seconds": POLL_SECONDS,
        "poll_count": 0,
    }
    write_json(STATE, state)
    log(
        f"watcher started (read-only, every {POLL_SECONDS}s, target {DC} >= {MIN_VRAM} GB "
        f"<= ${MAX_HOURLY:.2f}/h, pod deadline {POD_DEADLINE_SECONDS // 60} min)"
    )

    while True:
        try:
            enforce_deadline(key, state)
            found = capacity(key)
            state["poll_count"] = int(state.get("poll_count", 0)) + 1
            state["last_poll"] = {"at": now(), "candidates": found, "cheapest": found[0] if found else None}
            write_json(STATE, state)
            log(
                f"poll {state['poll_count']}: candidates={len(found)}"
                + (f" cheapest={found[0]['gpu']} ${found[0]['price_per_hour']}" if found else "")
            )

            if not found:
                enforce_deadline(key, state)
                time.sleep(POLL_SECONDS)
                continue

            # A real signal: act in this very cycle, through the product's own path.
            log("capacity detected — starting the live acceptance now")
            record = run_acceptance(key, state)
            write_json(RECORD, record)
            created = record.get("pods_after_chat") or []

            if record["result"] == "pass":
                released = release_everything(key, state)
                state["result"] = "pass"
                state["released_after_pass"] = released
                write_json(STATE, state)
                log(f"LIVE ACCEPTANCE PASS — released {released}")
                PIDFILE.unlink(missing_ok=True)
                return 0

            # Failure: terminate first, then decide whether watching may continue.
            released = release_everything(key, state)
            code = record.get("last_ai_code", "")
            capacity_race = not created and not released and code in CAPACITY_CODES
            if capacity_race:
                log(f"capacity race before any Pod existed (code={code or 'none'}) — back to watching")
                write_json(STATE, state)
                time.sleep(POLL_SECONDS)
                continue

            write_json(
                BLOCKER,
                {
                    "at": now(),
                    "result": record["result"],
                    "last_ai_code": code,
                    "pods_seen": created,
                    "released": released,
                    "chat_output": record.get("chat_output", "")[-4000:],
                },
            )
            state["result"] = "failed"
            write_json(STATE, state)
            log(f"LIVE ACCEPTANCE FAILED ({record['result']}, code={code or 'none'}) — stopping, no retry")
            PIDFILE.unlink(missing_ok=True)
            return 2
        except (RuntimeError, OSError, ValueError, TimeoutError) as error:
            log(f"scan error: {type(error).__name__}: {error}")
            time.sleep(POLL_SECONDS)


def self_test() -> int:
    """Offline proof of the two rules that cost money: the deadline and the no-retry decision."""
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    old = {"age_seconds": POD_DEADLINE_SECONDS + 1}
    fresh = {"age_seconds": 60}
    check("a Pod past 20 minutes is over the deadline", old["age_seconds"] > POD_DEADLINE_SECONDS)
    check("a fresh Pod is not", fresh["age_seconds"] <= POD_DEADLINE_SECONDS)
    for code in ("provider_unavailable", "gpu_capacity_unavailable", "", "snapshot_stale"):
        check(f"code {code!r} counts as a capacity race", code in CAPACITY_CODES)
    for code in ("compute_policy_invalid", "gateway_unavailable", "replica_not_ready"):
        check(f"code {code!r} is a real failure", code not in CAPACITY_CODES)
    sample = (
        "  ai states seen: disconnected/provider_unavailable -> disconnected/compute_policy_invalid"
    )
    check(
        "the last AI code is read from the harness output",
        last_code(sample) == "compute_policy_invalid",
        last_code(sample),
    )
    check(
        "the policy is the operator's own",
        (DC, MIN_VRAM, round(MAX_HOURLY, 2), POD_DEADLINE_SECONDS) == ("US-TX-3", 48, 2.0, 1200),
        f"{DC} {MIN_VRAM} {MAX_HOURLY} {POD_DEADLINE_SECONDS}",
    )
    failures = [name for name, ok, _ in results if not ok]
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail and not ok else ''}")
    print(f"acceptance-watcher self-test: {len(results) - len(failures)}/{len(results)} passed")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--once", action="store_true", help="one read-only scan and the verdict")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    key = key_or_exit()
    if args.once:
        found = capacity(key)
        live = pods(key)
        print(json.dumps({"candidates": found, "pods": live}, ensure_ascii=False, indent=1))
        return 0 if found else 4
    return watch(key)


if __name__ == "__main__":
    raise SystemExit(main())
