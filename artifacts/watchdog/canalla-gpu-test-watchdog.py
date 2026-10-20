#!/usr/bin/env python3
"""Independent GPU-test watchdog for one Canalla session.

It is deliberately outside the Gateway process and outside the agent's terminal: a systemd
timer runs it every 15 s. It never creates anything. It only:

  * adopts the managed Pod this test created (exact pod_id from the Gateway DB, or the
    alex-gw-* pod that appeared after arming),
  * terminates that Pod with a 45 s reserve before ``deadline_seconds`` so the provider can
    confirm the stop inside the approved cap (15 s tick + termination latency),
  * verifies through the RunPod API that the Pod is gone,
  * refuses a second alex-gw-* Pod for the test window and for 5 minutes after the stop,
  * never touches a Network Volume and never restarts compute.

Arming is explicit: ``--arm 700``. Without an armed state the timer is a no-op.
The 700-second ceiling is the approved window for one 1.2.3 acceptance Pod; it is still a hard
cap, so a caller cannot widen it to an unbounded session. The stop is requested at
``created + deadline - 45 s`` and the confirmation must land before ``created + deadline``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import httpx

STATE = "/var/lib/alex-gateway/gpu-test/state.json"
LOG = "/var/log/alex-gateway/gpu-test-watchdog.log"
DB_URI = "file:/var/lib/alex-gateway/gateway.db?mode=ro"
API = "https://api.runpod.io/v2"
PREFIX = "alex-gw-"
# The timer runs every 15 s and the provider needs time to confirm a terminate. Stopping at the
# cap itself is always late: the 20 Oct run stopped at 700 s and was confirmed at 726 s. The
# reserve is part of the test contract, not a suggestion.
STOP_RESERVE_SECONDS = 45


def now() -> datetime:
    return datetime.now(timezone.utc)


def log(message: str) -> None:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(now().isoformat() + " " + message + "\n")


def load_state() -> dict:
    with open(STATE, encoding="utf-8") as handle:
        return json.load(handle)


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE)


def api_key() -> str:
    with open("/etc/alex-gateway/runpod.env", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("RUNPOD_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def pods(key: str) -> list[dict]:
    response = httpx.get(API + "/pods", headers={"Authorization": "Bearer " + key}, timeout=20)
    response.raise_for_status()
    data = response.json()
    return data.get("pods", []) if isinstance(data, dict) else (data or [])


def terminate(key: str, pod_id: str) -> int:
    response = httpx.post(
        API + "/pods/" + pod_id + "/action",
        json={"action": "terminate"},
        headers={"Authorization": "Bearer " + key},
        timeout=20,
    )
    return response.status_code


def latest_session(armed_db: str):
    con = sqlite3.connect(DB_URI, uri=True)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "select id, pod_id, pod_name, state, gpu_type, hourly_rate, created_at "
            "from gateway_sessions "
            "where created_at >= ? order by created_at desc limit 1",
            (armed_db,),
        ).fetchone()
    finally:
        con.close()


def parse_db_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def stop_due_at(
    created: datetime, deadline_seconds: int, reserve: int = STOP_RESERVE_SECONDS
) -> datetime:
    """The moment the terminate is sent: early enough for the confirmation to fit the cap."""
    return created + timedelta(seconds=max(0, int(deadline_seconds) - max(0, int(reserve))))


def confirmed_within_cap(created: datetime, confirmed: datetime, deadline_seconds: int) -> bool:
    return (confirmed - created).total_seconds() <= int(deadline_seconds)


def self_test() -> int:
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert stop_due_at(created, 700) == created + timedelta(seconds=655)
    assert confirmed_within_cap(created, created + timedelta(seconds=655), 700)
    assert confirmed_within_cap(created, created + timedelta(seconds=700), 700)
    assert not confirmed_within_cap(created, created + timedelta(seconds=701), 700)
    print("SELF-TEST PASS reserve=45s stop_at=655s cap=700s")
    return 0


def managed(pod: dict) -> bool:
    return str(pod.get("name") or "").startswith(PREFIX)


def arm(seconds: int, gpu: str, max_price: float) -> int:
    # This test allows one paid Pod for at most 700 seconds — the approved 1.2.3 acceptance
    # window. The cap is hard: a caller cannot widen it to 900/1200/3600.
    seconds = max(30, min(int(seconds), 700))
    state = {
        "armed": True,
        "armed_at": now().isoformat(),
        "armed_at_db": now().strftime("%Y-%m-%d %H:%M:%S"),
        "deadline_seconds": seconds,
        "allowed_gpu": gpu,
        "allowed_max_hourly": max_price,
        "session_id": None,
        "pod_id": None,
        "pod_created_at": None,
        "status": "armed",
        "post_stop_until": None,
    }
    save_state(state)
    log(f"ARMED deadline_seconds={seconds} gpu={gpu} max_hourly={max_price}")
    print(json.dumps(state))
    return 0


def tick() -> int:
    state = load_state()
    if not state.get("armed"):
        return 0

    key = api_key()
    current = pods(key)
    current_ids = {str(p.get("id")) for p in current}

    if not state.get("pod_id"):
        row = latest_session(state["armed_at_db"])
        if row is not None and row["pod_id"] and row["state"] != "stopped":
            state["pod_id"] = row["pod_id"]
            state["session_id"] = row["id"]
            state["pod_created_at"] = row["created_at"]
            state["status"] = "adopted_db"
            save_state(state)
            log(
                f"ADOPTED session={row['id']} pod={row['pod_id']} state={row['state']} "
                f"gpu={row['gpu_type']} rate={row['hourly_rate']}"
            )
            # An unapproved substitution is stopped immediately, never continued.
            allowed_gpu = str(state.get("allowed_gpu") or "").lower()
            allowed_max = float(state.get("allowed_max_hourly") or 0)
            gpu_name = str(row["gpu_type"] or "").lower()
            rate = float(row["hourly_rate"] or 0)
            if (allowed_gpu and allowed_gpu not in gpu_name) or (allowed_max and rate > allowed_max + 0.0001):
                code = terminate(key, row["pod_id"])
                state["status"] = "rejected_substitution"
                state["armed"] = True
                save_state(state)
                log(
                    f"ALERT unapproved gpu={row['gpu_type']} rate={row['hourly_rate']} "
                    f"allowed={state.get('allowed_gpu')}<={allowed_max} terminate={code}"
                )
                return 0
        else:
            candidates = [p for p in current if managed(p)]
            if candidates:
                pod = candidates[0]
                state["pod_id"] = pod["id"]
                state["pod_created_at"] = pod.get("startedAt") or state["armed_at_db"]
                state["status"] = "adopted_api"
                save_state(state)
                log(f"ADOPTED (no session row yet) pod={pod['id']} name={pod.get('name')}")

    pod_id = state.get("pod_id")
    created = parse_db_ts(state.get("pod_created_at") or "")
    if created is None:
        created = datetime.fromisoformat(state["armed_at"])
    cap_seconds = int(state["deadline_seconds"])
    stop_at = stop_due_at(created, cap_seconds)

    # Post-stop guard: no automatic second Pod for five minutes after the stop is confirmed.
    if not pod_id and state.get("post_stop_until"):
        until = datetime.fromisoformat(state["post_stop_until"])
        rogues = [p for p in current if managed(p)]
        for pod in rogues:
            code = terminate(key, pod["id"])
            log(f"ALERT rogue managed pod after stop pod={pod['id']} terminate={code}")
        if now() >= until:
            state["armed"] = False
            state["status"] = "closed"
            save_state(state)
            log("CLOSED post-stop window is over")
        return 0

    if not pod_id:
        if now() > datetime.fromisoformat(state["armed_at"]) + timedelta(minutes=15):
            state["armed"] = False
            state["status"] = "no_pod"
            save_state(state)
            log("CLOSED no Pod appeared within 15 minutes")
        return 0

    if pod_id not in current_ids:
        # Gone: either the owner used Stop (normal) or the emergency deletion worked. Clear the
        # id once so the next ticks run the post-stop guard instead of re-confirming forever.
        if state.get("status") != "confirmed_stopped" or state.get("pod_id"):
            confirmed = now()
            elapsed = (confirmed - created).total_seconds()
            within = confirmed_within_cap(created, confirmed, cap_seconds)
            state["status"] = "confirmed_stopped"
            state["stopped_at"] = state.get("stopped_at") or confirmed.isoformat()
            state["confirmed_elapsed_seconds"] = round(elapsed, 1)
            state["within_cap"] = within
            state["post_stop_until"] = state.get("post_stop_until") or (
                confirmed + timedelta(minutes=5)
            ).isoformat()
            state["confirmed_pod_id"] = pod_id
            state["pod_id"] = None
            save_state(state)
            log(
                f"CONFIRMED pod absent id={pod_id} elapsed={elapsed:.1f}s cap={cap_seconds}s "
                f"within_cap={within}"
            )
            if not within:
                log(
                    f"ALERT OVERRUN pod={pod_id} elapsed={elapsed:.1f}s cap={cap_seconds}s "
                    f"stop_requested_at={state.get('stop_requested_at')}"
                )
        return 0

    # A rejected substitution is retried until the provider confirms it is gone.
    if state.get("status") == "rejected_substitution":
        code = terminate(key, pod_id)
        log(f"ALERT retrying terminate of rejected pod={pod_id} http={code}")
        return 0

    # A second managed Pod is never part of this test.
    them = [p for p in current if p.get("id") != pod_id and managed(p)]
    for pod in them:
        code = terminate(key, pod["id"])
        log(f"ALERT second managed pod pod={pod['id']} terminate={code}")

    deadline = created + timedelta(seconds=cap_seconds)
    if now() >= stop_at:
        code = terminate(key, pod_id)
        state["status"] = "terminating"
        state["stop_requested_at"] = state.get("stop_requested_at") or now().isoformat()
        state["stop_due_at"] = stop_at.isoformat()
        save_state(state)
        log(
            f"STOP_REQUESTED pod={pod_id} reserve={STOP_RESERVE_SECONDS}s "
            f"cap_in={(deadline - now()).total_seconds():.1f}s terminate_http={code}"
        )

    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    if "--arm" in argv:
        seconds = int(argv[argv.index("--arm") + 1])
        # The 1.2.3 acceptance conditions: the Gateway chooses the cheapest compatible 48 GB
        # card automatically, so the watchdog must accept any NVIDIA card up to the user's own
        # $2/hour ceiling — not pin the card the previous run happened to use.
        gpu = argv[argv.index("--gpu") + 1] if "--gpu" in argv else "NVIDIA"
        max_price = float(argv[argv.index("--max-price") + 1]) if "--max-price" in argv else 2.0
        return arm(seconds, gpu, max_price)
    if "--status" in argv:
        print(json.dumps(load_state(), indent=2))
        return 0
    if "--disarm" in argv:
        state = load_state()
        state["armed"] = False
        state["status"] = "disarmed"
        save_state(state)
        log("DISARMED by operator")
        return 0
    return tick()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
