"""File vs directory target resolution. One contract for local file tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from ..contracts import ToolError

FILE_OPS = {"read_file", "write_file", "hash_file", "patch_file", "delete_file"}
FILE_EXT = (
    r"txt|md|json|py|js|ts|jsx|tsx|toml|rs|go|log|csv|html|css|xml|yml|yaml|ini|cfg|"
    r"c|h|cpp|hpp|java|kt|rb|php|sh|ps1|bat|sql"
)
FILENAME = re.compile(rf"(?ix)\b((?:[\w.\-]+[\\/])*[\w.\-]+\.(?:{FILE_EXT}))\b")
ABSOLUTE = re.compile(r'(?:[A-Za-z]:\\|\\\\)[^\s"<>|*?]{1,240}')
QUOTED = re.compile(r"[«\"']([^\"'«»]{1,240})[»\"']")
REREAD = re.compile(r"(?i)перечитай|re-?read|заново прочит|ещё раз прочит|read (the )?file again")
CREATE = re.compile(
    r"(?i)создай.{0,80}файл|write_file|запис.{0,40}файл|create.{0,40}file|с текстом|with(?: the)? text"
)


def _norm(path: str) -> str:
    return (path or "").replace("/", "\\").rstrip("\\").casefold()


def _join(root: str, name: str) -> str:
    if not name:
        return root or ""
    cleaned = name.replace("/", "\\").strip("\\")
    if re.match(r"(?:[A-Za-z]:\\|\\\\)", cleaned):
        return cleaned
    if root:
        return str(PureWindowsPath(root.rstrip("\\/")) / cleaned)
    return cleaned


def extract_filename(prompt: str) -> str:
    text = prompt or ""
    for match in QUOTED.finditer(text):
        value = match.group(1).strip()
        found = FILENAME.search(value)
        if found:
            return found.group(1).replace("/", "\\")
        if re.search(rf"(?i)\.({FILE_EXT})$", value) and not re.match(r"(?:[A-Za-z]:\\|\\\\)", value):
            return value.replace("/", "\\")
    matches = [item.group(1).replace("/", "\\") for item in FILENAME.finditer(text)]
    if not matches:
        return ""
    relative = [item for item in matches if "\\" in item]
    return relative[-1] if relative else matches[-1]


def extract_absolute_paths(prompt: str) -> list[str]:
    found = []
    for match in ABSOLUTE.finditer(prompt or ""):
        value = match.group(0).rstrip(".,;:)")
        if value not in found:
            found.append(value)
    for match in QUOTED.finditer(prompt or ""):
        value = match.group(1).strip()
        if re.match(r"(?:[A-Za-z]:\\|\\\\)", value) and value not in found:
            found.append(value.replace("/", "\\"))
    return found


def is_directory_path(path: str, roots: list[str] | None = None) -> bool:
    value = (path or "").replace("/", "\\").strip()
    if not value:
        return False
    if value.endswith("\\") or value.endswith("/"):
        return True
    try:
        if Path(value).is_dir():
            return True
    except OSError:
        pass
    needle = _norm(value)
    for root in roots or []:
        if needle == _norm(root):
            return True
    return False


def looks_like_file_path(path: str, roots: list[str] | None = None) -> bool:
    value = (path or "").replace("/", "\\").strip()
    if not value or is_directory_path(value, roots):
        return False
    name = PureWindowsPath(value).name
    return bool(name and FILENAME.fullmatch(name))


@dataclass(frozen=True)
class TargetPath:
    workspace_root: str = ""
    target_directory: str = ""
    target_file: str | None = None
    explicit_path: str | None = None
    inferred_filename: str | None = None
    kind: str = "unknown"
    source: str = "inferred"
    path: str = ""


def last_file_target(facts: dict | None) -> str:
    from .facts import latest, store

    for kind in ("FILE_TARGET", "FILE_READ", "FILE_WRITTEN", "FILE_CREATED", "HASH_RESULT"):
        item = latest(facts or {}, kind)
        path = str((item or {}).get("path") or "")
        if path and looks_like_file_path(path):
            return path
    for item in reversed(store(facts or {})):
        path = str(item.get("path") or "")
        if path and looks_like_file_path(path):
            return path
    return ""


def resolve_target(
    prompt: str,
    *,
    workspace_root: str = "",
    roots: list[str] | None = None,
    previous_file: str | None = None,
    offered_path: str | None = None,
    expect: str = "file",
) -> TargetPath:
    roots = list(roots or ([] if not workspace_root else [workspace_root]))
    root = workspace_root or (roots[0] if roots else "")
    filename = extract_filename(prompt)
    text = prompt or ""
    lowered = text.replace("/", "\\")
    absolutes = extract_absolute_paths(text)
    for item in roots:
        if item and _norm(item) in _norm(lowered) and item not in absolutes:
            absolutes.append(item)
    offered = (offered_path or "").replace("/", "\\").strip()
    file_abs = [item for item in absolutes if looks_like_file_path(item, roots)]
    dir_abs = [item for item in absolutes if is_directory_path(item, roots)]
    directory = root
    for item in dir_abs:
        directory = item
        break
    if offered and is_directory_path(offered, roots):
        directory = offered
    explicit = None
    source = "inferred"
    if offered and looks_like_file_path(offered, roots):
        explicit = offered
        source = "explicit"
        filename = filename or PureWindowsPath(offered).name
    elif file_abs:
        explicit = file_abs[-1]
        source = "explicit"
        filename = filename or PureWindowsPath(explicit).name
    elif filename:
        explicit = _join(directory or root, filename)
        source = "inferred"
    elif (
        previous_file
        and looks_like_file_path(previous_file, roots)
        and (
            REREAD.search(text)
            or (
                expect == "file"
                and not CREATE.search(text)
                and (not offered or is_directory_path(offered, roots))
            )
        )
    ):
        explicit = previous_file
        source = "previous_action"
        filename = PureWindowsPath(previous_file).name
    kind = "file" if explicit and looks_like_file_path(explicit, roots) else "unknown"
    if explicit and is_directory_path(explicit, roots):
        kind = "directory"
    if expect == "file" and kind != "file" and filename:
        explicit = _join(directory or root, filename)
        kind = "file" if looks_like_file_path(explicit, roots) else kind
        source = "inferred"
    if expect == "file" and (not explicit or is_directory_path(explicit, roots)):
        kind = (
            "directory"
            if (explicit and is_directory_path(explicit, roots))
            or (directory and is_directory_path(directory, roots))
            else "unknown"
        )
        explicit = explicit or directory or root
    return TargetPath(
        workspace_root=root,
        target_directory=directory or root,
        target_file=filename,
        explicit_path=explicit,
        inferred_filename=filename,
        kind=kind,
        source=source,
        path=explicit or directory or root,
    )


def coerce_file_argument(
    tool: str,
    path: str,
    prompt: str,
    *,
    workspace_root: str = "",
    roots: list[str] | None = None,
    previous_file: str | None = None,
) -> str:
    if tool not in FILE_OPS:
        return path
    resolved = resolve_target(
        prompt,
        workspace_root=workspace_root,
        roots=roots,
        previous_file=previous_file,
        offered_path=path,
        expect="file",
    )
    if resolved.kind == "file" and resolved.path and not is_directory_path(resolved.path, roots):
        return resolved.path
    if is_directory_path(path, roots):
        raise ToolError("target_is_directory")
    if resolved.kind == "directory" and is_directory_path(path or resolved.path, roots):
        raise ToolError("target_is_directory")
    return path or resolved.path
