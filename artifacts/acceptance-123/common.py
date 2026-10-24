"""Shared transport and evidence helpers for the Canalla 1.2.3 acceptance suite.

Nothing here creates compute. `token()` reads the same local session credential the installed
app uses; `stream_chat()` records the real SSE events and timestamps; every evidence record is
plain JSON with no secret.

The SSE reader runs in a worker thread and calls `on_event` while the body is still open, so a
confirmation can be approved or denied before the executor's wait expires.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

BASE = "http://127.0.0.1:8000"
ROOT = os.path.join(os.environ["LOCALAPPDATA"], "Alex LLM")
SANDBOX = Path(
    os.environ.get(
        "CANALLA_ACCEPTANCE_SANDBOX",
        os.path.join(os.path.expanduser("~"), "Canalla Acceptance"),
    )
)


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_advapi32.CredReadW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
]
_advapi32.CredReadW.restype = wintypes.BOOL
_advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
_advapi32.CredWriteW.restype = wintypes.BOOL
_advapi32.CredFree.argtypes = [ctypes.c_void_p]


def read_secret(target: str) -> str:
    pointer = ctypes.POINTER(CREDENTIALW)()
    if not _advapi32.CredReadW(target, 1, 0, ctypes.byref(pointer)):
        raise RuntimeError("cred_read_failed " + target)
    try:
        credential = pointer.contents
        return ctypes.string_at(
            credential.CredentialBlob, credential.CredentialBlobSize
        ).decode("utf-8")
    finally:
        _advapi32.CredFree(pointer)


def write_secret(target: str, value: str) -> None:
    blob = value.encode("utf-8")
    buffer = ctypes.create_string_buffer(blob, len(blob))
    credential = CREDENTIALW()
    credential.Type = 1
    credential.TargetName = target
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    credential.Persist = 2
    if not _advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise RuntimeError("cred_write_failed")


def call(
    method: str,
    path: str,
    token: str | None = None,
    body: dict | None = None,
    headers: dict | None = None,
    timeout: float = 240,
) -> tuple[int | None, str]:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
    )
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, str(error)


def token() -> str:
    session_id = (
        open(os.path.join(ROOT, "runtime", "session.id"), encoding="utf-8").read().strip()
    )
    install_id = (
        open(os.path.join(ROOT, "runtime", "install.id"), encoding="utf-8").read().strip()
    )
    target = "Alex LLM/session/" + session_id
    status, text = call(
        "POST",
        "/auth/refresh",
        body={"session_id": session_id, "refresh_secret": read_secret(target)},
        headers={"X-Alex-Device-Id": install_id},
    )
    if status != 200:
        status, text = call(
            "POST",
            "/auth/refresh",
            body={"session_id": session_id, "refresh_secret": read_secret(target)},
            headers={"X-Alex-Device-Id": install_id},
        )
    if status != 200:
        raise SystemExit("refresh failed " + text[:200])
    payload = json.loads(text)
    write_secret(target, payload["refresh_secret"])
    return payload["access_token"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not root.exists():
        return result
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(root))] = sha256_file(path)
    return result


def _parse_event(block: str) -> tuple[str, dict] | None:
    event, data = "", ""
    for line in block.splitlines():
        if line.startswith("event: "):
            event = line[7:].strip()
        elif line.startswith("data: "):
            data += line[6:]
    if not event:
        return None
    try:
        value = json.loads(data) if data else {}
    except ValueError:
        value = {"raw": data}
    return event, value


def stream_chat(
    chat_id: str,
    prompt: str,
    *,
    timeout: int,
    token_value: str,
    on_event=None,
) -> dict:
    started = time.monotonic()
    record = {
        "chat_id": chat_id,
        "prompt": prompt,
        "started_at": now(),
        "http": None,
        "error": None,
        "events": [],
        "first_event_ms": None,
        "first_delta_ms": None,
        "done_ms": None,
    }
    request = urllib.request.Request(
        BASE + "/chats/" + chat_id + "/stream",
        data=json.dumps({"content": prompt}).encode("utf-8"),
        method="POST",
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", "Bearer " + token_value)

    def worker() -> None:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                record["http"] = response.status
                block = ""
                for raw in response:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line == "":
                        parsed = _parse_event(block)
                        block = ""
                        if parsed is None:
                            continue
                        event, data = parsed
                        elapsed = int((time.monotonic() - started) * 1000)
                        record["events"].append(
                            {"event": event, "data": data, "elapsed_ms": elapsed, "at": now()}
                        )
                        if record["first_event_ms"] is None:
                            record["first_event_ms"] = elapsed
                        if event == "delta" and record["first_delta_ms"] is None:
                            record["first_delta_ms"] = elapsed
                        if event == "done":
                            record["done_ms"] = elapsed
                        if on_event is not None:
                            on_event(event, data)
                    else:
                        block += line + "\n"
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            record["error"] = type(error).__name__ + ": " + str(error)[:300]

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout + 15)
    record["joined"] = not thread.is_alive()
    record["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return record


def collect_session(token_value: str, chat_id: str) -> dict:
    result: dict = {}
    for key, path in (
        ("tool_runs", "/tools/runs?chat_id=" + chat_id),
        ("messages", "/chats/" + chat_id + "/messages"),
        ("tasks", "/tasks?chat_id=" + chat_id),
    ):
        status, text = call("GET", path, token=token_value)
        try:
            result[key] = json.loads(text) if status == 200 else []
        except ValueError:
            result[key] = []
        result[key + "_http"] = status
    sources: list = []
    for message in result.get("messages") or []:
        if message.get("role") != "assistant":
            continue
        status, text = call(
            "GET", "/messages/" + str(message.get("id")) + "/web-sources", token=token_value
        )
        if status == 200:
            try:
                sources.extend(json.loads(text))
            except ValueError:
                pass
    result["sources"] = sources
    return result


def safe_run(run: dict, sandbox: Path) -> bool:
    name = str(run.get("tool_name") or "")
    summary = run.get("input_summary") or {}
    # A package directory has to exist before its modules can be written. The product asks for
    # confirmation on this NORMAL_CHANGE; the harness may approve it *inside* the sandbox, just
    # like write_file. This is a test-harness policy, never a product permission.
    if name == "run_process":
        executable = Path(str(summary.get("executable") or "").strip('"')).name.lower()
        if executable not in {"python", "python.exe"} or not summary.get("cwd"):
            return False
    elif name not in {
        "read_file",
        "list_directory",
        "write_file",
        "patch_file",
        "create_directory",
        "run_python",
    }:
        return False
    for key in ("path", "cwd", "root"):
        value = summary.get(key)
        if not value:
            continue
        try:
            child = Path(str(value).strip('"')).resolve()
            base = sandbox.resolve()
        except (OSError, ValueError):
            return False
        if child != base and base not in child.parents:
            return False
    return True


def stop_stale_tasks(token_value: str, sandbox: Path) -> list[dict]:
    """Stop parked acceptance tasks that would hold the computer lock.

    One task owns the whole per-user computer workspace. A task parked by an earlier session
    (`WAITING_LLM`, `WAITING_CONFIRMATION`, `WAITING_WORKSPACE`) keeps that lock, and a new
    scenario then returns `WAITING_WORKSPACE` without ever reaching the model. Only tasks whose
    title names this acceptance sandbox are touched, so a real user task is never stopped.
    """
    stopped: list[dict] = []
    status, text = call("GET", "/tasks?limit=100", token=token_value, timeout=30)
    if status != 200:
        return stopped
    try:
        rows = json.loads(text)
    except ValueError:
        return stopped
    marker = str(sandbox).casefold()
    for row in rows if isinstance(rows, list) else []:
        if str(row.get("status") or "") in {"COMPLETED", "FAILED", "STOPPED"}:
            continue
        haystack = (str(row.get("title") or "") + " " + str(row.get("original_user_request") or "")).casefold()
        if marker not in haystack:
            continue
        run_status, body = call(
            "POST", "/tasks/" + str(row.get("id")) + "/stop", token=token_value, body={}, timeout=30
        )
        stopped.append(
            {"id": row.get("id"), "status": row.get("status"), "http": run_status, "body": body[:120]}
        )
    return stopped


def confirm(token_value: str, run_id: str, allow: bool) -> tuple[int | None, str]:
    run_status, run_text = call("GET", "/tools/runs/" + run_id, token=token_value, timeout=20)
    digest = ""
    if run_status == 200:
        try:
            digest = str(json.loads(run_text).get("input_digest") or "")
        except ValueError:
            digest = ""
    body = {"allow": allow}
    if digest:
        body["digest"] = digest
    return call(
        "POST", "/tools/runs/" + run_id + "/confirm", token=token_value, body=body, timeout=20
    )
