"""Task workspace scope and Alex-owned scratch. Desktop is not a dump."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ...data_paths import default_root
from ..contracts import ToolError
from .paths import longest_existing_path, native_path

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
    """The real, case-normalized path: junctions, symlinks and ``..`` are resolved.

    The workspace boundary must compare what the filesystem will actually touch, not the
    spelling of the argument. A link inside the allowed directory that points elsewhere resolves
    outside the real root and is refused.
    """
    value = native_path(path).strip()
    if not value:
        return ""
    try:
        value = os.path.realpath(value)
    except OSError:
        pass
    extended = os.sep * 2 + "?" + os.sep
    if value.startswith(extended):
        value = value[len(extended) :]
    return os.path.normcase(os.path.normpath(value))


def _inside(path: str, root: str) -> bool:
    if not path or not root:
        return False
    child, base = _norm(path), _norm(root)
    return child == base or child.startswith(base.rstrip(os.sep) + os.sep)


def scratch_dir(task_id: str, *, create: bool = True) -> str:
    if not task_id or task_id == "pending":
        return ""
    path = default_root() / "tasks" / task_id / "tmp"
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


def _longest_existing_path(text: str, start: int) -> str:
    """Kept as the module-local name; the implementation is shared with the target resolver."""
    return longest_existing_path(text, start)


def infer_primary(prompt: str, roots: list[str], known_desktop: str = "") -> str:
    text = prompt or ""
    # A quoted path is unambiguous even when it contains spaces.
    quoted = re.search(r"[\"«']([A-Za-z]:\\[^\"»']{2,240})[\"»']", text)
    if quoted:
        value = quoted.group(1).rstrip(".,;")
        path = Path(value)
        if path.exists():
            return str(path.parent) if path.is_file() else str(path)
        return str(path.parent) if path.suffix else value
    # Prefer the longest prefix that exists. This keeps directory names with spaces intact
    # without guessing where the path ends in free-form text.
    for match in re.finditer(r"[A-Za-z]:\\", text):
        value = _longest_existing_path(text, match.start())
        if value:
            path = Path(value)
            return str(path.parent) if path.is_file() else value
    match = re.search(r"([A-Za-z]:\\[^\s\"«»]{3,200})", text)
    if match:
        value = match.group(1).rstrip(".,;")
        path = Path(value)
        if path.exists():
            return str(path.parent) if path.is_file() else str(path)
        if path.suffix:
            return str(path.parent)
        return value
    for root in roots or []:
        name = Path(native_path(root)).name
        if name and name.casefold() in text.casefold():
            return root
    folder = re.search(r"(Alex-LLM-E2E[\\/\w.\-]*)", text, re.I)
    if folder and known_desktop:
        return str(Path(known_desktop) / native_path(folder.group(1)))
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
    if name in {"run_python", "run_process", "run_powershell"}:
        # A script passed by absolute path is a workspace action even when cwd is inside the
        # task directory; it must resolve inside an allowed root too.
        for item in (arguments or {}).get("argv") or []:
            text = str(item)
            if not os.path.isabs(text):
                continue
            if any(_inside(text, root) for root in scope.allowed_roots):
                continue
            if scope.write_outside_scope:
                continue
            raise ToolError("workspace_scope")
