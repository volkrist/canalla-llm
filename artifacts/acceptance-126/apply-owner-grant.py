"""Apply the owner's 2026-11-11 paid permission to the existing coordinator state.

The owner raised the cycle cap from $0.85 to $1.10 and allowed at most five Pods in total
(four already exist), so exactly one more start is possible. Nothing else is reset: T3L stays
PASS, the four attempts stay recorded, and the measured spend stays the authority.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "coordinator" / "state.json"
BALANCE_NOW = 9.4207947257  # read-only RunPod probe before this grant was applied
BALANCE_START = 10.1309
BUDGET = 1.10
MAX_PODS = 5


def main() -> int:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    measured = round(BALANCE_START - BALANCE_NOW, 4)
    now = datetime.now(timezone.utc).isoformat()

    state["budget_usd"] = BUDGET
    state["max_pods"] = MAX_PODS
    state["cycle_pods_created"] = int(state.get("pods_created") or 0)
    state["finished"] = False
    state["cancelled"] = False
    state["outcome"] = None
    state["current_step"] = "monitoring"
    state["next_step"] = "wait for a placeable card; one T4 LIVE session is still allowed"
    state["reason"] = ""
    # The previous T4L verdict came from a session the product defect invalidated; it is not a
    # product failure and it must not be treated as one. T3L PASS is untouched.
    verdicts = dict(state.get("scenario_verdicts") or {})
    if verdicts.get("T4L") == "FAIL":
        verdicts["T4L"] = "BLOCKED"
    state["scenario_verdicts"] = verdicts

    billing = dict(state.get("billing") or {})
    billing.update(
        measured_at=now,
        balance_now_usd=BALANCE_NOW,
        measured_spend_usd=measured,
        budget_usd=BUDGET,
        pod_limit=MAX_PODS,
        remaining_usd=round(BUDGET - measured, 4),
        max_affordable_pod_seconds_at_l40s_rate=int((BUDGET - measured) / 1.09 * 3600),
    )
    state["billing"] = billing
    state["permission_extension"] = {
        "granted_at": now,
        "reason": "owner raised the cycle cap and allowed one more Pod after the free root-cause fix",
        "previous_budget_usd": 0.85,
        "new_budget_usd": BUDGET,
        "previous_max_pods": 3,
        "new_max_pods": MAX_PODS,
        "pods_created_before": int(state.get("pods_created") or 0),
        "additional_pods_allowed": 1,
        "hard_limits": {
            "one_pod_at_a_time": True,
            "max_pod_seconds": 600,
            "watchdog_reserve_seconds": 90,
            "max_hourly_usd": 1.79,
            "min_vram_gb": 48,
        },
    }
    state["events"].append(
        {
            "at": now,
            "message": "owner granted one more Pod inside a $1.10 cycle cap",
            "detail": {
                "budget_usd": BUDGET,
                "max_pods": MAX_PODS,
                "pods_created": state["pods_created"],
                "measured_spend_usd": measured,
                "remaining_usd": billing["remaining_usd"],
                "t4l_verdict": verdicts.get("T4L"),
            },
        }
    )
    state["updated_at"] = now

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    shutil.copy2(STATE, STATE.with_name(f"state-before-grant-{stamp}.json"))
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)
    print(
        json.dumps(
            {
                "budget_usd": state["budget_usd"],
                "max_pods": state["max_pods"],
                "pods_created": state["pods_created"],
                "measured_spend_usd": measured,
                "remaining_usd": billing["remaining_usd"],
                "verdicts": state["scenario_verdicts"],
                "finished": state["finished"],
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
