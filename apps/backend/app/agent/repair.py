"""The fix loop's memory: error fingerprints, attempts and a no-progress stop.

A repair loop without a ledger repeats the same patch forever. This one names the failure by a
fingerprint that ignores what changes between runs (tmp paths, line numbers, hex ids), records the
hypothesis and the files each attempt touched, and can say "we are not converging" *before* the
budget runs out — with the exact evidence that the last attempts changed nothing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

WINDOWS_PATH = re.compile(r"[A-Za-z]:\\[^\s'\"]+")
POSIX_PATH = re.compile(r"/(?:[\w.-]+/)+[\w.-]+")
ADDRESS = re.compile(r"0x[0-9a-fA-F]+")
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
NUMBER = re.compile(r"\b\d+\b")
LINE = re.compile(r"(?i)\bline\s+\d+")

ERROR_CLASSES: tuple[tuple[str, str], ...] = (
    (r"(?i)traceback|exception|error:", "python_exception"),
    (r"(?i)module not found|no module named|cannot find module", "missing_dependency"),
    (r"(?i)permission denied|access is denied|eacces", "permission"),
    (r"(?i)address already in use|eaddrinuse|port is already", "port_in_use"),
    (r"(?i)assertionerror|assert .* failed|tests? failed", "test_failure"),
    (r"(?i)database is locked|database_busy|sqlite_busy", "database_busy"),
    (r"(?i)timeout|timed out", "timeout"),
    (r"(?i)not found|404", "not_found"),
    (r"(?i)syntax error|indentationerror", "syntax"),
    (r"(?i)cannot find module|err_module_not_found", "missing_dependency"),
    (r"(?i)enoent", "missing_file"),
)


def classify(error_text: str) -> str:
    for pattern, name in ERROR_CLASSES:
        if re.search(pattern, error_text or ""):
            return name
    return "unknown"


def fingerprint(error_text: str) -> str:
    """What is *the same* about two occurrences: kind, message shape, location-free."""
    text = (error_text or "").strip()
    for pattern in (WINDOWS_PATH, POSIX_PATH, ADDRESS, UUID, LINE):
        text = pattern.sub("<x>", text)
    text = NUMBER.sub("<n>", text)
    text = re.sub(r"\s+", " ", text)[:600]
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass
class Attempt:
    fingerprint: str
    error_class: str
    hypothesis: str = ""
    files: list[str] = field(default_factory=list)
    command: str = ""
    outcome: str = ""
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def public(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "error_class": self.error_class,
            "hypothesis": self.hypothesis[:300],
            "files": self.files[:10],
            "command": self.command[:200],
            "outcome": self.outcome[:300],
            "at": self.at,
        }


class RepairLedger:
    def __init__(self, attempts: Iterable[Attempt] | None = None, max_attempts: int = 3):
        self.attempts: list[Attempt] = list(attempts or ())
        self.max_attempts = max(1, int(max_attempts))

    def record(
        self,
        error_text: str,
        *,
        hypothesis: str = "",
        files: Iterable[str] | None = None,
        command: str = "",
        outcome: str = "",
    ) -> Attempt:
        attempt = Attempt(
            fingerprint=fingerprint(error_text),
            error_class=classify(error_text),
            hypothesis=hypothesis,
            files=[str(item) for item in (files or ())][:10],
            command=command,
            outcome=outcome,
        )
        self.attempts.append(attempt)
        return attempt

    def same_fingerprint(self, error_text: str) -> int:
        wanted = fingerprint(error_text)
        return sum(1 for attempt in self.attempts if attempt.fingerprint == wanted)

    def stalled(self) -> bool:
        """The last `max_attempts` attempts share a fingerprint and touched no file."""
        if len(self.attempts) < self.max_attempts:
            return False
        tail = self.attempts[-self.max_attempts :]
        if len({attempt.fingerprint for attempt in tail}) != 1:
            return False
        return not any(attempt.files for attempt in tail)

    def advise(self, error_text: str = "") -> dict[str, Any]:
        repeats = self.same_fingerprint(error_text) if error_text else len(self.attempts)
        if self.stalled():
            return {
                "action": "stop_and_report",
                "reason": "no_progress",
                "detail": (
                    "the same failure repeated with no file change; stop, keep the diagnosis and "
                    "report the exact blocker instead of applying another blind patch"
                ),
                "repeats": repeats,
            }
        if repeats >= self.max_attempts:
            return {
                "action": "diagnose_differently",
                "reason": "fingerprint_repeated",
                "detail": "same fingerprint several times: collect new evidence before patching",
                "repeats": repeats,
            }
        return {
            "action": "patch",
            "reason": "progress_possible",
            "detail": "record the hypothesis, change the minimum code, then re-run the same check",
            "repeats": repeats,
        }

    def to_list(self) -> list[dict[str, Any]]:
        return [attempt.public() for attempt in self.attempts]

    @classmethod
    def from_list(cls, payload: Iterable[dict[str, Any]] | None, max_attempts: int = 3) -> "RepairLedger":
        return cls(
            (
                Attempt(
                    fingerprint=str(item.get("fingerprint") or ""),
                    error_class=str(item.get("error_class") or ""),
                    hypothesis=str(item.get("hypothesis") or ""),
                    files=list(item.get("files") or []),
                    command=str(item.get("command") or ""),
                    outcome=str(item.get("outcome") or ""),
                    at=str(item.get("at") or ""),
                )
                for item in payload or ()
            ),
            max_attempts=max_attempts,
        )
