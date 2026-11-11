"""Regression tests for the two defects that lost T4 LIVE on 2026-11-11.

The run showed a real Qwen turn (`gateway_plan_start/done`, `tool_calls=1`, `completed=True`)
and then died with an untyped `TimeoutError`: no `local_tasks` row had been created for a
prompt that only opened and saved named files, and a burst of device polls held every pooled
connection until the chat turn's own query timed out.

Both are product defects, not capacity. These tests pin them down without a GPU.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import TimeoutError as DatabaseBusy
from sqlalchemy.pool import QueuePool

from app.database import MAX_OVERFLOW, POOL_SIZE, POOL_TIMEOUT_SECONDS, SessionLocal, engine
from app.models import now
from app.tools.contracts import ToolError
from app.tools.executor import ExecutionContext, ToolExecutor
from app.tools.local import devices as device_module
from app.tools.local.plan import (
    looks_like_autonomous,
    looks_like_file_task,
    looks_like_write,
)
from app.tools.local.task import LocalTaskController
from app.tools.models import PairedDevice
from app.tools.policy import ToolLimits, WebSettings

# The exact shape of the T4 LIVE catalogue prompt: it names files, opens and saves them,
# and matches none of the older autonomy phrases.
T4L_PROMPT = (
    "В папке C:\\Users\\Volkr\\Canalla E2E\\T4L открой t4l.txt, сохрани его содержимое в "
    "t4l_out.txt, затем открой t4l_out.txt и подтверди содержимое. В конце выведи T4L_DONE."
)
T3L_PROMPT = (
    "В папке C:\\Users\\Volkr\\Canalla E2E\\T3L открой t3l.txt и покажи его содержимое. "
    "В конце выведи T3L_DONE."
)
PLAIN_QUESTION = "Как открыть main.py в редакторе и что такое вообще точка входа?"


def _context(user_id, chat_id, generation_id, workspace, prompt):
    events = []

    async def emit(event, value):
        events.append((event, value))

    return ExecutionContext(
        user_id,
        chat_id,
        generation_id,
        ToolLimits(max_calls=24, hard_max_calls=40),
        emit,
        mode="off",
        computer_mode="ask",
        settings=WebSettings(computer_mode="ask", workspace_roots=[str(workspace)], default_mode="auto"),
        user_prompt=prompt,
        host_online=True,
    )


def test_a_named_file_task_is_task_scale():
    """A concrete read→write→read task is a task, not a chat question."""
    assert looks_like_file_task(T4L_PROMPT)
    assert looks_like_file_task(T3L_PROMPT)
    assert looks_like_autonomous(T4L_PROMPT)
    assert looks_like_write(T4L_PROMPT), "«сохрани» is a write and must take the writer lock"


def test_the_old_signals_did_not_recognise_this_prompt():
    """The reproduction of the defect: every signal the old chain consulted said «chat».

    `looks_like_autonomous` was the only path to a `local_tasks` row that a device tool needs,
    so this exact prompt reached the model with no task, no progress and no resume target.
    """
    import re

    from app.tools.local.plan import AUTONOMY_HINT, looks_like_commit_request, looks_like_computer
    from app.tools.local.workspace import looks_like_coding

    assert not looks_like_computer(T4L_PROMPT)
    assert not looks_like_coding(T4L_PROMPT)
    assert not looks_like_commit_request(T4L_PROMPT)
    assert not re.search(AUTONOMY_HINT, T4L_PROMPT)
    assert looks_like_file_task(T4L_PROMPT)


def test_a_question_about_a_file_is_not_a_task():
    assert not looks_like_file_task(PLAIN_QUESTION)
    assert not looks_like_file_task("Что такое файл .gitignore?")
    assert not looks_like_file_task("Расскажи про index.html и его структуру")


@pytest.fixture
def task_context(client, auth, tmp_path):
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    # A real folder, like the live scenario: a path that exists is recovered in full even when
    # its parent contains a space (``Canalla E2E``).
    workspace = tmp_path / "Canalla E2E" / "T4L"
    workspace.mkdir(parents=True)
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "ask", "workspace_roots": [str(tmp_path)]},
    )
    from app.models import Message

    with SessionLocal() as db:
        message = Message(chat_id=chat_id, role="assistant", content="")
        db.add(message)
        db.commit()
        generation_id = message.id
    prompt = (
        f"В папке {workspace} открой t4l.txt, сохрани его содержимое в t4l_out.txt, "
        "затем открой t4l_out.txt и подтверди содержимое. В конце выведи T4L_DONE."
    )
    return _context(user_id, chat_id, generation_id, workspace, prompt), workspace


def test_the_t4l_prompt_opens_a_task_row(task_context):
    """The controller must own the task: resume, progress and the harness resolve all need it."""
    context, workspace = task_context
    controller = LocalTaskController()
    controller.attach(context)
    with SessionLocal() as db:
        row = controller.open(db, context)
        assert row is not None, "a named-file task must create a local_tasks row"
        assert context.user_prompt in row.original_user_request
        assert context.task_id == row.id
        # The task is bound to the workspace the prompt names, not to a guessed root.
        assert str(row.workspace).rstrip("\\/").endswith("T4L")


def test_a_plain_question_opens_no_task(task_context):
    context, _workspace = task_context
    context.user_prompt = PLAIN_QUESTION
    controller = LocalTaskController()
    controller.attach(context)
    with SessionLocal() as db:
        assert controller.open(db, context) is None


def test_a_device_poll_does_not_write_on_every_request(client, auth):
    """Idle polling is a read: one write per five seconds, not one per poll."""
    headers = auth()
    paired = client.post("/tools/devices/pair", headers=headers, json={"platform": "windows"}).json()
    with SessionLocal() as db:
        row = db.get(PairedDevice, paired["device_id"])
        assert row is not None
        user = row.user_id
        row.last_seen = now() - timedelta(seconds=1)
        db.commit()
        first = row.last_seen

        device_module.require_device(db, SimpleNamespace(id=user), row.id, paired["credential"])
        subject = db.get(PairedDevice, row.id)
        assert subject is not None
        assert subject.last_seen == first, "a poll one second later must not write"

        subject.last_seen = now() - timedelta(seconds=device_module.LAST_SEEN_WRITE_SECONDS + 1)
        db.commit()
        stale = subject.last_seen
        device_module.require_device(db, SimpleNamespace(id=user), row.id, paired["credential"])
        refreshed = db.get(PairedDevice, row.id)
        assert refreshed is not None
        assert refreshed.last_seen > stale


def test_the_pool_covers_the_polling_burst_that_starved_the_turn():
    assert isinstance(engine.pool, QueuePool)
    assert engine.pool.size() == POOL_SIZE
    assert POOL_SIZE + MAX_OVERFLOW >= 20, "the observed device burst was ~20 concurrent polls"
    assert POOL_TIMEOUT_SECONDS < 30, "a starved checkout must fail typed, not hang a generation"


def test_a_stream_turn_commits_the_task_it_was_given(client, auth, tmp_path):
    """The real stream path, not the controller in isolation: the row must survive the turn."""
    from sqlalchemy import select

    from app.tools.models import LocalTask as TaskRow

    headers = auth()
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    root = tmp_path / "Canalla E2E" / "T4L"
    root.mkdir(parents=True)
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "ask", "workspace_roots": [str(tmp_path)]},
    )
    prompt = (
        f"В папке {root} открой t4l.txt, сохрани его содержимое в t4l_out.txt, "
        "затем открой t4l_out.txt и подтверди содержимое. В конце выведи T4L_DONE."
    )
    response = client.post(f"/chats/{chat_id}/stream", headers=headers, json={"content": prompt})
    assert response.status_code == 200
    assert "event: meta" in response.text
    assert "stream_interrupted" not in response.text
    with SessionLocal() as db:
        rows = db.scalars(select(TaskRow).where(TaskRow.chat_id == chat_id)).all()
        assert rows, "the stream must persist the task it executed"
        assert prompt in rows[0].original_user_request


def test_a_database_timeout_is_a_typed_retryable_tool_error(monkeypatch):
    """The old behaviour was an untyped TimeoutError that killed the generation."""
    executor = ToolExecutor(SimpleNamespace(definitions=lambda: []))
    busy = DatabaseBusy("QueuePool limit of size 5 overflow 10 reached")

    async def starved(*args, **kwargs):
        raise busy

    monkeypatch.setattr(executor, "_execute", starved)
    context = SimpleNamespace(origin="model")
    with pytest.raises(ToolError) as raised:
        asyncio.run(executor.execute("read_file", {"path": "t4l.txt"}, context))
    assert raised.value.code == "database_busy"
