#!/usr/bin/env python3
"""Alex LLM evaluation harness.

spec/mock: no paid providers.
real: production APIs + OrcaRouter, but never starts RunPod without --allow-runpod.
"""

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
from paid import PaidConfig, PaidRefused, authorize_case, checkpoint, validate_real_start  # noqa: E402
from persist import load_payload, merge_case, persist_payload, terminal_ids  # noqa: E402
from real_cases import apply_real_overlay, is_non_critical, real_plan_ids, skip_reason  # noqa: E402
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


def display_status(mode: str, status: str, *, real_executed: bool = False) -> str:
    if mode == "real":
        if status == "SKIPPED" or not real_executed:
            return status if status == "SKIPPED" else status
        return f"REAL {status}"
    if mode == "mock" and status == "PASS":
        return "LOCAL MOCK PASS"
    return status


def skipped_row(task: dict, reason: str, metrics=None) -> dict:
    return {
        "id": task["id"],
        "title": task["title"],
        "category": task["category"],
        "status": "SKIPPED",
        "display_status": "SKIPPED",
        "reason": reason,
        "evidence": {"real": "NOT RUN"},
        "metrics": metrics or empty_totals(),
        "cleanup_status": "N/A",
        "model": "none",
        "expected_mock_status": task.get("expected_mock_status"),
    }


def run_case(task: dict, mode: str, run_id: str) -> dict:
    errors = validate_task(export_public_task(task))
    if errors:
        return {
            "id": task["id"],
            "title": task["title"],
            "category": task["category"],
            "status": "FAIL",
            "display_status": "FAIL",
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
            "display_status": "SKIPPED",
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
            "display_status": "SKIPPED",
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
            "display_status": scored["status"],
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
            "model": "mock-canned",
        }

    scored = evaluate_criteria(task, state)
    cleanup = cleanup_task(task, state)
    return {
        "id": task["id"],
        "title": task["title"],
        "category": task["category"],
        "status": scored["status"],
        "display_status": display_status(mode, scored["status"]),
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
        "model": "mock",
    }


def run_real_case(task: dict, run_id: str, session, paid: PaidConfig, keep_work: bool) -> dict:
    overlay = apply_real_overlay(task)
    errors = validate_task(export_public_task(task))
    if errors:
        return {
            "id": task["id"],
            "title": task["title"],
            "category": task["category"],
            "status": "FAIL",
            "display_status": "FAIL",
            "reason": "invalid spec: " + "; ".join(errors),
            "evidence": {"schema_errors": errors},
            "metrics": empty_totals(),
            "cleanup_status": "N/A",
            "model": "none",
        }
    reason = skip_reason(overlay) or authorize_case(paid, overlay["id"])
    if reason:
        return skipped_row(overlay, reason)

    workspace, state = setup_workspace(overlay, run_id)
    root = getattr(session, "workspace_root", None)
    if root and workspace:
        try:
            Path(workspace).resolve().relative_to(Path(root).resolve())
        except ValueError:
            # Fixtures stay under evaluation/.work/<run-id>/; trusted roots must include that tree.
            pass
    timeout = int(overlay.get("max_runtime_seconds") or 180)
    timed_out = False
    product = {}
    try:
        if hasattr(session, "execute_case"):
            product = session.execute_case(overlay, state, paid)
        else:
            from real_actor import execute_real_case

            product = execute_real_case(session, overlay, state, paid)
        if state.get("metrics", {}).get("runtime_seconds", 0) > timeout + 5:
            timed_out = True
            state["timeout"] = True
    except Exception as error:
        state["answer"] = state.get("answer") or ""
        state.setdefault("metrics", empty_totals())
        state["metrics"]["runtime_seconds"] = state["metrics"].get("runtime_seconds") or 0
        product = {"error": f"{type(error).__name__}:{error}", "tools": state.get("tools") or []}
        state["product_error"] = product["error"]

    real_ok = bool(getattr(session, "info", {}).get("provider") == "llamacpp" and getattr(session, "info", {}).get("mock") is False)
    scored = evaluate_criteria(overlay, state)
    status = scored["status"]
    if timed_out and status == "PASS":
        status = "PARTIAL"
        scored["reason"] = "timed out after mechanical pass"
    if state.get("fail_fast") and status == "PASS":
        status = "FAIL"
        scored["reason"] = state["fail_fast"]
    if not real_ok:
        # Never upgrade a mock/local backend into REAL PASS.
        display = status
        real_executed = False
    else:
        display = display_status("real", status, real_executed=True)
        real_executed = True
    cleanup = cleanup_task(overlay, state, keep_workspace=keep_work)
    tools = [row.get("name") or row.get("tool_name") for row in state.get("tools") or []]
    return {
        "id": overlay["id"],
        "title": overlay["title"],
        "category": overlay["category"],
        "status": status,
        "display_status": display,
        "reason": scored["reason"] if not state.get("product_error") else state["product_error"],
        "natural_prompt": overlay.get("natural_user_prompt"),
        "model": "real" if real_executed else "unknown",
        "task_id": state.get("task_ref") or (state.get("product") or {}).get("task_id"),
        "chat_id": state.get("chat_id") or (state.get("product") or {}).get("chat_id"),
        "expected_tools": overlay.get("expected_tool_family") or [],
        "actual_tools": tools,
        "origin": (state.get("origins") or [None])[0],
        "origins": state.get("origins") or [],
        "evidence": {
            "checks": scored.get("checks"),
            "answer": (state.get("answer") or "")[:2000],
            "tools": tools,
            "real": "RUN" if real_executed else "NOT REAL MODEL",
            "mechanical": True,
            "browser": (state.get("product") or {}).get("browser"),
            "queue": (state.get("product") or {}).get("queue") or state.get("queue"),
            "fail_fast": state.get("fail_fast"),
            "timeout": timed_out,
            "product_error": state.get("product_error"),
        },
        "metrics": state.get("metrics") or empty_totals(),
        "cleanup_status": cleanup["status"],
        "runpod_cost_usd": (state.get("metrics") or {}).get("runpod_cost_usd", 0),
        "tinyfish_cost_usd": (state.get("metrics") or {}).get("tinyfish_cost_usd", 0),
        "real_executed": real_executed,
    }


def build_payload(mode: str, run_id: str, cases: list[dict], paid: PaidConfig | None = None, extra: dict | None = None) -> dict:
    totals = empty_totals()
    for row in cases:
        add_metrics(totals, row.get("metrics") or {})
    paid = paid or PaidConfig()
    totals["tinyfish_cost_usd"] = paid.spent_tinyfish_browser_usd + paid.spent_tinyfish_agent_usd
    totals["runpod_cost_usd"] = paid.spent_runpod_usd
    payload = {
        "run_id": run_id,
        "mode": mode,
        "base_head": git_head(),
        "worktree": git_toplevel(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "paid_resources": {
            "runpod_calls": paid.runpod_calls,
            "tinyfish_agent_calls": paid.tinyfish_agent_calls,
            "tinyfish_browser_calls": paid.tinyfish_browser_calls,
            "tinyfish_search_fetch_calls": 0,
            "cost_usd": round(paid.spent_runpod_usd + paid.spent_tinyfish_browser_usd + paid.spent_tinyfish_agent_usd, 6),
            "runpod_usd": paid.spent_runpod_usd,
            "tinyfish_browser_usd": paid.spent_tinyfish_browser_usd,
            "tinyfish_agent_usd": paid.spent_tinyfish_agent_usd,
        },
        "metrics_totals": totals,
        "cases": cases,
        "status_counts": dict(Counter(row["status"] for row in cases)),
        "legend": {
            "PASS": "mechanical success_criteria all true (LOCAL MOCK unless display_status starts with REAL)",
            "PARTIAL": "some mechanical criteria true",
            "FAIL": "none true, safety violation, or canned weak-model regression",
            "SKIPPED": "SPEC READY, unpaid guard, budget, or not in this-stage plan",
            "REAL PASS": "OrcaRouter + production path + mechanical verification",
        },
    }
    if extra:
        payload.update(extra)
    return payload


def main(argv: list[str] | None = None, runtime=None) -> int:
    parser = argparse.ArgumentParser(description="Alex LLM evaluation harness")
    parser.add_argument("--suite", default="all", choices=sorted(SUITES))
    parser.add_argument("--task", dest="task_id")
    parser.add_argument("--mode", default="mock", choices=["spec", "mock", "real"])
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--export-json", action="store_true", default=True)
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--allow-runpod", action="store_true")
    parser.add_argument("--runpod-budget-usd", type=float, default=0.0)
    parser.add_argument("--allow-tinyfish-browser", action="store_true")
    parser.add_argument("--tinyfish-budget-usd", type=float, default=0.0)
    parser.add_argument("--resume", dest="resume_id")
    parser.add_argument("--rerun-failed", action="store_true")
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
        paid = PaidConfig(
            allow_runpod=bool(args.allow_runpod),
            runpod_budget_usd=float(args.runpod_budget_usd or 0),
            allow_tinyfish_browser=bool(args.allow_tinyfish_browser),
            tinyfish_budget_usd=float(args.tinyfish_budget_usd or 0),
            allow_tinyfish_agent=False,
            allow_tinyfish_search=True,
        )
        try:
            validate_real_start(paid)
        except PaidRefused as error:
            print(str(error), file=sys.stderr)
            return 2

    if args.export_json:
        export_task_files(all_tasks())

    ensure_ready()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = args.resume_id or f"{stamp}-{args.mode}-{args.suite}"
    out_dir = report_dir(run_id)

    if args.mode != "real":
        cases = [run_case(task, args.mode, run_id) for task in tasks]
        payload = build_payload(args.mode, run_id, cases)
        json_path, md_path = write_reports(out_dir, payload)
        if not args.keep_work:
            cleanup_run(run_id)
        print(
            json.dumps(
                {"run_id": run_id, "json": str(json_path), "markdown": str(md_path), "counts": payload["status_counts"]},
                ensure_ascii=False,
            )
        )
        return 0

    paid = PaidConfig(
        allow_runpod=bool(args.allow_runpod),
        runpod_budget_usd=float(args.runpod_budget_usd or 0),
        allow_tinyfish_browser=bool(args.allow_tinyfish_browser),
        tinyfish_budget_usd=float(args.tinyfish_budget_usd or 0),
    )
    ordered_ids = real_plan_ids(tasks, args.suite)
    by_id = {task["id"]: task for task in tasks}
    tasks = [by_id[tid] for tid in ordered_ids if tid in by_id]
    existing = load_payload(out_dir) if args.resume_id else None
    done = terminal_ids(existing, rerun_failed=args.rerun_failed) if existing else set()
    cases = list(existing.get("cases") or []) if existing else []
    session = runtime
    started_session = False
    extra = {"runtime": {}}
    try:
        if session is None:
            from paths import WORK
            from real_session import RealSession

            work = WORK / run_id / "_runtime"
            session = RealSession(work, paid, workspace_root=WORK / run_id)
            extra["runtime"] = session.start()
            started_session = True
        else:
            extra["runtime"] = getattr(session, "info", {}) or {"injected": True}

        persist_payload(out_dir, build_payload("real", run_id, cases, paid, extra))
        stop_rest = None
        for task in tasks:
            if task["id"] in done:
                continue
            gate = checkpoint(paid)
            if gate == "hard_stop":
                cases.append(skipped_row(task, "SKIPPED: RunPod hard budget reached"))
                continue
            if gate == "soft_stop" and is_non_critical(task):
                cases.append(skipped_row(task, "SKIPPED: 75% RunPod budget, non-critical suite stopped"))
                continue
            row = run_real_case(task, run_id, session, paid, keep_work=args.keep_work)
            payload = build_payload("real", run_id, merge_case({"cases": cases}, row)["cases"], paid, extra)
            cases = payload["cases"]
            persist_payload(out_dir, payload)
            done.add(task["id"])
            if row.get("evidence", {}).get("fail_fast"):
                stop_rest = row["evidence"]["fail_fast"]
                break
        if stop_rest:
            for task in tasks:
                if task["id"] not in done:
                    cases.append(skipped_row(task, f"SKIPPED: fail-fast ({stop_rest})"))
                    done.add(task["id"])
        payload = build_payload("real", run_id, cases, paid, extra)
        persist_payload(out_dir, payload)
    finally:
        if started_session and session is not None:
            extra["cleanup"] = session.stop()
            extra["runtime"] = getattr(session, "info", extra.get("runtime"))
            payload = build_payload("real", run_id, cases, paid, extra)
            persist_payload(out_dir, payload)
        if not args.keep_work:
            cleanup_run(run_id)

    payload = load_payload(out_dir) or build_payload("real", run_id, cases, paid, extra)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "json": str(out_dir / "results.json"),
                "markdown": str(out_dir / "summary.md"),
                "counts": payload.get("status_counts"),
                "paid": payload.get("paid_resources"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
