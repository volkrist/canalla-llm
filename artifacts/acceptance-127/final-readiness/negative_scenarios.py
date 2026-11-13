"""Negative-scenario matrix for the 1.2.7 autonomy surface.

Each scenario names a real check that exists. Pytest results are collected from targeted nodes
(never a repeated full suite), and the non-pytest scenarios read the acceptance artifacts produced
by the identity and watchdog runs. A scenario without evidence is reported as MISSING, never PASS.

Run:  python artifacts/acceptance-127/final-readiness/negative_scenarios.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "apps" / "backend"
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
OUT = Path(__file__).resolve().parent

SCENARIOS = [
    {
        "id": 1,
        "name": "model unavailable",
        "expected": "WAITING_LLM, no false COMPLETED",
        "node": "tests/test_task_status.py::test_a_dead_provider_is_a_model_wait_with_a_real_idle_time",
    },
    {
        "id": 1.1,
        "name": "provider outage after a completed tool",
        "expected": "real result kept, typed failure, no invented success",
        "node": "tests/test_autonomous_tasks.py::test_a_gateway_outage_after_a_tool_keeps_the_result_and_finishes",
    },
    {
        "id": 2,
        "name": "tool returns failure",
        "expected": "the model receives the error and the task does not claim success",
        "node": "tests/test_autonomous_tasks.py::test_stale_patch_is_conflict",
    },
    {
        "id": 3,
        "name": "Computer Connect disconnected",
        "expected": "WAITING_DEVICE, checkpoint kept, resumes after reconnect",
        "node": "tests/test_autonomous_tasks.py::test_device_disconnect_waits",
    },
    {
        "id": 4,
        "name": "fabricated tool result",
        "expected": "independent verifier refuses; provenance seal fails",
        "node": "tests/test_127_provenance.py::test_a_tampered_record_fails_the_seal",
    },
    {
        "id": 4.1,
        "name": "claimed file that does not exist",
        "expected": "verification fails instead of passing",
        "node": "tests/test_127_agent_core.py::test_verifier_checks_real_files_and_digests",
    },
    {
        "id": 5,
        "name": "process dies between write and verification",
        "expected": "same task_id resumes, real content re-checked, no repeated side effect",
        "node": "tests/test_127_recovery_e2e.py::test_a_killed_process_leaves_a_task_that_a_new_process_resumes",
    },
    {
        "id": 5.1,
        "name": "restart does not replay a completed tool",
        "expected": "no duplicate side effects",
        "node": "tests/test_autonomous_tasks.py::test_restart_does_not_rerun_completed_tools",
    },
    {
        "id": 6,
        "name": "defect not fixed after retries",
        "expected": "stall detected, bounded retries, no false PASS",
        "node": "tests/test_127_agent_core.py::test_repair_fingerprint_ignores_noise_and_detects_stall",
    },
    {
        "id": 6.1,
        "name": "retry schedule is bounded",
        "expected": "no infinite retry loop",
        "node": "tests/test_autonomous_tasks.py::test_the_recovery_retry_schedule_is_bounded",
    },
    {
        "id": 7,
        "name": "context budget exhausted",
        "expected": "the limit is reported honestly and a long task keeps its goal",
        "node": "tests/test_context_usage.py::test_summarize_never_hides_an_overflow",
    },
    {
        "id": 7.1,
        "name": "twenty-step task still runs",
        "expected": "no early stop before the budget",
        "node": "tests/test_long_autonomy.py::test_the_loop_runs_at_least_fifteen_model_calls",
    },
    {
        "id": 8,
        "name": "planned GPU cost above the budget",
        "expected": "refused before a Pod exists",
        "node": "tests/test_127_cost_policy.py::test_a_pricier_pod_is_refused_before_creation",
    },
]


def run_node(node: str) -> dict:
    completed = subprocess.run(
        [str(PYTHON), "-m", "pytest", node, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        timeout=900,
    )
    tail = [line for line in (completed.stdout or "").splitlines() if line.strip()][-3:]
    return {
        "node": node,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "summary": " | ".join(tail)[-400:],
    }


def artifact(path: Path, key: str) -> dict | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return {"path": str(path.relative_to(REPO)), "key": key, "value": payload.get(key)}


def main() -> int:
    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "targeted pytest nodes plus existing acceptance artifacts; no full-suite rerun",
        "scenarios": [],
    }
    for scenario in SCENARIOS:
        outcome = run_node(scenario["node"])
        report["scenarios"].append({**scenario, "evidence": outcome, "status": "PASS" if outcome["ok"] else "FAIL"})

    # Scenario 9: the isolated host ran while the installed application was live.
    isolation = artifact(OUT / "identity-isolation.json", "verdict")
    report["scenarios"].append(
        {
            "id": 9,
            "name": "installed Canalla running in parallel",
            "expected": "test identity isolated, production credential unchanged",
            "evidence": isolation,
            "status": "PASS" if isolation and isolation["value"] == "PASS" else "MISSING",
        }
    )

    # Scenario 10: an acceptance harness that dies leaves nothing of its own behind.
    lifecycle = artifact(OUT / "device-lifecycle.json", "verdict")
    report["scenarios"].append(
        {
            "id": 10,
            "name": "harness cleanup",
            "expected": "own credential removed, own processes gone, temp data removed",
            "evidence": lifecycle,
            "status": "PASS" if lifecycle and lifecycle["value"] == "PASS" else "MISSING",
        }
    )

    watchdog = artifact(OUT / "watchdog-dry-run.json", "verdict")
    report["scenarios"].append(
        {
            "id": 11,
            "name": "external watchdog stops the Pod",
            "expected": "stop requested inside the wall, termination confirmed, cost within budget",
            "evidence": watchdog,
            "status": "PASS" if watchdog and watchdog["value"] == "PASS" else "MISSING",
        }
    )

    failed = [item for item in report["scenarios"] if item["status"] != "PASS"]
    report["verdict"] = "PASS" if not failed else "FAIL"
    report["failed"] = [item["id"] for item in failed]
    (OUT / "autonomy-negative-tests.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in ("verdict", "failed")}, ensure_ascii=False))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
