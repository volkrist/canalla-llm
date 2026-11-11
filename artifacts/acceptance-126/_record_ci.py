"""Record the green CI run for the commit that carries the product fix."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checkpoints

checkpoints.record(
    "FINAL_CI",
    status="PASS",
    evidence="GitHub Actions run 43013360490 on c44ccfcab043e3418926a3a19f76c1cf32e452ec: "
             "7/7 jobs success (frontend-e2e, native-gate, llama-runtime, llama-runtime-image, "
             "developer-gate, installer-build, tor-runtime); workflow «Linux gate» run "
             "43013360332 also success",
    next_step="the release coordinator re-checks CI for the exact pushed SHA before publishing",
    extra={
        "sha": "c44ccfcab043e3418926a3a19f76c1cf32e452ec",
        "jobs": 7,
        "passed": 7,
        "local_developer_gate": "9/9 PASS",
        "backend_suite": "1078 passed, 1 skipped",
    },
)
print("FINAL_CI recorded")
