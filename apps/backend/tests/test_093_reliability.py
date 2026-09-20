"""0.9.3 weak-model grounding, routing, search efficiency, queue resume."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.database import SessionLocal
from app.models import Message
from app.tools.executor import ExecutionContext, ToolExecutor
from app.tools.local import machine
from app.tools.local.continue_task import continue_pending
from app.tools.local.facts import from_tool, latest, public_block, record
from app.tools.local.grounding import contradiction, fallback_answer, incomplete, issue_for
from app.tools.local.intent import select_local_route
from app.tools.local.progress import should_block
from app.tools.local.scope import assert_scope, build_scope
from app.tools.local.task import LocalTaskController
from app.tools.models import LocalTask
from app.tools.orchestrator import ToolOrchestrator
from app.tools.policy import COMPUTER_CORE_TOOLS, ToolLimits, WebSettings, computer_planner_tools
from app.tools.registry import make_registry
from app.tools.web_router import select_tinyfish_route
from tests.fake_host import FakeHost
from tests.test_autonomous_tasks import _project


def orch():
    registry = make_registry()
    return ToolOrchestrator(registry, ToolExecutor(registry))


class QuietProvider:
    supports_tools = True

    async def plan_tools(self, messages, tools, usage):
        return {"tool_calls": []}


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
        user_prompt="local",
        host_online=True,
    )
    host = FakeHost(client, device_headers, root).start()
    yield headers, context, events, root, host, client
    host.close()


def test_verified_facts_and_contradiction_blocked():
    facts = from_tool(
        {},
        "read_file",
        {
            "text": "sha256=abc\nALEX_EXTERNAL_FILE_CHANGE_7391",
            "metadata": {"path": r"C:\t\hello.txt", "sha256": "abc"},
        },
    )
    assert latest(facts, "FILE_READ")["content_excerpt"].startswith("ALEX_EXTERNAL")
    assert contradiction("I cannot access the filesystem", facts) == "denies_verified_local_access"
    assert incomplete("done", facts, "Прочитай файл и скажи что внутри") == "missing_file_content"
    answer = fallback_answer(facts, "Что внутри файла?")
    assert "ALEX_EXTERNAL_FILE_CHANGE_7391" in answer
    hashed = from_tool(
        {}, "hash_file", {"text": "a" * 64, "metadata": {"digest": "a" * 64, "path": r"C:\t\hello.txt"}}
    )
    assert incomplete("hash is ready", hashed, "Посчитай SHA256 hello.txt") == "missing_hash"
    assert fallback_answer(hashed, "Какой SHA256?") == "a" * 64
    installed = record({}, "SOFTWARE_INSTALLED", {"package_id": "jqlang.jq", "version": "jq-1.8.2"})
    assert contradiction("you need to install jq yourself", installed)
    assert "password" not in public_block(
        record({}, "FILE_READ", {"path": "x", "content_excerpt": "ok", "secret": "nope"})
    )


def test_deterministic_local_routes():
    root = r"C:\Users\Volkr\Desktop\Alex-LLM-E2E"
    context = SimpleNamespace(
        task_scope=SimpleNamespace(primary_root=root), settings=WebSettings(workspace_roots=[root])
    )
    assert select_local_route("Посчитай SHA256 hello.txt", context).action == "hash_file"
    assert (
        select_local_route("Скажи версию Windows, CPU, RAM и свободное место", context).action
        == "get_system_info"
    )
    search = select_local_route("Найди в этой папке файл с ALEX_SEARCH_MARKER_49127", context)
    assert search.action == "search_code" and search.arguments["query"] == "ALEX_SEARCH_MARKER_49127"
    read = select_local_route(f"Прочитай файл {root}\\hello.txt", context)
    assert read.action == "read_file"


def test_no_progress_and_duplicate_suppression():
    facts = {"action_signatures": [{"tool": "search_code", "digest": "x", "novelty": False}] * 2}
    from app.tools.local.progress import signature

    digest = signature("search_code", {"root": "r", "query": "q"})
    facts = {"action_signatures": [{"tool": "search_code", "digest": digest, "novelty": False}] * 2}
    assert should_block(facts, "search_code", {"root": "r", "query": "q"}) == "duplicate_readonly"
    assert should_block(facts, "read_file", {"path": "a"}, "Перечитай файл") is None


def test_task_scope_blocks_desktop_helpers():
    from app.tools.contracts import ToolError

    scope = build_scope(
        "работай в C:\\Users\\Volkr\\Desktop\\Alex-LLM-E2E",
        [r"C:\Users\Volkr\Desktop\Alex-LLM-E2E"],
        "task-1",
    )
    with pytest.raises(ToolError, match="workspace_scope"):
        assert_scope("write_file", {"path": r"C:\Users\Volkr\Desktop\alex_out.txt"}, scope)
    with pytest.raises(ToolError, match="workspace_scope"):
        assert_scope("write_file", {"path": r"C:\Users\Volkr\Desktop\__listdir.py"}, scope)
    assert_scope("write_file", {"path": r"C:\Users\Volkr\Desktop\Alex-LLM-E2E\hello.txt"}, scope)


def test_computer_core_is_narrow():
    allowed = computer_planner_tools("Создай файл hello.txt")
    assert "hash_file" in allowed
    assert "run_powershell" not in allowed
    assert "run_python" not in allowed
    assert "run_process" not in allowed
    search = computer_planner_tools("Найди в этой папке файл с ALEX_SEARCH_MARKER_49127")
    assert "search_code" in search and "run_python" not in search and "run_process" not in search
    assert allowed <= (
        COMPUTER_CORE_TOOLS
        | {
            "install_software",
            "run_powershell",
            "run_python",
            "run_process",
            "delete_file",
            "delete_directory",
            "git_status",
            "git_diff",
            "git_log",
            "git_add",
            "git_commit",
        }
    )
    assert len(COMPUTER_CORE_TOOLS) <= 20


def test_browser_intent_before_planner_and_tor_excludes():
    prompt = (
        "Открой в браузере официальный сайт Python, прочитай заголовок, "
        "перейди по ссылке на документацию и скажи заголовок следующей страницы."
    )
    browser = select_tinyfish_route(
        prompt, SimpleNamespace(user_prompt=prompt, mode="on", settings=WebSettings(), sources=[])
    )
    assert browser.paid == "browser"
    names = {
        item.name
        for item in orch().planner_definitions(
            SimpleNamespace(
                user_prompt=prompt,
                mode="on",
                settings=WebSettings(),
                computer_mode="off",
                host_online=False,
                assigned_device_id=None,
                coding_task=False,
                sources=[],
                tor_enabled=False,
            )
        )
    }
    assert "web_search" not in names and "web_fetch" not in names
    tor = select_tinyfish_route(
        "Через Tor найди onion Tor Project",
        SimpleNamespace(
            user_prompt="Через Tor найди onion Tor Project", mode="on", settings=WebSettings(), sources=[]
        ),
    )
    assert tor.paid is None and tor.reason == "tor_stack_only"
    blocked = select_tinyfish_route(
        "Buy a domain and submit the payment form",
        SimpleNamespace(
            user_prompt="Buy a domain and submit the payment form",
            mode="on",
            settings=WebSettings(),
            sources=[],
        ),
    )
    assert blocked.paid is None and blocked.classification == "SIDE_EFFECT"
    assert "web_browser" not in names


def test_browser_prepare_injects_when_planner_surface_empty(setup):
    _headers, context, _events, _root, _host, _client = setup
    prompt = (
        "Открой в браузере официальный сайт Python, прочитай заголовок, "
        "перейди по ссылке на документацию и скажи заголовок следующей страницы."
    )
    context.user_prompt = prompt
    context.mode = "on"
    context.computer_mode = "off"
    context.host_online = False
    context.assigned_device_id = None
    context.settings = WebSettings(
        computer_mode="off",
        default_mode="on",
        browser_mode="auto",
        agent_mode="auto",
        search_enabled=True,
        fetch_enabled=True,
    )
    recorded = []
    orchestrator = orch()

    async def fake_run(name, arguments, context, notes, origin="model"):
        recorded.append((name, origin, arguments))
        if name == "web_browser":
            context.tinyfish_browser_done = True
            context.sources = [
                {
                    "url": "https://www.python.org/",
                    "final_url": "https://www.python.org/",
                    "title": "Welcome to Python.org",
                    "excerpt": "Welcome to Python.org",
                    "label": "W1",
                    "kind": "W",
                    "details": {
                        "retrieval": "browser",
                        "links": [{"id": "L2", "url": "https://docs.python.org/3/", "text": "Documentation"}],
                    },
                }
            ]
            return {
                "ok": True,
                "text": "Welcome to Python.org",
                "sources": context.sources,
                "origin": origin,
            }
        return {"ok": True, "text": "", "sources": []}

    names = {item.name for item in orchestrator.planner_definitions(context)}
    assert "web_search" not in names
    assert "web_browser" not in names
    orchestrator._run = fake_run
    asyncio.run(orchestrator.prepare(QuietProvider(), [{"role": "user", "content": prompt}], 1, context, {}))
    assert any(name == "web_browser" and origin == "server_policy" for name, origin, _ in recorded)
    assert not any(name in {"web_search", "web_fetch"} for name, _origin, _arguments in recorded)


def _run(setup, prompt, extra=None):
    _headers, context, events, root, host, _client = setup
    context.user_prompt = prompt
    context.limits = ToolLimits(max_calls=24, hard_max_calls=40, max_seconds=60)
    context.local_intent_done = False
    context.skip_final_stream = False
    context.task_halt = None
    context.task_id = None
    context.resume_task_id = None
    if extra:
        extra(root)
    before = list(host.handled)
    asyncio.run(orch().prepare(QuietProvider(), [{"role": "user", "content": prompt}], 1, context, {}))
    return host.handled[len(before) :], root, context, events, host


def test_create_read_grounded(setup):
    handled, root, context, _events, host = _run(
        setup,
        "Создай файл grounded.txt с текстом GROUNDING_REAL_PASS, прочитай его и скажи, что именно записано в файле.",
    )
    path = root / "grounded.txt"
    assert path.read_text(encoding="utf-8") == "GROUNDING_REAL_PASS"
    assert handled.count("write_file") == 1
    assert handled.count("read_file") == 1
    assert len(handled) <= 6
    assert not (root / "alex_out.txt").exists()
    with SessionLocal() as db:
        row = db.get(LocalTask, context.task_id)
        text = fallback_answer(row.facts, context.user_prompt)
        assert "GROUNDING_REAL_PASS" in text
        assert issue_for("I don't have filesystem access", row.facts, context.user_prompt)


def test_hash_grounded(setup):
    def seed(root: Path):
        (root / "hello.txt").write_text("hello-hash", encoding="utf-8")

    handled, root, context, _events, _host = _run(setup, "Посчитай SHA256 hello.txt и скажи мне hash.", seed)
    digest = hashlib.sha256(b"hello-hash").hexdigest()
    assert "hash_file" in handled
    assert len(handled) <= 3
    with SessionLocal() as db:
        row = db.get(LocalTask, context.task_id)
        assert latest(row.facts, "HASH_RESULT")["digest"] == digest
        assert digest in fallback_answer(row.facts, context.user_prompt)


def test_system_info_grounded(setup):
    handled, _root, context, _events, _host = _run(
        setup, "Скажи версию Windows, CPU, RAM и свободное место на системном диске."
    )
    assert handled == ["get_system_info"] or handled.count("get_system_info") == 1 and len(handled) <= 3
    with SessionLocal() as db:
        row = db.get(LocalTask, context.task_id)
        text = fallback_answer(row.facts, context.user_prompt)
        assert "10.0" in text and "8" in text


def test_search_marker_efficient(setup):
    def seed(root: Path):
        (root / "alpha.txt").write_text("nope", encoding="utf-8")
        (root / "beta.md").write_text("nope", encoding="utf-8")
        (root / "data.json").write_text('{"k":"ALEX_SEARCH_MARKER_49127"}', encoding="utf-8")

    handled, root, context, _events, host = _run(
        setup,
        "Найди в этой папке файл, в котором есть ALEX_SEARCH_MARKER_49127, и скажи имя файла.",
        seed,
    )
    assert "search_code" in handled
    assert len(handled) <= 5
    assert "run_python" not in handled and "run_powershell" not in handled
    assert not (root / "alex_out.txt").exists()
    with SessionLocal() as db:
        row = db.get(LocalTask, context.task_id)
        assert "data.json" in fallback_answer(row.facts, context.user_prompt)
        metrics = (row.facts or {}).get("metrics") or {}
        assert int(metrics.get("tool_calls_total") or 0) <= 5


def test_external_reread_replaces_fact(setup):
    _headers, context, _events, root, _host, _client = setup
    (root / "hello.txt").write_text("OLD", encoding="utf-8")
    _handled, root, context, _events, _host = _run(setup, f"Прочитай файл {root / 'hello.txt'}")
    (root / "hello.txt").write_text("ALEX_EXTERNAL_FILE_CHANGE_7391", encoding="utf-8")
    context.user_prompt = f"Перечитай файл {root / 'hello.txt'}"
    context.resume_task_id = None
    context.task_id = None
    context.local_intent_done = False
    context.skip_final_stream = False
    asyncio.run(
        orch().prepare(QuietProvider(), [{"role": "user", "content": context.user_prompt}], 1, context, {})
    )
    with SessionLocal() as db:
        row = db.get(LocalTask, context.task_id)
        assert "ALEX_EXTERNAL_FILE_CHANGE_7391" in (latest(row.facts, "FILE_READ") or {}).get(
            "content_excerpt", ""
        )
        assert "ALEX_EXTERNAL_FILE_CHANGE_7391" in fallback_answer(row.facts, context.user_prompt)


def test_write_queue_auto_resumes(setup):
    _headers, context, events, root, host, _client = setup
    context.user_prompt = "Создай файл queue-a.txt с текстом QUEUE_A"
    waiting = ExecutionContext(
        context.user_id,
        context.chat_id,
        context.generation_id,
        ToolLimits(max_calls=8, hard_max_calls=40),
        context.emit,
        mode="off",
        computer_mode="trusted",
        settings=context.settings,
        user_prompt="Создай файл queue-b.txt с текстом QUEUE_B",
        host_online=True,
    )
    waiting.assigned_device_id = context.assigned_device_id
    with SessionLocal() as db:
        LocalTaskController().attach(context)
        owner = LocalTaskController().open(db, context)
        LocalTaskController().attach(waiting)
        queued = LocalTaskController().open(db, waiting)
        assert owner.status == machine.READY
        assert queued.status == machine.WAITING_WORKSPACE
        assert queued.workspace == owner.workspace
    asyncio.run(
        orch().prepare(QuietProvider(), [{"role": "user", "content": context.user_prompt}], 1, context, {})
    )
    assert (root / "queue-a.txt").read_text(encoding="utf-8") == "QUEUE_A"
    with SessionLocal() as db:
        queued = db.get(LocalTask, queued.id)
        assert queued.status == machine.READY
        assert (queued.facts or {}).get("auto_continue") is True
    asyncio.run(continue_pending(orch(), QuietProvider(), user_id=context.user_id))
    assert (root / "queue-b.txt").read_text(encoding="utf-8") == "QUEUE_B"
    assert not (Path.home() / "Desktop" / "тест" / "queue-b.txt").exists()


def test_owned_process_start_stop(setup):
    handled, _root, context, _events, host = _run(
        setup, "Запусти безопасный процесс python sleep на 30 секунд"
    )
    assert "run_python" in handled
    with SessionLocal() as db:
        row = db.get(LocalTask, context.task_id)
        started = latest(row.facts, "PROCESS_STARTED")
        assert started and started.get("pid")
        owned = (row.checkpoint or {}).get("owned_processes") or []
        assert owned
    context.user_prompt = "Останови процесс, который ты только что запустила."
    context.task_id = None
    context.resume_task_id = None
    context.local_intent_done = False
    context.skip_final_stream = False
    asyncio.run(
        orch().prepare(QuietProvider(), [{"role": "user", "content": context.user_prompt}], 1, context, {})
    )
    with SessionLocal() as db:
        row = db.get(LocalTask, context.task_id)
        stopped = latest(row.facts, "PROCESS_STOPPED")
        assert stopped and stopped.get("verified_dead")
    assert host.jobs == {} or all(proc.poll() is not None for proc in host.jobs.values())


def test_scratch_cleanup_and_no_pending_dir():
    from app.tools.local.scope import cleanup_scratch, scratch_dir

    pending = scratch_dir("pending")
    assert pending == ""
    path = Path(scratch_dir("task-scratch-093"))
    helper = path / "helper.py"
    helper.write_text("print(1)", encoding="utf-8")
    cleanup_scratch("task-scratch-093")
    assert not helper.exists()


def test_deterministic_fallback_and_repair_prompt():
    facts = from_tool(
        {},
        "read_file",
        {
            "text": "sha256=abc\nGROUNDING_REAL_PASS",
            "metadata": {"path": r"C:\t\grounded.txt", "sha256": "abc"},
        },
    )
    bad = "I don't have filesystem access"
    assert issue_for(bad, facts, "Создай файл grounded.txt и потом прочитай его")
    from app.tools.local.grounding import repair_prompt

    prompt = repair_prompt(facts, "denies_verified_local_access")
    assert "VERIFIED_RESULTS" in prompt and "Do not rerun" in prompt
    assert "GROUNDING_REAL_PASS" in fallback_answer(facts, "что записано в файле")
    page = record({}, "BROWSER_PAGE", {"url": "https://docs.python.org/3/", "title": "3.13.7 Documentation"})
    assert "3.13.7 Documentation" in fallback_answer(
        page, "Открой в браузере официальный сайт Python и скажи заголовок следующей страницы."
    )


def test_write_routes_preserve_requested_content():
    root = r"C:\eval\workspace"
    context = SimpleNamespace(
        task_scope=SimpleNamespace(primary_root=root), settings=WebSettings(workspace_roots=[root])
    )
    write = select_local_route(
        "Создай в тестовой папке файл notes.txt с текстом ALEX_EVAL_WRITE_OK и потом прочитай его.",
        context,
    )
    assert write.action == "write_file"
    assert write.arguments["content"] == "ALEX_EVAL_WRITE_OK"
    assert write.arguments["path"].endswith("notes.txt")
    assert "\\notes.txt\\notes.txt" not in write.arguments["path"]
    english = select_local_route("Create notes.txt with the text MARKER_OK in the test folder", context)
    assert english.action == "write_file"
    assert english.arguments["content"] == "MARKER_OK"
    scoped = select_local_route("В тестовой папке создай только файл inside.txt с текстом SCOPE_OK.", context)
    assert scoped.action == "write_file" and scoped.arguments["content"] == "SCOPE_OK"
    queued = select_local_route("Создай файл queue-a.txt с текстом QUEUE_A в тестовой папке.", context)
    assert queued.action == "write_file" and queued.arguments["content"] == "QUEUE_A"


def test_hash_ignores_non_hex_host_text():
    failed = from_tool({}, "hash_file", {"text": "read_failed", "metadata": {"path": r"C:\t\hello.txt"}})
    assert latest(failed, "HASH_RESULT") is None
    digest = "a" * 64
    ok = from_tool(
        {}, "hash_file", {"text": digest, "metadata": {"digest": digest, "path": r"C:\t\hello.txt"}}
    )
    assert latest(ok, "HASH_RESULT")["digest"] == digest
    assert digest in fallback_answer(ok, "What is the SHA256 of hello.txt?")


def test_write_file_on_disk_and_paraphrase(setup):
    handled, root, context, _events, _host = _run(
        setup,
        "Создай в тестовой папке файл notes.txt с текстом ALEX_EVAL_WRITE_OK и потом прочитай его. Скажи, что именно записано.",
    )
    path = root / "notes.txt"
    assert path.read_text(encoding="utf-8") == "ALEX_EVAL_WRITE_OK"
    assert "write_file" in handled
    with SessionLocal() as db:
        row = db.get(LocalTask, context.task_id)
        text = fallback_answer(row.facts, context.user_prompt)
        assert "ALEX_EVAL_WRITE_OK" in text
        written = latest(row.facts, "FILE_CREATED") or latest(row.facts, "FILE_WRITTEN")
        assert written and written.get("verified") is not False


def test_coding_red_baseline_without_patch_cannot_complete():
    controller = LocalTaskController()
    row = SimpleNamespace(
        original_user_request="В тестовом проекте divide считает неправильно. Исправь и прогони тесты.",
        facts={
            "baseline_tests": False,
            "tests_passed": True,
            "verified": [{"kind": "FILE_READ", "path": "app.py"}],
            "verification_stale": False,
        },
        verification={"tests": "passed", "git_reviewed": True},
        tool_calls_used=4,
        checkpoint={"commands": [{"tool": "run_python"}]},
        success_criteria={},
    )
    review = controller.final_review(row)
    assert review["ok"] is False
    assert review["reason"] == "no_effective_change"


def test_coding_stale_verification_blocks_complete():
    controller = LocalTaskController()
    row = SimpleNamespace(
        original_user_request="В тестовом проекте падают тесты. Найди синтаксическую ошибку, исправь и проверь.",
        facts={
            "baseline_tests": False,
            "tests_passed": False,
            "verification_stale": True,
            "file_hashes": {"app.py": "abc"},
            "verified": [],
        },
        verification={"tests": "stale", "git_reviewed": True},
        tool_calls_used=5,
        checkpoint={"commands": [{"tool": "patch_file"}]},
        success_criteria={},
    )
    review = controller.final_review(row)
    assert review["ok"] is False
    assert review["reason"] in {"verification_required", "tests_not_verified"}


def test_tor_only_policy_rejects_clearnet():
    from app.tools.contracts import ToolError
    from app.tools.policy import ToolPolicy
    from app.tools.registry import make_registry

    search = make_registry().get("web_search")[0]
    policy = ToolPolicy()
    with pytest.raises(ToolError, match="tor_route_violation_blocked"):
        policy.validate(
            search,
            WebSettings(),
            mode="on",
            network_route="TOR_ONLY",
            args=search.input_model(query="onion"),
        )
    names = {
        item.name
        for item in orch().planner_definitions(
            SimpleNamespace(
                user_prompt="Через Tor найди onion-сервис Tor Project.",
                mode="on",
                settings=WebSettings(),
                computer_mode="off",
                host_online=False,
                assigned_device_id=None,
                coding_task=False,
                sources=[],
                tor_enabled=True,
                tor_mode="auto",
            )
        )
    }
    assert "web_search" not in names
    assert "web_fetch" not in names
    assert "web_browser" not in names
