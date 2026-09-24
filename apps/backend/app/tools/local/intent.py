"""Deterministic Local Computer routing. Weak model does not rediscover obvious maps."""

import os
import re
from dataclasses import dataclass, field

from .paths import native_path
from .plan import looks_like_computer
from .targets import extract_absolute_paths, extract_filename, last_file_target, resolve_target

PATH = re.compile(r'(?:[A-Za-z]:\\|\\\\)[^\s"<>|*?]{3,240}')
MARKER = re.compile(r"ALEX_[A-Z0-9_]{4,}")
CONTENT_PHRASE = re.compile(
    r"(?i)(?:"
    r"с текстом|with(?: the)? text|containing|содерж(?:ащ(?:им|ий|ее)?|ит)"
    r")\s+(?:[«\"']([^\"'«»]+)[»\"']|(\S+))"
)


@dataclass(frozen=True)
class LocalDecision:
    action: str | None
    arguments: dict = field(default_factory=dict)
    reason: str = ""
    extra: tuple = ()


def quoted_path(prompt: str) -> str:
    match = re.search(r"[«\"']([^\"'«»]{3,240})[»\"']", prompt or "")
    if match and (
        "\\" in match.group(1) or "/" in match.group(1) or re.search(r"\.\w{1,8}$", match.group(1))
    ):
        return native_path(match.group(1))
    files = [
        item
        for item in extract_absolute_paths(prompt)
        if extract_filename(item) or "." in native_path(item).rsplit(os.sep, 1)[-1]
    ]
    if files:
        return files[-1]
    return ""


def filename(prompt: str) -> str:
    return extract_filename(prompt)


def marker(prompt: str) -> str:
    match = MARKER.search(prompt or "")
    return match.group(0) if match else ""


def requested_file_content(prompt: str) -> str:
    text = prompt or ""
    match = CONTENT_PHRASE.search(text)
    if match:
        value = (match.group(1) or match.group(2) or "").strip(" «»\"',.;:")
        if value and not re.search(r"[\\/]", value):
            return value
    token = marker(text)
    if token:
        return token
    for match in re.finditer(r"[«\"']([^\"'«»]{1,400})[»\"']", text):
        value = match.group(1).strip()
        if value and not re.search(r"[\\/]|\.txt$", value, re.I) and " " not in value:
            return value
    return ""


def _join(root: str, name: str) -> str:
    if not name:
        return root or ""
    if re.match(r"(?:[A-Za-z]:[\\/]|\\\\|/)", name):
        return native_path(name)
    if root:
        return os.path.join(native_path(root).rstrip(os.sep), native_path(name))
    return name


def select_local_route(prompt: str, context=None) -> LocalDecision:
    text = prompt or ""
    if not looks_like_computer(text) and not re.search(
        r"(?i)прочитай файл|прочитай .{0,80}\.\w{1,8}|read .{0,80}\.\w{1,8}|"
        r"создай .{0,80}\.\w{1,8}|create .{0,40}file|write .{0,20}file|"
        r"with(?: the)? text|с текстом|sha256|get_system_info|останови процесс|"
        r"найди в этой папке|перечитай|hash .{0,80}\.\w{1,8}|посчитай.{0,24}хеш",
        text,
    ):
        return LocalDecision(None, {}, "not_local")
    scope = getattr(context, "task_scope", None)
    root = getattr(scope, "primary_root", "") if scope else ""
    settings = getattr(context, "settings", None) if context is not None else None
    roots = list(getattr(settings, "workspace_roots", None) or [])
    if not root and roots:
        root = roots[0]
    previous = last_file_target(getattr(context, "verified_facts", None)) or getattr(
        context, "last_file_target", None
    )
    resolved = resolve_target(
        text,
        workspace_root=root,
        roots=roots,
        previous_file=previous,
        expect="file",
    )
    path = quoted_path(text)
    name = filename(text)
    target = resolved.path if resolved.kind == "file" else (path or (_join(root, name) if name else ""))
    if resolved.kind == "file":
        target = resolved.path
        name = resolved.target_file or name
    token = marker(text)
    if re.search(r"(?i)останови процесс|stop (the )?process", text):
        owned = getattr(context, "owned_process", None) if context is not None else None
        run_id = (owned or {}).get("tool_run_id") if isinstance(owned, dict) else None
        if run_id:
            return LocalDecision(
                "stop_process", {"tool_run_id": run_id, "purpose": "stop owned process"}, "owned_process_stop"
            )
        return LocalDecision(None, {}, "owned_process_unknown")
    if re.search(r"(?i)запусти.{0,40}(процесс|python|sleep)|start-sleep", text):
        return LocalDecision(
            "run_python",
            {
                "argv": ["-c", "import time; time.sleep(30)"],
                "timeout_seconds": 35,
                "wait": False,
                "purpose": "start harmless owned process",
            },
            "start_harmless_process",
        )
    if re.search(r"(?i)sha256|посчитай.{0,24}хеш|hash", text) and resolved.kind == "file":
        return LocalDecision("hash_file", {"path": resolved.path, "purpose": "sha256"}, "hash_file")
    if re.search(r"(?i)windows|cpu|ram|свободное место|system info|информаци.{0,12}компьютер", text):
        return LocalDecision("get_system_info", {"purpose": "host summary"}, "system_info")
    if token and re.search(r"(?i)найди|find", text):
        match = PATH.search(text)
        search_root = root or path or (match.group(0) if match else "")
        if search_root:
            return LocalDecision(
                "search_code",
                {"root": search_root, "query": token, "purpose": "find marker"},
                "search_marker",
            )
    if re.search(r"(?i)скопируй", text) and path:
        dest = filename(text) or "copy.txt"
        dest_path = dest if "\\" in dest or "/" in dest else _join(root, dest)
        return LocalDecision(
            "copy_file", {"source": path, "destination": dest_path, "purpose": "copy"}, "copy_file"
        )
    if re.search(r"(?i)перемест", text) and path:
        dest = filename(text) or path
        return LocalDecision(
            "move_file", {"source": path, "destination": dest, "purpose": "move"}, "move_file"
        )
    creating_file = bool(
        re.search(
            r"(?i)создай.{0,80}файл|write_file|запис.{0,40}файл|create.{0,40}file|с текстом|with(?: the)? text",
            text,
        )
        and (resolved.kind == "file" or name)
    )
    if creating_file:
        content = requested_file_content(text)
        path_write = resolved.path if resolved.kind == "file" else _join(root, name)
        extra = (("read_file", {"path": path_write, "purpose": "verify write"}),)
        return LocalDecision(
            "write_file",
            {"path": path_write, "content": content, "purpose": "create file"},
            "write_file",
            extra,
        )
    if re.search(r"(?i)создай.{0,24}папк|create.{0,24}folder|create_directory", text) and not re.search(
        r"(?i)файл|\.txt", text
    ):
        folder = path or (_join(root, name) if name else target)
        if folder:
            return LocalDecision(
                "create_directory", {"path": folder, "purpose": "create folder"}, "create_directory"
            )
    if re.search(r"(?i)установи|install .{0,12}(jq|winget)|winget install", text):
        package = "jqlang.jq" if re.search(r"(?i)\bjq\b", text) else "JanDeDobbeleer.OhMyPosh"
        return LocalDecision(
            "install_software",
            {"package": package, "purpose": "install requested package"},
            "install_software",
        )
    if re.search(r"(?i)перечитай|прочитай|что внутри|что записано|read (the )?file", text) and (
        resolved.kind == "file" or (previous and re.search(r"(?i)перечитай|re-?read", text))
    ):
        read_path = resolved.path if resolved.kind == "file" else previous
        if read_path:
            return LocalDecision(
                "read_file", {"path": read_path, "purpose": "read requested file"}, "read_file"
            )
    if re.search(r"(?i)удали(ть)? файл|delete (the )?file", text) and resolved.kind == "file":
        return LocalDecision(
            "delete_file", {"path": resolved.path, "purpose": "delete requested file"}, "delete_file"
        )
    return LocalDecision(None, {}, "ambiguous_local")
