"""Free two-process LEVEL 7 E2E: fake inference, real backend, real kill and real resume.

Run:

    apps/backend/.venv/Scripts/python.exe artifacts/acceptance-127/live-qwen/free_resume_e2e.py

Nothing here touches RunPod, the installed product or production data: one temp directory holds the
database, the data root, the documents and the fake server's scenario counter.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BACKEND = REPO / "apps" / "backend"
FAKE = HERE / "fake_llm.py"
PY = sys.executable


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def call(url: str, token: str = "", payload: dict | None = None, method: str = "POST"):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode()
        return response.status, (json.loads(body) if body.strip().startswith(("{", "[")) else body)


def wait_health(base: str, seconds: int = 90) -> bool:
    for _ in range(seconds):
        try:
            if call(base + "/health", method="GET")[0] == 200:
                return True
        except Exception:
            time.sleep(1)
    return False


def start_backend(env: dict, port: int, log: Path) -> subprocess.Popen:
    handle = log.open("a", encoding="utf-8")
    return subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BACKEND),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
    )


def db_counts(env: dict) -> tuple[int, str, int]:
    code = (
        "from app.database import SessionLocal\n"
        "from app.tools.models import LocalTask, ToolRun\n"
        "with SessionLocal() as db:\n"
        "    t = db.query(LocalTask).order_by(LocalTask.started_at.desc()).first()\n"
        "    n = db.query(ToolRun).filter(ToolRun.status == 'completed').count()\n"
        "    print(t.id if t else '', t.status if t else 'MISSING', n)\n"
    )
    out = subprocess.run(
        [PY, "-c", code], cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=60
    )
    parts = (out.stdout.strip() or " MISSING 0").split()
    return (parts[0] if parts else ""), (parts[1] if len(parts) > 1 else "MISSING"), int(parts[-1] or 0)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="canalla-free-e2e-"))
    fake_port, port = free_port(), free_port()
    base = f"http://127.0.0.1:{port}"
    log = work / "backend.log"
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "DATABASE_URL": "sqlite:///" + (work / "e2e.db").as_posix(),
        "ALEX_LLM_DATA_DIR": str(work / "data"),
        "DOCUMENT_STORAGE_DIR": str(work / "documents"),
        "JWT_SECRET": "e2e-only-secret-" + "x" * 48,
        "APP_ENV": "test",
        "ALEX_AI_MODE": "direct",
        "LLM_PROVIDER": "llamacpp",
        "LLM_CONNECTION_MODE": "static",
        "LLM_BASE_URL": f"http://127.0.0.1:{fake_port}",
        "LLM_MODEL": "fake-qwen",
        "TINYFISH_API_KEY": "",
        "COMPUTE_BACKGROUND_ENABLED": "false",
        "ALLOW_USER_COMPUTE_START": "false",
        "TOOLS_TASK_MAX_RUNTIME": "900",
        "TOOLS_TASK_MAX_CALLS": "40",
        "ADMIN_EMAILS": "[]",
    }
    fake = subprocess.Popen(
        [PY, str(FAKE), str(fake_port), str(work / "llm")],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    backend = None
    report: dict = {"work": str(work), "fake_port": fake_port, "backend_port": port}
    try:
        assert fake.stdout.readline().strip(), "fake server did not start"
        code = env.copy()
        code["DATABASE_URL"] = env["DATABASE_URL"]
        migrate = subprocess.run(
            [PY, "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND),
            env={**code, "ALEX_LLM_DATA_DIR": str(work / "data")},
            capture_output=True,
            text=True,
            timeout=180,
        )
        report["migrate"] = migrate.returncode
        if migrate.returncode != 0:
            report["migrate_stderr"] = migrate.stderr[-800:]
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return 1

        backend = start_backend(env, port, log)
        if not wait_health(base):
            report["error"] = "backend_a_not_healthy"
            report["log"] = log.read_text(encoding="utf-8", errors="replace")[-1500:]
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return 1
        report["backend_a_pid"] = backend.pid

        status, auth = call(
            base + "/auth/register",
            payload={"email": "e2e@example.com", "password": "e2e-password-123456"},
        )
        token = auth.get("access_token") or auth.get("token") or auth.get("jwt") or ""
        report["register_status"] = status
        if not token:
            report["auth_payload"] = auth
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return 1

        _, chat = call(base + "/chats", token, {})
        chat_id = chat["id"]
        stream_body = {
            "content": "Создай локальное приложение: файлы part1.txt, part2.txt, part3.txt и final.txt в рабочем каталоге. Это автономная задача с инструментами.",
            "web_mode": "off",
            "computer_mode": "trusted",
            "tor_mode": "off",
        }
        import threading

        stream_result: dict = {}

        def stream(url: str, payload: dict) -> None:
            try:
                request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
                request.add_header("Content-Type", "application/json")
                request.add_header("Authorization", f"Bearer {token}")
                with urllib.request.urlopen(request, timeout=600) as response:
                    stream_result["text"] = response.read().decode()
            except Exception as error:  # noqa: BLE001 - the kill is expected to break the stream
                stream_result["error"] = f"{type(error).__name__}: {error}"

        thread = threading.Thread(target=stream, args=(f"{base}/chats/{chat_id}/stream", stream_body))
        thread.start()
        before_task, _, before_runs = "", 0, 0
        for _ in range(180):
            before_task, status_name, before_runs = db_counts(env)
            if before_task and before_runs >= 3:
                break
            time.sleep(1)
        report["before"] = {"task": before_task, "status": status_name, "completed_runs": before_runs}
        if before_runs < 3:
            report["error"] = "no_real_tool_progress"
            report["log"] = log.read_text(encoding="utf-8", errors="replace")[-2000:]
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return 1
        if status_name == "COMPLETED":
            report["error"] = "task_completed_before_the_interrupt"
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return 1

        backend.terminate()
        backend.wait(40)
        report["backend_a_exit"] = backend.returncode
        thread.join(timeout=30)
        report["stream_after_kill"] = stream_result.get("error", "no_error")
        _, after_death_status, _ = db_counts(env)
        report["status_after_death"] = after_death_status

        backend = start_backend(env, port, log)
        report["backend_b_pid"] = backend.pid
        if not wait_health(base):
            report["error"] = "backend_b_not_healthy"
            return 1
        thread = threading.Thread(
            target=stream, args=(f"{base}/tasks/{before_task}/resume", {})
        )
        thread.start()
        for _ in range(240):
            _, final_status, final_runs = db_counts(env)
            if final_status == "COMPLETED" and final_runs > before_runs:
                break
            time.sleep(1)
        thread.join(timeout=60)
        same_task, final_status, final_runs = db_counts(env)
        report["after"] = {"task": same_task, "status": final_status, "completed_runs": final_runs}
        report["same_task"] = same_task == before_task
        report["new_progress"] = final_runs > before_runs

        code_check = (
            "from app.database import SessionLocal\n"
            "from app.tools.models import TaskEvent\n"
            "with SessionLocal() as db:\n"
            "    rows = db.query(TaskEvent).filter(TaskEvent.kind == 'COMPLETED').all()\n"
            "    print(len(rows))\n"
        )
        completed = subprocess.run(
            [PY, "-c", code_check], cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=60
        )
        report["completed_events"] = int((completed.stdout or "0").strip() or 0)

        files = sorted(p.name for p in Path(work).rglob("part*.txt")) + sorted(
            p.name for p in Path(work).rglob("final.txt")
        )
        report["files"] = files
        ok = (
            report["same_task"]
            and report["new_progress"]
            and final_status == "COMPLETED"
            and report["completed_events"] == 1
            and report["backend_a_exit"] is not None
            and report["backend_b_pid"] != report["backend_a_pid"]
            and {"part1.txt", "part2.txt", "part3.txt", "final.txt"} <= set(files)
        )
        report["verdict"] = "PASS" if ok else "FAIL"
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if ok else 1
    finally:
        for process in (backend, fake):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(20)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
