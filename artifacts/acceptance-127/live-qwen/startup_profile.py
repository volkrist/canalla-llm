"""Measure the free parts of the LIVE start-up: migrations and isolated backend readiness.

No Pod is created: the backend only reacts to a chat request, and this script sends none.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "apps" / "backend"


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="canalla-startup-"))
    port = free_port()
    env = {
        **os.environ,
        "DATABASE_URL": "sqlite:///" + (work / "live.db").as_posix(),
        "JWT_SECRET": secrets.token_urlsafe(48),
        "APP_ENV": "test",
        "ALEX_AI_MODE": "direct",
        "LLM_PROVIDER": "llamacpp",
        "LLM_CONNECTION_MODE": "runpod",
        "RUNPOD_NETWORK_VOLUME_ID": "y92bt25ru8",
        "RUNPOD_DATACENTER": "US-MO-1",
        "COMPUTE_BACKGROUND_ENABLED": "true",
        "ALLOW_USER_COMPUTE_START": "true",
        "DOCUMENT_STORAGE_DIR": str(work / "documents"),
        "ALEX_LLM_DATA_DIR": str(work / "app-data"),
    }
    started = time.monotonic()
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND),
        env=env,
        check=True,
        capture_output=True,
    )
    migrations = round(time.monotonic() - started, 2)

    log = (work / "backend.log").open("w", encoding="utf-8", errors="replace")
    started = time.monotonic()
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BACKEND),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    ready_at = None
    try:
        for _ in range(600):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    if response.status == 200:
                        ready_at = round(time.monotonic() - started, 2)
                        break
            except OSError:
                pass
            time.sleep(0.05)
        # The first authenticated call also pays any lazy import: measure it.
        body = json.dumps({"email": "t@example.com", "password": "live-pass-123"}).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/auth/register",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
        register = round(time.monotonic() - started, 2)
    finally:
        backend.terminate()
        try:
            backend.wait(timeout=15)
        except subprocess.TimeoutExpired:
            backend.kill()
        log.close()
    print(
        json.dumps(
            {
                "migrations_seconds": migrations,
                "backend_ready_seconds": ready_at,
                "register_seconds": register,
                "total_seconds": round(migrations + (ready_at or 0) + register, 2),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
