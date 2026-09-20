"""Cleanup and leftover detection. Never deletes user files."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
import subprocess

from paths import WORK

PROTECTED_PREFIXES = [
    str(Path.home() / "Documents"),
    str(Path.home() / "Desktop" / "резюме"),
    str(Path.home() / "Desktop" / "Alex-LLM"),
]


def _under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _is_protected(path: Path) -> bool:
    if _under(path, WORK):
        return False
    text = str(path.resolve())
    return any(text.startswith(prefix) for prefix in PROTECTED_PREFIXES)


def stop_owned_processes(state: dict) -> list[str]:
    stopped = []
    for pid in list(state.get("owned_pids") or []):
        try:
            subprocess.run(["taskkill", "/PID", str(int(pid)), "/F"], capture_output=True, check=False)
            stopped.append(str(pid))
        except Exception:
            try:
                os.kill(int(pid), 9)
                stopped.append(str(pid))
            except OSError:
                pass
    return stopped


def leftover_scan(state: dict, workspace: Path | None) -> list[str]:
    leftovers: list[str] = []
    for pid in state.get("owned_pids") or []:
        try:
            os.kill(int(pid), 0)
            leftovers.append(f"owned_pid:{pid}")
        except OSError:
            pass
    for rel in state.get("workspace_violation_paths") or []:
        path = Path(rel)
        if path.exists() and _under(path, WORK):
            leftovers.append(f"violation:{rel}")
    return leftovers


def cleanup_task(task: dict, state: dict, *, keep_workspace: bool = False) -> dict:
    stop_owned_processes(state)
    workspace = Path(state["workspace"]) if state.get("workspace") else None
    leftovers_before = leftover_scan(state, workspace)
    deleted = []
    if workspace and workspace.exists() and not keep_workspace:
        resolved = workspace.resolve()
        if not _under(resolved, WORK):
            return {
                "status": "REFUSED",
                "reason": f"workspace is not under evaluation/.work: {resolved}",
                "leftovers": leftovers_before,
            }
        if _is_protected(resolved):
            return {
                "status": "REFUSED",
                "reason": f"refused to delete protected path {resolved}",
                "leftovers": leftovers_before,
            }
        shutil.rmtree(resolved, ignore_errors=False)
        deleted.append(str(resolved))
    leftovers = leftover_scan(state, None)
    status = "CLEAN" if not leftovers else "LEFTOVERS"
    return {
        "status": status,
        "deleted": deleted,
        "leftovers": leftovers,
        "preserve_user_files": True,
        "strategy": (task.get("cleanup") or {}).get("strategy"),
    }


def cleanup_run(run_id: str) -> None:
    path = WORK / run_id
    if path.exists() and _under(path, WORK):
        shutil.rmtree(path, ignore_errors=True)
