"""Fake-provider browser multi-page lifecycle. No live TinyFish calls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.tools.contracts import ToolError
from app.tools.local.facts import from_tool, record
from app.tools.local.grounding import fallback_answer, goal_met, incomplete
from app.tools.orchestrator import ToolOrchestrator
from app.tools.policy import WebSettings
from tests.fakes_web import FakeBrowser, fake_registry, python_org_home_links

PROMPT = (
    "Открой в браузере официальный сайт Python, прочитай заголовок, "
    "перейди в документацию и скажи заголовок страницы документации."
)


def _context():
    return SimpleNamespace(
        user_prompt=PROMPT,
        user_id="user-1",
        mode="on",
        computer_mode="off",
        host_online=False,
        assigned_device_id=None,
        sources=[],
        secrets=(),
        settings=WebSettings(
            computer_mode="off",
            default_mode="on",
            browser_mode="auto",
            agent_mode="off",
            search_enabled=True,
            fetch_enabled=True,
        ),
        resolver=None,
        emit=lambda *args, **kwargs: None,
        progress=lambda **kwargs: None,
    )


def test_python_org_style_links_include_relative_and_absolute_docs():
    links = python_org_home_links()
    assert any(item["href"] == "/doc/" for item in links)
    assert any(item["href"] == "https://docs.python.org/3/" for item in links)
    assert links[0]["text"] != "Documentation"


def test_click_success_and_goto_fallback():
    browser = FakeBrowser()
    context = _context()

    async def run():
        opened = await browser.execute(
            SimpleNamespace(operation="open", url="https://www.python.org/", link_id=None, seconds=0),
            context,
        )
        assert opened.metadata["title"] == "Welcome to Python.org"
        docs = next(item for item in browser.links if item["text"] == "Documentation")
        clicked = await browser.execute(
            SimpleNamespace(operation="click", url=None, link_id=docs["id"], seconds=0),
            context,
        )
        assert clicked.metadata["current_url"].endswith("/doc/")
        assert clicked.metadata["navigation_method"] == "click"
        browser.click_fails = True
        try:
            await browser.execute(
                SimpleNamespace(operation="click", url=None, link_id=docs["id"], seconds=0),
                context,
            )
            raise AssertionError("click should fail")
        except ToolError as error:
            assert error.code == "browser_click_failed"
        fallback = await browser.execute(
            SimpleNamespace(operation="open", url=docs["resolved_url"], link_id=None, seconds=0),
            context,
        )
        assert fallback.metadata["final_url"].endswith("/doc/")
        assert fallback.metadata["navigation_method"] == "goto"

    asyncio.run(run())


def test_page2_becomes_w_source_and_empty_llm_uses_fallback():
    facts = from_tool(
        {},
        "web_browser",
        {
            "text": "Welcome to Python.org",
            "sources": [
                {
                    "url": "https://www.python.org/",
                    "final_url": "https://www.python.org/",
                    "title": "Welcome to Python.org",
                    "label": "W1",
                }
            ],
            "metadata": {"title": "Welcome to Python.org", "current_url": "https://www.python.org/"},
        },
        arguments={"operation": "open", "url": "https://www.python.org/"},
        tool_run_id="run-1",
    )
    facts = from_tool(
        facts,
        "web_browser",
        {
            "text": "3.13.7 Documentation",
            "sources": [
                {
                    "url": "https://docs.python.org/3/",
                    "final_url": "https://docs.python.org/3/",
                    "title": "3.13.7 Documentation",
                    "label": "W2",
                }
            ],
            "metadata": {
                "title": "3.13.7 Documentation",
                "current_url": "https://docs.python.org/3/",
                "navigation_method": "goto",
            },
        },
        arguments={"operation": "open", "url": "https://docs.python.org/3/"},
        tool_run_id="run-2",
    )
    pages = [item for item in facts["verified"] if item["kind"] == "BROWSER_PAGE"]
    assert [item["page_index"] for item in pages] == [1, 2]
    assert pages[1]["verified"] is True
    assert pages[1]["source_tool_run_id"] == "run-2"
    assert goal_met(facts, PROMPT)
    text = fallback_answer(facts, PROMPT)
    assert "3.13.7 Documentation" in text
    assert "Welcome to Python.org" in text


def test_navigation_failure_is_visible_error():
    facts = record(
        {},
        "BROWSER_PAGE",
        {"url": "https://www.python.org/", "title": "Welcome to Python.org", "page_index": 1},
    )
    facts = record(
        facts,
        "BROWSER_ERROR",
        {"error_code": "documentation_page_not_loaded", "session_status": "FAILED"},
    )
    text = fallback_answer(facts, PROMPT)
    assert "browser navigation failed" in text
    assert "documentation_page_not_loaded" in text
    assert incomplete("Welcome to Python.org", facts, PROMPT)


def test_terminal_paths_clear_registry():
    browser = FakeBrowser()
    context = _context()

    async def run():
        await browser.execute(
            SimpleNamespace(operation="open", url="https://www.python.org/", link_id=None, seconds=0),
            context,
        )
        assert browser.sessions
        await browser.execute(SimpleNamespace(operation="close", url=None, link_id=None, seconds=0), context)
        assert browser.sessions == {}
        await browser.execute(
            SimpleNamespace(operation="open", url="https://www.python.org/", link_id=None, seconds=0),
            context,
        )
        await browser.close_all()
        assert browser.sessions == {}
        await browser.execute(
            SimpleNamespace(operation="open", url="https://www.python.org/", link_id=None, seconds=0),
            context,
        )
        await browser.stop("fake-browser", context.user_id)
        assert browser.sessions == {}
        browser.raise_on_open = True
        try:
            await browser.execute(
                SimpleNamespace(operation="open", url="https://www.python.org/", link_id=None, seconds=0),
                context,
            )
        except ToolError:
            await browser.close_all()
        assert browser.sessions == {}

    asyncio.run(run())


def test_controller_selects_buried_docs_then_closes():
    orchestrator = ToolOrchestrator(fake_registry(), None)
    recorded = []

    async def fake_run(name, arguments, context, notes, origin="model"):
        recorded.append((name, origin, arguments))
        payload = str(arguments)
        if "close" in payload:
            return {"ok": True, "text": "closed", "sources": [], "metadata": {"session_status": "CLOSED"}}
        context.tinyfish_browser_done = True
        if "click" in payload or "/doc" in payload or "docs.python" in payload:
            context.sources = [
                {
                    "url": "https://www.python.org/doc/",
                    "final_url": "https://www.python.org/doc/",
                    "title": "Python Docs",
                    "label": "W2",
                    "links": [],
                }
            ]
            return {
                "ok": True,
                "text": "Python Docs",
                "sources": context.sources,
                "metadata": {"title": "Python Docs", "current_url": "https://www.python.org/doc/"},
            }
        context.sources = [
            {
                "url": "https://www.python.org/",
                "final_url": "https://www.python.org/",
                "title": "Welcome to Python.org",
                "label": "W1",
                "links": python_org_home_links(),
            }
        ]
        return {
            "ok": True,
            "text": "Welcome to Python.org",
            "sources": context.sources,
            "metadata": {"title": "Welcome to Python.org", "current_url": "https://www.python.org/"},
        }

    _, browser = orchestrator.registry.get("browser_start")
    browser._owned_session = lambda uid: SimpleNamespace(
        session_id="s", user_id=uid, active=True, links=python_org_home_links()
    )
    orchestrator._run = fake_run
    context = _context()

    async def run():
        await orchestrator._maybe_tinyfish_paid(
            context, [], PROMPT, SimpleNamespace(required=False), planning=[]
        )
        await orchestrator._close_tinyfish_browser(context, [])

    asyncio.run(run())
    assert recorded[0][0] == "web_browser" and recorded[0][1] == "server_policy"
    assert any("click" in str(arguments) or "/doc" in str(arguments) for _n, _o, arguments in recorded)
    assert any("close" in str(arguments) for _n, _o, arguments in recorded)
