"""Pin the checkpoints to the final rebuilt candidate (sha b859f2a2, commit 7273cda)."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checkpoints

SHA = "b859f2a20e35e56ea14f789857a914a068c8bc2c8cec02b299ed28791669fcdd"
SIDECAR = "7c2f465f96d7e186ebf7ac07a8b05a80f9d18d82ee92381f587187f7dffcdfd6"
COMMIT = "7273cda5f4b463fd7d11c4b21af1a1a0dd7310f0"
readiness = json.loads((HERE / "final-readiness.json").read_text(encoding="utf-8"))

checkpoints.record(
    "T4_FREE_READY",
    status="PASS",
    evidence=f"preflight on sidecar {SIDECAR}: t3_free_ready and t4_free_ready PASS",
    extra={"readiness": readiness.get("flags"), "installed_version": readiness.get("installed_version")},
)
checkpoints.record(
    "SIGNATURE",
    status="PASS",
    evidence=f"verify-artifact 8/8 PASS, key 9B328EFF111D1FB2, commit {COMMIT}",
    extra={"installer_sha256": SHA, "installed_sidecar_sha": SIDECAR},
)
checkpoints.record(
    "INSTALLED_SMOKE",
    status="PASS",
    evidence=f"installed-status.mjs -> INSTALLED-UI: PASS on {SHA}",
    extra={"installer_sha256": SHA},
)
checkpoints.record(
    "FINAL_CI",
    status="PASS",
    evidence=f"GitHub Actions CI 7/7 on {COMMIT[:7]} (developer gate included) + Linux gate",
    extra={"sha": COMMIT, "jobs": 7, "passed": 7,
           "backend_suite": "1081 passed, 1 skipped",
           "note": "re-checked for the exact pushed SHA at publish time"},
)
checkpoints.record(
    "RELEASE_READINESS",
    status="BLOCKED",
    evidence=f"final-readiness.json (10/10 free flags), verify-artifact 8/8 on {SHA}, "
             "installed smoke PASS, CI 7/7 — T4 LIVE remains",
    next_step="one paid T4 LIVE after a new owner grant, then release_coordinator --execute",
    blocker="t4_live is not PASS; the cycle budget (5/5 Pods, $1.0058 of $1.10) is exhausted",
)
print("pinned", SHA)
