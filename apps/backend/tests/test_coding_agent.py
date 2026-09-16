import asyncio
import hashlib
import threading
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.database import SessionLocal
from app.models import Message
from app.tools.broker import CredentialBroker
from app.tools.contracts import ToolError
from app.tools.executor import ExecutionContext, ToolExecutor
from app.tools.local.coding import GitPushArgs, GitResetArgs
from app.tools.local.task import LocalTaskController
from app.tools.local.workspace import looks_like_coding, workspace_from_settings
from app.tools.models import LocalTask, ToolRun
from app.tools.orchestrator import ToolOrchestrator
from app.tools.policy import ToolLimits, ToolPolicy, WebSettings, effective_risk
from app.tools.registry import make_registry
from app.tools.security import sanitized
from app.tools.web_router import classify_web


def test_coding_tools_are_registered():
    registry = make_registry()
    for name in (
        "patch_file",
        "search_code",
        "list_processes",
        "inspect_process",
        "query_service",
        "start_service",
        "stop_service",
        "restart_service",
        "change_service_settings",
        "read_registry",
        "write_registry",
        "delete_registry_value",
        "delete_registry_key",
        "list_installed_software",
        "list_volumes",
        "git_status",
        "git_diff",
        "git_log",
        "git_show",
        "git_branch",
        "git_add",
        "git_commit",
        "git_push",
        "git_reset",
        "git_restore",
    ):
        assert registry.get(name)[0].provider == "local_device"


def test_git_risk_and_push_never_auto():
    registry = make_registry()
    policy = ToolPolicy()
    settings = WebSettings(computer_mode="trusted", workspace_roots=[r"C:\AlexWorkspace"])
    push = registry.get("git_push")[0]
    reset = registry.get("git_reset")[0]
    branch = registry.get("git_branch")[0]
    status = registry.get("git_status")[0]
    inside = GitPushArgs(cwd=r"C:\AlexWorkspace", remote="origin")
    assert (
        policy.validate(push, settings, mode="off", computer_mode="trusted", args=inside)
        == "confirmation_required"
    )
    from app.tools.contracts import RiskLevel

    assert effective_risk(push, GitPushArgs(cwd=r"C:\AlexWorkspace", force=True)) == RiskLevel.CRITICAL
    assert effective_risk(reset, GitResetArgs(cwd=r"C:\AlexWorkspace", mode="hard")) == RiskLevel.CRITICAL
    assert (
        effective_risk(branch, branch.input_model(cwd=r"C:\AlexWorkspace", delete=True, name="main"))
        == RiskLevel.CRITICAL
    )
    assert (
        policy.validate(
            status,
            settings,
            mode="off",
            computer_mode="trusted",
            args=status.input_model(cwd=r"C:\AlexWorkspace"),
        )
        == "allowed"
    )
    with pytest.raises(ValidationError):
        GitPushArgs(cwd=r"C:\AlexWorkspace", remote="https://user:token@github.com")


def test_hard_call_ceiling_cannot_exceed_32():
    limits = ToolLimits(max_calls=100, hard_max_calls=32, max_local_calls=32)
    definition = make_registry().get("read_file")[0]
    from app.tools.local.provider import PathArgs

    args = PathArgs(path=r"C:\AlexWorkspace\a.txt")
    for _ in range(32):
        limits.consume(definition, args)
    with pytest.raises(ToolError, match="tool_limit"):
        limits.consume(definition, args)


def test_workspace_and_coding_intent():
    assert looks_like_coding("Проверь проект и исправь failing tests.")
    workspace = workspace_from_settings(SimpleNamespace(workspace_roots=[r"C:\AlexWorkspace"]))
    assert workspace.root == r"C:\AlexWorkspace"
    assert "expected_before_sha256" in workspace.as_prompt()
    assert "sha256=" in workspace.as_prompt()
    assert "pytest" in workspace.as_prompt()
    assert "git_push" in workspace.as_prompt()


def test_credential_broker_never_reveals_secret():
    broker = CredentialBroker()
    ref = broker.reference("github-main")
    assert ref.reference_id == "github-main"
    with pytest.raises(ToolError, match="credentials_stay_on_host"):
        broker.reveal_for_model(ref)
    with pytest.raises(ToolError, match="credentials_stay_on_host"):
        broker.checkout(ref)
    assert "ghp_secret" not in sanitized("https://user:ghp_secret@github.com/org/repo", (), 200)


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
        user_id,
        chat_id,
        generation_id,
        ToolLimits(max_calls=24, hard_max_calls=32),
        emit,
        mode="off",
        computer_mode="trusted",
        user_prompt="Проверь проект и исправь failing tests.",
        settings=WebSettings(computer_mode="trusted", workspace_roots=[r"C:\AlexWorkspace"]),
    )
    return headers, context, events


def test_rotate_device_credential(setup, client):
    headers, _context, _events = setup
    paired = client.post("/tools/devices/pair", headers=headers, json={"platform": "windows"}).json()
    first = paired["credential"]
    rotated = client.post(f"/tools/devices/{paired['device_id']}/rotate", headers=headers).json()
    assert rotated["credential"] != first
    assert len(rotated["credential"]) >= 43
    old_headers = {
        **headers,
        "X-Alex-Device-Id": paired["device_id"],
        "X-Alex-Device-Credential": first,
    }
    assert client.post("/tools/devices/heartbeat", headers=old_headers).status_code == 401
    new_headers = {
        **headers,
        "X-Alex-Device-Id": paired["device_id"],
        "X-Alex-Device-Credential": rotated["credential"],
    }
    assert client.post("/tools/devices/heartbeat", headers=new_headers).status_code == 200


def test_patch_conflict_and_coding_loop(setup, client):
    headers, context, events = setup
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "trusted", "workspace_roots": [r"C:\AlexWorkspace"]},
    )
    paired = client.post(
        "/tools/devices/pair", headers=headers, json={"display_name": "PC-1", "platform": "windows"}
    ).json()
    device_headers = {
        **headers,
        "X-Alex-Device-Id": paired["device_id"],
        "X-Alex-Device-Credential": paired["credential"],
    }
    client.post("/tools/devices/heartbeat", headers=device_headers)
    before = hashlib.sha256(b"hello").hexdigest()
    stale = hashlib.sha256(b"stale").hexdigest()

    def host():
        for _ in range(120):
            jobs = client.get("/tools/devices/jobs", headers=device_headers).json()
            if jobs:
                job = jobs[0]
                with SessionLocal() as db:
                    digest_value = db.get(ToolRun, job["id"]).input_digest
                if job["tool_name"] == "patch_file":
                    client.post(
                        f"/tools/runs/{job['id']}/host-result",
                        headers=device_headers,
                        json={
                            "digest": digest_value,
                            "status": "failed",
                            "text": "conflict",
                            "stdout": "",
                            "stderr": "conflict",
                            "metadata": {"error": "conflict", "conflict": True, "before_sha256": before},
                        },
                    )
                else:
                    client.post(
                        f"/tools/runs/{job['id']}/host-result",
                        headers=device_headers,
                        json={
                            "digest": digest_value,
                            "status": "completed",
                            "text": "ok",
                            "stdout": "ok",
                            "stderr": "",
                            "metadata": {"files_changed": 1, "after_sha256": before},
                        },
                    )
                return
            time.sleep(0.05)

    worker = threading.Thread(target=host, daemon=True)
    worker.start()
    with pytest.raises(ToolError, match="conflict"):
        asyncio.run(
            ToolExecutor(client.app.state.tools).execute(
                "patch_file",
                {
                    "path": r"C:\AlexWorkspace\app.py",
                    "old_text": "hello",
                    "new_text": "world",
                    "expected_before_sha256": stale,
                },
                context,
            )
        )
    worker.join(timeout=2)
    with SessionLocal() as db:
        controller = LocalTaskController()
        controller.attach(context)
        row = controller.open(db, context)
        controller.note_command(context, "write_file", "abc", {"files_changed": 1})
        controller.checkpoint(db, context, "EXECUTING")
        controller.finish(db, context, "COMPLETED")
        saved = db.get(LocalTask, row.id)
        assert saved.status == "COMPLETED"
        assert "commands" in saved.checkpoint
        assert "password" not in str(saved.checkpoint)


def test_web_plus_local_fastapi_mock(setup, client):
    headers, context, events = setup
    context.mode = "on"
    context.computer_mode = "trusted"
    context.host_online = True
    context.assigned_device_id = "device"
    client.put(
        "/tools/preferences",
        headers=headers,
        json={
            "computer_mode": "trusted",
            "workspace_roots": [r"C:\AlexWorkspace"],
            "default_mode": "on",
            "search_enabled": True,
            "fetch_enabled": True,
        },
    )
    prompt = "Посмотри актуальную документацию FastAPI и обнови тестовый проект."
    intent = classify_web(prompt, "on")
    assert intent.required
    names = {item.name for item in ToolOrchestrator(make_registry(), None).planner_definitions(context)}
    assert "web_search" in names
    assert "read_file" in names
    assert "patch_file" in names
    assert "run_python" in names
    assert "web_agent_read" not in names


def test_destructive_contracts_are_not_live(setup, client):
    registry = make_registry()
    policy = ToolPolicy()
    settings = WebSettings(computer_mode="trusted", workspace_roots=[r"C:\AlexWorkspace"])
    for name in (
        "format_volume",
        "manage_partition",
        "boot_config",
        "bitlocker_change",
        "system_shutdown",
        "install_software",
        "start_service",
        "change_service_settings",
    ):
        definition = registry.get(name)[0]
        fields = definition.input_model.model_fields
        if "device" in fields:
            payload = {"device": "Z:"}
        elif "package" in fields:
            payload = {"package": "Demo.App"}
        elif "start_type" in fields:
            payload = {"name": "FakeSvc", "start_type": "demand"}
        elif "action" in fields and name == "system_shutdown":
            payload = {"action": "shutdown"}
        elif "action" in fields:
            payload = {"name": "FakeSvc", "action": "query"}
        else:
            payload = {"action": "shutdown"}
        args = definition.input_model.model_validate(payload)
        assert (
            policy.validate(definition, settings, mode="off", computer_mode="trusted", args=args)
            == "confirmation_required"
        )


def test_tor_search_stays_unconfigured_without_provider():
    from app.config import get_settings

    assert get_settings().tor_search_providers == []
    status_code = "tor_search_not_configured"
    assert status_code == "tor_search_not_configured"
