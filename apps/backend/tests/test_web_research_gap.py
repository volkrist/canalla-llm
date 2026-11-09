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
from app.tools.web_router import canonical_url, pick_fetch_urls
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


def test_official_documentation_is_read_before_secondary_sources():
    sources = [
        {"url": "https://www.reddit.com/r/python/comments/x", "retrieval": "search"},
        {"url": "https://example.com/blog/post", "retrieval": "search"},
        {"url": "https://docs.python.org/3/library/asyncio.html", "retrieval": "search"},
        {"url": "https://github.com/org/project/releases", "retrieval": "search"},
    ]
    picked = pick_fetch_urls(sources, limit=2)
    assert picked[0] == "https://docs.python.org/3/library/asyncio.html"
    assert picked[1] == "https://github.com/org/project/releases"


def test_a_page_already_read_is_never_requested_again():
    sources = [
        {"url": "https://docs.python.org/3/a.html", "retrieval": "search"},
        {"url": "https://docs.python.org/3/b.html", "retrieval": "search"},
    ]
    exclude = {canonical_url("https://docs.python.org/3/a.html")}
    assert pick_fetch_urls(sources, exclude=exclude) == ["https://docs.python.org/3/b.html"]


def test_the_controller_reads_two_batches_and_stops():
    """Six sources, two batches of three pages, no repeats, then it stops."""
    from app.tools.contracts import ToolRegistry
    from app.tools.registry import make_registry
    from app.tools.web_router import classify_web

    base = make_registry()
    registry = ToolRegistry()
    for definition in base.definitions(auto_only=False):
        registry.register(definition, base.get(definition.name)[1])
    orchestrator = ToolOrchestrator(registry, ToolExecutor(registry))
    context = ExecutionContext(
        "user-1",
        "chat-1",
        None,
        ToolLimits(max_calls=24, hard_max_calls=40, max_fetch=12, max_pages=36),
        _noop,
        mode="on",
        computer_mode="off",
        settings=WebSettings(computer_mode="off", default_mode="on"),
        user_prompt="Изучи документацию",
    )
    context.sources = [
        {
            "url": f"https://example.com/doc{i}",
            "final_url": f"https://example.com/doc{i}",
            "title": f"Page {i}",
            "excerpt": f"page {i} text",
            "retrieval": "search",
        }
        for i in range(6)
    ]
    context.web_search_done = True
    notes: list[str] = []
    requested: list[list[str]] = []

    async def fake_run(name, arguments, ctx, note_list, origin):
        payload = json.loads(arguments) if isinstance(arguments, str) else arguments
        requested.append(list(payload.get("urls") or []))
        return {"text": "read", "sources": []}

    orchestrator._run = fake_run
    asyncio.run(
        orchestrator._bounded_research_reads(context, notes, classify_web("Изучи документацию", "on"))
    )

    assert context.research_batches == 2
    assert len(requested) == 2
    flat = [url for batch in requested for url in batch]
    assert len(flat) == 6
    assert len(set(flat)) == 6
    # A third call would need a third batch, which the controller must not take.
    asyncio.run(
        orchestrator._bounded_research_reads(context, notes, classify_web("Изучи документацию", "on"))
    )
    assert len(requested) == 2


async def _noop(event, value):
    return None


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
