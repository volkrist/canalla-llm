"""D2: the final answer is never a truncated tool-protocol prefix.

The live LA run persisted `Процесс запущен, pid=26724` — the text before a tool-call block the
model emitted where prose was expected. The block is a plain text completion (no `tools` array is
sent), so the model keeps writing its protocol after the tool phase, and the old stream published
whatever came before the marker as the answer.

These tests drive the real `/chats/{id}/stream` endpoint, not the helper in isolation.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.main import app
from app.models import Message
from app.tools.local.public_text import (
    acknowledges_failure,
    annotate_failed_answer,
    carries_tool_protocol,
    completion_summary,
    failure_summary,
    final_answer_problem,
    final_summary_for_task,
    is_tool_echo,
    public_assistant_text,
    strip_tool_protocol,
)

TOOL_BLOCK = """
<tool_call>
{"name": "run_python", "arguments": {"argv": ["-m", "la_app.cli"]}}
</tool_call>
"""


# ----------------------------------------------------------------------------- pure helpers


def test_a_clean_answer_has_no_problem():
    assert final_answer_problem("Готово: тесты прошли, отчёт сохранён.") is None


def test_a_tool_call_in_the_answer_is_typed():
    assert final_answer_problem("Процесс запущен, pid=26724" + TOOL_BLOCK) == "tool_call_in_final_answer"


def test_an_empty_answer_is_typed():
    assert final_answer_problem("   ") == "empty_model_answer"
    assert final_answer_problem(TOOL_BLOCK) == "tool_call_in_final_answer"


def test_the_prefix_before_a_tool_block_is_not_an_answer():
    """The defect itself: the visible text was the part before the marker."""
    assert strip_tool_protocol("Процесс запущен, pid=26724" + TOOL_BLOCK) == "Процесс запущен, pid=26724"
    assert carries_tool_protocol("Процесс запущен, pid=26724" + TOOL_BLOCK)


def test_a_failure_summary_never_claims_success():
    facts = {
        "verified": [
            {"kind": "FILE_CREATED", "path": r"C:\Work\la_app\core.py"},
            {"kind": "PROCESS_STARTED", "pid": 26724},
        ]
    }
    text = failure_summary(facts, "verification_required", missing_answer=True)
    lowered = text.lower()
    assert "ошибк" in lowered
    assert "verification_required" in text
    assert "успешн" not in lowered
    assert "core.py" in text
    assert "pid=26724" in text
    # A FAILED task does not advertise an automatic resume.
    assert "автоматически" not in lowered


def test_the_tool_call_halt_message_is_honest():
    text = public_assistant_text("", "tool_call_in_final_answer")
    assert "инструмент" in text
    assert "сохранены" in text


def test_a_fragment_does_not_acknowledge_a_failure():
    """The live LA answer: a true sentence that says nothing about the failed task."""
    assert not acknowledges_failure("Процесс запущен, pid=26724")
    assert acknowledges_failure("Задача завершилась с ошибкой: verification_required.")
    assert acknowledges_failure("Не удалось подтвердить тесты.")
    assert acknowledges_failure("The task failed verification.")


# ------------------------------------------------------------------------ stream integration


def _answer_text(response_text: str) -> str:
    """All text the user would actually see, assembled from the SSE deltas."""
    out: list[str] = []
    for block in response_text.split("\n\n"):
        if not block.startswith("event: delta"):
            continue
        for line in block.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]).get("content") or "")
    return "".join(out)


@pytest.fixture
def tool_turn(client, auth, tmp_path):
    """An authenticated chat with a paired device, so a real tool can run first.

    The D2 rules apply to a turn that actually ran tools: a plain chat keeps streaming, because
    the window's own Stop control depends on the first delta.
    """
    from tests.fake_host import FakeHost

    headers = auth()
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
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
    host = FakeHost(client, device_headers, root).start()
    try:
        yield client, headers, chat_id, root
    finally:
        host.close()


class _Provider:
    """A model that reads one file, then answers with scripted prose."""

    supports_tools = True

    def __init__(
        self,
        *,
        answer: str,
        retry: str | None = None,
        first: tuple[str, dict] | None = None,
        stream_error: str | None = None,
    ):
        self.answer = answer
        self.retry = retry
        self.first = first
        self.stream_error = stream_error
        self.prompts: list[str] = []
        self.plans = 0

    async def plan_tools(self, messages, tools, usage):
        self.plans += 1
        if self.first and self.plans == 1:
            name, arguments = self.first
            return {
                "tool_calls": [
                    {
                        "id": "d2-first",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(arguments),
                        },
                    }
                ]
            }
        return {"tool_calls": []}

    async def stream_with_usage(self, messages, usage):
        if self.stream_error:
            from app.providers import LLMError

            raise LLMError(self.stream_error)
        yield self.answer

    async def chat(self, messages):
        self.prompts.append(str(messages[-1].get("content")))
        return self.retry if self.retry is not None else ""

    async def health(self):
        return True

    async def status(self):
        return "ready"


def _run(
    client,
    headers,
    chat_id,
    provider,
    content="Выполни задачу полностью: перечисли содержимое папки.",
):
    previous = app.state.provider
    app.state.provider = provider
    try:
        return client.post(
            f"/chats/{chat_id}/stream",
            headers=headers,
            json={"content": content},
        )
    finally:
        app.state.provider = previous


def _last_answer(chat_id):
    from app.database import SessionLocal

    with SessionLocal() as db:
        rows = db.scalars(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
        ).all()
    return rows[-1]


def test_a_clean_answer_is_published_unchanged(tool_turn):
    client, headers, chat_id, root = tool_turn
    provider = _Provider(
        answer="Готово: файл создан и проверен.",
        first=("list_directory", {"path": str(root), "purpose": "list"}),
    )
    response = _run(client, headers, chat_id, provider)

    assert response.status_code == 200
    visible = _answer_text(response.text)
    assert "Готово: файл создан и проверен." in visible
    assert _last_answer(chat_id).content.startswith("Готово")


def test_a_tool_call_instead_of_prose_is_not_published_as_a_prefix(tool_turn):
    """The D2 regression: the prefix must never reach the chat."""
    client, headers, chat_id, root = tool_turn
    provider = _Provider(
        answer="Процесс запущен, pid=26724" + TOOL_BLOCK,
        retry="Проверка не завершена: pytest не подтвердил изменения.",
        first=("list_directory", {"path": str(root), "purpose": "list"}),
    )
    response = _run(client, headers, chat_id, provider)

    assert response.status_code == 200
    visible = _answer_text(response.text)
    assert "pid=26724" not in visible
    assert "<tool_call>" not in visible
    assert "Проверка не завершена" in visible
    # The prose-only retry was actually asked for, with an explicit no-tools instruction.
    assert provider.prompts and "Не выводи JSON" in provider.prompts[-1]
    assert "pid=26724" not in _last_answer(chat_id).content


def test_a_malformed_tool_block_falls_back_to_the_honest_summary(tool_turn):
    """When the retry also emits a protocol block, the user gets the truthful state."""
    client, headers, chat_id, root = tool_turn
    provider = _Provider(
        answer="Процесс запущен, pid=26724" + TOOL_BLOCK,
        retry="Вот вызов: <tool_call>{broken",
        first=("list_directory", {"path": str(root), "purpose": "list"}),
    )
    response = _run(client, headers, chat_id, provider)

    assert response.status_code == 200
    visible = _answer_text(response.text)
    assert "pid=26724" not in visible
    assert "<tool_call>" not in visible
    assert ("Задача завершена" in visible) or ("инструмент" in visible.lower())
    saved = _last_answer(chat_id).content
    assert "pid=26724" not in saved


def test_a_tool_block_alone_never_becomes_an_empty_bubble(tool_turn):
    client, headers, chat_id, root = tool_turn
    provider = _Provider(
        answer=TOOL_BLOCK, retry="", first=("list_directory", {"path": str(root), "purpose": "list"})
    )
    response = _run(client, headers, chat_id, provider)

    assert response.status_code == 200
    visible = _answer_text(response.text)
    assert visible.strip()
    assert "<tool_call>" not in visible
    assert "Задача завершена" in visible


def test_quoted_text_with_a_marker_is_never_executed(tool_turn):
    """A tool block in prose is not a call: nothing is executed, and the user still gets text."""
    client, headers, chat_id, root = tool_turn
    provider = _Provider(
        answer="Модель иногда печатает <tool_call> в тексте — это не вызов.",
        retry="Это цитата, а не вызов инструмента.",
        first=("list_directory", {"path": str(root), "purpose": "list"}),
    )
    response = _run(client, headers, chat_id, provider)

    assert response.status_code == 200
    visible = _answer_text(response.text)
    assert "<tool_call>" not in visible
    saved = _last_answer(chat_id)
    assert saved.role == "assistant"
    assert saved.status == "complete"


def test_an_answer_that_names_the_failure_is_left_alone():
    facts = {"verified": [{"kind": "FILE_CREATED", "path": r"C:\Work.py"}]}
    answer = "Не удалось подтвердить тесты: pytest упал на test_core."
    assert annotate_failed_answer(answer, facts, "verification_required") == answer


def test_a_fragment_gets_the_real_outcome_appended():
    """The live LA answer for a task the controller had already failed."""
    facts = {"verified": [{"kind": "FILE_CREATED", "path": r"C:\Work.py"}]}
    text = annotate_failed_answer("Процесс запущен, pid=26724", facts, "verification_required")
    assert text.startswith("Процесс запущен, pid=26724")
    assert "Задача завершилась с ошибкой" in text
    assert "verification_required" in text
    assert "успешно" not in text.lower()


def test_a_tool_echo_is_not_a_report():
    assert is_tool_echo("Процесс запущен, pid=13696")
    assert is_tool_echo(r"Прочитан файл: C:\Work\app.py")
    assert not is_tool_echo("Готово: создан пакет la_app, тесты прошли, отчёт сохранён.")
    assert not is_tool_echo("Процесс запущен, pid=13696 — но pytest ещё не подтвердил исправление")


def test_a_process_completion_echo_is_not_a_report():
    """A finished/exit-code line is the same defect one step after PROCESS_STARTED."""
    assert is_tool_echo("Процесс завершён, exit_code=0")
    assert is_tool_echo("exit code: 0")
    assert is_tool_echo("Process exited with exit_code=2")
    assert not is_tool_echo("Процесс завершён, exit_code=1 — тест не прошёл, нужна правка.")


def test_the_final_summary_follows_the_controller_status():
    facts = {"verified": [{"kind": "FILE_CREATED", "path": r"C:\Work\la_app\core.py"}]}
    done = final_summary_for_task("COMPLETED", facts, None, "request addressed")
    assert done.startswith("Задача завершена")
    assert "core.py" in done
    failed = final_summary_for_task("FAILED", facts, "verification_required", None)
    assert "Задача завершилась с ошибкой" in failed
    assert "verification_required" in failed
    assert "Задача завершена" not in failed
    parked = final_summary_for_task(None, facts, None, None)
    assert "инструмент" in parked.lower()


def test_a_completed_task_reports_its_work_when_the_retry_also_emits_protocol(tool_turn):
    """A second protocol block must not degrade into a generic note.

    The controller completed the task; the user still deserves the verified report instead of
    "the model called a tool instead of answering".
    """
    client, headers, chat_id, root = tool_turn
    provider = _Provider(
        answer="Процесс запущен, pid=26724" + TOOL_BLOCK,
        retry="Вот ещё раз: <tool_call>{broken",
        first=("list_directory", {"path": str(root), "purpose": "list"}),
    )
    response = _run(client, headers, chat_id, provider)

    assert response.status_code == 200
    visible = _answer_text(response.text)
    assert "<tool_call>" not in visible
    assert "pid=26724" not in visible
    assert "Задача завершена" in visible
    assert visible == _last_answer(chat_id).content


def test_a_failed_task_names_the_real_reason_after_a_second_protocol(tool_turn):
    client, headers, chat_id, root = tool_turn
    provider = _Provider(
        answer="Готово, всё проверено." + TOOL_BLOCK,
        retry="Снова вызов: <tool_call>{broken",
        first=("list_directory", {"path": str(root), "purpose": "list"}),
    )
    # A coding request cannot complete on one read: the controller fails it as unverified.
    response = _run(
        client,
        headers,
        chat_id,
        provider,
        content="Проверь проект, исправь падающие тесты и запусти pytest.",
    )

    assert response.status_code == 200
    visible = _answer_text(response.text)
    assert "Задача завершилась с ошибкой" in visible
    assert "tests_not_verified" in visible
    assert "Задача завершена" not in visible
    assert "<tool_call>" not in visible
    assert visible == _last_answer(chat_id).content


def test_a_completed_task_with_a_process_echo_gets_the_controller_report(tool_turn):
    client, headers, chat_id, root = tool_turn
    provider = _Provider(
        answer="Процесс завершён, exit_code=0",
        first=("list_directory", {"path": str(root), "purpose": "list"}),
    )
    response = _run(client, headers, chat_id, provider)

    assert response.status_code == 200
    visible = _answer_text(response.text)
    assert "Задача завершена" in visible
    assert "exit_code=0" not in visible
    assert visible == _last_answer(chat_id).content


def test_an_empty_completion_never_leaves_an_empty_bubble(tool_turn):
    client, headers, chat_id, root = tool_turn
    provider = _Provider(
        answer="", retry="", first=("list_directory", {"path": str(root), "purpose": "list"})
    )
    response = _run(client, headers, chat_id, provider)

    assert response.status_code == 200
    visible = _answer_text(response.text)
    assert visible.strip()
    assert "<tool_call>" not in visible
    saved = _last_answer(chat_id)
    assert saved.content.strip()
    assert saved.content == visible.strip()


def test_a_lost_final_stream_is_typed_and_never_claims_a_completed_answer(tool_turn):
    """No first token after the tool phase is an error, not an empty successful bubble."""
    client, headers, chat_id, root = tool_turn
    provider = _Provider(
        answer="",
        first=("list_directory", {"path": str(root), "purpose": "list"}),
        stream_error="llm_timeout",
    )
    response = _run(client, headers, chat_id, provider)

    assert response.status_code == 200
    assert "event: error" in response.text
    saved = _last_answer(chat_id)
    assert saved.status != "complete"
    assert "<tool_call>" not in saved.content


def test_the_stop_control_stays_available_for_a_plain_chat():
    """A turn without completed tools must keep streaming, or the UI's Stop never appears."""
    from app.tools.local.public_text import is_tool_echo

    assert not is_tool_echo("обычный ответ")
    # The stream test above (`tool_turn`) covers the tool branch; the plain branch is a token
    # stream and is asserted by the Playwright generation-navigation spec in CI.


def test_a_completed_task_gets_the_controller_report_not_a_file_dump(tool_turn, monkeypatch):
    """A completed task with an inadequate model answer gets a real summary.

    The first 1.2.6 LA run ended by quoting the whole README through `fallback_answer`, so the
    final answer named one file and hid the tests and the task status. The controller report is
    the honest answer for a completed task with real tool work.
    """
    import app.tools.local.grounding as grounding

    monkeypatch.setattr(grounding, "issue_for", lambda text, facts, prompt: "missing_status")
    client, headers, chat_id, root = tool_turn
    provider = _Provider(
        answer="Готово.", retry="", first=("list_directory", {"path": str(root), "purpose": "list"})
    )
    response = _run(client, headers, chat_id, provider)

    assert response.status_code == 200
    visible = _answer_text(response.text)
    assert "Задача завершена" in visible
    assert "Готово." not in visible
    assert visible == _last_answer(chat_id).content


def test_a_completed_task_can_report_its_own_work():
    facts = {
        "verified": [
            {"kind": "FILE_CREATED", "path": r"C:\Work\la_app\core.py"},
            {"kind": "PROCESS_STARTED", "pid": 13696},
        ]
    }
    text = completion_summary(facts, "Original request is addressed")
    assert text.startswith("Задача завершена")
    assert "core.py" in text
    assert "pid=13696" in text
