"""Deterministic Local Computer routing. Weak model does not rediscover obvious maps."""

import re
from dataclasses import dataclass, field

from .plan import looks_like_computer

PATH = re.compile(r'(?:[A-Za-z]:\\|\\\\)[^\s"<>|*?]{3,240}')
MARKER = re.compile(r"ALEX_[A-Z0-9_]{4,}")
FILENAME = re.compile(r"\b([\w.\-]+\.(?:txt|md|json|py|log|csv))\b", re.I)


@dataclass(frozen=True)
class LocalDecision:
    action: str | None
    arguments: dict = field(default_factory=dict)
    reason: str = ""
    extra: tuple = ()


def quoted_path(prompt: str) -> str:
    match = re.search(r"[«\"']([^\"'«»]{3,240})[»\"']", prompt or "")
    if match and ("\\" in match.group(1) or "/" in match.group(1) or match.group(1).endswith(".txt")):
        return match.group(1).replace("/", "\\")
    found = PATH.search(prompt or "")
    return found.group(0) if found else ""


def filename(prompt: str) -> str:
    match = FILENAME.search(prompt or "")
    return match.group(1) if match else ""


def marker(prompt: str) -> str:
    match = MARKER.search(prompt or "")
    return match.group(0) if match else ""


def select_local_route(prompt: str, context=None) -> LocalDecision:
    text = prompt or ""
    if not looks_like_computer(text) and not re.search(
        r"(?i)прочитай файл|sha256|get_system_info|останови процесс|найди в этой папке", text
    ):
        return LocalDecision(None, {}, "not_local")
    scope = getattr(context, "task_scope", None)
    root = getattr(scope, "primary_root", "") if scope else ""
    settings = getattr(context, "settings", None) if context is not None else None
    roots = list(getattr(settings, "workspace_roots", None) or [])
    if not root and roots:
        root = roots[0]
    path = quoted_path(text)
    name = filename(text)
    target = path or (f"{root}\\{name}" if root and name else name)
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
    if re.search(r"(?i)sha256|посчитай.{0,24}хеш|hash", text) and target:
        return LocalDecision("hash_file", {"path": target, "purpose": "sha256"}, "hash_file")
    if re.search(r"(?i)windows|cpu|ram|свободное место|system info|информаци.{0,12}компьютер", text):
        return LocalDecision("get_system_info", {"purpose": "host summary"}, "system_info")
    if token and re.search(r"(?i)найди|find", text):
        search_root = root or path or (PATH.search(text).group(0) if PATH.search(text) else "")
        if search_root:
            return LocalDecision(
                "search_code",
                {"root": search_root, "query": token, "purpose": "find marker"},
                "search_marker",
            )
    if re.search(r"(?i)скопируй", text) and path:
        dest = filename(text) or "copy.txt"
        dest_path = dest if "\\" in dest else f"{root}\\{dest}" if root else dest
        return LocalDecision(
            "copy_file", {"source": path, "destination": dest_path, "purpose": "copy"}, "copy_file"
        )
    if re.search(r"(?i)перемест", text) and path:
        dest = filename(text) or path
        return LocalDecision(
            "move_file", {"source": path, "destination": dest, "purpose": "move"}, "move_file"
        )
    if re.search(r"(?i)создай.{0,24}папк|create.{0,24}folder|create_directory", text):
        folder = path or (f"{root}\\{name}" if root and name else target)
        if folder:
            extra = ()
            if name and re.search(r"(?i)файл|\.txt", text):
                extra = (
                    (
                        "write_file",
                        {"path": f"{folder}\\{name}" if "\\" not in name else name, "content": ""},
                    ),
                )
            return LocalDecision(
                "create_directory", {"path": folder, "purpose": "create folder"}, "create_directory", extra
            )
    if re.search(r"(?i)создай.{0,40}файл|write_file|запис", text) and (target or name):
        content = "GROUNDING_REAL_PASS" if "GROUNDING_REAL_PASS" in text else ""
        match = re.search(r"(?i)с текстом\s+(\S+)", text)
        if match:
            content = match.group(1).strip(" «»\"',.;:")
        path_write = target or (f"{root}\\{name}" if root and name else name)
        extra = (("read_file", {"path": path_write, "purpose": "verify write"}),)
        return LocalDecision(
            "write_file",
            {"path": path_write, "content": content, "purpose": "create file"},
            "write_file",
            extra,
        )
    if re.search(r"(?i)перечитай|прочитай|что внутри|что записано", text) and target:
        return LocalDecision("read_file", {"path": target, "purpose": "read requested file"}, "read_file")
    return LocalDecision(None, {}, "ambiguous_local")
