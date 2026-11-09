"""Installed-runtime smoke for the packaged sidecar built from this tree.

Not a unit test: it starts the real `alex-backend.exe`, waits for `/health`, drives the real HTTP
API with the mock provider (no GPU, no network) and asserts the payload contract the new UI reads.
An isolated data root and credential target keep the owner's installation untouched.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXE = REPO / "apps" / "desktop" / "src-tauri" / "sidecar" / "alex-backend" / "alex-backend.exe"
OUT = REPO / "artifacts" / "acceptance-agent-status"
PORT = 8013


def get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def request(path, payload=None, token=None, method=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=data,
        method=method or ("POST" if data is not None else "GET"),
        headers={"Content-Type": "application/json"},
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=120) as response:
        body = response.read().decode("utf-8")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def wait_health(deadline=60):
    started = time.monotonic()
    while time.monotonic() - started < deadline:
        try:
            return get("/health")
        except (urllib.error.URLError, ConnectionError, socket.timeout, OSError):
            time.sleep(0.5)
    raise RuntimeError("backend did not answer /health")


def main() -> int:
    if not EXE.is_file():
        print(json.dumps({"status": "SKIP", "reason": f"missing {EXE}"}))
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix="canalla-smoke-"))
    env = {
        **os.environ,
        "ALEX_LLM_DATA_DIR": str(data_dir),
        "ALEX_DEVICE_DIR": str(data_dir / "device"),
        "ALEX_DEVICE_CREDENTIAL_TARGET": "CanallaSmoke/device-status",
        "ALEX_DEVICE_CREDENTIAL_SERVICE": "CanallaSmoke/device-status",
        "ALEX_BACKEND_PORT": str(PORT),
        "APP_ENV": "development",
        "LLM_PROVIDER": "mock",
        "ALEX_AI_MODE": "direct",
        "ALEX_RUNTIME_MODE": "dev_owned",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    log = (OUT / "installed-sidecar.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(EXE)],
        cwd=str(EXE.parent),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    result = {"exe": str(EXE), "port": PORT, "data_dir": str(data_dir)}
    try:
        health = wait_health()
        result["health"] = health
        token = request(
            "/auth/register",
            {"email": f"smoke-{int(time.time())}@example.com", "password": "test-password-123"},
        )["access_token"]
        chat = request("/chats", {}, token)
        prompt = (
            "Изучи документацию Python и создай в рабочей папке CLI-проект с тестами и README."
        )
        stream = request(
            f"/chats/{chat['id']}/stream",
            {"content": prompt, "web_mode": "off"},
            token,
        )
        result["stream_done"] = isinstance(stream, str) and "event: done" in stream
        tasks = request("/tasks?chat_id=" + chat["id"], token=token)
        result["tasks"] = len(tasks)
        if tasks:
            task = tasks[0]
            result["task"] = {
                "status": task.get("status"),
                "phase": task.get("phase"),
                "terminal": task.get("terminal"),
                "current_action": task.get("current_action"),
                "completed_count": task.get("completed_count"),
                "remaining_count": task.get("remaining_count"),
                "last_progress_at": task.get("last_progress_at"),
                "budgets": task.get("budgets"),
                "web": task.get("web"),
            }
            detail = request(f"/tasks/{task['id']}", token=token)
            result["events"] = len(detail.get("events") or [])
        result["status"] = "PASS"
        return 0
    except Exception as error:  # noqa: BLE001 - the point is to report the real failure
        result["status"] = "FAIL"
        result["error"] = f"{type(error).__name__}: {error}"
        return 1
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
        (OUT / "installed-sidecar-smoke.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
