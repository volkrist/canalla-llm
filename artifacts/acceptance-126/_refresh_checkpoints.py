"""Refresh the named checkpoints with what is true after the free diagnosis and the new build."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checkpoints

state = json.loads((HERE / "coordinator" / "state.json").read_text(encoding="utf-8"))
billing = state.get("billing") or {}
measured = billing.get("measured_spend_usd")
remaining = billing.get("remaining_usd")

checkpoints.record(
    "T4_FREE_READY",
    status="PASS",
    evidence="python artifacts/acceptance-126/free_restart_selftest.py t4 -> verdict PASS "
             "(t4free-7.log, the rebuilt 1.2.6 sidecar a5dd6e9450980562)",
    next_step="one paid T4 LIVE session on Qwen inside a raised money cap",
    extra={
        "t3": "t3free-7.log verdict PASS",
        "checks": "11/11 mechanics true, same task id, Resume via the UI button, "
                  "no repeated payloads, device id stable",
        "product_fix": "a named-file task now opens a local_tasks row (test_126_autonomy_defects)",
    },
)

checkpoints.record(
    "CURRENT_MONITOR_STATE",
    status="STOPPED_POD_LIMIT",
    evidence="artifacts/acceptance-126/coordinator/state.json",
    next_step="no automatic start: the remaining cycle budget is below one worst-case Pod",
    blocker=(
        f"4 Pods created (plan cap 3); ${measured} of $0.85 measured spent, "
        f"${remaining} left; a 600 s L40S Pod is $0.18 at the Gateway's real $1.09/h"
    ),
    extra={
        "pods_running": 0,
        "pods_created": 4,
        "measured_spend_usd": measured,
        "attempt_attributed_usd": billing.get("attempt_attributed_usd"),
        "remaining_usd": remaining,
        "root_cause_fixed": "T4L prompt classified as an autonomous file task; "
                            "device polling no longer writes on every request; "
                            "pool starvation is a typed database_busy",
    },
)

checkpoints.record(
    "T4_LIVE_VERDICT",
    status="BLOCKED",
    evidence="artifacts/REPORT-canalla-autonomy-next-and-release-126.md",
    next_step="one L40S session of at most 480-520 s once the owner raises the cap",
    blocker=(
        "not run since the fix: the 2026-11-11 18:59 session died of a local database pool "
        "starvation and had no local_tasks row. Cause found and fixed free; the remaining "
        f"obstacle is money (${remaining} of the $0.85 cap left, a 600 s Pod is $0.18)"
    ),
    extra={
        "previous_verdict": "INVALID (product defect, not capacity)",
        "task_row_created_in_free_rerun": True,
    },
)

checkpoints.record(
    "INSTALLED_SMOKE",
    status="PASS",
    evidence="node apps/desktop/e2e/installed-status.mjs -> INSTALLED-UI: PASS on the rebuilt "
             "1.2.6 candidate (sha 12b36e6d2bce59fdabe190f1717fad06d111992e02f7c24578a60f9bc396f99d)",
    next_step="re-run after any further rebuild",
)

checkpoints.record(
    "SIGNATURE",
    status="PASS",
    evidence="release-gate.py verify-artifact -> 8/8 PASS, key id 9B328EFF111D1FB2, "
             "Ed25519 over BLAKE2b-512",
    next_step="sign again only if the sources change again",
    extra={"installer_sha256": "12b36e6d2bce59fdabe190f1717fad06d111992e02f7c24578a60f9bc396f99d"},
)

checkpoints.record(
    "GPU_CLEANUP",
    status="CONFIRMED",
    evidence="read-only RunPod probe: pods [] and watchdog disarmed",
    next_step="",
    extra={"pods_running": 0, "balance_usd": billing.get("balance_now_usd")},
)

checkpoints.record(
    "RELEASE_READINESS",
    status="BLOCKED",
    evidence="artifacts/acceptance-126/final-readiness.json (all free flags PASS) + "
             "artifacts/acceptance-126/release-evidence.json",
    next_step="T4 LIVE on the rebuilt candidate, then release_coordinator --execute",
    blocker="t4_live is not PASS; every other release gate is green or already proven",
)
print("checkpoints updated")
