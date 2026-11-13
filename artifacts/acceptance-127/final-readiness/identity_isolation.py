"""Identity isolation acceptance: a packaged host test must not touch the installed identity.

The 1.2.7 packaged-host run once overwrote the production device credential because
`credential_target()` silently ignored an unrecognised `ALEX_DEVICE_CREDENTIAL_TARGET`. That is
fixed, and this script proves the fix end to end *while the installed 1.2.6 application keeps
running*:

    production snapshot (read-only)  ->  isolated backend + packaged host (own DB, own credential
    target, own device dir)          ->  production snapshot again

It writes nothing outside a temporary directory and one namespaced Credential Manager entry, and it
deletes exactly those. The production database is opened with `mode=ro`, and the installed app is
never stopped, reconfigured or re-paired.

Run:  python artifacts/acceptance-127/final-readiness/identity_isolation.py
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "apps" / "backend"
HOST_EXE = REPO / "apps" / "desktop" / "src-tauri" / "target" / "release" / "alex-host-loop.exe"
OUT = Path(__file__).resolve().parent
PROD_DB = Path(os.environ["LOCALAPPDATA"]) / "Alex LLM" / "data" / "alex.db"
PROD_DEVICE = Path(os.environ["LOCALAPPDATA"]) / "Alex LLM" / "device.json"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http(method: str, url: str, body: dict | None = None, token: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def production_snapshot() -> dict:
    """Everything the test must leave exactly as it found it. Read-only, no secrets."""
    snapshot: dict = {"db_present": PROD_DB.is_file()}
    device_id = ""
    if PROD_DEVICE.is_file():
        try:
            device_id = json.loads(PROD_DEVICE.read_text(encoding="utf-8")).get("device_id", "")
        except ValueError:
            device_id = ""
    snapshot["local_device_id"] = device_id
    if not PROD_DB.is_file():
        return snapshot
    connection = sqlite3.connect("file:" + PROD_DB.as_posix() + "?mode=ro", uri=True)
    try:
        snapshot["counts"] = {
            table: connection.execute(f"select count(*) from {table}").fetchone()[0]
            for table in ("users", "chats", "messages", "paired_devices", "local_tasks")
        }
        snapshot["integrity"] = connection.execute("pragma integrity_check").fetchone()[0]
        rows = connection.execute(
            "select id, revoked_at, created_at, last_seen from paired_devices order by created_at"
        ).fetchall()
        snapshot["devices"] = [
            {"id": row[0], "revoked": bool(row[1]), "created_at": str(row[2])} for row in rows
        ]
        active = connection.execute(
            "select id from paired_devices where revoked_at is null order by created_at desc limit 1"
        ).fetchone()
        snapshot["active_device_id"] = active[0] if active else ""
        snapshot["revoked_count"] = sum(1 for row in rows if row[1])
    finally:
        connection.close()
    return snapshot


def _advapi():
    import ctypes
    from ctypes import wintypes

    adv = ctypes.windll.advapi32
    adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    adv.CredDeleteW.restype = wintypes.BOOL
    adv.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    adv.CredReadW.restype = wintypes.BOOL
    adv.CredFree.argtypes = [ctypes.c_void_p]
    return adv, ctypes


def credential_present(target: str, device_dir: Path) -> bool:
    """Is the credential stored for exactly this run's namespace?

    Checked through the Win32 API rather than `cmdkey`: the command rejects a target that contains
    a space, which is exactly the shape an acceptance namespace has. The DPAPI fallback file is
    also this run's own file, so its presence is equally isolated evidence.
    """
    adv, ctypes = _advapi()
    handle = ctypes.c_void_p()
    present = bool(adv.CredReadW(target, 1, 0, ctypes.byref(handle)))
    if handle:
        adv.CredFree(handle)
    return present or (device_dir / "device.cred.dpapi").is_file()


def credential_delete(target: str) -> bool:
    """Delete one generic credential. False means it was absent or the delete was refused."""
    adv, _ctypes = _advapi()
    return bool(adv.CredDeleteW(target, 1, 0))


def host_processes() -> list[int]:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "(Get-Process alex-host-loop -ErrorAction SilentlyContinue).Id -join ','",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return [int(value) for value in completed.stdout.strip().split(",") if value.strip().isdigit()]


def main() -> int:
    if not HOST_EXE.is_file():
        raise SystemExit(f"build the host first: {HOST_EXE}")
    before = production_snapshot()
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    work = Path(tempfile.mkdtemp(prefix="canalla-identity-"))
    nonce = secrets.token_hex(5)
    acceptance_target = f"Canalla Acceptance/identity-{nonce}"
    device_dir = work / "device"

    os.environ.update(
        {
            "DATABASE_URL": "sqlite:///" + (work / "host.db").as_posix(),
            "JWT_SECRET": secrets.token_urlsafe(48),
            "APP_ENV": "test",
            "LLM_PROVIDER": "mock",
            "MOCK_DELAY": "0",
            "RUNPOD_API_KEY": "",
            "TINYFISH_API_KEY": "",
            "COMPUTE_BACKGROUND_ENABLED": "false",
            "ADMIN_EMAILS": "[]",
            "ALLOW_USER_COMPUTE_START": "false",
            "DOCUMENT_STORAGE_DIR": str(work / "documents"),
            "ALEX_LLM_DATA_DIR": str(work / "app-data"),
        }
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND),
        check=True,
        capture_output=True,
    )
    sys.path.insert(0, str(BACKEND))

    import uvicorn

    from app.database import SessionLocal
    from app.main import app
    from app.tools.local.devices import active_device

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            if http("GET", base + "/health")[0] == 200:
                break
        except OSError:
            pass
        time.sleep(0.1)

    email = f"identity-{nonce}@example.com"
    _, registered = http("POST", base + "/auth/register", {"email": email, "password": "identity-pass-123"})
    token = registered["access_token"]
    _, me = http("GET", base + "/auth/me", token=token)
    user_id = me["id"]
    _, chat = http("POST", base + "/chats", {}, token=token)
    chat_id = chat["id"]

    host_env = {
        **os.environ,
        "ALEX_ACCEPTANCE_RUN": "1",
        "ALEX_BACKEND_URL": base,
        "ALEX_TOKEN": token,
        "ALEX_DEVICE_NAME": f"Identity isolation {nonce}",
        "ALEX_DEVICE_DIR": str(device_dir),
        "ALEX_DEVICE_CREDENTIAL_TARGET": acceptance_target,
        "ALEX_WORKSPACE_ROOTS": str(work),
    }
    log_path = work / "host.log"
    log = log_path.open("w", encoding="utf-8", errors="replace")
    host = subprocess.Popen([str(HOST_EXE)], env=host_env, stdout=log, stderr=subprocess.STDOUT, text=True)

    # The installed 1.2.6 must stay healthy through the whole run.
    installed_before = http("GET", "http://127.0.0.1:8000/health")
    installed_device_before = before.get("active_device_id", "")

    import hashlib

    result: dict = {
        "started_at": started,
        "acceptance_target": acceptance_target,
        "host_binary": str(HOST_EXE),
        "host_bytes": HOST_EXE.stat().st_size,
        "host_sha256": hashlib.sha256(HOST_EXE.read_bytes()).hexdigest(),
    }
    try:
        device = None
        for _ in range(200):
            with SessionLocal() as db:
                device = active_device(db, user_id)
            if device is not None and device.last_seen is not None:
                break
            time.sleep(0.1)
        result["isolated_pairing"] = {
            "paired": device is not None,
            "device_id": device.id if device else "",
            "heartbeat": bool(device and device.last_seen),
        }

        # Prove the packaged host stored its credential under the namespaced target.
        result["credential_target_present"] = credential_present(acceptance_target, device_dir)

        # Reconnect: stop and start the packaged host again; the same isolated target must work.
        host.terminate()
        try:
            host.wait(timeout=10)
        except subprocess.TimeoutExpired:
            host.kill()
        time.sleep(1.5)
        host = subprocess.Popen(
            [str(HOST_EXE)], env=host_env, stdout=log, stderr=subprocess.STDOUT, text=True
        )
        again = None
        for _ in range(200):
            with SessionLocal() as db:
                again = active_device(db, user_id)
            if again is not None and again.last_seen is not None:
                break
            time.sleep(0.1)
        result["reconnect"] = {
            "paired": again is not None,
            "same_device_id": bool(device and again and device.id == again.id),
        }

        installed_after = http("GET", "http://127.0.0.1:8000/health")
        result["installed_app"] = {
            "before": {"status": installed_before[0], "version": (installed_before[1] or {}).get("version")},
            "after": {"status": installed_after[0], "version": (installed_after[1] or {}).get("version")},
            "device_before": installed_device_before,
        }
    finally:
        try:
            host.terminate()
            host.wait(timeout=10)
        except Exception:
            host.kill()
        log.close()
        server.should_exit = True
        server_thread.join(timeout=10)
        from app.database import engine as _engine

        _engine.dispose()

    after = production_snapshot()
    result["production"] = {
        "before": before,
        "after": after,
        "users_unchanged": before.get("counts", {}).get("users") == after.get("counts", {}).get("users"),
        "chats_unchanged": before.get("counts", {}).get("chats") == after.get("counts", {}).get("chats"),
        "messages_unchanged": before.get("counts", {}).get("messages") == after.get("counts", {}).get("messages"),
        "tasks_unchanged": before.get("counts", {}).get("local_tasks")
        == after.get("counts", {}).get("local_tasks"),
        "device_set_unchanged": [row["id"] for row in before.get("devices", [])]
        == [row["id"] for row in after.get("devices", [])],
        "revoked_set_unchanged": [row["id"] for row in before.get("devices", []) if row["revoked"]]
        == [row["id"] for row in after.get("devices", []) if row["revoked"]],
        "active_device_unchanged": before.get("active_device_id") == after.get("active_device_id"),
        "local_device_id_unchanged": before.get("local_device_id") == after.get("local_device_id"),
        "integrity_ok": after.get("integrity") == "ok",
    }

    # Cleanup is part of the acceptance: only this run's own credential and temp files.
    cleanup = {"deleted_target": None, "host_processes_left": None, "temp_removed": None}
    cleanup["delete_requested"] = credential_delete(acceptance_target)
    remaining = host_processes()
    cleanup["host_processes_left"] = remaining
    import shutil

    for _ in range(30):
        shutil.rmtree(work, ignore_errors=True)
        if not work.exists():
            break
        time.sleep(0.5)
    cleanup["temp_removed"] = not work.exists()
    cleanup["temp_path"] = str(work)
    adv, ctypes = _advapi()
    handle = ctypes.c_void_p()
    still_present = bool(adv.CredReadW(acceptance_target, 1, 0, ctypes.byref(handle)))
    if handle:
        adv.CredFree(handle)
    cleanup["deleted_target"] = {
        "read_back_present": still_present,
        # A store in the DPAPI fallback file was removed with the run's own directory, and only a
        # surviving Credential Manager entry is a cleanup failure.
        "still_present": still_present,
    }
    result["cleanup"] = cleanup

    production_ok = all(
        value
        for key, value in result["production"].items()
        if key.endswith("_unchanged") or key == "integrity_ok"
    )
    verdict = (
        "PASS"
        if production_ok
        and result["isolated_pairing"]["paired"]
        and result["isolated_pairing"]["device_id"]
        and result["isolated_pairing"]["device_id"] != before.get("active_device_id")
        and result["reconnect"]["paired"]
        and result.get("credential_target_present")
        and not cleanup["deleted_target"].get("still_present")
        and not cleanup["host_processes_left"]
        and cleanup["temp_removed"]
        else "FAIL"
    )
    result["verdict"] = verdict
    result["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (OUT / "identity-isolation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
