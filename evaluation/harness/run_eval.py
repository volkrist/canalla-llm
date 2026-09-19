#!/usr/bin/env python3
"""Local evaluation runner. No RunPod, no TinyFish, no production imports."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
EVAL_DIR = HARNESS_DIR.parent
WORKTREE = EVAL_DIR.parent
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR.parent))
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

from catalog import SUITES, all_tasks, select  # noqa: E402
from cleanup import cleanup_run, cleanup_task  # noqa: E402
from fixtures import ensure_ready, setup_workspace  # noqa: E402  # type: ignore
from mock_actor import play  # noqa: E402
from report import report_dir, write_reports  # noqa: E402
from schema_check import dump_json, export_public_task, validate_task  # noqa: E402
from verify import evaluate_criteria, score_canned_trace  # noqa: E402


def git_head() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=WORKTREE,
        capture_output=True,
        text=True,
        check=False,
    )
    return (proc.stdout or "").strip() or "UNKNOWN"


def git_toplevel() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=WORKTREE,
        capture_output=True,
        text=True,
        check=False,
    )
    return (proc.stdout or "").strip().replace("\\", "/")


def export_task_files(tasks: list[dict]) -> None:
    out_dir = EVAL_DIR / "tasks"
    out_dir.mkdir(parents=True, exist_ok=True)
    public = [export_public_task(task) for task in tasks]
    dump_json(out_dir / "all.json", public)
    by_cat: dict[str, list] = {}
    for task in public:
        by_cat.setdefault(task["category"], []).append(task)
    for category, rows in by_cat.items():
        dump_json(out_dir / f"{category}.json", rows)
    index = [
        {
            "id": task["id"],
            "title": task["title"],
            "category": task["category"],
            "difficulty": task["difficulty"],
        }
        for task in public
    ]
    dump_json(out_dir / "index.json", index)


def empty_totals() -> dict:
    return {
        "total_tool_calls": 0,
        "successful_calls": 0,
        "failed_calls": 0,
        "duplicate_calls": 0,
        "no_progress_events": 0,
        "replans": 0,
        "workspace_violations": 0,
        "response_repairs": 0,
        "verified_facts_used": 0,
        "runtime_seconds": 0,
        "tinyfish_cost_usd": 0,
        "runpod_cost_usd": 0,
    }


def add_metrics(totals: dict, metrics: dict) -> None:
    for key in totals:
        totals[key] += metrics.get(key, 0) or 0


def run_case(task: dict, mode: str, run_id: str) -> dict:
    errors = validate_task(export_public_task(task))
    if errors:
        return {
            "id": task["id"],
            "title": task["title"],
            "category": task["category"],
            "status": "FAIL",
            "reason": "invalid spec: " + "; ".join(errors),
            "evidence": {"schema_errors": errors},
            "metrics": empty_totals(),
            "cleanup_status": "N/A",
        }

    if mode == "spec":
        return {
            "id": task["id"],
            "title": task["title"],
            "category": task["category"],
            "status": "SKIPPED",
            "reason": "SPEC READY; provider not executed",
            "evidence": {"spec": "valid", "real": "NOT RUN"},
            "metrics": empty_totals(),
            "cleanup_status": "N/A",
            "expected_mock_status": task.get("expected_mock_status"),
        }

    workspace, state = setup_workspace(task, run_id)
    state = play(task, state, mode)

    if state.get("skip_reason"):
        cleanup = cleanup_task(task, state)
        return {
            "id": task["id"],
            "title": task["title"],
            "category": task["category"],
            "status": "SKIPPED",
            "reason": state["skip_reason"],
            "evidence": {"real": "NOT RUN", "answer": state.get("answer")},
            "metrics": state.get("metrics") or empty_totals(),
            "cleanup_status": cleanup["status"],
            "expected_mock_status": task.get("expected_mock_status"),
        }

    if (task.get("workspace_setup") or {}).get("kind") == "canned_trace":
        scored = score_canned_trace(task, state)
        cleanup = cleanup_task(task, state)
        return {
            "id": task["id"],
            "title": task["title"],
            "category": task["category"],
            "status": scored["status"],
            "reason": scored["reason"],
            "evidence": {
                "checks": scored["checks"],
                "answer": scored.get("answer"),
                "real": "NOT RUN",
                "canned": True,
            },
            "metrics": scored.get("metrics") or state.get("metrics"),
            "cleanup_status": cleanup["status"],
            "expected_mock_status": task.get("expected_mock_status"),
        }

    scored = evaluate_criteria(task, state)
    cleanup = cleanup_task(task, state)
    return {
        "id": task["id"],
        "title": task["title"],
        "category": task["category"],
        "status": scored["status"],
        "reason": scored["reason"],
        "evidence": {
            "checks": scored["checks"],
            "answer": state.get("answer"),
            "tools": [row.get("name") for row in state.get("tools") or []],
            "real": "NOT RUN",
            "mock": True,
        },
        "metrics": state.get("metrics") or empty_totals(),
        "cleanup_status": cleanup["status"],
        "expected_mock_status": task.get("expected_mock_status"),
    }


def build_payload(mode: str, run_id: str, cases: list[dict]) -> dict:
    totals = empty_totals()
    for row in cases:
        add_metrics(totals, row.get("metrics") or {})
    totals["tinyfish_cost_usd"] = 0
    totals["runpod_cost_usd"] = 0
    return {
        "run_id": run_id,
        "mode": mode,
        "base_head": git_head(),
        "worktree": git_toplevel(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "paid_resources": {
            "runpod_calls": 0,
            "tinyfish_agent_calls": 0,
            "tinyfish_browser_calls": 0,
            "tinyfish_search_fetch_calls": 0,
            "cost_usd": 0,
        },
        "metrics_totals": totals,
        "cases": cases,
        "status_counts": dict(Counter(row["status"] for row in cases)),
        "legend": {
            "PASS": "mechanical success_criteria all true (LOCAL MOCK unless mode=real)",
            "PARTIAL": "some mechanical criteria true",
            "FAIL": "none true, safety violation, or canned weak-model regression",
            "SKIPPED": "SPEC READY or REAL NOT RUN",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alex LLM evaluation harness (no paid providers)")
    parser.add_argument("--suite", default="all", choices=sorted(SUITES))
    parser.add_argument("--task", dest="task_id")
    parser.add_argument("--mode", default="mock", choices=["spec", "mock", "real"])
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--export-json", action="store_true", default=True)
    parser.add_argument("--keep-work", action="store_true")
    args = parser.parse_args(argv)

    toplevel = git_toplevel().replace("\\", "/").lower()
    if toplevel.endswith("/alex-llm") and "alex-llm-eval" not in toplevel:
        print("REFUSING to run in the main alex-llm worktree. Use alex-llm-eval.", file=sys.stderr)
        return 3

    tasks = select(suite=args.suite, task_id=args.task_id)
    if not tasks:
        print("No tasks selected.", file=sys.stderr)
        return 1

    if args.list:
        for task in tasks:
            print(f"{task['id']}\t{task['category']}\t{task['title']}")
        print(f"total\t{len(tasks)}")
        return 0

    if args.mode == "real":
        print(
            "REAL mode is disabled in this evaluation pack. "
            "It must not start RunPod, GPU, TinyFish Agent/Browser, or TinyFish Search/Fetch.",
            file=sys.stderr,
        )
        return 2

    if args.export_json:
        export_task_files(all_tasks())

    ensure_ready()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{stamp}-{args.mode}-{args.suite}"
    cases = [run_case(task, args.mode, run_id) for task in tasks]
    payload = build_payload(args.mode, run_id, cases)
    out_dir = report_dir(run_id)
    json_path, md_path = write_reports(out_dir, payload)
    if not args.keep_work:
        cleanup_run(run_id)
    print(json.dumps({"run_id": run_id, "json": str(json_path), "markdown": str(md_path), "counts": payload["status_counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
