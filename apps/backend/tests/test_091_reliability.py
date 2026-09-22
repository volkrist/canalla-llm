"""0.9.1 workspace queue, git policy, external actions, pause sanitization."""

from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest

from app.database import SessionLocal
from app.models import Message
from app.tools.contracts import RiskLevel, ToolError
from app.tools.executor import ExecutionContext, ToolExecutor, reconcile_tools
from app.tools.local import machine
from app.tools.local.plan import looks_like_computer, looks_like_write
from app.tools.local.public_text import public_assistant_text, strip_tool_protocol
from app.tools.local.task import LocalTaskController
from app.tools.local.workspace import looks_like_coding
from app.tools.models import LocalTask, ToolRun
from app.tools.orchestrator import ToolOrchestrator
from app.tools.policy import ToolLimits, ToolPolicy, WebSettings, assert_explicit_git_add
from app.tools.registry import make_registry
from tests.db_helpers import require_row
from tests.fake_host import FakeHost
from tests.test_autonomous_tasks import _project


class _Pages(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        del format, args

    def do_GET(self):
        if self.path.startswith("/form"):
            body = (
                b"<form method='POST' action='/submit'>"
                b"<input name='name'><textarea name='message'></textarea>"
                b"<button type='submit'>Send</button></form>"
            )
        else:
            body = (
                b'<div data-item="Alex Test Item" data-price="1.23" data-currency="USD" '
                b'data-seller="Alex Test Shop">Alex Test Item $1.23</div>'
            )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.path.startswith("/submit"):
            body = b"FORM_SUBMIT_OK"
        else:
            payload = json.loads(raw.decode("utf-8") or "{}")
            assert payload["item"] == "Alex Test Item"
            assert payload["total_price"] == "1.23"
            body = b"PURCHASE_TEST_CONFIRMED"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def pages():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.fixture
def setup(client, auth, tmp_path):
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    root = _project(tmp_path)
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "trusted", "workspace_roots": [str(root)]},
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
    events = []

    async def emit(event, value):
        events.append((event, value))

    context = ExecutionContext(
        user_id,
        chat_id,
        generation_id,
        ToolLimits(max_calls=24, hard_max_calls=40),
        emit,
        mode="off",
        computer_mode="trusted",
        settings=WebSettings(computer_mode="trusted", workspace_roots=[str(root)]),
        user_prompt="Полностью проверь этот проект и исправь ошибки",
        host_online=True,
    )
    host = FakeHost(client, device_headers, root).start()
    yield headers, context, events, root, host, client
    host.close()


def test_computer_and_write_hints():
    assert looks_like_computer("Создай на моём рабочем столе новую тестовую папку Alex-LLM-E2E")
    assert looks_like_write("Создай hello.txt")
    assert not looks_like_write("Перечитай hello.txt и скажи содержимое")
    assert looks_like_computer("Посчитай SHA256 файла hello.txt")
    assert looks_like_computer("В тестовой папке запусти безопасный PowerShell Start-Sleep на 20 секунд")
    assert not looks_like_coding("Создай на моём рабочем столе новую тестовую папку Alex-LLM-E2E")
    assert not looks_like_computer("Исправь файл sample.txt и сделай локальный git commit.")


def test_local_tool_notes_are_not_web_untrusted():
    from app.context_builder import ContextBuilder

    messages = [{"role": "system", "content": "You are Alex."}, {"role": "user", "content": "read hello"}]
    out = ContextBuilder.with_web(messages, 2, [], ['{"text":"ALEX_EXTERNAL_FILE_CHANGE_7391"}'])
    blob = "\n".join(item["content"] for item in out)
    assert "[Local computer tool results]" in blob
    assert "ALEX_EXTERNAL_FILE_CHANGE_7391" in blob
    assert "Untrusted reference material — web/tools" not in blob


def test_computer_planner_exposes_desktop_tools():
    context = SimpleNamespace(
        mode="off",
        tor_enabled=False,
        computer_mode="trusted",
        assigned_device_id="device",
        host_online=True,
        coding_task=False,
        settings=WebSettings(computer_mode="trusted"),
        user_prompt="Создай на моём рабочем столе тестовую папку и hello.txt",
    )
    names = {item.name for item in ToolOrchestrator(make_registry(), None).planner_definitions(context)}
    assert "get_known_folders" in names
    assert "create_directory" in names
    assert "copy_file" in names
    assert "move_file" in names
    assert "search_code" in names
    assert "hash_file" in names
    assert "install_software" not in names
    assert "git_push" not in names
    jq = SimpleNamespace(**{**context.__dict__, "user_prompt": "Установи jq через winget"})
    jq_names = {item.name for item in ToolOrchestrator(make_registry(), None).planner_definitions(jq)}
    assert "install_software" in jq_names


def test_git_commit_when_explicitly_requested(setup):
    _headers, context, _events, root, _host, _client = setup
    context.user_prompt = "Исправь тестовый файл, проверь изменение и сделай локальный git commit."
    (root / "note.txt").write_text("hello\n", encoding="utf-8")
    result = asyncio.run(
        ToolExecutor(make_registry()).execute(
            "git_add",
            {"cwd": str(root), "paths": [str(root / "note.txt")], "purpose": "stage intended file"},
            context,
        )
    )
    assert result.metadata.get("exit_code") in {0, None} or "note.txt" in (result.text or "")
    committed = asyncio.run(
        ToolExecutor(make_registry()).execute(
            "git_commit",
            {"cwd": str(root), "message": "add note.txt after verification", "purpose": "commit"},
            context,
        )
    )
    assert committed.metadata.get("exit_code") in {0, None} or "commit" in (committed.text or "").lower()


def test_install_software_is_sensitive():
    registry = make_registry()
    install = registry.get("install_software")[0]
    assert install.risk_level == RiskLevel.SENSITIVE
    decision = ToolPolicy().validate(
        install,
        WebSettings(computer_mode="trusted"),
        mode="off",
        computer_mode="trusted",
        args=install.input_model(package="jqlang.jq", purpose="test"),
    )
    assert decision == "confirmation_required"


def test_trusted_process_without_cwd_is_auto():
    registry = make_registry()
    powershell = registry.get("run_powershell")[0]
    settings = WebSettings(computer_mode="trusted", workspace_roots=[r"C:\AlexWorkspace"])
    args = powershell.input_model(argv=["-NoProfile", "-Command", "Get-Date"], purpose="clock")
    assert (
        ToolPolicy().validate(powershell, settings, mode="off", computer_mode="trusted", args=args)
        == "allowed"
    )
    assert (
        ToolPolicy().validate(powershell, settings, mode="off", computer_mode="ask", args=args)
        == "confirmation_required"
    )


def test_pause_text_never_shows_raw_tool_protocol():
    raw = '<tool_call>{"name":"write_file","arguments":{"path":"x"}}'
    assert "tool_call" not in strip_tool_protocol(raw)
    assert public_assistant_text(raw, "task_paused") == "Paused while preparing next action."
    assert "tool_calls" not in public_assistant_text('```json\n{"tool_calls":[]}')


def test_computer_write_queue_promotes(setup):
    _headers, context, _events, _root, _host, _client = setup
    context.user_prompt = "Создай hello.txt на рабочем столе"
    waiting_prompt = "Создай copy.txt на рабочем столе"
    with SessionLocal() as db:
        LocalTaskController().attach(context)
        owner = LocalTaskController().open(db, context)
        waiting_context = ExecutionContext(
            context.user_id,
            context.chat_id,
            context.generation_id,
            ToolLimits(max_calls=8),
            context.emit,
            computer_mode="trusted",
            settings=context.settings,
            user_prompt=waiting_prompt,
        )
        LocalTaskController().attach(waiting_context)
        waiting = LocalTaskController().open(db, waiting_context)
        assert owner.status == machine.READY
        assert waiting.status == machine.WAITING_WORKSPACE
        assert waiting.workspace == owner.workspace
        LocalTaskController().finish(db, context, "COMPLETED")
        db.expire_all()
        waiting = require_row(db, LocalTask, waiting.id)
        assert waiting.status == machine.READY
        assert (waiting.facts or {}).get("promoted_from_queue") is True


def test_read_tasks_do_not_queue(setup):
    _headers, context, _events, _root, _host, _client = setup
    context.user_prompt = "Перечитай hello.txt"
    other_prompt = "Покажи информацию об этом компьютере: Windows, CPU, RAM"
    with SessionLocal() as db:
        LocalTaskController().attach(context)
        first = LocalTaskController().open(db, context)
        other = ExecutionContext(
            context.user_id,
            context.chat_id,
            context.generation_id,
            ToolLimits(max_calls=8),
            context.emit,
            computer_mode="trusted",
            settings=context.settings,
            user_prompt=other_prompt,
        )
        LocalTaskController().attach(other)
        second = LocalTaskController().open(db, other)
        assert first.status == machine.READY
        assert second.status == machine.READY
        assert second.id != first.id


def test_stale_lock_promotes_waiter(setup):
    _headers, context, _events, _root, _host, _client = setup
    with SessionLocal() as db:
        LocalTaskController().attach(context)
        owner = LocalTaskController().open(db, context)
        waiting_context = ExecutionContext(
            context.user_id,
            context.chat_id,
            context.generation_id,
            ToolLimits(max_calls=8),
            context.emit,
            computer_mode="trusted",
            settings=context.settings,
            user_prompt=context.user_prompt,
        )
        LocalTaskController().attach(waiting_context)
        waiting = LocalTaskController().open(db, waiting_context)
        assert waiting.status == machine.WAITING_WORKSPACE
        owner.status = machine.COMPLETED
        owner.current_phase = machine.COMPLETED
        db.commit()
        owner_id, waiting_id = owner.id, waiting.id
    reconcile_tools()
    with SessionLocal() as db:
        waiting = require_row(db, LocalTask, waiting_id)
        owner = require_row(db, LocalTask, owner_id)
        assert owner.status in {machine.COMPLETED, machine.INTERRUPTED} or owner.finished_at
        assert waiting.status == machine.READY


def test_git_add_rejects_blanket_paths():
    with pytest.raises(ToolError, match="invalid_arguments"):
        assert_explicit_git_add(["-A"])
    with pytest.raises(ToolError, match="invalid_arguments"):
        assert_explicit_git_add(["."])
    assert_explicit_git_add(["hello.txt"])


def test_git_commit_requires_explicit_request(setup):
    _headers, context, _events, root, _host, _client = setup
    context.user_prompt = "Исправь тесты"
    context.settings = WebSettings(computer_mode="trusted", workspace_roots=[str(root)], auto_commit=False)
    with pytest.raises(ToolError, match="git_commit_not_requested"):
        asyncio.run(
            ToolExecutor(make_registry()).execute(
                "git_commit",
                {"cwd": str(root), "message": "fix tests", "purpose": "commit"},
                context,
            )
        )


def test_git_push_is_sensitive_and_not_auto():
    registry = make_registry()
    push = registry.get("git_push")[0]
    settings = WebSettings(computer_mode="trusted", workspace_roots=[r"C:\AlexWorkspace"], allow_push=False)
    decision = ToolPolicy().validate(
        push,
        settings,
        mode="off",
        computer_mode="trusted",
        args=push.input_model(cwd=r"C:\AlexWorkspace", remote="origin"),
    )
    assert decision == "confirmation_required"
    assert push.risk_level == RiskLevel.SENSITIVE


def test_form_real_submit(setup, pages):
    headers, context, _events, _root, _host, client = setup
    executor = ToolExecutor(make_registry())
    asyncio.run(executor.execute("inspect_form", {"url": pages + "/form", "purpose": "fields"}, context))
    asyncio.run(
        executor.execute(
            "fill_form_field",
            {"url": pages + "/form", "field": "name", "value": "Alex", "purpose": "fill"},
            context,
        )
    )
    asyncio.run(
        executor.execute(
            "fill_form_field",
            {"url": pages + "/form", "field": "message", "value": "FORM_REAL_PASS", "purpose": "fill"},
            context,
        )
    )

    async def emit(event, value):
        if value.get("status") == "waiting_confirmation":
            client.post(f"/tools/runs/{value['id']}/confirm", headers=headers, json={"allow": True})

    context.emit = emit
    result = asyncio.run(
        executor.execute("submit_form", {"url": pages + "/form", "purpose": "submit"}, context)
    )
    assert "FORM_SUBMIT_OK" in result.text


def test_purchase_is_critical_and_local_only(setup, pages):
    headers, context, _events, _root, _host, client = setup
    registry = make_registry()
    checkout = registry.get("checkout_purchase")[0]
    assert checkout.risk_level == RiskLevel.CRITICAL
    args = {
        "url": pages + "/shop",
        "item": "Alex Test Item",
        "quantity": 1,
        "currency": "USD",
        "total_price": "1.23",
        "seller": "Alex Test Shop",
        "purpose": "test",
    }
    policy = ToolPolicy()
    assert (
        policy.validate(checkout, WebSettings(), mode="off", args=checkout.input_model(**args))
        == "confirmation_required"
    )
    executor = ToolExecutor(registry)
    asyncio.run(executor.execute("inspect_product", {"url": pages + "/shop", "purpose": "inspect"}, context))

    async def refuse(event, value):
        if value.get("status") == "waiting_confirmation":
            client.post(f"/tools/runs/{value['id']}/confirm", headers=headers, json={"allow": False})

    context.emit = refuse
    with pytest.raises(ToolError, match="confirmation_denied"):
        asyncio.run(executor.execute("checkout_purchase", args, context))

    async def allow(event, value):
        if value.get("status") == "waiting_confirmation":
            client.post(f"/tools/runs/{value['id']}/confirm", headers=headers, json={"allow": True})

    context.emit = allow
    result = asyncio.run(executor.execute("checkout_purchase", args, context))
    assert "PURCHASE_TEST_CONFIRMED" in result.text
    mutated = dict(args)
    mutated["total_price"] = "9.99"
    with pytest.raises(ToolError, match="confirmation_mismatch"):
        asyncio.run(executor.execute("checkout_purchase", mutated, context))


def test_email_contract_not_configured(setup):
    headers, context, _events, _root, _host, client = setup
    email = make_registry().get("email_send")[0]
    policy = ToolPolicy()
    args = email.input_model(to="nobody@example.com", subject="x", body="y", purpose="test")
    assert policy.validate(email, WebSettings(), mode="off", args=args) == "confirmation_required"
    assert policy.validate(email, WebSettings(), mode="off", confirmed=True, args=args) == "allowed"

    async def allow(event, value):
        if value.get("status") == "waiting_confirmation":
            client.post(f"/tools/runs/{value['id']}/confirm", headers=headers, json={"allow": True})

    context.emit = allow
    with pytest.raises(ToolError, match="email_not_configured"):
        asyncio.run(
            ToolExecutor(make_registry()).execute(
                "email_send",
                {"to": "nobody@example.com", "subject": "x", "body": "y", "purpose": "test"},
                context,
            )
        )


def test_payload_mutation_invalidates_approval(setup, pages):
    headers, context, _events, _root, _host, client = setup
    executor = ToolExecutor(make_registry())
    asyncio.run(executor.execute("inspect_form", {"url": pages + "/form", "purpose": "fields"}, context))
    asyncio.run(
        executor.execute(
            "fill_form_field",
            {"url": pages + "/form", "field": "name", "value": "Alex", "purpose": "fill"},
            context,
        )
    )

    async def allow(event, value):
        if value.get("status") == "waiting_confirmation":
            with SessionLocal() as db:
                row = require_row(db, ToolRun, value["id"])
                row.input_digest = "0" * 64
                db.commit()
            client.post(f"/tools/runs/{value['id']}/confirm", headers=headers, json={"allow": True})

    context.emit = allow
    with pytest.raises(ToolError, match="confirmation_payload_changed"):
        asyncio.run(executor.execute("submit_form", {"url": pages + "/form", "purpose": "submit"}, context))
