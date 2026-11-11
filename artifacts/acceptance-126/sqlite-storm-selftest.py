"""Free concurrency proof: a chat turn must survive a device-poll storm.

The T4 LIVE failure of 2026-11-11 was not the model. Every authenticated device request
committed `last_seen`, the host loop polls the job queue several times a second, and a burst of
polls held every pooled SQLite connection until the chat turn's own query died with an untyped
``QueuePool ... TimeoutError``.

This script reproduces that load on a real uvicorn server with the mock model and no GPU:

* one paired device is hammered by 12 threads (heartbeat + jobs, answering the jobs it claims
  like a host would, so the turn keeps moving);
* at the same time a real ``/chats/{id}/stream`` turn runs the exact T4 prompt;
* the verdict is a PASS only when the stream ends normally, the turn created its ``local_tasks``
  row and no ``database_busy`` / ``stream_interrupted`` reached the client.

Run: python artifacts/acceptance-126/sqlite-storm-selftest.py
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "apps" / "backend"
PORT = int(os.environ.get("STORM_PORT", "8137"))
BASE = f"http://127.0.0.1:{PORT}"
THREADS = int(os.environ.get("STORM_THREADS", "12"))
STORM_SECONDS = float(os.environ.get("STORM_SECONDS", "45"))

PROMPT = (
    "В папке {root} открой t4l.txt, сохрани его содержимое в t4l_out.txt, "
    "затем открой t4l_out.txt и подтверди содержимое. В конце выведи T4L_DONE."
)


def call(method: str, path: str, *, token: str | None = None, body=None, headers=None, timeout=60):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
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
    except Exception as error:  # noqa: BLE001 - the storm must never abort the test
        return 0, f"{type(error).__name__}: {error}"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="canalla-storm-", ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        workspace = root / "Canalla E2E" / "T4L"
        workspace.mkdir(parents=True)
        (workspace / "t4l.txt").write_text("T4L_HOST_VALUE_417\n", encoding="utf-8")
        os.environ.update(
            DATABASE_URL="sqlite:///" + (root / "storm.db").as_posix(),
            JWT_SECRET=secrets.token_urlsafe(48),
            APP_ENV="test",
            LLM_PROVIDER="mock",
            MOCK_DELAY="0",
            RUNPOD_API_KEY="",
            TINYFISH_API_KEY="",
            COMPUTE_BACKGROUND_ENABLED="false",
            BALANCE_BACKGROUND_ENABLED="false",
            TOR_BROWSER_AUTOMATION_ENABLED="false",
            ADMIN_EMAILS="[]",
            ALLOW_USER_COMPUTE_START="false",
            DOCUMENT_STORAGE_DIR=str(root / "documents"),
            ALEX_LLM_DATA_DIR=str(root / "app-data"),
            PYTHONIOENCODING="utf-8",
        )
        sys.path.insert(0, str(BACKEND))
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND),
            check=True,
            capture_output=True,
        )

        import uvicorn

        from app.database import SessionLocal
        from app.main import app
        from app.tools.models import LocalTask, ToolRun

        config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(120):
            if call("GET", "/health")[0] == 200:
                break
            time.sleep(0.25)
        else:
            print(json.dumps({"verdict": "FAIL", "why": "the test backend never became healthy"}))
            return 1

        status, body = call("POST", "/auth/register", body={"email": "storm@example.com",
                                                            "password": "storm-password-123"})
        token = json.loads(body)["access_token"]
        _, body = call("POST", "/chats", token=token, body={})
        chat_id = json.loads(body)["id"]
        call("PUT", "/tools/preferences", token=token,
             body={"computer_mode": "ask", "workspace_roots": [str(root)]})
        _, body = call("POST", "/tools/devices/pair", token=token, body={"platform": "windows"})
        paired = json.loads(body)
        device_headers = {
            "X-Alex-Device-Id": paired["device_id"],
            "X-Alex-Device-Credential": paired["credential"],
        }

        stop = threading.Event()
        counters = {"polls": 0, "jobs": 0, "errors": 0, "confirmed": 0, "answered": 0}
        lock = threading.Lock()

        def bump(name: str) -> None:
            with lock:
                counters[name] += 1

        def storm() -> None:
            while not stop.is_set():
                code, _ = call("POST", "/tools/devices/heartbeat", token=token,
                               headers=device_headers)
                if code != 200:
                    bump("errors")
                code, payload = call("GET", "/tools/devices/jobs", token=token,
                                     headers=device_headers)
                bump("polls")
                if code == 200:
                    for job in json.loads(payload or "[]"):
                        bump("jobs")
                        claim = {"claim_id": job.get("claim_id") or ""}
                        call("POST", f"/tools/runs/{job['id']}/host-started", token=token,
                             headers=device_headers, body=claim)
                        call("POST", f"/tools/runs/{job['id']}/host-result", token=token,
                             headers=device_headers,
                             body={**claim, "digest": job["input_digest"], "status": "failed",
                                   "stderr": "storm-selftest: synthetic host"})
                        bump("answered")
                elif code != 200:
                    bump("errors")
                time.sleep(0.02)

        workers = [threading.Thread(target=storm, daemon=True) for _ in range(THREADS)]
        for worker in workers:
            worker.start()

        def confirm_waiting() -> None:
            """computer_mode=ask is the permission boundary: approve the fixture's own tools."""
            while not stop.is_set():
                code, payload = call("GET", f"/tools/runs?chat_id={chat_id}", token=token)
                if code == 200:
                    for run in json.loads(payload or "[]"):
                        if run.get("status") == "waiting_confirmation":
                            call("POST", f"/tools/runs/{run['id']}/confirm", token=token,
                                 body={"allow": True, "digest": run.get("input_digest")})
                            bump("confirmed")
                time.sleep(0.2)

        threading.Thread(target=confirm_waiting, daemon=True).start()
        # Let the storm fill the pool before the turn arrives: the failure being reproduced was
        # a burst that was already in flight when the chat turn needed its own connection.
        time.sleep(3.0)

        result = {}

        def turn() -> None:
            started = time.monotonic()
            code, payload = call("POST", f"/chats/{chat_id}/stream", token=token,
                                 body={"content": PROMPT.format(root=workspace)}, timeout=240)
            result.update(code=code, body=payload, seconds=round(time.monotonic() - started, 1))

        turn_thread = threading.Thread(target=turn, daemon=True)
        turn_thread.start()
        turn_thread.join(timeout=STORM_SECONDS + 180)
        stop.set()
        for worker in workers:
            worker.join(timeout=5)
        body = result.get("body") or ""
        with SessionLocal() as db:
            tasks = db.query(LocalTask).filter(LocalTask.chat_id == chat_id).all()
            runs = db.query(ToolRun).filter(ToolRun.chat_id == chat_id).count()
            task = tasks[0] if tasks else None
            evidence = {
                "task_id": task.id if task else None,
                "task_status": task.status if task else None,
                "task_origin": task.original_user_request[:60] if task else None,
                "task_steps": len(task.checkpoint.get("plan", [])) if task and task.checkpoint else 0,
                "tool_runs": runs,
            }
        checks = {
            "stream_finished": '"event: done"' in body or result.get("code") == 200
            and "event: error" not in body,
            "no_stream_interrupted": "stream_interrupted" not in body,
            "no_database_busy": "database_busy" not in body,
            "task_row_created": evidence["task_id"] is not None,
            "turn_completed_in_time": (result.get("seconds") or 999) < 180,
        }
        verdict = "PASS" if all(checks.values()) else "FAIL"
        print(json.dumps({
            "verdict": verdict,
            "checks": checks,
            "turn_seconds": result.get("seconds"),
            "http": result.get("code"),
            "storm": {**counters, "threads": THREADS},
            "evidence": evidence,
            "errors_event": [line for line in body.splitlines() if line.startswith("event: error")][:2],
        }, ensure_ascii=False, indent=1))
        server.should_exit = True
        return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
