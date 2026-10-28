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
    failure_summary,
    final_answer_problem,
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
    """An authenticated chat whose preferences make this a tool turn (so the answer is buffered)."""
    headers = auth()
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "trusted", "workspace_roots": [str(tmp_path)]},
    )
    return client, headers, chat_id


class _Provider:
    """A model whose planner is inert and whose prose is scripted."""

    supports_tools = True

    def __init__(self, *, answer: str, retry: str | None = None):
        self.answer = answer
        self.retry = retry
        self.prompts: list[str] = []

    async def plan_tools(self, messages, tools, usage):
        return {"tool_calls": []}

    async def stream_with_usage(self, messages, usage):
        yield self.answer

    async def chat(self, messages):
        self.prompts.append(str(messages[-1].get("content")))
        return self.retry if self.retry is not None else ""

    async def health(self):
        return True

    async def status(self):
        return "ready"


def _run(client, headers, chat_id, provider, content="Собери небольшой проект и проверь его."):
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
    client, headers, chat_id = tool_turn
    provider = _Provider(answer="Готово: файл создан и проверен.")
    response = _run(client, headers, chat_id, provider)

    assert response.status_code == 200
    visible = _answer_text(response.text)
    assert "Готово: файл создан и проверен." in visible
    assert _last_answer(chat_id).content.startswith("Готово")


def test_a_tool_call_instead_of_prose_is_not_published_as_a_prefix(tool_turn):
    """The D2 regression: the prefix must never reach the chat."""
    client, headers, chat_id = tool_turn
    provider = _Provider(
        answer="Процесс запущен, pid=26724" + TOOL_BLOCK,
        retry="Проверка не завершена: pytest не подтвердил изменения.",
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
    client, headers, chat_id = tool_turn
    provider = _Provider(
        answer="Процесс запущен, pid=26724" + TOOL_BLOCK,
        retry="Вот вызов: <tool_call>{broken",
    )
    response = _run(client, headers, chat_id, provider)

    assert response.status_code == 200
    visible = _answer_text(response.text)
    assert "pid=26724" not in visible
    assert "<tool_call>" not in visible
    assert "инструмент" in visible.lower()
    saved = _last_answer(chat_id).content
    assert "pid=26724" not in saved


def test_a_tool_block_alone_never_becomes_an_empty_bubble(tool_turn):
    client, headers, chat_id = tool_turn
    provider = _Provider(answer=TOOL_BLOCK, retry="")
    response = _run(client, headers, chat_id, provider)

    assert response.status_code == 200
    visible = _answer_text(response.text)
    assert visible.strip()
    assert "<tool_call>" not in visible
    assert "инструмент" in visible.lower()


def test_quoted_text_with_a_marker_is_never_executed(tool_turn):
    """A tool block in prose is not a call: nothing is executed, and the user still gets text."""
    client, headers, chat_id = tool_turn
    provider = _Provider(
        answer="Модель иногда печатает <tool_call> в тексте — это не вызов.",
        retry="Это цитата, а не вызов инструмента.",
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
