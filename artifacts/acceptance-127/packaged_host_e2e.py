"""Packaged native host acceptance: real backend, real host binary, real UI Automation.

Chain under test:

    Canalla backend (uvicorn, isolated DB)
      -> ToolExecutor
      -> Computer Connect (pair + heartbeat + job claim over HTTP)
      -> alex-host-loop.exe (release build from this source tree)
      -> uia.ps1 (embedded in that binary)
      -> Windows UI Automation
      -> a compiled WinForms application

Nothing here is a fake host: the job is claimed over the product's own HTTP API by the packaged
binary and the automation runs in that binary's process.

Run:  python artifacts/acceptance-127/packaged_host_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "apps" / "backend"
HOST_EXE = REPO / "apps" / "desktop" / "src-tauri" / "target" / "release" / "alex-host-loop.exe"
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
APP_SOURCE = BACKEND / "tests" / "fixtures" / "uia_acceptance_app.cs"
MARKER = "UIA_PACKAGED_417"
WINDOW = "Canalla Acceptance App"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http(method: str, url: str, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def build_app(target: Path) -> Path:
    exe = target / "AcceptanceApp.exe"
    completed = subprocess.run(
        [str(CSC), "/nologo", "/target:winexe", f"/out:{exe}", str(APP_SOURCE)],
        capture_output=True, text=True, timeout=180,
    )
    assert exe.is_file(), completed.stdout + completed.stderr
    return exe


def _call(name, arguments, call_id):
    return {
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            }
        ]
    }


class Planner:
    supports_tools = True

    def __init__(self, exe: Path, out_file: Path, shots: Path):
        self.exe, self.out_file, self.shots = exe, out_file, shots
        self.step = 0

    async def plan_tools(self, messages, tools, usage):
        self.step += 1
        window = WINDOW
        sequence = {
            1: ("run_process", {"executable": str(self.exe), "argv": [str(self.out_file)],
                                "wait": False, "purpose": "open the application"}),
            2: ("window_inspect", {"action": "wait_window", "window": window, "timeout_ms": 20000}),
            3: ("window_act", {"action": "set_value", "window": window,
                               "class_name": "EDIT", "value": MARKER}),
            4: ("window_inspect", {"action": "get_text", "window": window, "class_name": "EDIT"}),
            5: ("window_act", {"action": "invoke", "window": window, "name": "Save"}),
            6: ("verify_result", {"checks": [
                {"kind": "file_contains", "target": str(self.out_file), "expected": MARKER},
            ], "purpose": "the document was saved"}),
            7: ("window_inspect", {"action": "screenshot", "window": window,
                                   "path": str(self.shots / "01-saved.png")}),
            8: ("window_act", {"action": "close_window", "window": window}),
            9: ("run_process", {"executable": str(self.exe), "argv": [str(self.out_file)],
                                "wait": False, "purpose": "reopen the saved document"}),
            10: ("window_inspect", {"action": "wait_window", "window": window, "timeout_ms": 20000}),
            11: ("window_inspect", {"action": "get_text", "window": window, "class_name": "EDIT"}),
            12: ("window_inspect", {"action": "screenshot", "window": window,
                                    "path": str(self.shots / "02-reopened.png")}),
            13: ("verify_result", {"checks": [
                {"kind": "file_contains", "target": str(self.out_file) + ".loaded",
                 "expected": MARKER},
                {"kind": "file_exists", "target": str(self.shots / "01-saved.png")},
                {"kind": "file_exists", "target": str(self.shots / "02-reopened.png")},
            ], "purpose": "reopened document and screenshots"}),
            14: ("window_act", {"action": "close_window", "window": window}),
        }
        if self.step in sequence:
            name, arguments = sequence[self.step]
            return _call(name, arguments, f"p-{self.step}")
        return {"tool_calls": []}


class ReconnectPlanner:
    """After a host restart: can the new process still see the desktop?"""

    supports_tools = True

    def __init__(self):
        self.step = 0

    async def plan_tools(self, messages, tools, usage):
        self.step += 1
        if self.step == 1:
            return _call("window_inspect", {"action": "list_windows"}, "r-1")
        return {"tool_calls": []}


def main() -> int:
    if not HOST_EXE.is_file():
        raise SystemExit(f"build the host first: {HOST_EXE}")
    work = Path(tempfile.mkdtemp(prefix="canalla-packaged-"))
    root = work / "workspace"
    shots = work / "shots"
    root.mkdir()
    shots.mkdir()
    exe = build_app(work)
    out_file = root / "uia_out.txt"

    database = work / "host.db"
    os.environ.update(
        DATABASE_URL="sqlite:///" + database.as_posix(),
        JWT_SECRET=secrets.token_urlsafe(48),
        APP_ENV="test",
        LLM_PROVIDER="mock",
        MOCK_DELAY="0",
        RUNPOD_API_KEY="",
        TINYFISH_API_KEY="",
        COMPUTE_BACKGROUND_ENABLED="false",
        ADMIN_EMAILS="[]",
        ALLOW_USER_COMPUTE_START="false",
        DOCUMENT_STORAGE_DIR=str(work / "documents"),
        ALEX_LLM_DATA_DIR=str(work / "app-data"),
    )
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   cwd=str(BACKEND), check=True, capture_output=True)
    sys.path.insert(0, str(BACKEND))

    from app.database import SessionLocal
    from app.models import Message
    from app.tools.executor import ExecutionContext, ToolExecutor
    from app.tools.local.devices import active_device
    from app.tools.models import LocalTask
    from app.tools.orchestrator import ToolOrchestrator
    from app.tools.policy import ToolLimits, WebSettings
    from app.tools.registry import make_registry

    from app.main import app
    import uvicorn

    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            status, _ = http("GET", base + "/health")
            if status == 200:
                break
        except OSError:
            pass
        time.sleep(0.1)
    else:
        raise SystemExit("backend did not start")

    email = f"packaged-{secrets.token_hex(4)}@example.com"
    _, registered = http("POST", base + "/auth/register",
                         {"email": email, "password": "packaged-password-123"})
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
        "ALEX_DEVICE_NAME": "Packaged UIA acceptance",
        "ALEX_WORKSPACE_ROOTS": os.pathsep.join([str(root), str(work)]),
        "ALEX_DEVICE_DIR": str(work / "device"),
        "ALEX_DEVICE_CREDENTIAL_TARGET": "Canalla Acceptance/packaged-uia",
    }
    host_log = (work / "host.log").open("w", encoding="utf-8", errors="replace")
    host = subprocess.Popen([str(HOST_EXE)], env=host_env, stdout=host_log,
                            stderr=subprocess.STDOUT, text=True)

    def device_row():
        with SessionLocal() as db:
            return active_device(db, user_id)

    try:
        device = None
        for _ in range(150):
            device = device_row()
            if device is not None and device.last_seen is not None:
                break
            time.sleep(0.1)
        assert device is not None, "the packaged host never paired"

        with SessionLocal() as db:
            message = Message(chat_id=chat_id, role="assistant", content="")
            db.add(message)
            db.commit()
            generation_id = message.id

        async def emit(event, value):
            return None

        def context():
            ctx = ExecutionContext(
                user_id, chat_id, generation_id, ToolLimits(max_calls=40, hard_max_calls=60), emit,
                mode="off", computer_mode="trusted",
                settings=WebSettings(computer_mode="trusted", workspace_roots=[str(root), str(work)],
                                     default_mode="off"),
                user_prompt="Открой тестовое приложение, введи текст, сохрани файл и подтверди содержимое",
                host_online=True,
            )
            ctx.assigned_device_id = device.id
            return ctx

        ctx = context()
        orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
        asyncio.run(orchestrator.prepare(
            Planner(exe, out_file, shots),
            [{"role": "user", "content": "Открой тестовое приложение, введи текст, сохрани файл и подтверди содержимое"}], 0, ctx, {},
        ))
        task_id = ctx.task_id
        with SessionLocal() as db:
            row = db.get(LocalTask, task_id) if task_id else None
            task_state = row.status if row else None
            evidence_ok = bool((row.verification or {}).get("evidence_ok")) if row else False

        # The same turn records the model→tool chain. A scripted planner must never be read as a
        # live model, but the absence of any chain would mean the recording path is broken.
        from app.agent import provenance as provenance_module
        from app.config import get_settings

        with SessionLocal() as db:
            events, runs = provenance_module.from_task(db, task_id) if task_id else ([], [])
        provenance_verdict = provenance_module.classify(
            events, secret=get_settings().jwt_secret, tool_runs=runs
        )

        saved = out_file.read_text(encoding="utf-8") if out_file.is_file() else ""
        reloaded = (
            Path(str(out_file) + ".loaded").read_text(encoding="utf-8")
            if Path(str(out_file) + ".loaded").is_file() else ""
        )
        shots_made = sorted(path.name for path in shots.glob("*.png"))

        # -- reconnect: stop the packaged host, start it again, use it once more ----
        host.terminate()
        try:
            host.wait(timeout=10)
        except subprocess.TimeoutExpired:
            host.kill()
        time.sleep(1.5)
        host = subprocess.Popen([str(HOST_EXE)], env=host_env, stdout=host_log,
                                stderr=subprocess.STDOUT, text=True)
        for _ in range(150):
            again = device_row()
            if again is not None and again.last_seen is not None:
                break
            time.sleep(0.1)

        async def emit2(event, value):
            return None

        ctx2 = context()
        ctx2.emit = emit2
        ctx2.user_prompt = "List the desktop windows after the host restarted"
        asyncio.run(orchestrator.prepare(
            ReconnectPlanner(),
            [{"role": "user", "content": "List the desktop windows after the host restarted"}],
            0, ctx2, {},
        ))
        reconnect_ok = bool(ctx2.tool_transcript) or ctx2.task_id is not None

        payload = {
            "verdict": "PASS" if (
                task_state == "COMPLETED" and evidence_ok and saved == MARKER
                and reloaded == MARKER and len(shots_made) >= 2
                and provenance_verdict.model_responses >= 1
                and provenance_verdict.tool_calls >= 1
                and provenance_verdict.status == provenance_module.MOCK_MODEL
            ) else "FAIL",
            "provenance_status": provenance_verdict.status,
            "provenance_model_responses": provenance_verdict.model_responses,
            "provenance_tool_calls": provenance_verdict.tool_calls,
            "host_binary": str(HOST_EXE),
            "host_bytes": HOST_EXE.stat().st_size,
            "device_id": device.id,
            "task_id": task_id,
            "task_state": task_state,
            "evidence_ok": evidence_ok,
            "saved_document": saved,
            "reloaded_document": reloaded,
            "screenshots": shots_made,
            "reconnect_task_id": ctx2.task_id,
            "reconnect_observed": reconnect_ok,
            "host_log_tail": (work / "host.log").read_text(encoding="utf-8", errors="replace")[-1500:],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        out = REPO / "artifacts" / "acceptance-127" / "packaged-host"
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        for shot in shots.glob("*.png"):
            (out / shot.name).write_bytes(shot.read_bytes())
        (out / "saved-document.txt").write_text(saved, encoding="utf-8")
        return 0 if payload["verdict"] == "PASS" else 1
    finally:
        try:
            host.terminate()
            host.wait(timeout=10)
        except Exception:
            host.kill()
        host_log.close()
        server.should_exit = True
        server_thread.join(timeout=10)
        from app.database import engine as _engine

        _engine.dispose()
        # Remove exactly this run's own credential entry, then prove no test process survived.
        try:
            import ctypes
            from ctypes import wintypes

            adv = ctypes.windll.advapi32
            adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            adv.CredDeleteW.restype = wintypes.BOOL
            adv.CredDeleteW("Canalla Acceptance/packaged-uia", 1, 0)
        except Exception:
            pass
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-Process AcceptanceApp -ErrorAction SilentlyContinue | Stop-Process -Force"],
                       capture_output=True)
        # The evidence was copied into the repository above; the scratch tree is this run's own.
        import shutil

        for _ in range(20):
            shutil.rmtree(work, ignore_errors=True)
            if not work.exists():
                break
            time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
