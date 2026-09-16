"""Workspace path policy. Host still canonicalizes on Windows; this is the backend contract."""

from pathlib import PureWindowsPath

from ..contracts import ToolError
from .secrets import deny_secret

UNC = ("\\\\", "//")


def normalize_preview(path: str) -> str:
    return str(path or "").replace("/", "\\").strip()


def assert_allowed_path(path: str, roots: list[str]):
    value = normalize_preview(path)
    if not value or value.startswith(UNC) or "\x00" in value:
        raise ToolError("path_denied")
    if any(part == ".." for part in PureWindowsPath(value).parts):
        raise ToolError("path_denied")
    deny_secret(value)
    allowed = False
    candidate = PureWindowsPath(value)
    for root in roots:
        base = PureWindowsPath(normalize_preview(root))
        try:
            candidate.relative_to(base)
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        raise ToolError("path_denied")
    return value
