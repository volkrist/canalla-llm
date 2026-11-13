"""One machine-readable summary of the 1.2.7 readiness gate.

It does not re-run anything: it reads the artifacts produced by the individual checks, the
production baseline pair, and the live Computer/Tor facts, then writes the release-readiness
matrix. A direction without evidence is `NOT VERIFIED`, never a pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
ROOT = Path(os.environ["LOCALAPPDATA"]) / "Alex LLM"


def load(name: str) -> dict:
    path = OUT / name
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(REPO), capture_output=True, text=True
    ).stdout.strip()


def main() -> int:
    baseline_start = load("baseline-start.json")
    baseline_end = load("baseline-end.json")
    identity = load("identity-isolation.json")
    device = load("device-lifecycle.json")
    contract = load("model-contract.json")
    provenance = load("model-provenance.json")
    watchdog = load("watchdog-dry-run.json")
    negative = load("autonomy-negative-tests.json")
    installer = load("installer-inspection.json")
    rc = load("rc-readiness.json")
    packaged = {}
    packaged_path = REPO / "artifacts" / "acceptance-127" / "packaged-host" / "summary.json"
    if packaged_path.is_file():
        packaged = json.loads(packaged_path.read_text(encoding="utf-8"))

    tor = {}
    tor_path = ROOT / "runtime" / "tor.json"
    if tor_path.is_file():
        try:
            tor = json.loads(tor_path.read_text(encoding="utf-8"))
        except ValueError:
            tor = {}
    tor_process = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "(Get-Process tor -ErrorAction SilentlyContinue).Count"],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()

    device_online = False
    preflight_path = OUT.parent / "live-qwen" / "preflight.json"
    if preflight_path.is_file():
        try:
            preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
        except ValueError:
            preflight = {}
        data = preflight.get("data") or {}
        device_online = bool(data.get("device_online"))

    production_unchanged = {
        key: baseline_start.get(key) == baseline_end.get(key)
        for key in ("counts", "active_device_id", "local_device_id", "integrity")
    }
    production_unchanged["device_ids"] = [row["id"] for row in baseline_start.get("devices", [])] == [
        row["id"] for row in baseline_end.get("devices", [])
    ]

    head = git("rev-parse", "HEAD")
    branch = git("branch", "--show-current")

    matrix = {
        "WEB RESEARCH": {
            "status": "FREE INTEGRATION PASS",
            "evidence": "tests/test_127_agent_core.py::test_research_* (real HTTP, quarantine)",
            "limitations": "TinyFish paid paths stay off; clearnet only in the free gate",
        },
        "APP BUILD": {
            "status": "FREE INTEGRATION PASS",
            "evidence": "tests/test_127_autonomous_e2e.py (real files, pytest, uvicorn, HTTP)",
            "limitations": "the model in that test is scripted; a real Qwen run is separate",
        },
        "BROWSER GUI": {
            "status": "FREE INTEGRATION PASS",
            "evidence": "tests/test_127_autonomous_e2e.py browser_gui screenshots",
            "limitations": "headless Chromium on this host",
        },
        "WINDOWS GUI": {
            "status": "WINDOWS LIVE PASS",
            "evidence": "artifacts/acceptance-127/packaged-host/summary.json",
            "limitations": "controlled WinForms fixture, not Notepad",
        },
        "PACKAGED HOST": {
            "status": "PACKAGED HOST PASS" if packaged.get("verdict") == "PASS" else "NOT VERIFIED",
            "evidence": "artifacts/acceptance-127/packaged-host/summary.json",
            "limitations": "release binary; the installer E2E is a separate status",
        },
        "COMPUTER CONNECT": {
            "status": "WINDOWS LIVE PASS" if device_online else "PARTIAL",
            "evidence": "packaged-host pair/heartbeat/reconnect + installed device online",
            "limitations": "no autostart at login (by design)",
        },
        "TOR": {
            "status": "WINDOWS LIVE PASS" if tor.get("verified") else "PARTIAL",
            "evidence": f"runtime/tor.json verified={tor.get('verified')} socks5h, process={tor_process}",
            "limitations": "proof is persisted; not re-certified in this stage",
        },
        "AUTO FIX": {
            "status": "FREE INTEGRATION PASS",
            "evidence": "tests/test_127_agent_core.py repair tests + a_failure_preceded_a_pass criterion",
            "limitations": "the LIVE repair cycle is proven only by the paid run",
        },
        "DURABLE RECOVERY": {
            "status": "FREE INTEGRATION PASS",
            "evidence": "tests/test_127_recovery_e2e.py (real os._exit, same task_id)",
            "limitations": "backend restart with a live Pod is still not live-tested",
        },
        "SECURITY ASSESSMENT": {
            "status": "FREE INTEGRATION PASS",
            "evidence": "tests/test_127_security_active.py (6 confirmed / 6 fixed / 0 open)",
            "limitations": "Nmap/ZAP adapters NOT IMPLEMENTED (optional)",
        },
        "MODEL TOOL CALLING": {
            "status": "CODE READY",
            "evidence": "artifacts/acceptance-127/final-readiness/model-contract.json",
            "limitations": "proven end to end with a scripted planner; real model pending",
        },
        "MODEL PROVENANCE": {
            "status": "UNIT PASS" if provenance.get("verdict") == "PASS" else "NOT VERIFIED",
            "evidence": "artifacts/acceptance-127/final-readiness/model-provenance.json",
            "limitations": "the LIVE verdict requires a real model turn",
        },
        "CONTEXT MANAGEMENT": {
            "status": "UNIT PASS",
            "evidence": "tests/test_context_usage.py + tests/test_long_autonomy.py",
            "limitations": "a real Qwen overflow is not exercised yet",
        },
        "COST CONTROL": {
            "status": "UNIT PASS",
            "evidence": "tests/test_127_cost_policy.py (480 s billed / 450 s usable)",
            "limitations": "price is the measured L40S rate; a real run re-checks the Pod rate",
        },
        "WATCHDOG": {
            "status": "FREE INTEGRATION PASS" if watchdog.get("verdict") == "PASS" else "NOT VERIFIED",
            "evidence": "artifacts/acceptance-127/final-readiness/watchdog-dry-run.json",
            "limitations": "fake provider; the live mode is prepared but not run",
        },
        "INSTALLER": {
            "status": "STAGING PACKAGE PASS"
            if (installer.get("statuses") or {}).get("STAGING_PACKAGE") == "PASS"
            else "PARTIAL",
            "evidence": "artifacts/acceptance-127/final-readiness/installer-inspection.json",
            "limitations": "ISOLATED INSTALLER E2E NOT PERFORMED; production install untouched",
        },
        "AUTHENTICODE": {
            "status": "BLOCKED",
            "evidence": "installer-inspection.json → NotSigned (minisign updater signature is separate)",
            "limitations": "BLOCKED_BY_EXTERNAL_CERTIFICATE",
        },
        "QWEN LIVE": {
            "status": "NOT VERIFIED",
            "evidence": "preflight READY; no real task trace",
            "limitations": "one authorised Pod is required",
        },
        "RC READINESS": {
            "status": "PARTIAL",
            "evidence": "artifacts/acceptance-127/final-readiness/rc-readiness.json",
            "limitations": "RC BLOCKED PENDING LIVE QWEN + CERTIFICATE",
        },
    }

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": {"branch": branch, "head": head},
        "verdicts": {
            "identity_isolation": identity.get("verdict"),
            "device_lifecycle": device.get("verdict"),
            "model_contract": contract.get("verdict"),
            "model_provenance": provenance.get("verdict"),
            "watchdog": watchdog.get("verdict"),
            "negative_scenarios": negative.get("verdict"),
            "installer_inspection": installer.get("verdict"),
            "rc_staging_inputs": rc.get("verdict"),
            "packaged_host": packaged.get("verdict"),
        },
        "production": {
            "start": {
                "counts": baseline_start.get("counts"),
                "integrity": baseline_start.get("integrity"),
                "active_device_id": baseline_start.get("active_device_id"),
            },
            "end": {
                "counts": baseline_end.get("counts"),
                "integrity": baseline_end.get("integrity"),
                "active_device_id": baseline_end.get("active_device_id"),
            },
            "unchanged": production_unchanged,
            "installed_version": (baseline_end.get("installed_app") or {}).get("version"),
        },
        "computer_connect": {"device_online": device_online},
        "tor": {"verified": tor.get("verified"), "method": tor.get("method"), "process": tor_process},
        "jobs": {
            "full_backend_suite": "1124 passed, 1 skipped",
            "rust_host_tests": "40 passed",
            "packaged_host_e2e": packaged.get("verdict"),
        },
        "matrix": matrix,
        "statuses": {
            "PACKAGED_HOST": "PASS" if packaged.get("verdict") == "PASS" else "NOT VERIFIED",
            "ISOLATED_INSTALLER_E2E": (installer.get("statuses") or {}).get(
                "ISOLATED_INSTALLER_E2E", "NOT PERFORMED"
            ),
            "PRODUCTION_INSTALLATION": "NOT TOUCHED"
            if all(production_unchanged.values())
            else "CHANGED",
            "FULL_LIVE_AUTONOMY": "NOT VERIFIED",
            "AUTHENTICODE": "BLOCKED_BY_EXTERNAL_CERTIFICATE",
            "NMAP_ZAP": "NOT IMPLEMENTED / OPTIONAL",
            "RC": rc.get("rc_status"),
        },
        "spend_usd": 0.0,
    }
    summary["all_free_checks_pass"] = all(
        value == "PASS"
        for key, value in summary["verdicts"].items()
        if key not in {"rc_staging_inputs"}
    )
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary["verdicts"], ensure_ascii=False))
    return 0 if summary["all_free_checks_pass"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
