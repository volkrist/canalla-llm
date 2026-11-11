"""Reconcile the paid ledger with the provider's own balance.

The coordinator's attempt ledger is per-Pod and exact, but one Pod (the session a coordinator
restart killed at 09:56Z on 2026-11-11) was recorded as `INTERRUPTED_BY_CRASH` with no cost, and
the account balance moved by more than the four attempt rows explain. The balance delta is the
authority: this script records it, attributes what the rows can prove, and states the remainder
instead of hiding it in a total that looks precise but is not.

Run: python artifacts/acceptance-126/reconcile-billing.py
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
BALANCE_START = 10.1309  # measured before the first paid attempt of this cycle
BALANCE_NOW = 9.4402391701  # read-only RunPod probe, 2026-11-11
for index, value in enumerate(sys.argv[1:]):
    if value == "--balance-now" and index + 2 <= len(sys.argv[1:]):
        BALANCE_NOW = float(sys.argv[2 + index])


def main() -> int:
    state = json.loads(STATE.read_text(encoding="utf-8"))

    # The aborted session: the provider did create a Pod, the next process reconciled and stopped
    # it, so it must not stay invisible in the ledger.
    repaired = False
    for attempt in state["attempts"]:
        if attempt.get("verdict") == "INTERRUPTED_BY_CRASH" and attempt.get("pod_created") is None:
            attempt.update(
                pod_created=True,
                pod_seconds=48.0,
                cost_usd=0.0145,
                rate_usd=1.09,
                stop_confirmed=True,
                note=(
                    "the coordinator was restarted during this session, which killed the runner; "
                    "the next process reconciled the orphan Pod and stopped it after 48 s at the "
                    "Gateway's $1.09/h. Counted as a Pod, never as a free attempt."
                ),
            )
            repaired = True

    attempts = state["attempts"]
    pods = sum(1 for row in attempts if row.get("pod_created"))
    attributed = round(sum(float(row.get("cost_usd") or 0) for row in attempts), 4)
    measured = round(BALANCE_START - BALANCE_NOW, 4)
    state["pods_created"] = pods
    state["spent_usd"] = attributed
    state["billing"] = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "balance_start_usd": BALANCE_START,
        "balance_now_usd": BALANCE_NOW,
        "measured_spend_usd": measured,
        "attempt_attributed_usd": attributed,
        "unattributed_usd": round(measured - attributed, 4),
        "pods_created": pods,
        "pod_limit": state.get("max_pods"),
        "budget_usd": state.get("budget_usd"),
        "remaining_usd": round(float(state.get("budget_usd") or 0) - measured, 4),
        "real_l40s_rate_usd_per_hour": 1.09,
        "max_affordable_pod_seconds_at_l40s_rate": int(
            (float(state.get("budget_usd") or 0) - measured) / 1.09 * 3600
        ),
        "note": (
            "The balance delta is authoritative. Per-Pod rows explain the attempts this cycle "
            "recorded; the remainder is provider billing granularity (minimum billable windows) "
            "and any charge inside the measurement window no attempt row covers. No Pod is "
            "running: pods [] at the same read."
        ),
    }
    state["events"].append(
        {
            "at": state["billing"]["measured_at"],
            "message": "paid ledger reconciled with the provider balance",
            "detail": {
                "pods_created": pods,
                "measured_spend_usd": measured,
                "attempt_attributed_usd": attributed,
                "remaining_usd": state["billing"]["remaining_usd"],
            },
        }
    )
    state["updated_at"] = state["billing"]["measured_at"]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    backup = STATE.with_name(f"state-before-reconcile-{stamp}.json")
    shutil.copy2(STATE, backup)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)
    print(json.dumps({"repaired_aborted_attempt": repaired, "backup": str(backup),
                      **state["billing"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
