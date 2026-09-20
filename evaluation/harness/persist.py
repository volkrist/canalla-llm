"""Crash-safe incremental evaluation reports and resume."""

from __future__ import annotations

import json
import os
from pathlib import Path

from report import render_markdown, write_json

TERMINAL = {"PASS", "PARTIAL", "FAIL", "SKIPPED"}


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def persist_payload(run_dir: Path, payload: dict) -> tuple[Path, Path]:
    json_path = run_dir / "results.json"
    md_path = run_dir / "summary.md"
    atomic_write_json(json_path, payload)
    atomic_write_text(md_path, render_markdown(payload))
    cases_dir = run_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    for row in payload.get("cases") or []:
        tid = row.get("id")
        if tid:
            atomic_write_json(cases_dir / f"{tid}.json", row)
    return json_path, md_path


def load_payload(run_dir: Path) -> dict | None:
    path = run_dir / "results.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def terminal_ids(payload: dict, *, rerun_failed: bool = False) -> set[str]:
    out = set()
    for row in payload.get("cases") or []:
        status = row.get("status")
        if status not in TERMINAL:
            continue
        if rerun_failed and status in {"FAIL", "PARTIAL"}:
            continue
        tid = row.get("id")
        if tid:
            out.add(tid)
    return out


def merge_case(payload: dict, case: dict) -> dict:
    cases = [row for row in payload.get("cases") or [] if row.get("id") != case.get("id")]
    cases.append(case)
    payload["cases"] = cases
    return payload


# Keep write_json import used by older callers.
__all__ = [
    "TERMINAL",
    "atomic_write_json",
    "atomic_write_text",
    "persist_payload",
    "load_payload",
    "terminal_ids",
    "merge_case",
    "write_json",
]
