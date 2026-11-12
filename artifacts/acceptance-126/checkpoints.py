"""The named checkpoints the owner asked for, in one atomically replaced file.

A checkpoint is not a log line: it answers "what is true right now, what proved it, what is next
and what is blocking". The coordinator records one at every transition it owns, and anything that
restarts reads them instead of guessing.

    python artifacts/acceptance-126/checkpoints.py status
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FILE = HERE / "checkpoints.json"
STATE = HERE / "coordinator" / "state.json"

# The order the owner asked for. A checkpoint may be refreshed; it is never silently dropped.
NAMES = (
    "BASELINE",
    "CURRENT_MONITOR_STATE",
    "T3_LIVE_PASS_PRESERVED",
    "T4_FREE_READY",
    "GPU_CAPACITY_MONITORING",
    "GPU_ACQUIRED",
    "T4_LIVE_RUNNING",
    "T4_LIVE_VERDICT",
    "OPTIONAL_AUTONOMY_LIVE_VERDICT",
    "GPU_CLEANUP",
    "BUDGET_RECONCILED",
    "DATA_SAFE",
    "DEVICE_STABLE",
    "RELEASE_READINESS",
    "FINAL_CI",
    "SIGNATURE",
    "INSTALLED_SMOKE",
    "GITHUB_RELEASE",
    "UPDATER",
    "POST_RELEASE_VERIFY",
    "NEXT_AUTONOMY_DEVELOPMENT",
)


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO),
            capture_output=True, timeout=60,
        ).stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _state() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def load() -> dict:
    if not FILE.exists():
        return {"schema": 1, "checkpoints": {}}
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except ValueError:
        return {"schema": 1, "checkpoints": {}}


def record(name: str, *, status: str, evidence: str = "", next_step: str = "",
           blocker: str = "", extra: dict | None = None) -> dict:
    """Write one checkpoint with everything needed to resume from it."""
    if name not in NAMES:
        raise ValueError(f"unknown checkpoint {name!r}")
    state = _state()
    payload = load()
    attempts = state.get("attempts") or []
    last = attempts[-1] if attempts else {}
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_head(),
        "status": status,
        "task_id": (extra or {}).get("task_id") or last.get("task_id"),
        "pod_id": (extra or {}).get("pod_id") or last.get("pod_id"),
        "pod_created_total": state.get("pods_created"),
        "spent_usd": round(sum(float(a.get("cost_usd") or 0) for a in attempts), 4),
        "evidence": evidence,
        "next_step": next_step,
        "blocker": blocker,
        "detail": extra or {},
    }
    payload.setdefault("checkpoints", {})[name] = entry
    payload["updated_at"] = entry["at"]
    payload["order"] = list(NAMES)
    tmp = FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, FILE)
    return entry


def status() -> int:
    payload = load()
    entries = payload.get("checkpoints") or {}
    for name in NAMES:
        entry = entries.get(name)
        if not entry:
            print(f"[{'':9s}] {name}")
            continue
        print(f"[{entry['status']:9s}] {name} — {entry['at'][:19]} "
              f"sha {entry.get('git_sha') or '?'} · {entry.get('blocker') or entry.get('next_step') or ''}")
    print(f"\n{len(entries)} recorded; file {FILE}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "status":
        return status()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
