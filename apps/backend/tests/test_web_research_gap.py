"""A search that never opened a page is not research.

The reported run ended with «Web: 1 Search / 0 Fetch» and «Достигнут лимит загруженных страниц».
Two separate defects were behind that pair:

* the task page budget (10) was smaller than the task fetch budget (12), so a legal fetch walk
  was refused by an unnamed cap first;
* `tor_fetch` spent the *direct* page budget, so the Web family could show 0 Fetch while pages
  had been consumed elsewhere.

A third gap is behavioural: for a computer task the server deliberately skips its own cheap
search, and if the model then only searches, nothing ever reads the page. The controller now
performs one bounded, free fetch of the sources it already has, recorded as `server_policy`.
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from app.database import SessionLocal
from app.tools.executor import ExecutionContext, ToolExecutor
from app.tools.models import LocalTask, ToolRun
from app.tools.orchestrator import ToolOrchestrator
from app.tools.policy import ToolLimits, WebSettings
from tests.fakes_web import fake_registry

RESEARCH_AND_BUILD = "Изучи актуальную документацию Python и создай в папке CLI-проект с тестами."


class SearchOnly:
    """A model that searches once and then stops calling tools."""

    supports_tools = True

    def __init__(self, url: str):
        self.url = url
        self.turns = 0

    async def plan_tools(self, messages, tools, usage):
        self.turns += 1
        if self.turns == 1:
            return {
                "tool_calls": [
                    {
                        "id": "search-1",
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "arguments": json.dumps({"query": "python documentation"}),
                        },
                    }
                ]
            }
        return {"tool_calls": []}


def test_a_search_only_turn_still_reads_one_page(client, auth, tmp_path):
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    root = tmp_path / "workspace"
    root.mkdir()
    from app.models import Message

    with SessionLocal() as db:
        message = Message(chat_id=chat_id, role="assistant", content="")
        db.add(message)
        db.commit()
        generation_id = message.id
    events = []

    async def emit(event, value):
        events.append((event, value))

    registry = fake_registry()
    context = ExecutionContext(
        user_id,
        chat_id,
        generation_id,
        ToolLimits(max_calls=24, hard_max_calls=40),
        emit,
        mode="on",
        computer_mode="off",
        settings=WebSettings(computer_mode="off", default_mode="on"),
        user_prompt=RESEARCH_AND_BUILD,
        host_online=True,
    )

    orchestrator = ToolOrchestrator(registry, ToolExecutor(registry))
    asyncio.run(
        orchestrator.prepare(
            SearchOnly("https://example.com"),
            [{"role": "user", "content": RESEARCH_AND_BUILD}],
            0,
            context,
            {},
        )
    )

    with SessionLocal() as db:
        task = db.scalar(select(LocalTask).where(LocalTask.chat_id == chat_id))
        assert task is not None
        runs = db.scalars(select(ToolRun).where(ToolRun.task_id == task.id)).all()
    names = [(row.tool_name, row.origin) for row in runs]
    assert ("web_search", "model") in names
    assert ("web_fetch", "server_policy") in names
