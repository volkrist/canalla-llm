"""Task workspace scope and Alex-owned scratch. Desktop is not a dump."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..contracts import ToolError

WRITE_TOOLS = {
    "write_file",
    "create_directory",
    "copy_file",
    "move_file",
    "delete_file",
    "delete_directory",
    "patch_file",
    "run_python",
    "run_powershell",
    "run_process",
}

HELPER_NAME = re.compile(r"(?i)^(alex_out|alex_test_folders|__listdir)")


@dataclass
class TaskScope:
    primary_root: str = ""
    allowed_roots: list[str] = field(default_factory=list)
    read_outside_scope: bool = False
    write_outside_scope: bool = False
    scratch: str = ""


def _norm(path: str) -> str:
    return (path or "").replace("/", "\\").rstrip("\\").casefold()


def _inside(path: str, root: str) -> bool:
    if not path or not root:
        return False
    child, base = _norm(path), _norm(root)
    return child == base or child.startswith(base + "\\")


def scratch_dir(task_id: str, *, create: bool = True) -> str:
    if not task_id or task_id == "pending":
        return ""
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(local) / "Alex LLM" / "tasks" / task_id / "tmp"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return str(path)


def cleanup_scratch(task_id: str):
    path = Path(scratch_dir(task_id, create=False))
    if not path or not path.exists():
        return
    for item in path.rglob("*"):
        if item.is_file():
            try:
                item.unlink()
            except OSError:
                pass


def infer_primary(prompt: str, roots: list[str], known_desktop: str = "") -> str:
    text = prompt or ""
    match = re.search(r"([A-Za-z]:\\[^\s\"«»]{3,200})", text)
    if match:
        value = match.group(1).rstrip(".,;")
        if Path(value).suffix:
            return str(Path(value).parent)
        return value
    for root in roots or []:
        name = Path(root).name
        if name and name.casefold() in text.casefold():
            return root
    folder = re.search(r"(Alex-LLM-E2E[\\/\w.\-]*)", text, re.I)
    if folder and known_desktop:
        return str(Path(known_desktop) / folder.group(1).replace("/", "\\"))
    if re.search(r"(?i)этой папке|this folder", text) and roots:
        return roots[0]
    return (roots or [""])[0]


def build_scope(prompt: str, roots: list[str], task_id: str, known_desktop: str = "") -> TaskScope:
    primary = infer_primary(prompt, roots, known_desktop)
    scratch = scratch_dir(task_id) if task_id and task_id != "pending" else ""
    allowed = [item for item in [primary, scratch, *(roots or [])] if item]
    return TaskScope(primary_root=primary, allowed_roots=allowed, scratch=scratch)


def assert_scope(name: str, arguments: dict, scope: TaskScope | None):
    if scope is None or not scope.primary_root:
        return
    paths = []
    for key in ("path", "destination", "cwd", "root"):
        value = (arguments or {}).get(key)
        if value:
            paths.append(str(value))
    if name == "copy_file":
        paths = [str((arguments or {}).get("destination") or "")]
    for path in paths:
        if HELPER_NAME.search(Path(path).name or ""):
            if not _inside(path, scope.scratch):
                raise ToolError("workspace_scope")
        if name not in WRITE_TOOLS:
            continue
        if any(_inside(path, root) for root in scope.allowed_roots):
            continue
        if scope.write_outside_scope:
            continue
        raise ToolError("workspace_scope")
