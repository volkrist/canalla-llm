"""CANALLA LLM — the hard kill-switch for paid migration/copy Pods. Independent by construction.

A separate process that reads **only** provider data (``GET /v2/pods``) and the clock. It imports no
log parser, no migration state machine, no ownership bookkeeping, and it never reads the watcher's
state file — so a bug in any of those cannot keep a Pod billing. This is the layer that answers the
$6.3351 incident, where a crashed pipeline plus an "it's my Pod, skip it" rule left a migration Pod
running for almost six hours.

The allowed shapes are *explicit* — everything else is an incident:

===============================  ==========================================
shape                            meaning
===============================  ==========================================
one ``canalla-migrate-*``        a measurement Pod (primary measurement)
one pair                         a copy transaction: exactly one
                                 ``canalla-copy-source-<tx>`` **and** exactly
                                 one ``canalla-copy-destination-<tx>``, with
                                 the *same* ``<tx>``
anything else with ``canalla-``  unrecognized → incident
===============================  ==========================================

Rules applied on every pass (30 s):

1. **TTL** — every live migration/copy Pod older than 15 minutes is terminated, using the provider's
   own ``createdAt``. A pair is two Pods on one clock: if *either* end reaches the TTL, **both** are
   terminated, the attempt is marked incomplete/resumable, and the reaper stops.
2. **SHAPE** — more than one migration Pod, two sources, two destinations, different transaction ids,
   a measurement Pod next to a copy transaction, or any unrecognized ``canalla-`` Pod is an incident:
   all of them are terminated, ``reaper-incident.json`` is written, and the reaper stops (exit 2).
3. An age that cannot be read counts as expired: the money-safe answer is "terminate", and the
   Network Volume keeps every byte either way.

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
COPY_INCOMPLETE = ARTIFACTS / "reaper-copy-incomplete.json"
LOCKFILE = ARTIFACTS / "reaper.lock"

# The names this product creates for paid, short-lived work.
MIGRATION_PREFIX = "canalla-migrate-"
COPY_SOURCE_PREFIX = "canalla-copy-source-"
COPY_DESTINATION_PREFIX = "canalla-copy-destination-"
CANALLA_PREFIX = "canalla-"
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


def classify(name: str) -> tuple[str, str]:
    """(family, transaction) — ``("unknown", "")`` for anything we did not create."""
    if name.startswith(COPY_SOURCE_PREFIX):
        tx = name[len(COPY_SOURCE_PREFIX) :]
        return ("copy_source", tx) if tx else ("unknown", "")
    if name.startswith(COPY_DESTINATION_PREFIX):
        tx = name[len(COPY_DESTINATION_PREFIX) :]
        return ("copy_destination", tx) if tx else ("unknown", "")
    if name.startswith(MIGRATION_PREFIX):
        return ("migration", name[len(MIGRATION_PREFIX) :])
    return ("unknown", "")


def our_pods(rows: list[dict] | None) -> list[dict]:
    """Live Pods this product owns, matched by the provider's own name and status fields."""
    found = []
    for row in rows or []:
        name = str(row.get("name") or "")
        status = str(row.get("status") or "").upper()
        if not name.startswith(CANALLA_PREFIX) or status not in LIVE_STATUSES:
            continue
        family, tx = classify(name)
        age = age_seconds(row.get("createdAt"))
        found.append(
            {
                "id": str(row.get("id") or ""),
                "name": name,
                "status": status,
                "family": family,
                "transaction": tx,
                "createdAt": row.get("createdAt"),
                "age_seconds": None if age is None else int(age),
            }
        )
    return found


def expired(pod: dict) -> bool:
    """An unreadable age fails closed: the Volume keeps the data, the Pod costs money."""
    return pod["age_seconds"] is None or pod["age_seconds"] > TTL_SECONDS


def incident(reason: str, pods: list[dict]) -> dict:
    return {
        "action": "incident",
        "reason": reason,
        "terminate": [pod["id"] for pod in pods],
        "copy_incomplete": False,
    }


def decide(pods: list[dict]) -> dict:
    """The whole policy as a pure function of provider facts: what to do about these Pods."""
    if not pods:
        return {"action": "none", "reason": "no canalla Pod alive", "terminate": [], "copy_incomplete": False}

    unknown = [pod for pod in pods if pod["family"] == "unknown"]
    if unknown:
        return incident(
            f"unrecognized canalla Pod(s): {[pod['name'] for pod in unknown]}", pods
        )

    migrations = [pod for pod in pods if pod["family"] == "migration"]
    sources = [pod for pod in pods if pod["family"] == "copy_source"]
    destinations = [pod for pod in pods if pod["family"] == "copy_destination"]

    if len(migrations) > 1:
        return incident(f"{len(migrations)} measurement Pods alive at once", pods)
    if len(sources) > 1 or len(destinations) > 1:
        return incident(
            f"duplicate copy end(s): {len(sources)} source, {len(destinations)} destination", pods
        )
    if migrations and (sources or destinations):
        return incident("a measurement Pod and a copy transaction are alive at once", pods)

    if (
        sources
        and destinations
        and sources[0]["transaction"] != destinations[0]["transaction"]
    ):
        return incident(
            "copy ends belong to different transactions: "
            f"{sources[0]['transaction']} != {destinations[0]['transaction']}",
            pods,
        )

    if len(pods) > 2:
        return incident(f"{len(pods)} canalla Pods alive at once — at most two (one copy pair)", pods)

    over = [pod for pod in pods if expired(pod)]
    if over:
        pair = len(pods) == 2
        detail = ", ".join(
            f"{pod['name']} age={pod['age_seconds']}" for pod in over
        )
        return {
            "action": "copy_incomplete" if pair else "terminate",
            "reason": (
                f"a copy end reached the {TTL_SECONDS}s TTL ({detail}) — both ends are released"
                if pair
                else f"{over[0]['name']} is past the {TTL_SECONDS}s TTL ({detail})"
            ),
            "terminate": [pod["id"] for pod in pods] if pair else [over[0]["id"]],
            "copy_incomplete": pair,
        }

    shape = "copy pair" if len(pods) == 2 else pods[0]["family"]
    return {
        "action": "none",
        "reason": f"one {shape}, oldest age {max(pod['age_seconds'] for pod in pods)}s, inside the TTL",
        "terminate": [],
        "copy_incomplete": False,
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

    if decision["action"] == "copy_incomplete":
        COPY_INCOMPLETE.write_text(
            json.dumps(
                {
                    "at": now(),
                    "reason": decision["reason"],
                    "pods": pods,
                    "terminated": released,
                    "resumable": True,
                    "ttl_seconds": TTL_SECONDS,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        log(f"COPY INCOMPLETE — released {released}, resumable, reaper stops")
        return 4

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
    """Offline proof of the rules, from provider-shaped rows only."""
    import datetime as dt

    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    def row(name: str, age: float | None, status: str = "RUNNING", pod_id: str = "p1") -> dict:
        stamp = (
            "not-a-date"
            if age is None
            else (datetime.now(timezone.utc) - dt.timedelta(seconds=age)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        )
        return {"id": pod_id, "name": name, "status": status, "createdAt": stamp}

    def plan(rows: list[dict]) -> dict:
        return decide(our_pods(rows))

    fresh = plan([row("canalla-migrate-1", 60)])
    check("a fresh measurement Pod is left alone", fresh["action"] == "none", str(fresh))

    old = plan([row("canalla-migrate-1", TTL_SECONDS + 1)])
    check(
        "a Pod past the 15-minute TTL is terminated",
        old["action"] == "terminate" and old["terminate"] == ["p1"],
        str(old),
    )

    boundary = plan([row("canalla-migrate-1", TTL_SECONDS - 1)])
    check("the TTL boundary is inclusive-safe", boundary["action"] == "none")

    unreadable = plan([row("canalla-migrate-1", None)])
    check("an unreadable createdAt fails closed", unreadable["terminate"] == ["p1"])

    pair_fresh = plan(
        [
            row("canalla-copy-source-tx7", 40, pod_id="s1"),
            row("canalla-copy-destination-tx7", 35, pod_id="d1"),
        ]
    )
    check(
        "one source plus one destination of the same transaction is allowed",
        pair_fresh["action"] == "none" and not pair_fresh["terminate"],
        str(pair_fresh),
    )

    pair_source_old = plan(
        [
            row("canalla-copy-source-tx7", TTL_SECONDS + 3, pod_id="s1"),
            row("canalla-copy-destination-tx7", 20, pod_id="d1"),
        ]
    )
    check(
        "a source past the TTL releases BOTH ends and marks the copy incomplete",
        pair_source_old["action"] == "copy_incomplete"
        and sorted(pair_source_old["terminate"]) == ["d1", "s1"]
        and pair_source_old["copy_incomplete"],
        str(pair_source_old),
    )

    pair_dest_old = plan(
        [
            row("canalla-copy-source-tx7", 20, pod_id="s1"),
            row("canalla-copy-destination-tx7", TTL_SECONDS + 30, pod_id="d1"),
        ]
    )
    check(
        "a destination past the TTL releases both ends too",
        sorted(pair_dest_old["terminate"]) == ["d1", "s1"],
        str(pair_dest_old),
    )

    mixed_tx = plan(
        [
            row("canalla-copy-source-tx7", 20, pod_id="s1"),
            row("canalla-copy-destination-tx8", 20, pod_id="d1"),
        ]
    )
    check(
        "different transaction ids are an incident",
        mixed_tx["action"] == "incident" and sorted(mixed_tx["terminate"]) == ["d1", "s1"],
        str(mixed_tx),
    )

    two_sources = plan(
        [
            row("canalla-copy-source-tx7", 20, pod_id="s1"),
            row("canalla-copy-source-tx7", 20, pod_id="s2"),
        ]
    )
    check("two sources are an incident", two_sources["action"] == "incident", str(two_sources))

    two_destinations = plan(
        [
            row("canalla-copy-destination-tx7", 20, pod_id="d1"),
            row("canalla-copy-destination-tx7", 20, pod_id="d2"),
        ]
    )
    check(
        "two destinations are an incident",
        two_destinations["action"] == "incident",
        str(two_destinations),
    )

    three = plan(
        [
            row("canalla-copy-source-tx7", 20, pod_id="s1"),
            row("canalla-copy-destination-tx7", 20, pod_id="d1"),
            row("canalla-migrate-9", 20, pod_id="m1"),
        ]
    )
    check(
        "a copy pair plus a measurement Pod is an incident",
        three["action"] == "incident" and len(three["terminate"]) == 3,
        str(three),
    )

    legacy = plan([row("canalla-copy-1790383189", 20, pod_id="x1")])
    check(
        "an unrecognized canalla Pod is an incident",
        legacy["action"] == "incident" and legacy["terminate"] == ["x1"],
        str(legacy),
    )

    empty_tx = plan([row("canalla-copy-source-", 20, pod_id="x2")])
    check("a copy name without a transaction id is unrecognized", empty_tx["action"] == "incident")

    strangers = plan(
        [
            row("not-canalla-migrate-1", TTL_SECONDS + 600, pod_id="x1"),
            row("my-own-pod", TTL_SECONDS + 600, pod_id="x2"),
        ]
    )
    check("foreign Pods are never touched", strangers["action"] == "none", str(strangers))

    settled = plan([row("canalla-migrate-1", TTL_SECONDS + 600, status="EXITED")])
    check("an already exited Pod is not a live Pod", settled["action"] == "none")

    check("no canalla Pod is a no-op", plan([])["action"] == "none")

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

    log(
        f"reaper started (TTL {TTL_SECONDS}s, shapes: one measurement Pod or one copy pair, "
        f"every {POLL_SECONDS}s)"
    )
    while True:
        try:
            code = enforce(key)
            if code in {2, 4}:  # incident or aborted copy: nothing left to watch, stop
                return code
        except (RuntimeError, OSError, ValueError, TimeoutError) as error:
            log(f"pass failed: {type(error).__name__}: {error} — will look again")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
