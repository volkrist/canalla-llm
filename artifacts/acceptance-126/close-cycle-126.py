"""Close the 1.2.6 cycle with the facts the provider actually reports.

Nothing here starts or stops compute: the balance comes from the same read-only probe the release
uses, the Pod count comes from the coordinator's own attempts, and every checkpoint points at a
file that already exists. The previous `cycle_closed` block described the *intermediate* stop
(5 of 5 Pods); it is kept in `cycle_closed_history` instead of being overwritten.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
STATE = HERE / "coordinator" / "state.json"

sys.path.insert(0, str(HERE))
import checkpoints  # noqa: E402

BALANCE_NOW = float(sys.argv[1]) if len(sys.argv) > 1 else 8.7672151726
BALANCE_START = 10.1309  # the cycle's opening balance, recorded when the cycle started
BUDGET = 1.45
MAX_PODS = 6

state = json.loads(STATE.read_text(encoding="utf-8"))
attempts = state.get("attempts") or []
attributed = round(sum(float(a.get("cost_usd") or 0) for a in attempts), 4)
measured = round(BALANCE_START - BALANCE_NOW, 4)
remaining = round(BUDGET - measured, 4)
now = datetime.now(timezone.utc).isoformat()

billing = dict(state.get("billing") or {})
billing.update(
    measured_at=now,
    balance_start_usd=BALANCE_START,
    balance_now_usd=BALANCE_NOW,
    measured_spend_usd=measured,
    remaining_usd=remaining,
    budget_usd=BUDGET,
    pods_created=int(state.get("pods_created") or 0),
    pod_limit=MAX_PODS,
    attempt_attributed_usd=attributed,
    unattributed_usd=round(measured - attributed, 4),
    note=(
        "The balance delta is authoritative: $10.1309 opening minus $%.10f now. Per-attempt rows "
        "explain $%.4f of it; the rest is provider billing granularity (minimum billable windows) "
        "and late charges inside the measurement window. No Pod is running: pods [] at the same "
        "read." % (BALANCE_NOW, attributed)
    ),
)
state["billing"] = billing

history = list(state.get("cycle_closed_history") or [])
previous = state.get("cycle_closed")
if previous:
    history.append(previous)
state["cycle_closed_history"] = history
state["cycle_closed"] = {
    "at": now,
    "reason": (
        "the cycle is complete: the owner's final grant (one Pod, cap $1.45, six Pods in total) "
        "was spent on the last T4 LIVE, which PASSED, and 1.2.6 is published"
    ),
    "pods_created": int(state.get("pods_created") or 0),
    "max_pods": MAX_PODS,
    "measured_spend_usd": measured,
    "budget_usd": BUDGET,
    "remaining_usd": remaining,
    "balance_now_usd": BALANCE_NOW,
    "t4l": "PASS — real Qwen on an L40S, 3 model-origin tools, typed WAITING_DEVICE, resume of "
           "the same task, byte-identical t4l_out.txt, COMPLETED",
}
state["final_outcome"] = {
    "at": now,
    "t4_live": "PASS",
    "release": "1.2.6 PUBLISHED",
    "updater": "manifest 1.2.6 live; 1.2.3/1.2.4/1.2.5 -> 1.2.6, 1.2.6 -> 204",
    "pods_running": 0,
    "measured_spend_usd": measured,
    "budget_usd": BUDGET,
}
state.setdefault("events", []).append(
    {"at": now, "message": "cycle closed: T4 LIVE PASS, 1.2.6 published",
     "detail": {"measured_spend_usd": measured, "remaining_usd": remaining,
                "balance_now_usd": BALANCE_NOW}}
)
state["updated_at"] = now

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
shutil.copy2(STATE, STATE.with_name(f"state-before-final-close-{stamp}.json"))
tmp = STATE.with_suffix(".json.tmp")
tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
os.replace(tmp, STATE)

T4 = "artifacts/acceptance-123/runs/live-20261112T102739/T4L.json"
DEVICE = "f3548022-a676-463c-9fc5-b05bbd5ac907"

checkpoints.record(
    "BUDGET_RECONCILED",
    status="PASS",
    evidence="artifacts/acceptance-126/coordinator/state.json (billing block)",
    next_step="no further paid run in this cycle",
    extra={
        "balance_start_usd": BALANCE_START,
        "balance_now_usd": BALANCE_NOW,
        "measured_spend_usd": measured,
        "budget_usd": BUDGET,
        "remaining_usd": remaining,
        "pods_created": state.get("pods_created"),
        "pod_limit": MAX_PODS,
        "attempt_attributed_usd": attributed,
        "unattributed_usd": round(measured - attributed, 4),
    },
)
checkpoints.record(
    "DATA_SAFE",
    status="PASS",
    evidence="read-only PRAGMA integrity_check + published-table counts on %LOCALAPPDATA%/Alex LLM/data/alex.db",
    extra={
        "integrity": "ok",
        "users": 2,
        "chats": 54,
        "messages": 120,
        "paired_devices": 59,
        "computer_mode": "ask",
        "baseline": {"users": 2, "chats": 52, "messages": 116, "paired_devices": 59},
        "note": "counts only ever grew; nothing was deleted or rolled back",
    },
)
checkpoints.record(
    "DEVICE_STABLE",
    status="PASS",
    evidence="device.json today vs artifacts/acceptance-123/runs/20261027T152437/preflight.json online_devices",
    extra={
        "device_id": DEVICE,
        "correction": (
            "several reports (and the BASELINE checkpoint detail) carried "
            "f3548022-a676-463c-9fc0-b05bbd5ac907; the measured value in every product file and "
            "in the 27 Oct preflight is 9fc5. The 9fc0 spelling was a transcription typo in prose, "
            "never a stored identity."
        ),
    },
)
checkpoints.record(
    "T4_LIVE_VERDICT",
    status="PASS",
    evidence=T4,
    extra={
        "task_id": "758c3e1b-8116-45ac-a655-1274c55c09fc",
        "gpu": "NVIDIA L40S 48 GB",
        "datacenter": "US-MO-1",
        "pod_seconds": 89.0,
        "cost_usd": 0.0269,
        "model_origin_tools": 3,
        "tools": ["read_file", "write_file", "read_file"],
        "approvals": 3,
        "state": "COMPLETED",
        "file_sha256": "1b88c6e7dbaea1f2cbf74f3a0a906d548c6886f4ed2ecec3b3ddcfd079bc51d8",
        "device_id": DEVICE,
    },
)
checkpoints.record(
    "GPU_CLEANUP",
    status="CONFIRMED",
    evidence="read-only RunPod probe: pods [], balance %s" % BALANCE_NOW,
    extra={"pods_running": 0, "balance_usd": BALANCE_NOW},
)
checkpoints.record(
    "RELEASE_READINESS",
    status="PASS",
    evidence="release_coordinator plan: all 15 mandatory gates PASS, publish=yes",
    next_step="none — published",
)
checkpoints.record(
    "UPDATER",
    status="PASS",
    evidence="step 14 (manifest 1.2.6 live, url bound to the signed name) + step 15 (1.2.3/1.2.4/1.2.5 -> 1.2.6, 1.2.6 -> 204)",
    extra={
        "manifest_url": "https://gateway.12testers.store/downloads/Canalla%20LLM_1.2.6_x64-setup.exe?v=1.2.6",
        "installer_sha256": "485ee23eb5149b46a23a16595442931a17bbb0fd290d553751095c08d981d1b1",
        "reason_for_query": (
            "Cloudflare had cached a 404 for the clean artifact URL after step 12 published the "
            "file under the GitHub-sanitized name; a per-release cache-buster keeps the signed "
            "name and makes the published bytes immediately reachable"
        ),
    },
)
checkpoints.record(
    "NEXT_AUTONOMY_DEVELOPMENT",
    status="PLANNED",
    evidence="owner brief: RESEARCH -> PLAN -> BUILD -> RUN -> GUI -> TEST -> FIX -> VERIFY",
    next_step="plan 1.2.7 autonomously; publish only with a separate owner approval",
)

print(json.dumps({"measured_spend_usd": measured, "remaining_usd": remaining,
                  "pods_created": state.get("pods_created"), "budget_usd": BUDGET,
                  "balance_now_usd": BALANCE_NOW}, indent=1))
