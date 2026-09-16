"""Local computer path policy. Trusted roots reduce confirmations; they are not a jail."""

import re
from pathlib import PureWindowsPath

from ..contracts import ToolError
from .secrets import deny_secret

UNC = ("\\\\", "//")
DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def normalize_preview(path: str) -> str:
    return str(path or "").replace("/", "\\").strip()


def assert_local_path(path: str) -> str:
    value = normalize_preview(path)
    if not value or value.startswith(UNC) or "\x00" in value:
        raise ToolError("path_denied")
    if any(part == ".." for part in PureWindowsPath(value).parts):
        raise ToolError("path_denied")
    if not DRIVE.match(value):
        raise ToolError("path_denied")
    deny_secret(value)
    return value


def inside_trusted(path: str, roots: list[str]) -> bool:
    value = normalize_preview(path)
    if not value or not roots:
        return False
    candidate = PureWindowsPath(value)
    for root in roots:
        base = PureWindowsPath(normalize_preview(root))
        if not str(base):
            continue
        try:
            candidate.relative_to(base)
            return True
        except ValueError:
            continue
    return False


def assert_allowed_path(path: str, roots: list[str] | None = None):
    """Full local computer access. `roots` is ignored for allow/deny."""
    del roots
    return assert_local_path(path)
