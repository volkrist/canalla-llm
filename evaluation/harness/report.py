"""JSON + Markdown evaluation reports."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from paths import REPORTS


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_markdown(payload: dict) -> str:
    counts = Counter(row["status"] for row in payload["cases"])
    lines = [
        f"# Evaluation report `{payload['run_id']}`",
        "",
        f"- mode: **{payload['mode']}**",
        f"- base_head: `{payload['base_head']}`",
        f"- worktree: `{payload.get('worktree', '')}`",
        f"- cases: {len(payload['cases'])}",
        f"- PASS: {counts.get('PASS', 0)}",
        f"- PARTIAL: {counts.get('PARTIAL', 0)}",
        f"- FAIL: {counts.get('FAIL', 0)}",
        f"- SKIPPED: {counts.get('SKIPPED', 0)}",
        "",
        "## Paid resources",
        "",
        f"- RunPod calls: {payload['paid_resources']['runpod_calls']}",
        f"- TinyFish Agent calls: {payload['paid_resources']['tinyfish_agent_calls']}",
        f"- TinyFish Browser calls: {payload['paid_resources']['tinyfish_browser_calls']}",
        f"- TinyFish Search/Fetch calls: {payload['paid_resources']['tinyfish_search_fetch_calls']}",
        f"- cost_usd: {payload['paid_resources']['cost_usd']}",
        "",
        "## Totals",
        "",
    ]
    for key, value in payload["metrics_totals"].items():
        lines.append(f"- {key}: {value}")
    lines += ["", "## Cases", "", "| id | category | status | reason | tools | cleanup |", "|---|---|---|---|---|---|"]
    for row in payload["cases"]:
        reason = (row.get("reason") or "").replace("|", "/")
        lines.append(
            f"| {row['id']} | {row.get('category', '')} | {row['status']} | {reason} | "
            f"{(row.get('metrics') or {}).get('total_tool_calls', 0)} | {row.get('cleanup_status', '')} |"
        )
    lines += ["", "## Legend", "", "- SPEC READY / SKIPPED: spec validated, provider not executed.", "- LOCAL MOCK PASS: harness actor + mechanical checks, not the production model.", "- REAL NOT RUN: OrcaRouter/RunPod/TinyFish/Tor were not started.", ""]
    return "\n".join(lines) + "\n"


def write_reports(run_dir: Path, payload: dict) -> tuple[Path, Path]:
    json_path = run_dir / "results.json"
    md_path = run_dir / "summary.md"
    write_json(json_path, payload)
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return json_path, md_path


def report_dir(run_id: str) -> Path:
    path = REPORTS / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path
