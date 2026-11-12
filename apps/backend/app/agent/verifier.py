"""An independent verifier: the acceptance criteria are checked against the machine.

The planner proposes, the executor acts, and this module *re-checks*. It never reads the model's
answer to decide anything: a check either observed a fact or it did not, and an unsupported check
is reported as unsupported instead of quietly passing.

Checks are declarative so a plan can carry them and a resumed task can re-run them:

    {"kind": "file_exists", "target": "C:/work/taskdesk/main.py", "min_bytes": 1}
    {"kind": "file_sha256", "target": "...", "expected": "<hex>"}
    {"kind": "process_alive", "target": "12345"}
    {"kind": "port_open", "target": "127.0.0.1:8000"}
    {"kind": "http_ok", "target": "http://127.0.0.1:8000/health", "status": 200, "contains": "ok"}
    {"kind": "command", "target": "python -m pytest -q", "cwd": "...", "exit_code": 0}
    {"kind": "db_row", "target": "C:/work/data.db", "query": "select count(*) from tasks", "at_least": 1}
    {"kind": "window_present", "target": "TaskDesk"}
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .evidence import Evidence, EvidenceLedger

DEFAULT_TIMEOUT = 30.0


class ProbeUnavailable(RuntimeError):
    """The host cannot observe this kind of fact. Never converted into a PASS."""


@dataclass
class Report:
    ledger: EvidenceLedger
    unsupported: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.ledger.ok and not self.unsupported

    @property
    def failed(self) -> list[Evidence]:
        return self.ledger.failed()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.ledger.summary(),
            "unsupported": self.unsupported,
            "failed": [item.to_dict() for item in self.failed],
            "evidence": self.ledger.to_list(),
        }


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            # STILL_ACTIVE is 259; a terminated process reports its exit code.
            return bool(ok) and code.value == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def _port_open(target: str, timeout: float) -> bool:
    host, _, port = target.rpartition(":")
    if not host or not port:
        raise ValueError(f"port target must be host:port, got {target!r}")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _http_check(target: str, timeout: float) -> tuple[int | None, str]:
    request = urllib.request.Request(target, headers={"User-Agent": "canalla-verifier/1.2.7"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(200_000).decode("utf-8", "replace")
            return response.status, body
    except urllib.error.HTTPError as error:
        return error.code, error.read(200_000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return None, ""


class Verifier:
    def __init__(self, *, window_probe: Callable[[str], bool] | None = None):
        self.window_probe = window_probe

    # -- one check -------------------------------------------------------------------------
    def check(self, spec: dict[str, Any]) -> Evidence:
        kind = str(spec.get("kind") or "").strip()
        target = str(spec.get("target") or "").strip()
        timeout = float(spec.get("timeout") or DEFAULT_TIMEOUT)
        source = str(spec.get("source") or "verifier")
        detail = dict(spec.get("detail") or {})
        if not kind:
            return Evidence("CHECK_INVALID", target, False, "kind", "missing", source, detail=detail)

        try:
            if kind == "file_exists":
                exists = os.path.isfile(target)
                size = os.path.getsize(target) if exists else 0
                minimum = int(spec.get("min_bytes") or 1)
                return Evidence(
                    kind,
                    target,
                    exists and size >= minimum,
                    expected=f"file >= {minimum} bytes",
                    observed=f"exists={exists} size={size}",
                    source=source,
                    detail={**detail, "size": size},
                )

            if kind == "file_sha256":
                import hashlib

                expected = str(spec.get("expected") or "").lower()
                if not expected:
                    return Evidence(kind, target, False, "sha256", "missing expected", source, detail=detail)
                try:
                    digest = hashlib.sha256(open(target, "rb").read()).hexdigest()
                except OSError as error:
                    return Evidence(
                        kind, target, False, expected, type(error).__name__, source, detail=detail
                    )
                return Evidence(kind, target, digest == expected, expected, digest, source, detail=detail)

            if kind == "file_contains":
                needle = str(spec.get("expected") or "")
                try:
                    text = open(target, encoding="utf-8", errors="replace").read(500_000)
                except OSError as error:
                    return Evidence(kind, target, False, needle, type(error).__name__, source, detail=detail)
                return Evidence(
                    kind,
                    target,
                    needle in text,
                    needle,
                    "present" if needle in text else "absent",
                    source,
                    detail=detail,
                )

            if kind == "process_alive":
                alive = _process_alive(int(target))
                return Evidence(
                    kind, target, alive, "running", "running" if alive else "gone", source, detail=detail
                )

            if kind == "port_open":
                opened = _port_open(target, timeout)
                return Evidence(
                    kind,
                    target,
                    opened,
                    "listening",
                    "listening" if opened else "closed",
                    source,
                    detail=detail,
                )

            if kind == "http_ok":
                status, body = _http_check(target, timeout)
                expected_status = int(spec.get("status") or 200)
                contains = str(spec.get("contains") or "")
                ok = status == expected_status and (not contains or contains in body)
                return Evidence(
                    kind,
                    target,
                    ok,
                    f"status {expected_status}" + (f" + {contains!r}" if contains else ""),
                    f"status {status}"
                    + (
                        "" if not contains else (" + body matched" if contains in body else " + body missing")
                    ),
                    source,
                    detail={**detail, "body_bytes": len(body)},
                )

            if kind == "command":
                argv = spec.get("argv") or target.split()
                cwd = spec.get("cwd") or None
                expected_code = int(spec.get("exit_code") or 0)
                if not isinstance(argv, list) or not argv:
                    return Evidence(kind, target, False, "command", "invalid argv", source, detail=detail)
                completed = subprocess.run(
                    [str(part) for part in argv],
                    cwd=cwd,
                    capture_output=True,
                    timeout=timeout,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                observed = f"exit {completed.returncode}"
                return Evidence(
                    kind,
                    target,
                    completed.returncode == expected_code,
                    f"exit {expected_code}",
                    observed,
                    source,
                    detail={**detail, "stdout": completed.stdout[-2000:], "stderr": completed.stderr[-2000:]},
                )

            if kind == "db_row":
                import sqlite3

                query = str(spec.get("query") or "")
                at_least = int(spec.get("at_least") or 1)
                if not query or not os.path.isfile(target):
                    return Evidence(
                        kind,
                        target,
                        False,
                        f">= {at_least} row(s)",
                        "database missing or query empty",
                        source,
                        detail=detail,
                    )
                connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=timeout)
                try:
                    rows = connection.execute(query).fetchall()
                finally:
                    connection.close()
                count = int(rows[0][0]) if rows and isinstance(rows[0], tuple) else len(rows)
                return Evidence(
                    kind,
                    target,
                    count >= at_least,
                    f">= {at_least}",
                    str(count),
                    source,
                    detail={**detail, "rows": count},
                )

            if kind == "window_present":
                if self.window_probe is None:
                    raise ProbeUnavailable("window_probe")
                found = bool(self.window_probe(target))
                return Evidence(
                    kind,
                    target,
                    found,
                    "window listed",
                    "listed" if found else "not found",
                    source,
                    detail=detail,
                )

            raise ProbeUnavailable(kind)
        except ProbeUnavailable as error:
            return Evidence(
                kind,
                target,
                False,
                "supported probe",
                f"probe unavailable: {error}",
                source,
                detail={**detail, "unsupported": True},
            )
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            return Evidence(
                kind,
                target,
                False,
                "check completed",
                f"{type(error).__name__}: {error}",
                source,
                detail=detail,
            )

    # -- many checks -----------------------------------------------------------------------
    def run(self, specs: Iterable[dict[str, Any]]) -> Report:
        ledger = EvidenceLedger()
        unsupported: list[str] = []
        for spec in specs:
            evidence = self.check(spec)
            ledger.add(evidence)
            if evidence.detail.get("unsupported"):
                unsupported.append(evidence.kind)
        return Report(ledger=ledger, unsupported=unsupported)
