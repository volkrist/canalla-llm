import asyncio
import threading
import time

import pytest

from app.database import SessionLocal
from app.models import Message
from app.tools.contracts import ToolError
from app.tools.executor import ExecutionContext, ToolExecutor
from app.tools.local.paths import assert_allowed_path
from app.tools.local.secrets import deny_secret
from app.tools.models import ToolRun
from app.tools.policy import ToolLimits, ToolPolicy, WebSettings
from app.tools.registry import make_registry


def test_secret_and_path_policy():
    deny_secret(r"C:\work\notes.txt")
    with pytest.raises(ToolError):
        deny_secret(r"C:\work\.env")
    with pytest.raises(ToolError):
        deny_secret(r"C:\Users\me\.ssh\id_ed25519")
    assert_allowed_path(r"C:\AlexWorkspace\file.txt", [r"C:\AlexWorkspace"])
    with pytest.raises(ToolError):
        assert_allowed_path(r"C:\Windows\System32\cmd.exe", [r"C:\AlexWorkspace"])
    with pytest.raises(ToolError):
        assert_allowed_path(r"C:\AlexWorkspace\..\Windows\win.ini", [r"C:\AlexWorkspace"])


def test_trusted_vs_process_confirmation():
    policy = ToolPolicy()
    registry = make_registry()
    read = registry.get("read_file")[0]
    proc = registry.get("run_python")[0]
    settings = WebSettings(computer_mode="trusted", workspace_roots=[r"C:\AlexWorkspace"])
    assert policy.validate(read, settings, mode="off", computer_mode="trusted") == "allowed"
    assert policy.validate(proc, settings, mode="off", computer_mode="trusted") == "confirmation_required"
    with pytest.raises(Exception):
        policy.validate(read, settings, mode="off", computer_mode="off")


def test_web_off_does_not_block_computer_policy():
    policy = ToolPolicy()
    read = make_registry().get("list_directory")[0]
    assert policy.validate(read, WebSettings(), mode="off", computer_mode="ask") == "confirmation_required"


def test_sanitized_child_environment_drops_secrets():
    from app.tools.local.env import sanitized_environment

    env = sanitized_environment(
        {
            "SystemRoot": r"C:\Windows",
            "PATH": r"C:\Windows\System32;C:\Users\me\bin",
            "RUNPOD_API_KEY": "secret",
            "TINYFISH_API_KEY": "secret",
            "LLM_API_KEY": "secret",
            "JWT_SECRET": "secret",
            "ALEX_DEVICE_CREDENTIAL": "secret",
            "TEMP": r"C:\Windows\Temp",
        }
    )
    assert "RUNPOD_API_KEY" not in env
    assert "TINYFISH_API_KEY" not in env
    assert "JWT_SECRET" not in env
    assert env["PATH"].lower().startswith(r"c:\windows")
    assert r"C:\Users\me\bin" not in env["PATH"]


def test_forbidden_tools_are_unregistered():
    registry = make_registry()
    for name in (
        "delete_file",
        "delete_directory",
        "registry_write",
        "service_install",
        "shutdown",
        "elevate",
    ):
        with pytest.raises(Exception):
            registry.get(name)


def test_local_host_result_requires_device(setup, client):
    headers, context, events = setup
    context.computer_mode = "trusted"
    context.mode = "off"
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "trusted", "workspace_roots": [r"C:\AlexWorkspace"]},
    )
    with pytest.raises(Exception):
        asyncio.run(
            ToolExecutor(client.app.state.tools).execute(
                "read_file", {"path": r"C:\AlexWorkspace\notes.txt"}, context
            )
        )


@pytest.fixture
def setup(client, auth):
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    with SessionLocal() as db:
        message = Message(chat_id=chat_id, role="assistant", content="")
        db.add(message)
        db.commit()
        generation_id = message.id
    events = []

    async def emit(event, value):
        events.append((event, value))

    context = ExecutionContext(
        user_id, chat_id, generation_id, ToolLimits(), emit, mode="off", computer_mode="trusted"
    )
    return headers, context, events


def test_paired_device_host_result(setup, client):
    headers, context, events = setup
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "trusted", "workspace_roots": [r"C:\AlexWorkspace"]},
    )
    paired = client.post(
        "/tools/devices/pair",
        headers=headers,
        json={"display_name": "Test-PC", "platform": "windows"},
    ).json()
    assert "credential" in paired and paired["device_id"]
    device_headers = {
        **headers,
        "X-Alex-Device-Id": paired["device_id"],
        "X-Alex-Device-Credential": paired["credential"],
    }
    assert client.post("/tools/devices/heartbeat", headers=device_headers).status_code == 200

    def host():
        for _ in range(80):
            jobs = client.get("/tools/devices/jobs", headers=device_headers).json()
            if jobs:
                job = jobs[0]
                with SessionLocal() as db:
                    row = db.get(ToolRun, job["id"])
                    digest_value = row.input_digest
                response = client.post(
                    f"/tools/runs/{job['id']}/host-result",
                    headers=device_headers,
                    json={
                        "digest": digest_value,
                        "status": "completed",
                        "exit_code": 0,
                        "stdout": "file-contents",
                        "stderr": "",
                        "text": "file-contents",
                        "metadata": {"after_sha256": "abc"},
                    },
                )
                assert response.status_code == 200
                return
            time.sleep(0.05)

    worker = threading.Thread(target=host, daemon=True)
    worker.start()
    result = asyncio.run(
        ToolExecutor(client.app.state.tools).execute(
            "read_file", {"path": r"C:\AlexWorkspace\notes.txt"}, context, origin="model"
        )
    )
    worker.join(timeout=2)
    assert "file-contents" in result.text
    runs = client.get("/tools/runs", headers=headers).json()
    assert runs[0]["origin"] == "model"
    assert client.post(
        f"/tools/runs/{runs[0]['id']}/host-result",
        headers=device_headers,
        json={
            "digest": "0" * 64,
            "stdout": "replay",
            "stderr": "",
            "text": "replay",
            "metadata": {},
        },
    ).status_code in {404, 409}
    other = client.post(
        "/auth/register", json={"email": "device-thief@example.com", "password": "test-password-123"}
    ).json()
    stolen = {
        "Authorization": "Bearer " + other["access_token"],
        "X-Alex-Device-Id": paired["device_id"],
        "X-Alex-Device-Credential": paired["credential"],
    }
    assert client.post("/tools/devices/heartbeat", headers=stolen).status_code == 401
    listed = client.get("/tools/runs", headers=headers).json()
    assert "host_args" not in (listed[0].get("result_metadata") or {})
    jwt_only = client.post(
        f"/tools/runs/{listed[0]['id']}/host-result",
        headers=headers,
        json={
            "digest": "0" * 64,
            "stdout": "forged",
            "stderr": "",
            "text": "forged",
            "metadata": {},
        },
    )
    assert jwt_only.status_code == 401


def test_host_result_rejects_user_jwt_without_device(setup, client):
    headers, _context, _events = setup
    response = client.post(
        "/tools/runs/00000000-0000-0000-0000-000000000001/host-result",
        headers=headers,
        json={
            "digest": "0" * 64,
            "stdout": "",
            "stderr": "",
            "text": "forged",
            "metadata": {},
        },
    )
    assert response.status_code == 401


def test_planner_hides_paid_tools_and_keeps_channels_independent():
    from types import SimpleNamespace

    from app.tools.orchestrator import ToolOrchestrator

    orch = ToolOrchestrator(make_registry(), None)
    local_on = SimpleNamespace(
        mode="off",
        tor_enabled=True,
        computer_mode="ask",
        assigned_device_id="device",
        host_online=True,
    )
    names = {item.name for item in orch.planner_definitions(local_on)}
    assert "web_search" not in names
    assert "web_agent_read" not in names
    assert "browser_start" not in names
    assert "tor_search" in names
    assert "read_file" in names
    assert "run_python" in names
    web_only = SimpleNamespace(
        mode="on",
        tor_enabled=False,
        computer_mode="off",
        assigned_device_id=None,
        host_online=False,
    )
    names = {item.name for item in orch.planner_definitions(web_only)}
    assert "web_search" in names and "web_fetch" in names
    assert "read_file" not in names
    assert "tor_search" not in names
    assert "web_agent_read" not in names
