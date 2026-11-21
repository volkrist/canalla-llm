"""A real network research smoke: the product's own fetcher against a public page.

Opt-in on purpose (`live_web`): the suite must stay green offline. This is the smallest honest
proof that research is not only a faked pipeline — `fetch_page` really reaches a public host,
respects robots.txt, extracts readable text, and `DeepResearch` records the source URL it read.

    .venv\\Scripts\\python.exe -m pytest tests/test_live_web_smoke.py -m live_web -q
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent.research import DeepResearch, ResearchBudget
from app.agent.webclient import fetch_page
from app.tools.search.chain import free_search
from app.tools.search.provider import WebFetchProvider
from app.tools.tinyfish.web import FetchArgs

pytestmark = pytest.mark.live_web

SOURCE = "https://example.com/"


def test_the_product_fetcher_reads_a_real_public_page():
    page = fetch_page(SOURCE, timeout=30)
    assert page.url.startswith("https://example.com")
    assert "Example Domain" in page.text
    assert len(page.text.strip()) > 50


def test_deep_research_records_the_source_it_really_read():
    async def search(query: str):
        return [{"url": SOURCE, "title": "Example Domain"}]

    async def fetch(url: str):
        page = await asyncio.to_thread(fetch_page, url, timeout=30)
        return {"url": page.url, "text": page.text, "title": "Example Domain"}

    engine = DeepResearch(
        search,
        fetch,
        budget=ResearchBudget(max_rounds=1, max_queries=1, max_pages=1, max_seconds=45),
    )
    result = asyncio.run(engine.run("What does the example domain page say?", ["Example Domain"]))

    payload = result.to_dict()
    assert payload["sources"], "a real page was read, so a source must be recorded"
    assert any(source["url"].startswith("https://example.com") for source in payload["sources"])
    assert payload["rounds"] >= 1


def test_free_search_reaches_a_real_public_api():
    """No key, no mock: the free chain must return real URLs from a public API."""
    outcome = asyncio.run(free_search("FastAPI", top=3))
    assert outcome.sources, outcome.errors
    assert outcome.providers
    assert all(source["url"].startswith("https://") for source in outcome.sources)


def test_free_search_returns_arxiv_papers_for_a_research_query():
    outcome = asyncio.run(
        free_search("retrieval augmented generation", top=3, domain_type="research_paper")
    )
    assert outcome.sources, outcome.errors
    assert any("arxiv.org" in source["url"] for source in outcome.sources)


def test_web_fetch_tool_reads_a_public_page_without_a_key():
    result = asyncio.run(WebFetchProvider().execute(FetchArgs(urls=[SOURCE]), None))
    assert result.sources
    assert "Example Domain" in result.sources[0]["excerpt"]
