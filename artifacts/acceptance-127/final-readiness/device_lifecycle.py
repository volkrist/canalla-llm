"""Device credential lifecycle on a real, isolated backend.

Covers what the isolation incident touched and what an acceptance harness can break:

    create -> read -> heartbeat -> re-pair -> revoke -> re-pair -> cleanup

and the failure modes that must be typed rather than silently accepted: a corrupt credential, an
unknown device, a revoked device, and a device from another user. The credential is never stored
in plaintext (the database keeps a SHA-256), and this script deletes only its own temporary data.

Run:  python artifacts/acceptance-127/final-readiness/device_lifecycle.py
"""

from __future__ import annotations

import hashlib
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
OUT = Path(__file__).resolve().parent


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http(method: str, url: str, body=None, token: str | None = None, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="canalla-device-"))
    database = work / "device.db"
    os.environ.update(
        {
            "DATABASE_URL": "sqlite:///" + database.as_posix(),
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

    from app.database import engine
    from app.main import app

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            if http("GET", base + "/health")[0] == 200:
                break
        except OSError:
            pass
        time.sleep(0.1)

    checks: list[dict] = []

    def check(name: str, ok: bool, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})

    def register(email: str) -> str:
        status, payload = http(
            "POST", base + "/auth/register", {"email": email, "password": "device-pass-123"}
        )
        assert status == 201, (status, payload)
        return payload["access_token"]

    def pair(token: str, name: str, previous: str | None = None):
        body = {"display_name": name, "platform": "windows"}
        if previous:
            body["previous_device_id"] = previous
        return http("POST", base + "/tools/devices/pair", body, token=token)

    def device_headers(device_id: str, credential: str) -> dict:
        return {"X-Alex-Device-Id": device_id, "X-Alex-Device-Credential": credential}

    result: dict = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        alice = register(f"alice-{secrets.token_hex(4)}@example.com")
        bob = register(f"bob-{secrets.token_hex(4)}@example.com")

        status, first = pair(alice, "Lifecycle A")
        check("pair_creates_a_device", status == 200 and first.get("device_id"), first.get("device_id"))
        check("pair_returns_a_credential", bool(first.get("credential")))
        check("pair_reports_online", bool(first.get("online")))
        alice_device, alice_secret = first["device_id"], first["credential"]

        status, beat = http(
            "POST",
            base + "/tools/devices/heartbeat",
            {},
            token=alice,
            headers=device_headers(alice_device, alice_secret),
        )
        check("heartbeat_accepts_the_credential", status == 200 and beat.get("device_id") == alice_device, status)

        status, _ = http(
            "GET",
            base + "/tools/devices/jobs",
            token=alice,
            headers=device_headers(alice_device, alice_secret),
        )
        check("jobs_accept_the_credential", status == 200, status)

        status, _ = http(
            "POST",
            base + "/tools/devices/heartbeat",
            {},
            token=alice,
            headers=device_headers(alice_device, "corrupt-credential-value"),
        )
        check("corrupt_credential_is_refused", status == 401, status)

        status, _ = http(
            "POST",
            base + "/tools/devices/heartbeat",
            {},
            token=alice,
            headers=device_headers("00000000-0000-0000-0000-000000000000", alice_secret),
        )
        check("unknown_device_is_refused", status == 401, status)

        status, _ = http(
            "POST",
            base + "/tools/devices/heartbeat",
            {},
            token=bob,
            headers=device_headers(alice_device, alice_secret),
        )
        check("another_users_device_is_refused", status in (401, 403), status)

        # Re-pair supersedes the old row without losing the audit trail.
        status, second = pair(alice, "Lifecycle A (re-pair)", previous=alice_device)
        check("re_pair_succeeds", status == 200 and second.get("device_id") != alice_device, status)
        new_device, new_secret = second["device_id"], second["credential"]
        status, _ = http(
            "POST",
            base + "/tools/devices/heartbeat",
            {},
            token=alice,
            headers=device_headers(alice_device, alice_secret),
        )
        check("superseded_device_is_revoked", status == 401, status)
        status, _ = http(
            "POST",
            base + "/tools/devices/heartbeat",
            {},
            token=alice,
            headers=device_headers(new_device, new_secret),
        )
        check("re_paired_device_works", status == 200, status)

        status, forgotten = http("POST", f"{base}/tools/devices/{new_device}/forget", {}, token=alice)
        check("revoke_succeeds", status == 200 and forgotten.get("revoked") is True, status)
        status, _ = http(
            "POST",
            base + "/tools/devices/heartbeat",
            {},
            token=alice,
            headers=device_headers(new_device, new_secret),
        )
        check("revoked_device_is_refused", status == 401, status)

        status, third = pair(alice, "Lifecycle A (after revoke)")
        check("re_pair_after_revoke_works", status == 200 and third.get("device_id"), status)

        status, devices = http("GET", base + "/tools/devices", token=alice)
        ids = {row["device_id"] for row in devices} if isinstance(devices, list) else set()
        check(
            "only_the_active_device_is_listed",
            status == 200 and ids == {third["device_id"]},
            ids,
        )

        connection = sqlite3.connect("file:" + database.as_posix() + "?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "select id, credential_hash, revoked_at from paired_devices"
            ).fetchall()
            integrity = connection.execute("pragma integrity_check").fetchone()[0]
        finally:
            connection.close()
        check("integrity_ok", integrity == "ok", integrity)
        check("revoked_rows_are_kept", len(rows) >= 3, len(rows))
        check(
            "credentials_are_hashed_at_rest",
            all(row[1] == hashlib.sha256(b"").hexdigest() or len(row[1]) == 64 for row in rows),
            [len(row[1]) for row in rows],
        )
        raw = database.read_bytes()
        check(
            "no_plaintext_credential_in_the_database",
            alice_secret.encode() not in raw and new_secret.encode() not in raw,
        )
        result["devices"] = [
            {"id": row[0], "revoked": bool(row[2]), "hash_len": len(row[1])} for row in rows
        ]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        engine.dispose()
        import shutil

        for _ in range(20):
            shutil.rmtree(work, ignore_errors=True)
            if not work.exists():
                break
            time.sleep(0.5)
        result["cleanup"] = {"temp_removed": not work.exists(), "temp_path": str(work)}

    result["checks"] = checks
    result["verdict"] = "PASS" if all(item["ok"] for item in checks) and result["cleanup"]["temp_removed"] else "FAIL"
    (OUT / "device-lifecycle.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
