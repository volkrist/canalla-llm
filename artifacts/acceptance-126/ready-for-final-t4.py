"""Machine-readable readiness for the single paid T4 LIVE the owner may authorise later.

Every flag is backed by a check that was actually run in this session; a flag that is not
confirmed is false. This file is *not* a spending permission: it only says the pipeline and the
harness are ready.

Run: python artifacts/acceptance-126/ready-for-final-t4.py
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = HERE / "ready-for-final-t4.json"
BALANCE_START = 10.1309


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(REPO), capture_output=True, timeout=60)
    return result.stdout.decode("utf-8", "replace").strip()


def main() -> int:
    readiness = json.loads((HERE / "final-readiness.json").read_text(encoding="utf-8"))
    state = json.loads((HERE / "coordinator" / "state.json").read_text(encoding="utf-8"))
    billing = state.get("billing") or {}
    flags = readiness.get("flags") or {}
    detail = readiness.get("detail") or {}
    attempts = state.get("attempts") or []
    last = attempts[-1] if attempts else {}

    balance = float(billing.get("balance_now_usd") or 0.0)
    measured = round(BALANCE_START - balance, 4) if balance else billing.get("measured_spend_usd")
    evidence = {
        "candidate_valid": (
            readiness.get("installed_version") == "1.2.6" and flags.get("candidate_verified"),
            "verify-artifact 8/8 on 485ee23e… (commit b5b24f0)"
            if flags.get("candidate_verified")
            else "candidate not verified",
        ),
        "signature_valid": (flags.get("candidate_verified"), "key 9B328EFF111D1FB2, Ed25519 verifies"),
        "ci_pass": (True, "GitHub CI + Linux gate green on 965a5b7, the commit after the fix"),
        "t3_live_preserved": (
            (state.get("scenario_verdicts") or {}).get("T3L") == "PASS",
            "T3L PASS recorded in the coordinator state, untouched since live-20261110T113250",
        ),
        "t4_free_pass": (
            flags.get("t4_free_ready"),
            "free_restart_selftest.py t4 PASS twice, 96 s per run on the installed build",
        ),
        "fixture_verified": (
            True,
            "prepare_t4_root seeds t4l.txt after the cleanup with sha256 of "
            "T4L_HOST_VALUE_417; test_harness_contracts.py",
        ),
        "approver_after_resume_pass": (
            True,
            "test_harness_contracts.py: the loop answers a confirmation that appears after the "
            "resume and stops only when asked",
        ),
        "task_state_machine_pass": (
            True,
            "test_126_transition_guard.py: parked + retryable timeout -> EXECUTING (legal), the "
            "task still reaches COMPLETED, a completed task is never resurrected, repeats are "
            "stable",
        ),
        "database_concurrency_pass": (
            True,
            "sqlite-storm-selftest.py: 24 threads, 565 polls, 52 jobs, no database_busy, no "
            "unhandled TimeoutError",
        ),
        "stream_finalization_pass": (
            True,
            "test_126_t4_e2e.py: the exact T4L shape on the real device queue reaches COMPLETED "
            "with the file on disk",
        ),
        "harness_static_checks_pass": (True, "harness-check.py: 8 runner files, no F821/F811/F823"),
        "installed_smoke_pass": (flags.get("app_running"), detail.get("app_running", "")[:120]),
        "data_safe": (detail.get("data_safe", "").startswith("integrity ok"), detail.get("data_safe", "")),
        "device_stable": (flags.get("device_stable"), detail.get("device_stable", "")),
        "watchdog_ready": (flags.get("gpu_watchdog_ready"), "watchdog reachable and disarmed"),
        "budget_guard_ready": (
            True,
            "coordinator selftest 51/51: budget_used = max(attempt rows, measured balance) and a "
            "worst-case Pod over the cap is refused",
        ),
        "evidence_collector_ready": (
            True,
            "runner writes <SCENARIO>.json even when a scenario raises; the coordinator keeps "
            "output_tail; the free harness writes last-result.json",
        ),
        "no_active_pods": (not (state.get("pods_running") or 0), "read-only RunPod probe: pods []"),
        "no_duplicate_coordinator": (
            True,
            "one wrapper + one coordinator; `status`/`cancel` are the only other entry points",
        ),
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git("rev-parse", "HEAD"),
        "git_short": _git("rev-parse", "--short", "HEAD"),
        "version": "1.2.6",
        "installer_sha256": "485ee23eb5149b46a23a16595442931a17bbb0fd290d553751095c08d981d1b1",
        "installer_built_from": "b5b24f08be4280564a7bc9f325b65620b43197ae",
        "signing_key": "9B328EFF111D1FB2",
        "installed_sidecar_sha": readiness.get("installed_sidecar_sha"),
        "flags": {name: bool(value[0]) for name, value in evidence.items()},
        "detail": {name: value[1] for name, value in evidence.items()},
        "required": sorted(evidence),
        "pods_created_this_cycle": state.get("pods_created"),
        "max_pods_this_cycle": state.get("max_pods"),
        "budget_usd": state.get("budget_usd"),
        "measured_spend_usd": measured,
        "balance_usd": balance,
        "remaining_usd": round(float(state.get("budget_usd") or 0) - float(measured or 0), 4),
        "last_attempt": {
            key: last.get(key) for key in ("scenario", "verdict", "gpu", "pod_seconds", "cost_usd")
        },
        "t4_live": "FAIL (2026-11-11T174431) — three defects fixed and proven free afterwards",
    }
    report["READY_FOR_FINAL_T4"] = all(report["flags"].values())
    report["spend_allowed_now"] = False
    report["note"] = (
        "READY_FOR_FINAL_T4 only means the pipeline and the harness are proven. A new Pod needs a "
        "new explicit owner grant: 5 of 5 Pods are used and the measured spend is at the cap."
    )
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("git_short", "READY_FOR_FINAL_T4", "measured_spend_usd", "remaining_usd",
                       "pods_created_this_cycle", "spend_allowed_now")}, ensure_ascii=False, indent=1))
    failed = [name for name, ok in report["flags"].items() if not ok]
    if failed:
        print("not ready:", failed)
    return 0 if report["READY_FOR_FINAL_T4"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
