"""Lightweight task-spec validation without third-party jsonschema."""

from __future__ import annotations

import json
import re
from pathlib import Path

from paths import SCHEMA

ID_RE = re.compile(r"^[A-Z]{2}-[0-9]{2}$")
CATEGORIES = {
    "local-computer",
    "coding",
    "web",
    "tinyfish-routing",
    "tor",
    "rag",
    "memory",
    "autonomous",
    "recovery",
    "safety",
    "weak-model",
    "efficiency",
    "ambiguity",
    "deep-research",
    "high-autonomy",
}
DIFFICULTIES = {"simple", "standard", "deep"}
STATUSES = {"PASS", "PARTIAL", "FAIL", "SKIPPED"}


def load_task_schema() -> dict:
    return json.loads((SCHEMA / "task.schema.json").read_text(encoding="utf-8"))


def validate_task(task: dict) -> list[str]:
    schema = load_task_schema()
    errors: list[str] = []
    required = schema.get("required", [])
    for key in required:
        if key not in task:
            errors.append(f"missing required field {key}")
    tid = task.get("id")
    if not isinstance(tid, str) or not ID_RE.match(tid):
        errors.append(f"invalid id: {tid!r}")
    cat = task.get("category")
    if cat not in CATEGORIES:
        errors.append(f"invalid category: {cat!r}")
    diff = task.get("difficulty")
    if diff not in DIFFICULTIES:
        errors.append(f"invalid difficulty: {diff!r}")
    prompt = task.get("natural_user_prompt")
    if not isinstance(prompt, str) or len(prompt) < 8:
        errors.append("natural_user_prompt too short")
    for name in (
        "preconditions",
        "expected_capabilities",
        "expected_tool_family",
        "forbidden_tools",
        "success_criteria",
        "weak_model_risks",
    ):
        if name in task and not isinstance(task[name], list):
            errors.append(f"{name} must be a list")
    if "max_tool_calls" in task and (not isinstance(task["max_tool_calls"], int) or task["max_tool_calls"] < 1):
        errors.append("max_tool_calls must be int >= 1")
    if "max_runtime_seconds" in task and (
        not isinstance(task["max_runtime_seconds"], int) or task["max_runtime_seconds"] < 1
    ):
        errors.append("max_runtime_seconds must be int >= 1")
    if "max_paid_cost_usd" in task and (
        not isinstance(task["max_paid_cost_usd"], (int, float)) or task["max_paid_cost_usd"] < 0
    ):
        errors.append("max_paid_cost_usd must be >= 0")
    if "deterministic_fallback_expected" in task and not isinstance(
        task["deterministic_fallback_expected"], bool
    ):
        errors.append("deterministic_fallback_expected must be bool")
    status = task.get("expected_mock_status")
    if status is not None and status not in STATUSES:
        errors.append(f"invalid expected_mock_status: {status!r}")
    allowed = set(schema.get("properties", {}))
    extra = set(task) - allowed
    if extra:
        errors.append(f"unknown fields: {sorted(extra)}")
    return errors


def export_public_task(task: dict) -> dict:
    schema = load_task_schema()
    allowed = set(schema.get("properties", {}))
    return {k: v for k, v in task.items() if k in allowed}


def dump_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
