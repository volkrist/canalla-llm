"""Pin the checkpoints to the final rebuilt artifact (sha 5fffd403…, HEAD 46a7a5f)."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checkpoints

SHA = "5fffd4032c3acafc3ac78a94ccbcfce18d304f6b5538935580298c47d26c551f"
SIDECAR = "a5d176db4690e8e86d82f36aa299d20b93f78fda2cc2e087b7393c7aa6e2674e"
readiness = json.loads((HERE / "final-readiness.json").read_text(encoding="utf-8"))
state = json.loads((HERE / "coordinator" / "state.json").read_text(encoding="utf-8"))
billing = state.get("billing") or {}

checkpoints.record(
    "INSTALLED_SMOKE",
    status="PASS",
    evidence="node apps/desktop/e2e/installed-status.mjs -> INSTALLED-UI: PASS on "
             f"installer {SHA} (installed sidecar {SIDECAR})",
    extra={"installer_sha256": SHA, "installed_sidecar_sha": SIDECAR},
)
checkpoints.record(
    "SIGNATURE",
    status="PASS",
    evidence="release-gate.py verify-artifact -> 8/8 PASS, key id 9B328EFF111D1FB2, "
             "commit 46a7a5fac35c547e7137662cd0fc9a031e257365",
    extra={"installer_sha256": SHA},
)
checkpoints.record(
    "T4_FREE_READY",
    status="PASS",
    evidence="free_restart_selftest.py t4 -> verdict PASS on the rebuilt sidecar "
             f"{SIDECAR} (t4free-8.log); t3 -> verdict PASS (t3free-8.log)",
    next_step="one paid T4 LIVE session on Qwen inside a raised money cap",
    extra={
        "checks": "11/11 T4 mechanics true, 11/11 T3 mechanics true, same task id, "
                  "Resume via the UI button, no repeated payloads, device id stable",
        "readiness": readiness.get("flags"),
        "installed_version": readiness.get("installed_version"),
    },
)
checkpoints.record(
    "CURRENT_MONITOR_STATE",
    status="STOPPED_POD_LIMIT",
    evidence="artifacts/acceptance-126/coordinator/state.json",
    next_step="owner decision: raise the cap, then one bounded T4 LIVE session",
    blocker=(
        f"4 Pods created (plan cap 3); ${billing.get('measured_spend_usd')} of $0.85 measured "
        f"spent; ${billing.get('remaining_usd')} left, a worst-case 600 s L40S Pod is $0.18"
    ),
    extra={
        "pods_running": 0,
        "measured_spend_usd": billing.get("measured_spend_usd"),
        "remaining_usd": billing.get("remaining_usd"),
        "max_affordable_pod_seconds": billing.get("max_affordable_pod_seconds_at_l40s_rate"),
    },
)
print("checkpoints pinned to", SHA)
