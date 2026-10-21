"""Local computer path policy. Trusted roots reduce confirmations; they are not a jail."""

import os
import re
from pathlib import PurePath

from ..contracts import ToolError
from .secrets import deny_secret

UNC = ("\\\\", "//")
DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def native_path(path: str | None) -> str:
    """Separators as this platform writes them: "\\" on Windows, "/" on POSIX. `None` and an
    empty value are the empty path, which is what every caller already relied on."""
    return str(path or "").replace("\\", os.sep).replace("/", os.sep)


def normalize_preview(path: str) -> str:
    return native_path(str(path or "").strip())


def longest_existing_path(text: str, start: int) -> str:
    """The longest on-disk path beginning at ``start``, spaces included.

    A regex stops at the first whitespace, so ``C:\\Users\\...\\Desktop\\Canalla E2E 1.2.3\\a.txt``
    became ``C:\\Users\\...\\Desktop\\Canalla`` and the rest of the name was treated as prose. The
    filesystem is the only authority for where a real path ends, so the path is grown from the
    drive letter until it no longer exists.
    """
    if not text or start < 0 or start >= len(text):
        return ""
    tail = text[start:]
    for end in range(len(tail), 2, -1):
        candidate = tail[:end].rstrip(" .,;:\t" + os.sep + "/")
        if len(candidate) == 2 and candidate[1] == ":":
            candidate += os.sep
        if not candidate:
            continue
        try:
            if os.path.exists(candidate):
                return candidate
        except (OSError, ValueError):
            continue
    return ""


def assert_local_path(path: str) -> str:
    value = normalize_preview(path)
    if not value or value.startswith(UNC) or "\x00" in value:
        raise ToolError("path_denied")
    if any(part == ".." for part in PurePath(value).parts):
        raise ToolError("path_denied")
    if not (PurePath(value).is_absolute() or DRIVE.match(value)):
        raise ToolError("path_denied")
    deny_secret(value)
    return value


def inside_trusted(path: str, roots: list[str]) -> bool:
    value = normalize_preview(path)
    if not value or not roots:
        return False
    candidate = PurePath(value)
    for root in roots:
        base = PurePath(normalize_preview(root))
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
