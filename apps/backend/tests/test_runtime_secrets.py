"""A17 — Canalla's own runtime secrets are out of reach of the Computer tools.

`jwt.secret`, `shutdown.token`, the session/installation ids, the device record and the DPAPI
fallbacks all live in a directory the signed-in user can read with the OS. The path guard must
refuse them anyway, and any secret that does reach a sanitizer must be scrubbed uniformly.
"""

import pytest

from app.tools.contracts import ToolError
from app.tools.local.secrets import deny_secret
from app.tools.security import sanitized

WINDOWS_ROOT = r"C:\Users\me\AppData\Local\Alex LLM"
POSIX_ROOT = "/home/me/.local/share/alex-llm"


def test_runtime_secret_paths_are_denied() -> None:
    for path in (
        WINDOWS_ROOT + r"\runtime\jwt.secret",
        WINDOWS_ROOT + r"\runtime\shutdown.token",
        WINDOWS_ROOT + r"\runtime\install.id",
        WINDOWS_ROOT + r"\runtime\session.id",
        WINDOWS_ROOT + r"\runtime\host-journal.json",
        WINDOWS_ROOT + r"\runtime\migration.json",
        WINDOWS_ROOT + r"\device.json",
        WINDOWS_ROOT + r"\device.cred.dpapi",
        POSIX_ROOT + "/runtime/jwt.secret",
        POSIX_ROOT + "/runtime/cred-session-abc.dpapi",
    ):
        with pytest.raises(ToolError):
            deny_secret(path)


def test_runtime_directory_is_denied_even_for_unlisted_names() -> None:
    # The runtime directory itself is a secret boundary, not just the known file names.
    with pytest.raises(ToolError):
        deny_secret(WINDOWS_ROOT + r"\runtime\something-nobody-listed.bin")
    with pytest.raises(ToolError):
        deny_secret(POSIX_ROOT + "/runtime/something-nobody-listed.bin")


def test_ordinary_files_are_still_allowed() -> None:
    deny_secret(r"C:\Users\me\notes.txt")
    deny_secret("/home/me/projects/readme.md")
    deny_secret(WINDOWS_ROOT + r"\documents\report.docx")


def test_a_canary_secret_is_scrubbed_from_text_and_outputs() -> None:
    canary = "CANARY-jwt-secret-value-0123456789abcdef"
    leaked = f"stdout line\nAuthorization: Bearer abc\nvalue={canary}\n"
    cleaned = sanitized(leaked, [canary])
    assert canary not in cleaned
    assert "[redacted]" in cleaned
    # The same scrub applies when only the generic patterns catch it (no explicit secret list).
    patterned = sanitized(f"password={canary}")
    assert canary not in patterned
