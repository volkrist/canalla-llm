"""Windows UI Automation acceptance on a real desktop application.

The transport is the test host, but the automation is the product's own script (`uia.ps1`) driving
a real, compiled WinForms program through the Windows UI Automation API: the same parameter file,
the same post-action observation, the same JSON answer the packaged host returns.

Status this test can prove: WINDOWS LIVE PASS for the UIA adapter itself. It does not prove the
packaged Rust host's dispatch (that is `cargo check` plus the fake-host integration here) and it is
not a Qwen run — the planner is scripted.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Message
from app.tools.executor import ExecutionContext, ToolExecutor
from app.tools.local import machine
from app.tools.models import LocalTask
from app.tools.orchestrator import ToolOrchestrator
from app.tools.policy import ToolLimits, WebSettings
from app.tools.registry import make_registry

WINDOW = "Canalla Acceptance App"
MARKER = "UIA_ACCEPTANCE_417"
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
SOURCE = Path(__file__).resolve().parent / "fixtures" / "uia_acceptance_app.cs"

PROMPT = (
    "Открой тестовое приложение, введи текст, сохрани файл, закрой программу, открой снова и "
    "подтверди содержимое."
)


def _build_app(target: Path) -> Path:
    exe = target / "AcceptanceApp.exe"
    completed = subprocess.run(
        [str(CSC), "/nologo", "/target:winexe", f"/out:{exe}", str(SOURCE)],
        capture_output=True,
        text=True,
        timeout=180,
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
        self.exe = exe
        self.out_file = out_file
        self.shots = shots
        self.step = 0

    async def plan_tools(self, messages, tools, usage):
        self.step += 1
        window = WINDOW
        sequence = {
            1: (
                "run_process",
                {
                    "executable": str(self.exe),
                    "argv": [str(self.out_file)],
                    "wait": False,
                    "purpose": "open the application",
                },
            ),
            2: ("window_inspect", {"action": "wait_window", "window": window, "timeout_ms": 15000}),
            3: (
                "window_act",
                {"action": "set_value", "window": window, "class_name": "EDIT", "value": MARKER},
            ),
            4: ("window_inspect", {"action": "get_text", "window": window, "class_name": "EDIT"}),
            5: ("window_act", {"action": "invoke", "window": window, "name": "Save"}),
            6: (
                "verify_result",
                {
                    "checks": [
                        {"kind": "file_contains", "target": str(self.out_file), "expected": MARKER},
                    ],
                    "purpose": "the document was saved",
                },
            ),
            7: (
                "window_inspect",
                {"action": "screenshot", "window": window, "path": str(self.shots / "01-saved.png")},
            ),
            8: ("window_act", {"action": "close_window", "window": window}),
            9: (
                "run_process",
                {
                    "executable": str(self.exe),
                    "argv": [str(self.out_file)],
                    "wait": False,
                    "purpose": "reopen the saved document",
                },
            ),
            10: ("window_inspect", {"action": "wait_window", "window": window, "timeout_ms": 15000}),
            11: ("window_inspect", {"action": "get_text", "window": window, "class_name": "EDIT"}),
            12: (
                "window_inspect",
                {"action": "screenshot", "window": window, "path": str(self.shots / "02-reopened.png")},
            ),
            13: (
                "verify_result",
                {
                    "checks": [
                        {
                            "kind": "file_contains",
                            "target": str(self.out_file) + ".loaded",
                            "expected": MARKER,
                        },
                        {"kind": "file_exists", "target": str(self.shots / "01-saved.png")},
                        {"kind": "file_exists", "target": str(self.shots / "02-reopened.png")},
                    ],
                    "purpose": "reopened document and screenshots",
                },
            ),
            14: ("window_act", {"action": "close_window", "window": window}),
        }
        if self.step in sequence:
            name, arguments = sequence[self.step]
            return _call(name, arguments, f"step-{self.step}")
        return {"tool_calls": []}


@pytest.mark.skipif(sys.platform != "win32" or not CSC.is_file(), reason="needs Windows and csc.exe")
def test_uia_drives_a_real_application_and_proves_the_result(client, auth, tmp_path):
    from tests.fake_host import FakeHost

    exe = _build_app(tmp_path)
    out_file = tmp_path / "uia_out.txt"
    shots = tmp_path / "shots"
    shots.mkdir()

    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "trusted", "workspace_roots": [str(tmp_path)]},
    )
    paired = client.post("/tools/devices/pair", headers=headers, json={"platform": "windows"}).json()
    device_headers = {
        **headers,
        "X-Alex-Device-Id": paired["device_id"],
        "X-Alex-Device-Credential": paired["credential"],
    }
    client.post("/tools/devices/heartbeat", headers=device_headers)
    with SessionLocal() as db:
        message = Message(chat_id=chat_id, role="assistant", content="")
        db.add(message)
        db.commit()
        generation_id = message.id

    async def emit(event, value):
        return None

    context = ExecutionContext(
        user_id,
        chat_id,
        generation_id,
        ToolLimits(max_calls=30, hard_max_calls=40),
        emit,
        mode="off",
        computer_mode="trusted",
        settings=WebSettings(computer_mode="trusted", workspace_roots=[str(tmp_path)], default_mode="off"),
        user_prompt=PROMPT,
        host_online=True,
    )
    host = FakeHost(client, device_headers, tmp_path).start()
    try:
        orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
        asyncio.run(
            orchestrator.prepare(
                Planner(exe, out_file, shots), [{"role": "user", "content": PROMPT}], 0, context, {}
            )
        )
    finally:
        host.close()

    # The application really saved and really reloaded the exact document.
    assert out_file.read_text(encoding="utf-8") == MARKER
    assert (tmp_path / "uia_out.txt.loaded").read_text(encoding="utf-8") == MARKER
    assert (shots / "01-saved.png").stat().st_size > 1000
    assert (shots / "02-reopened.png").stat().st_size > 1000

    with SessionLocal() as db:
        row = db.scalar(select(LocalTask).where(LocalTask.id == context.task_id))
        assert row is not None
        assert row.status == machine.COMPLETED, (row.status, row.last_error, row.completion_summary)
        verification = row.verification or {}
        assert verification.get("evidence_ok") is True, verification.get("evidence")
