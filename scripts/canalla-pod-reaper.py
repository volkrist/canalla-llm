"""CANALLA LLM — the hard kill-switch for paid migration Pods. Independent by construction.

A separate process that reads **only** provider data (``GET /v2/pods``) and the clock. It imports no
log parser, no migration state machine, no ownership bookkeeping, and it never reads the watcher's
state file — so a bug in any of those cannot keep a Pod billing. This is the layer that answers the
$6.0869 incident, where a crashed pipeline plus an "it's my Pod, skip it" rule left a migration Pod
running for almost six hours.

Two absolute rules, applied on every pass:

1. **TTL** — a live ``canalla-migrate-*`` or ``canalla-copy-*`` Pod older than 15 minutes is
   terminated. The age comes from the provider's own ``createdAt``, never from our bookkeeping.
2. **SINGLE** — more than one live ``canalla-*`` Pod account-wide is an incident: all of them are
   terminated, the incident is written to ``reaper-incident.json``, and the reaper stops (exit 2,
   with no retry loop of its own).

An age that cannot be read counts as expired: the money-safe answer is "terminate", and the Network
Volume keeps every byte either way.

    python scripts/canalla-pod-reaper.py --self-test   # offline proof of the rules, no provider call
    python scripts/canalla-pod-reaper.py --once        # one pass, then exit
    python scripts/canalla-pod-reaper.py               # the loop (30 s), meant to be detached
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from runpod_api_tools import call, key_or_exit, terminate

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts" / "runpod-tx3-watcher"
LOG = ARTIFACTS / "reaper.log"
STATE = ARTIFACTS / "reaper-state.json"
INCIDENT = ARTIFACTS / "reaper-incident.json"
LOCKFILE = ARTIFACTS / "reaper.lock"

# The names this product creates for paid, short-lived work.
POD_PREFIXES = ("canalla-migrate-", "canalla-copy-")
TTL_SECONDS = 15 * 60
POLL_SECONDS = 30
LIVE_STATUSES = {"RUNNING", "STARTING", "PROVISIONING"}

_LOCK_HANDLE = None


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message: str) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    line = f"[{now()}] {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def write_state(payload: dict) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )


def take_lock() -> bool:
    """One reaper at a time; the lock is held for the process lifetime, not just checked."""
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
    """The provider's own timestamp, or None when it cannot be read at all."""
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def our_pods(rows: list[dict] | None) -> list[dict]:
    """Live Pods this product owns, matched by the provider's own name and status fields."""
    found = []
    for row in rows or []:
        name = str(row.get("name") or "")
        status = str(row.get("status") or "").upper()
        if not name.startswith(POD_PREFIXES) or status not in LIVE_STATUSES:
            continue
        age = age_seconds(row.get("createdAt"))
        found.append(
            {
                "id": str(row.get("id") or ""),
                "name": name,
                "status": status,
                "createdAt": row.get("createdAt"),
                "age_seconds": None if age is None else int(age),
            }
        )
    return found


def decide(pods: list[dict]) -> dict:
    """The whole policy, as a pure function of provider facts: what to do about these Pods."""
    if len(pods) > 1:
        return {
            "action": "incident",
            "reason": f"{len(pods)} canalla Pods alive at once — at most one is ever allowed",
            "terminate": [pod["id"] for pod in pods],
        }
    if not pods:
        return {"action": "none", "reason": "no canalla Pod alive", "terminate": []}
    pod = pods[0]
    if pod["age_seconds"] is None:
        return {
            "action": "terminate",
            "reason": "age unreadable from the provider's createdAt — fail closed",
            "terminate": [pod["id"]],
        }
    if pod["age_seconds"] > TTL_SECONDS:
        return {
            "action": "terminate",
            "reason": f"age {pod['age_seconds']}s exceeds the {TTL_SECONDS}s TTL",
            "terminate": [pod["id"]],
        }
    return {
        "action": "none",
        "reason": f"one live Pod, age {pod['age_seconds']}s, inside the TTL",
        "terminate": [],
    }


def fetch_pods(key: str) -> list[dict]:
    status, payload = call("/v2/pods", key)
    rows = (payload or {}).get("pods") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(rows, list):
        raise RuntimeError(f"/v2/pods → HTTP {status}: {str(payload)[:200]}")
    return our_pods(rows)


def enforce(key: str) -> int:
    """One authoritative pass: look at the provider, act, write it down."""
    pods = fetch_pods(key)
    decision = decide(pods)
    write_state(
        {
            "checked_at": now(),
            "pods": pods,
            "action": decision["action"],
            "reason": decision["reason"],
        }
    )
    if decision["action"] == "none":
        log(f"ok: {decision['reason']}")
        return 0

    released = []
    for pod_id in decision["terminate"]:
        try:
            terminate(key, pod_id)
            released.append(pod_id)
            log(f"terminated {pod_id}: {decision['reason']}")
        except (RuntimeError, OSError, ValueError) as error:
            log(f"could not terminate {pod_id}: {type(error).__name__}: {error}")

    if decision["action"] == "incident":
        INCIDENT.write_text(
            json.dumps(
                {
                    "at": now(),
                    "reason": decision["reason"],
                    "pods": pods,
                    "terminated": released,
                    "ttl_seconds": TTL_SECONDS,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        log(f"INCIDENT: {decision['reason']} — released {released}, reaper stops")
        return 2
    return 0


def self_test() -> int:
    """Offline proof of the two rules, from provider-shaped rows only."""
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    def row(name: str, age: float | None, status: str = "RUNNING", pod_id: str = "p1") -> dict:
        stamp = (
            "not-a-date"
            if age is None
            else (datetime.now(timezone.utc) - __import__("datetime").timedelta(seconds=age))
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        return {
            "id": pod_id,
            "name": name,
            "status": status,
            "createdAt": stamp,
        }

    fresh = our_pods([row("canalla-migrate-1", 60)])
    check("a fresh migration Pod is left alone", decide(fresh)["action"] == "none", str(fresh))

    old = our_pods([row("canalla-migrate-1", TTL_SECONDS + 1)])
    check(
        "a Pod past the 15-minute TTL is terminated",
        decide(old)["terminate"] == ["p1"],
        str(decide(old)),
    )

    boundary = our_pods([row("canalla-copy-1", TTL_SECONDS - 1)])
    check("the TTL boundary is inclusive-safe", decide(boundary)["action"] == "none")

    copy_old = our_pods([row("canalla-copy-1", TTL_SECONDS + 5, pod_id="p2")])
    check("a copy Pod obeys the same TTL", decide(copy_old)["terminate"] == ["p2"])

    two = our_pods(
        [
            row("canalla-migrate-1", 30, pod_id="p1"),
            row("canalla-copy-1", 30, pod_id="p2"),
        ]
    )
    decision = decide(two)
    check(
        "two live canalla Pods are an incident and both are released",
        decision["action"] == "incident" and sorted(decision["terminate"]) == ["p1", "p2"],
        str(decision),
    )

    check("no canalla Pod is a no-op", decide(our_pods([]))["action"] == "none")

    unreadable = our_pods([row("canalla-migrate-1", None)])
    check(
        "an unreadable createdAt fails closed",
        decide(unreadable)["terminate"] == ["p1"],
        str(decide(unreadable)),
    )

    strangers = our_pods(
        [
            row("not-canalla-migrate-1", TTL_SECONDS + 600, pod_id="x1"),
            row("my-own-pod", TTL_SECONDS + 600, pod_id="x2"),
        ]
    )
    check("foreign Pods are never touched", decide(strangers)["action"] == "none", str(strangers))

    settled = our_pods([row("canalla-migrate-1", TTL_SECONDS + 600, status="EXITED")])
    check("an already exited Pod is not a live Pod", decide(settled)["action"] == "none")

    check(
        "our own Pods are read from provider fields only",
        our_pods([row("canalla-migrate-9", 10, pod_id="p9")])[0]
        == {
            "id": "p9",
            "name": "canalla-migrate-9",
            "status": "RUNNING",
            "createdAt": our_pods([row("canalla-migrate-9", 10)])[0]["createdAt"],
            "age_seconds": our_pods([row("canalla-migrate-9", 10)])[0]["age_seconds"],
        },
    )

    failures = [name for name, ok, _ in results if not ok]
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail and not ok else ''}")
    print(f"reaper self-test: {len(results) - len(failures)}/{len(results)} passed")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument("--self-test", action="store_true", help="offline proof of the rules")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if not take_lock():
        log("another reaper holds the lock — refusing to start a second one")
        return 3

    key = key_or_exit()
    if args.once:
        return enforce(key)

    log(f"reaper started (TTL {TTL_SECONDS}s, single-Pod rule, every {POLL_SECONDS}s)")
    while True:
        try:
            code = enforce(key)
            if code == 2:  # incident: terminate everything, stop, do not retry by itself
                return code
        except (RuntimeError, OSError, ValueError, TimeoutError) as error:
            log(f"pass failed: {type(error).__name__}: {error} — will look again")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
