#!/usr/bin/env python
r"""Session restore + first-run acceptance against the packaged backend sidecar.

Runs the REAL PyInstaller sidecar with an isolated data root. No GPU, no
TinyFish, no real RunPod key: the controller short-circuits when unconfigured,
so the scenario spends $0 and never touches the Network Volume.

Usage (PowerShell, repo root):
  apps\backend\.venv\Scripts\python.exe scripts\acceptance-session-first-run.py

Scenarios:
  A  fresh install: first_run -> owner bootstrap -> restart -> restore -> logout
  B  existing user: register -> restart -> login -> restore
  D  reinstall: relaunch from a copied binary with the preserved data root
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SIDECAR = REPO / "apps" / "desktop" / "src-tauri" / "sidecar" / "alex-backend" / "alex-backend.exe"
PASSWORD = "acceptance-password-123"
RUNTIME_TOKEN = "acceptance-runtime-token-0123456789abcdef"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def request(
    method: str, port: int, path: str, body: dict | None = None, headers: dict | None = None
) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"detail": raw.decode(errors="replace")}
        return error.code, payload


def wait_ready(port: int, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, body = request("GET", port, "/health")
            if status == 200 and body.get("status") == "ok":
                return body
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("backend never became ready")


class Sidecar:
    def __init__(self, root: Path, port: int, exe: Path = SIDECAR, runtime_token: str = ""):
        self.root = root
        self.port = port
        self.process: subprocess.Popen | None = None
        self.exe = exe
        self.runtime_token = runtime_token

    def env(self):
        import os

        env = {
            **os.environ,
            "ALEX_PACKAGED": "1",
            "ALEX_LLM_DATA_DIR": str(self.root),
            "ALEX_BACKEND_PORT": str(self.port),
            "ALEX_BACKEND_HOST": "127.0.0.1",
            "ALEX_BACKEND_INSTANCE": "acceptance",
        }
        if self.runtime_token:
            env["ALEX_RUNTIME_TOKEN"] = self.runtime_token
        # Deliberately NO JWT_SECRET / DATABASE_URL / DOCUMENT_STORAGE_DIR:
        # the sidecar must generate them (core no-env requirement).
        for key in ("JWT_SECRET", "DATABASE_URL", "DOCUMENT_STORAGE_DIR"):
            env.pop(key, None)
        return env

    def start(self) -> dict:
        self.process = subprocess.Popen(
            [str(self.exe)],
            env=self.env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return wait_ready(self.port)

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            # PyInstaller onedir: the bootstrap spawns the real runtime as a
            # child that survives the parent's termination. Stop the whole
            # tree rooted at OUR pid (never kill by name).
            subprocess.run(
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                capture_output=True,
            )
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        deadline = time.monotonic() + 10
        listing = ""
        while time.monotonic() < deadline:
            listing = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq alex-backend.exe", "/FO", "CSV"],
                capture_output=True,
                text=True,
            ).stdout
            if "alex-backend.exe" not in listing:
                break
            time.sleep(0.5)
        else:
            print(f"  [WARN] sidecar processes still alive after stop: {listing}")
        time.sleep(0.5)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def device_headers(token: str) -> dict:
    return {"X-Alex-Device-Id": "install-acceptance-0001", **bearer(token)}


def scenario_a(tmp: Path):
    print("Scenario A — fresh install, owner bootstrap, restart restore, logout")
    root = tmp / "user-a"
    sidecar = Sidecar(root, 8050, runtime_token=RUNTIME_TOKEN)
    health = sidecar.start()
    check("backend Ready with product identity", health.get("product") == "alex-llm", str(health))
    check("packaged sidecar defaults to llamacpp provider", health.get("provider") == "llamacpp", str(health.get("provider")))
    check("runtime data layout created without env", (root / "runtime" / "jwt.secret").is_file())

    status, state = request("GET", 8050, "/auth/state")
    check("first-run state on fresh install", status == 200 and state == {"users_exist": False, "state": "first_run"}, str(state))

    status, _ = request(
        "POST", 8050, "/auth/bootstrap",
        {"email": "owner@example.com", "password": PASSWORD, "display_name": "Владелец"},
    )
    check("bootstrap without local runtime proof rejected", status == 403, str(status))

    status, body = request(
        "POST", 8050, "/auth/bootstrap",
        {"email": "owner@example.com", "password": PASSWORD, "display_name": "Владелец"},
        headers={"X-Alex-Runtime-Token": RUNTIME_TOKEN, "X-Alex-Device-Id": "install-acceptance-0001"},
    )
    check("owner bootstrap accepted", status == 201, str(status))
    access, session_id, refresh = body.get("access_token"), body.get("session_id"), body.get("refresh_secret")
    check("bootstrap returns access + session + refresh", bool(access and session_id and refresh), str(body.get("user")))
    check("owner email is the bootstrap email", body.get("user", {}).get("email") == "owner@example.com")

    status, _ = request(
        "POST", 8050, "/auth/bootstrap",
        {"email": "second@example.com", "password": PASSWORD},
        headers={"X-Alex-Runtime-Token": RUNTIME_TOKEN},
    )
    check("bootstrap closed after owner exists", status == 409, str(status))

    status, me = request("GET", 8050, "/auth/me", headers=bearer(access))
    check("access token authenticates", status == 200 and me.get("email") == "owner@example.com", str(status))

    status, compute = request("GET", 8050, "/compute/status", headers=bearer(access))
    check("owner can control compute (FakeRunPod-free, $0)", status == 200 and compute.get("can_control") is True and compute.get("configured") is False, str(compute.get("state")))

    status, _ = request("POST", 8050, "/chats", {}, headers=bearer(access))
    check("owner creates a chat", status == 200 or status == 201, str(status))

    # Backend restart (same install): the desktop restores with the refresh secret.
    sidecar.stop()
    sidecar.start()
    status, state = request("GET", 8050, "/auth/state")
    check("state is auth_required after owner exists", status == 200 and state.get("state") == "auth_required", str(state))
    status, restored = request(
        "POST", 8050, "/auth/refresh", {"session_id": session_id, "refresh_secret": refresh},
        headers={"X-Alex-Device-Id": "install-acceptance-0001"},
    )
    check("backend restart + session restore without password", status == 200, str(status))
    access2, refresh2 = restored.get("access_token"), restored.get("refresh_secret")
    check("restored user is the owner", restored.get("user", {}).get("email") == "owner@example.com")

    status, replay = request("POST", 8050, "/auth/refresh", {"session_id": session_id, "refresh_secret": refresh})
    check("rotated (old) refresh secret rejected", status == 401, str(status))

    status, chats = request("GET", 8050, "/chats", headers=bearer(access2))
    check("chat history preserved across restart", status == 200 and isinstance(chats, list) and len(chats) == 1, str(status))

    # Reinstall simulation: same data root, binaries from a different location.
    copy = tmp / "Programs" / "Alex LLM" / "alex-backend"
    shutil.copytree(SIDECAR.parent, copy)
    sidecar.stop()
    reinstalled = Sidecar(root, 8050, exe=copy / "alex-backend.exe", runtime_token=RUNTIME_TOKEN)
    reinstalled.start()
    status, restored2 = request(
        "POST", 8050, "/auth/refresh", {"session_id": session_id, "refresh_secret": refresh2},
        headers={"X-Alex-Device-Id": "install-acceptance-0001"},
    )
    check("reinstall preserves data root and restores session", status == 200, str(status))
    refresh3 = restored2.get("refresh_secret")

    # Logout: revoke + relaunch must NOT restore.
    status, revoked = request("POST", 8050, "/auth/revoke", {"session_id": session_id, "refresh_secret": refresh3})
    check("logout revokes the persistent session", status == 200 and revoked.get("revoked") is True, str(revoked))
    status, _ = request("POST", 8050, "/auth/refresh", {"session_id": session_id, "refresh_secret": refresh3})
    check("revoked session rejected", status == 401, str(status))
    reinstalled.stop()
    reinstalled.start()
    status, _ = request("POST", 8050, "/auth/refresh", {"session_id": session_id, "refresh_secret": refresh3})
    check("logout + full restart does NOT restore", status == 401, str(status))
    reinstalled.stop()
    import sqlite3

    with sqlite3.connect(root / "data" / "alex.db") as db:
        version = db.execute("SELECT version_num FROM alembic_version").fetchone()
        raw = db.execute("SELECT token_hash FROM auth_sessions LIMIT 1").fetchone()
    check("database migrated to 0014 on the packaged sidecar", version == ("0014",), str(version))
    check("refresh secret stored hashed only", bool(raw) and raw[0] not in {refresh, refresh2, refresh3}, "hash-only")


def scenario_b(tmp: Path):
    print("Scenario B — existing user, login once, restart restores")
    root = tmp / "user-b"
    sidecar = Sidecar(root, 8051)
    sidecar.start()
    status, registered = request(
        "POST", 8051, "/auth/register", {"email": "alice@example.com", "password": PASSWORD},
        headers={"X-Alex-Device-Id": "install-acceptance-0001"},
    )
    check("existing-user registration works", status == 201, str(status))
    alice_id = registered.get("user", {}).get("id")

    sidecar.stop()
    sidecar.start()
    status, state = request("GET", 8051, "/auth/state")
    check("existing installation shows auth_required", status == 200 and state.get("state") == "auth_required", str(state))
    status, login = request(
        "POST", 8051, "/auth/login", {"email": "alice@example.com", "password": PASSWORD},
        headers={"X-Alex-Device-Id": "install-acceptance-0001"},
    )
    check("login once creates a persistent session", status == 200 and login.get("session_id"), str(status))
    check("same user preserved (no recreation)", login.get("user", {}).get("id") == alice_id)

    sidecar.stop()
    sidecar.start()
    status, restored = request(
        "POST", 8051, "/auth/refresh",
        {"session_id": login["session_id"], "refresh_secret": login["refresh_secret"]},
        headers={"X-Alex-Device-Id": "install-acceptance-0001"},
    )
    check("full restart restores the existing user session", status == 200, str(status))
    sidecar.stop()


def main() -> int:
    if not SIDECAR.is_file():
        print(f"sidecar missing: {SIDECAR}")
        print("run scripts/build-backend-sidecar.ps1 first")
        return 2
    with tempfile.TemporaryDirectory(prefix="alex-acceptance-", ignore_cleanup_errors=True) as tmp_dir:
        tmp = Path(tmp_dir)
        scenario_a(tmp)
        scenario_b(tmp)
    print()
    if failures:
        print(f"ACCEPTANCE FAILED: {len(failures)} failing check(s): {failures}")
        return 1
    print("ACCEPTANCE PASS — RunPod $0, TinyFish $0, GPU 0, Volume untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
