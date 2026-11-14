"""Free measurement of the prepared fast path: everything up to the first request, no Pod.

Builds the same payload the monitor hands the harness (fresh observation, cached READY preflight)
and runs the acceptance in `--startup-probe` mode, which stops before sending the chat.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
LIVE_DIR = REPO / "artifacts" / "acceptance-127" / "live-qwen"
PREFLIGHT = LIVE_DIR / "preflight.json"
PYTHON = REPO / "apps" / "backend" / ".venv" / "Scripts" / "python.exe"


def main() -> int:
    preflight = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {
        "schema": 1,
        "run_id": "startup-probe",
        "generated_at": now,
        "observed_at": now,
        "pod_count": 0,
        "placement": {
            "datacenter": "US-MO-1",
            "volume_id": "y92bt25ru8",
            "storage_mode": "regional_network_volume",
        },
        "rate_usd_per_hour": preflight.get("budget", {}).get("policy", {}).get("hourly_rate_usd")
        or 1.09,
        "budget_usd": 2.00,
        "model_state": "VERIFIED",
        "requires_selftest": False,
        "preflight": {
            "verdict": "READY",
            "checked_at": now,
            "blockers": [],
            "report": preflight,
        },
    }
    if preflight.get("verdict") != "READY":
        print(json.dumps({"ok": False, "reason": "preflight_not_ready"}))
        return 2
    path = Path(tempfile.gettempdir()) / "canalla-startup-probe.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    # Its own evidence directory: a probe must never overwrite the files a live attempt is writing.
    evidence = Path(tempfile.gettempdir()) / "canalla-startup-probe-evidence"
    shutil.rmtree(evidence, ignore_errors=True)
    evidence.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            str(PYTHON),
            str(LIVE_DIR / "live_acceptance.py"),
            "--live",
            "--budget-usd",
            "2.00",
            "--confirm",
            "I AUTHORIZE ONE PAID POD",
            "--volume",
            "y92bt25ru8",
            "--prepared",
            str(path),
            "--startup-probe",
            "--run-id",
            "startup-probe",
            "--evidence-dir",
            str(evidence),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=600,
    )
    print(completed.stdout[-3000:])
    if completed.returncode != 0:
        print(completed.stderr[-2000:])
    timings = {}
    try:
        timings = json.loads((evidence / "startup-timings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    logs = evidence / "logs" / "backend.log"
    flood = 0
    if logs.is_file():
        text = logs.read_text(encoding="utf-8", errors="replace")
        flood = text.count("--- Logging error ---")
    print(
        json.dumps(
            {"exit": completed.returncode, "timings": timings, "logging_errors": flood},
            ensure_ascii=False,
        )
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
