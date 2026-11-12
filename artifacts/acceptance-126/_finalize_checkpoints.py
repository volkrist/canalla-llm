"""Refresh the checkpoints that still described the pre-release state."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checkpoints  # noqa: E402

RUN = "artifacts/acceptance-123/runs/live-20261112T102739"

checkpoints.record(
    "CURRENT_MONITOR_STATE",
    status="FINISHED",
    evidence="artifacts/acceptance-126/coordinator/state.json",
    next_step="no paid run in this cycle; 1.2.7 planning only",
    extra={"pods_running": 0, "cycle": "closed", "reason": "T4 LIVE PASS, 1.2.6 published"},
)
checkpoints.record(
    "GPU_CAPACITY_MONITORING",
    status="CLOSED",
    evidence="the one granted Pod was created and stopped; pods []",
    next_step="none",
)
checkpoints.record(
    "T4_LIVE_RUNNING",
    status="COMPLETED",
    evidence=f"{RUN}/T4L.json",
    extra={
        "task_id": "758c3e1b-8116-45ac-a655-1274c55c09fc",
        "pod_seconds": 89.0,
        "cost_usd": 0.0269,
        "gpu": "NVIDIA L40S 48 GB",
        "datacenter": "US-MO-1",
    },
)
checkpoints.record(
    "GITHUB_RELEASE",
    status="PUBLISHED",
    evidence="https://github.com/volkrist/canalla-llm/releases/tag/v1.2.6",
    extra={
        "assets": [
            {"name": "Canalla.LLM_1.2.6_x64-setup.exe",
             "digest": "sha256:485ee23eb5149b46a23a16595442931a17bbb0fd290d553751095c08d981d1b1",
             "size": 95540283},
            {"name": "Canalla.LLM_1.2.6_x64-setup.exe.sig",
             "digest": "sha256:d2f90538b842af59f936695ea491458aa8c995f7c66ed91821b9506359f45dca",
             "size": 424},
            {"name": "SHA256SUMS-1.2.6.txt",
             "digest": "sha256:c8301d04a51f3d9d38fb2656e908f5ec38db48066380a85b2a21742b9fb0a572",
             "size": 200},
        ],
        "draft": False,
        "prerelease": False,
        "published_at": "2026-11-12T14:08:10Z",
    },
)
print("checkpoints refreshed")
