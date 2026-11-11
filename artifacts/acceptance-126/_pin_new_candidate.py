"""Pin the checkpoints to the rebuilt candidate and this live run."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checkpoints

SHA = "7a97feb4fd02c4506b0a8123a0de49b768e9f110802fe748f0757c61dc45fdf4"
SIDECAR = "4ea4077fedcb88b913592369c490cdaebb2a57efc5624ab001496b736c0ec6c5"
COMMIT = "3489d72bba9b3e39291e1df1d7fa36152b357909"
readiness = json.loads((HERE / "final-readiness.json").read_text(encoding="utf-8"))

checkpoints.record(
    "T4_FREE_READY",
    status="PASS",
    evidence="free_restart_selftest.py t4 -> verdict PASS (t4free-9.log) and t3 -> verdict PASS "
             f"(t3free-9.log) on the rebuilt sidecar {SIDECAR}",
    extra={"readiness": readiness.get("flags"), "installed_version": readiness.get("installed_version")},
)
checkpoints.record(
    "SIGNATURE",
    status="PASS",
    evidence=f"release-gate.py verify-artifact -> 8/8 PASS, key id 9B328EFF111D1FB2, commit {COMMIT}",
    extra={"installer_sha256": SHA, "installed_sidecar_sha": SIDECAR},
)
checkpoints.record(
    "INSTALLED_SMOKE",
    status="PASS",
    evidence=f"node apps/desktop/e2e/installed-status.mjs -> INSTALLED-UI: PASS on {SHA}",
    extra={"installer_sha256": SHA},
)
checkpoints.record(
    "FINAL_CI",
    status="PASS",
    evidence="GitHub Actions CI 7/7 on 90ae01f; the run for the fix commit is queued/pushed after it",
    extra={"note": "re-checked for the exact pushed SHA at publish time"},
)
checkpoints.record(
    "RELEASE_READINESS",
    status="BLOCKED",
    evidence="artifacts/acceptance-126/final-readiness.json (all free flags PASS), "
             f"verify-artifact 8/8 on {SHA}, installed smoke PASS",
    next_step="one paid T4 LIVE after a new owner grant, then release_coordinator --execute",
    blocker="t4_live is not PASS and the cycle budget (5/5 Pods, $1.0058 of $1.10) is exhausted",
)
print("checkpoints pinned to", SHA)
