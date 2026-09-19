import asyncio
import threading
import time

import pytest

from app.database import SessionLocal
from app.models import Message
from app.tools.contracts import ToolError
from app.tools.executor import ExecutionContext, ToolExecutor
from app.tools.local.paths import assert_allowed_path, assert_local_path, inside_trusted
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
    assert_local_path(r"C:\AlexWorkspace\file.txt")
    assert_local_path(r"C:\Windows\System32\cmd.exe")
    assert_allowed_path(r"D:\mounted\share-file.txt", [r"C:\AlexWorkspace"])
    assert not inside_trusted(r"C:\Windows\System32\cmd.exe", [r"C:\AlexWorkspace"])
    assert inside_trusted(r"C:\AlexWorkspace\file.txt", [r"C:\AlexWorkspace"])
    with pytest.raises(ToolError):
        assert_local_path(r"C:\AlexWorkspace\..\Windows\win.ini")
    with pytest.raises(ToolError):
        assert_local_path(r"\\server\share\file.txt")


def test_trusted_reduces_normal_change_confirmations():
    policy = ToolPolicy()
    registry = make_registry()
    read = registry.get("read_file")[0]
    write = registry.get("write_file")[0]
    proc = registry.get("run_python")[0]
    delete = registry.get("delete_file")[0]
    critical = registry.get("system_shutdown")[0]
    settings = WebSettings(computer_mode="trusted", workspace_roots=[r"C:\AlexWorkspace"])
    from app.tools.local.provider import InterpreterArgs, PathArgs, ShutdownArgs, WriteArgs

    inside = PathArgs(path=r"C:\AlexWorkspace\notes.txt")
    outside = PathArgs(path=r"C:\Windows\System32\drivers\etc\hosts")
    assert policy.validate(read, settings, mode="off", computer_mode="trusted", args=inside) == "allowed"
    assert policy.validate(read, settings, mode="off", computer_mode="trusted", args=outside) == "allowed"
    assert (
        policy.validate(
            write,
            settings,
            mode="off",
            computer_mode="trusted",
            args=WriteArgs(path=r"C:\AlexWorkspace\a.txt", content="x"),
        )
        == "allowed"
    )
    assert (
        policy.validate(
            write,
            settings,
            mode="off",
            computer_mode="trusted",
            args=WriteArgs(path=r"C:\Temp\a.txt", content="x"),
        )
        == "confirmation_required"
    )
    assert (
        policy.validate(
            proc,
            settings,
            mode="off",
            computer_mode="trusted",
            args=InterpreterArgs(argv=["-c", "print(1)"], cwd=r"C:\AlexWorkspace"),
        )
        == "allowed"
    )
    assert (
        policy.validate(
            proc, settings, mode="off", computer_mode="trusted", args=InterpreterArgs(argv=["-c", "print(1)"])
        )
        == "allowed"
    )
    assert (
        policy.validate(delete, settings, mode="off", computer_mode="trusted", args=inside)
        == "confirmation_required"
    )
    assert (
        policy.validate(
            critical,
            settings,
            mode="off",
            computer_mode="trusted",
            args=ShutdownArgs(action="shutdown"),
            confirmed=True,
        )
        == "allowed"
    )
    assert (
        policy.validate(
            critical, settings, mode="off", computer_mode="trusted", args=ShutdownArgs(action="shutdown")
        )
        == "confirmation_required"
    )
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


def test_sensitive_tools_are_registered_and_gated():
    registry = make_registry()
    for name in (
        "delete_file",
        "delete_directory",
        "registry_write",
        "windows_service_control",
        "install_software",
        "uninstall_software",
        "system_shutdown",
        "format_volume",
        "credential_use",
    ):
        definition = registry.get(name)[0]
        assert definition.provider == "local_device"
    settings = WebSettings(computer_mode="trusted", workspace_roots=[r"C:\AlexWorkspace"])
    policy = ToolPolicy()
    from app.tools.local.provider import PathArgs

    delete = registry.get("delete_file")[0]
    assert (
        policy.validate(
            delete,
            settings,
            mode="off",
            computer_mode="trusted",
            args=PathArgs(path=r"C:\AlexWorkspace\cache"),
        )
        == "confirmation_required"
    )


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
    assert "tor_search" not in names
    assert "read_file" in names
    assert "run_python" in names
    assert "delete_file" in names
    assert "registry_write" in names
    assert "patch_file" in names
    assert "git_status" in names
    assert "git_push" not in names
    local_on.user_prompt = "Через Tor найди официальный onion-ресурс Tor Project."
    names = {item.name for item in orch.planner_definitions(local_on)}
    assert "tor_search" in names
    assert "tor_fetch" in names
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


def test_pairing_defaults_to_windows_device(setup, client):
    headers, _context, _events = setup
    paired = client.post("/tools/devices/pair", headers=headers, json={"platform": "windows"}).json()
    assert paired["display_name"] == "Windows device"
    assert "credential" in paired
    assert len(paired["credential"]) >= 43
    forgotten = client.post(f"/tools/devices/{paired['device_id']}/forget", headers=headers)
    assert forgotten.status_code == 200
    listed = client.get("/tools/devices", headers=headers).json()
    assert listed == []
    assert client.post(f"/tools/devices/{paired['device_id']}/forget", headers=headers).status_code == 404


def test_sensitive_explanation_and_immutable_digest(setup, client):
    from pydantic import ValidationError

    from app.tools.local.credentials import LocalCredentialProvider
    from app.tools.routes import Confirmation
    from app.tools.security import digest, input_summary

    headers, context, events = setup
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "ask", "workspace_roots": [r"C:\AlexWorkspace"]},
    )
    paired = client.post(
        "/tools/devices/pair",
        headers=headers,
        json={"display_name": "PC-1", "platform": "windows"},
    ).json()
    device_headers = {
        **headers,
        "X-Alex-Device-Id": paired["device_id"],
        "X-Alex-Device-Credential": paired["credential"],
    }
    client.post("/tools/devices/heartbeat", headers=device_headers)
    registry = client.app.state.tools
    definition = registry.get("delete_file")[0]
    args = definition.input_model.model_validate(
        {"path": r"C:\AlexWorkspace\cache", "purpose": "старый cache мешает обновлению."}
    )
    summary = input_summary(definition, args)
    assert summary["reason"] == "старый cache мешает обновлению."
    assert "cache" in summary["action_detail"] or "cache" in summary["target"]
    assert summary["risk_level"] == "SENSITIVE"
    assert summary["elevation_required"] in {"yes", "no"}
    first = digest(args.model_dump(mode="json"), "delete_file")
    changed = digest({**args.model_dump(mode="json"), "path": r"C:\Windows\cache"}, "delete_file")
    assert first != changed
    assert digest(args.model_dump(mode="json"), "read_file") != first
    with pytest.raises(ValidationError):
        Confirmation.model_validate({"allow": True, "always": True})
    with pytest.raises(ToolError, match="credentials_stay_on_host"):
        LocalCredentialProvider().resolve("local")

    def host():
        for _ in range(80):
            jobs = client.get("/tools/devices/jobs", headers=device_headers).json()
            if jobs:
                job = jobs[0]
                with SessionLocal() as db:
                    row = db.get(ToolRun, job["id"])
                    digest_value = row.input_digest
                    assert row.input_summary["reason"]
                    assert row.input_summary["risk_level"] == "SENSITIVE"
                client.post(
                    f"/tools/runs/{job['id']}/host-result",
                    headers=device_headers,
                    json={
                        "digest": digest_value,
                        "status": "completed",
                        "text": "deleted",
                        "stdout": "deleted",
                        "stderr": "",
                        "metadata": {"files_changed": 1},
                    },
                )
                return
            time.sleep(0.05)

    async def confirm(event, value):
        events.append((event, value))
        if value.get("status") == "waiting_confirmation":
            assert "Always allow" not in str(value)
            assert value["input_summary"]["risk_level"] == "SENSITIVE"
            client.post(f"/tools/runs/{value['id']}/confirm", headers=headers, json={"allow": True})

    context.emit = confirm
    context.computer_mode = "ask"
    worker = threading.Thread(target=host, daemon=True)
    worker.start()
    result = asyncio.run(
        ToolExecutor(registry).execute(
            "delete_file",
            {"path": r"C:\AlexWorkspace\cache", "purpose": "старый cache мешает обновлению."},
            context,
        )
    )
    worker.join(timeout=2)
    assert "deleted" in result.text
    with pytest.raises(ValidationError):
        definition.input_model.model_validate({"path": r"C:\x", "secret": "password"})


def test_network_channel_and_no_tor_fallback():
    import inspect

    from app.tools.policy import network_channel
    from app.tools.tor import provider as tor_provider

    registry = make_registry()
    assert network_channel(registry.get("web_search")[0]) == "direct"
    assert network_channel(registry.get("tor_search")[0]) == "tor"
    assert network_channel(registry.get("read_file")[0]) is None
    source = inspect.getsource(tor_provider)
    assert "TinyFishSearchProvider" not in source
    assert "TinyFishClient" not in source
    assert "TorTransport" in source


def test_registry_key_policy_does_not_execute():
    from app.tools.local.registry_keys import assert_registry_key

    assert assert_registry_key("HKCU", r"Software\AlexLLM\Test") == r"Software\AlexLLM\Test"
    with pytest.raises(ToolError):
        assert_registry_key("HKCU", r"..\Windows")
    with pytest.raises(ToolError):
        assert_registry_key("HKCU", r"SAM\SAM")


def test_explicit_execute_uses_preference_computer_mode(setup, client):
    headers, context, _events = setup
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "trusted", "workspace_roots": [r"C:\AlexWorkspace"]},
    )
    paired = client.post(
        "/tools/devices/pair", headers=headers, json={"display_name": "E2E-PC", "platform": "windows"}
    ).json()
    device_headers = {
        **headers,
        "X-Alex-Device-Id": paired["device_id"],
        "X-Alex-Device-Credential": paired["credential"],
    }
    client.post("/tools/devices/heartbeat", headers=device_headers)

    def host():
        for _ in range(80):
            jobs = client.get("/tools/devices/jobs", headers=device_headers).json()
            if jobs:
                job = jobs[0]
                with SessionLocal() as db:
                    digest_value = db.get(ToolRun, job["id"]).input_digest
                client.post(
                    f"/tools/runs/{job['id']}/host-result",
                    headers=device_headers,
                    json={
                        "digest": digest_value,
                        "status": "completed",
                        "text": "platform=windows",
                        "stdout": "platform=windows",
                        "stderr": "",
                        "metadata": {},
                    },
                )
                return
            time.sleep(0.05)

    worker = threading.Thread(target=host, daemon=True)
    worker.start()
    with client.stream(
        "POST",
        "/tools/execute",
        headers=headers,
        json={"chat_id": context.chat_id, "name": "get_system_info", "arguments": {}},
    ) as response:
        body = "".join(response.iter_text())
    worker.join(timeout=2)
    assert "computer_disabled" not in body
    assert "tool_result" in body or "done" in body
    runs = client.get("/tools/runs", headers=headers, params={"chat_id": context.chat_id}).json()
    assert runs and runs[0]["origin"] == "explicit"
    listed = client.get("/tools/devices", headers=headers).json()
    assert "credential" not in listed[0]
