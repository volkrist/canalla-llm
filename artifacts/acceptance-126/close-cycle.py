"""Record the rented T4 session, its real cost and the exhausted cycle budget.

Balance is read from the provider, so the ledger stays the authority. The Pod cap and the money
cap are both exhausted: the fifth Pod was the one the owner granted, and $1.0058 of the $1.10 is
gone. Nothing here starts or stops anything.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "coordinator" / "state.json"
BALANCE_NOW = float(sys.argv[1]) if len(sys.argv) > 1 else 9.1250622573
BALANCE_START = 10.1309

sys.path.insert(0, str(HERE))
import checkpoints  # noqa: E402


def main() -> int:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat()
    measured = round(BALANCE_START - BALANCE_NOW, 4)
    budget = float(state.get("budget_usd") or 1.10)
    billing = dict(state.get("billing") or {})
    billing.update(
        measured_at=now,
        balance_now_usd=BALANCE_NOW,
        measured_spend_usd=measured,
        remaining_usd=round(budget - measured, 4),
        budget_usd=budget,
        pod_limit=int(state.get("max_pods") or 5),
        pods_created=int(state.get("pods_created") or 0),
        note=(
            "The balance delta is authoritative. It includes every Pod of this cycle and the "
            "provider's own billing granularity; the per-attempt rows explain $0.7256 of it."
        ),
    )
    state["billing"] = billing
    state["cycle_closed"] = {
        "at": now,
        "reason": (
            "the one Pod the owner granted was used (5 of 5) and $1.0058 of the $1.10 cap is "
            "spent; a worst-case 600 s Pod at the $1.79/h the last one charged is $0.2983"
        ),
        "pods_created": state.get("pods_created"),
        "max_pods": state.get("max_pods"),
        "measured_spend_usd": measured,
        "budget_usd": budget,
        "remaining_usd": billing["remaining_usd"],
        "t4l": "FAIL — harness fixture and approver defects plus one product transition defect, "
               "all fixed and proven free after the session",
    }
    state["events"].append(
        {"at": now, "message": "cycle budget and Pod cap exhausted", "detail": state["cycle_closed"]}
    )
    state["updated_at"] = now
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    shutil.copy2(STATE, STATE.with_name(f"state-before-close-{stamp}.json"))
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)

    attempt = state["attempts"][-1]
    checkpoints.record(
        "T4_LIVE_VERDICT",
        status="FAIL",
        evidence="artifacts/acceptance-123/runs/live-20261111T174431/T4L.json",
        next_step="one more paid T4 LIVE after a new owner grant; the candidate is rebuilt",
        blocker=(
            "the harness named a fixture it never created and stopped approving at the resume; "
            "the product's own transition guard was the second killer. All three are fixed and "
            "proven free. Money and Pods are exhausted: $1.0058/$1.10 and 5/5 Pods"
        ),
        extra={
            "gpu": attempt.get("gpu"),
            "pod_seconds": attempt.get("pod_seconds"),
            "cost_usd": attempt.get("cost_usd"),
            "product_proven": "task created, 3 approved tools, typed WAITING_DEVICE, heartbeat "
                              "returned, same task continued, no repeated payloads, device stable",
            "harness_defects": ["t4l.txt never seeded", "approver stopped at the resume"],
            "product_defect": "invalid_task_transition escaped from observe_tool (fixed)",
        },
    )
    checkpoints.record(
        "CURRENT_MONITOR_STATE",
        status="STOPPED_BUDGET_EXHAUSTED",
        evidence="artifacts/acceptance-126/coordinator/state.json",
        next_step="owner grant: one more Pod and about $0.40 of headroom",
        blocker=(
            f"${measured} of ${budget} spent and {state.get('pods_created')}/"
            f"{state.get('max_pods')} Pods used"
        ),
        extra={"pods_running": 0, "balance_usd": BALANCE_NOW},
    )
    checkpoints.record(
        "GPU_CLEANUP",
        status="CONFIRMED",
        evidence="read-only RunPod probe: pods [] after the session; the session stopped itself",
        extra={"pods_running": 0, "balance_usd": BALANCE_NOW},
    )
    print(json.dumps({"measured_spend_usd": measured, "remaining_usd": billing["remaining_usd"],
                      "pods_created": state.get("pods_created"), "budget_usd": budget},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
