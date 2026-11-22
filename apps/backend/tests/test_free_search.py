"""Free search must be real work, not a renamed error.

These tests use `httpx.MockTransport`, so they prove the parsing, selection, dedupe and typed
failure of every adapter without touching the network. The live half of the proof is
`test_live_web_smoke.py -m live_web`.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.tools.contracts import ToolError
from app.tools.search import chain as chain_module
from app.tools.search.chain import SearchOutcome, adapters_for, free_search, search_sources
from app.tools.search.provider import WebFetchProvider, WebSearchProvider
from app.tools.search.providers import (
    AdapterError,
    ArxivAdapter,
    GitHubAdapter,
    Hit,
    StackExchangeAdapter,
    WikipediaAdapter,
)
from app.tools.tinyfish.web import FetchArgs, SearchArgs


def run(coro):
    return asyncio.run(coro)


def transport_for(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host or ""
        for key, value in routes.items():
            if key in host:
                if isinstance(value, Exception):
                    raise value
                return value
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def test_wikipedia_adapter_parses_real_api_shape():
    payload = {
        "query": {
            "search": [
                {
                    "title": "FastAPI",
                    "snippet": '<span class="searchmatch">FastAPI</span> is a web framework',
                    "timestamp": "2026-01-01T00:00:00Z",
                    "wordcount": 1200,
                }
            ]
        }
    }
    adapter = WikipediaAdapter(transport=transport_for({"wikipedia.org": httpx.Response(200, json=payload)}))
    hits = run(adapter.search("FastAPI", top=5))
    assert hits[0].title == "FastAPI"
    assert hits[0].url == "https://en.wikipedia.org/wiki/FastAPI"
    assert hits[0].excerpt == "FastAPI is a web framework"
    assert hits[0].provider == "wikipedia"


def test_wikipedia_picks_the_russian_edition_for_a_cyrillic_query():
    payload = {"query": {"search": [{"title": "Пушкин", "snippet": "поэт", "timestamp": None}]}}
    seen = {}

    def handler(request):
        seen["host"] = request.url.host
        return httpx.Response(200, json=payload)

    adapter = WikipediaAdapter(transport=httpx.MockTransport(handler))
    run(adapter.search("Пушкин", top=3))
    assert seen["host"] == "ru.wikipedia.org"


def test_arxiv_adapter_parses_atom_and_upgrades_the_scheme():
    atom = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Adaptive Agents
      for Real Work</title>
    <summary>We study bounded agents.</summary>
    <published>2024-01-01T00:00:00Z</published>
  </entry>
</feed>"""
    adapter = ArxivAdapter(transport=transport_for({"export.arxiv.org": httpx.Response(200, content=atom)}))
    hits = run(adapter.search("agents", top=5))
    assert hits[0].url == "https://arxiv.org/abs/2401.00001v1"
    assert hits[0].title == "Adaptive Agents for Real Work"
    assert hits[0].published_at == "2024-01-01"


def test_stackexchange_and_github_adapters_parse_their_rows():
    stack = {
        "items": [
            {
                "title": "How to use FastAPI &amp; SQLite?",
                "link": "https://stackoverflow.com/q/1",
                "score": 12,
                "is_answered": True,
                "tags": ["python", "fastapi"],
                "creation_date": 1700000000,
            }
        ]
    }
    github = {
        "items": [
            {
                "html_url": "https://github.com/tiangolo/fastapi",
                "full_name": "tiangolo/fastapi",
                "description": "FastAPI framework",
                "stargazers_count": 70000,
                "language": "Python",
                "updated_at": "2026-01-02T03:04:05Z",
            }
        ]
    }
    routes = {
        "api.stackexchange.com": httpx.Response(200, json=stack),
        "api.github.com": httpx.Response(200, json=github),
    }
    se_hits = run(StackExchangeAdapter(transport=transport_for(routes)).search("fastapi", top=5))
    gh_hits = run(GitHubAdapter(transport=transport_for(routes)).search("fastapi", top=5))
    assert se_hits[0].title == "How to use FastAPI & SQLite?"
    assert "answered=True" in se_hits[0].excerpt
    assert gh_hits[0].url == "https://github.com/tiangolo/fastapi"
    assert "stars=70000" in gh_hits[0].excerpt


def test_rate_limit_is_a_typed_failure_not_an_empty_result():
    adapter = WikipediaAdapter(transport=transport_for({"wikipedia.org": httpx.Response(429, json={})}))
    with pytest.raises(AdapterError) as error:
        run(adapter.search("anything", top=3))
    assert error.value.code == "rate_limited"


def test_adapter_selection_follows_the_question():
    assert [a.name for a in adapters_for("Пушкин биография", "web")] == ["wikipedia"]
    assert [a.name for a in adapters_for("python asyncio timeout", "web")] == [
        "stackexchange",
        "github",
        "wikipedia",
    ]
    assert [a.name for a in adapters_for("agentic systems survey", "research_paper")] == [
        "arxiv",
        "wikipedia",
    ]
    assert [a.name for a in adapters_for("breaking news", "news")] == ["wikipedia"]


class FakeAdapter:
    def __init__(self, name, hits=None, error=None):
        self.name = name
        self.hits = hits or []
        self.error = error
        self.calls = []

    def available(self):
        return True

    async def search(self, query, *, top, language=None):
        self.calls.append(query)
        if self.error:
            raise AdapterError(self.error)
        return self.hits[:top]


def test_free_search_interleaves_providers_and_drops_duplicate_urls():
    first = FakeAdapter(
        "alpha",
        [Hit("https://example.com/a", "A", provider="alpha"), Hit("https://example.com/b", "B")],
    )
    second = FakeAdapter(
        "beta",
        [Hit("https://example.com/a/", "A again"), Hit("https://example.com/c", "C")],
    )
    outcome = run(free_search("question", top=3, adapters=[first, second]))
    urls = [source["url"] for source in outcome.sources]
    assert urls == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
    assert outcome.providers == ["alpha", "beta"]


def test_a_failing_provider_is_reported_and_the_others_still_answer():
    good = FakeAdapter("good", [Hit("https://example.com/ok", "OK", provider="good")])
    bad = FakeAdapter("bad", error="rate_limited")
    outcome = run(free_search("question", adapters=[good, bad]))
    assert [source["url"] for source in outcome.sources] == ["https://example.com/ok"]
    assert outcome.errors == ["bad:rate_limited"]


def test_include_and_exclude_domains_are_applied_before_the_cut():
    adapter = FakeAdapter(
        "alpha",
        [
            Hit("https://keep.example.com/1", "keep"),
            Hit("https://drop.example.com/2", "drop"),
        ],
    )
    outcome = run(free_search("q", adapters=[adapter], exclude=["drop.example.com"], top=5))
    assert [source["url"] for source in outcome.sources] == ["https://keep.example.com/1"]


def test_search_sources_uses_tinyfish_when_a_key_exists(monkeypatch):
    class Credentials:
        def configured(self, provider):
            return provider == "tinyfish"

    class Client:
        credentials = Credentials()

    class Provider:
        def __init__(self, *args, **kwargs):
            pass

        async def execute(self, args, context):
            from app.tools.contracts import ToolResult

            return ToolResult(
                sources=[
                    {
                        "url": "https://paid.example.com/x",
                        "final_url": "https://paid.example.com/x",
                        "title": "paid",
                        "excerpt": "paid result",
                        "retrieval": "search",
                        "secret_provider_field": "must not leak",
                    }
                ]
            )

    import app.tools.tinyfish.client as client_module
    import app.tools.tinyfish.web as web_module

    monkeypatch.setattr(client_module, "get_tinyfish_client", lambda: Client())
    monkeypatch.setattr(web_module, "TinyFishSearchProvider", Provider)

    outcome = run(search_sources("query", top=3))
    assert outcome.paid is True
    assert outcome.sources[0]["url"] == "https://paid.example.com/x"
    assert "secret_provider_field" not in outcome.sources[0]


def test_search_sources_falls_back_to_free_apis_when_the_key_is_missing(monkeypatch):
    class Credentials:
        def configured(self, provider):
            return False

    class Client:
        credentials = Credentials()

    import app.tools.tinyfish.client as client_module

    monkeypatch.setattr(client_module, "get_tinyfish_client", lambda: Client())

    async def fake_free(query, **kwargs):
        return SearchOutcome(
            sources=[{"url": "https://free.example.com/1", "title": "free"}], providers=["wikipedia"]
        )

    monkeypatch.setattr(chain_module, "free_search", fake_free)
    outcome = run(search_sources("query", top=3))
    assert outcome.paid is False
    assert outcome.sources[0]["url"] == "https://free.example.com/1"


def test_web_search_tool_reports_a_typed_failure_when_every_provider_fails(monkeypatch):
    async def no_results(query, **kwargs):
        return SearchOutcome(errors=["wikipedia:timeout", "github:rate_limited"])

    monkeypatch.setattr("app.tools.search.provider.search_sources", no_results)
    with pytest.raises(ToolError) as error:
        run(WebSearchProvider().execute(SearchArgs(query="anything"), None))
    assert error.value.code == "search_unavailable"
    assert "wikipedia:timeout" in error.value.detail


def test_web_search_tool_returns_sources_with_the_provider_recorded(monkeypatch):
    async def results(query, **kwargs):
        return SearchOutcome(
            sources=[
                {
                    "url": "https://example.com/one",
                    "title": "One",
                    "excerpt": "first",
                    "provider": "wikipedia",
                }
            ],
            providers=["wikipedia"],
        )

    monkeypatch.setattr("app.tools.search.provider.search_sources", results)
    result = run(WebSearchProvider().execute(SearchArgs(query="anything"), None))
    assert result.sources[0]["provider"] == "wikipedia"
    assert result.cost_estimate == 0


def test_web_fetch_uses_the_local_reader_when_no_key_exists(monkeypatch):
    class Credentials:
        def configured(self, provider):
            return False

    class Client:
        credentials = Credentials()

    class Page:
        url = "https://example.com/final"
        status = 200
        content_type = "text/html; charset=utf-8"
        text = (
            "<html><head><title>Example</title></head><body><p>Hello research</p>"
            + "<p>"
            + "A real paragraph with enough words to be readable by the agent. " * 6
            + "</p>"
            + "</body></html>"
        )
        bytes = 80

    import app.tools.tinyfish.client as client_module

    monkeypatch.setattr(client_module, "get_tinyfish_client", lambda: Client())
    monkeypatch.setattr("app.tools.search.provider.fetch_page", lambda url, timeout=20: Page())

    result = run(WebFetchProvider().execute(FetchArgs(urls=["https://example.com"]), None))
    assert result.sources[0]["title"] == "Example"
    assert "Hello research" in result.sources[0]["excerpt"]
    assert result.sources[0]["needs_browser"] is False
    assert result.cost_estimate == 0


def test_web_fetch_refuses_private_addresses_before_reading(monkeypatch):
    called = []

    class Credentials:
        def configured(self, provider):
            return False

    class Client:
        credentials = Credentials()

    import app.tools.tinyfish.client as client_module

    monkeypatch.setattr(client_module, "get_tinyfish_client", lambda: Client())
    monkeypatch.setattr("app.tools.search.provider.fetch_page", lambda url, timeout=20: called.append(url))
    with pytest.raises(ToolError) as error:
        run(WebFetchProvider().execute(FetchArgs(urls=["http://127.0.0.1:8000/"]), None))
    assert error.value.code == "unsafe_url"
    assert called == []


def test_deep_research_provider_search_uses_the_chain(monkeypatch):
    from app.tools import agent_tools

    async def fake_search(query, **kwargs):
        return SearchOutcome(sources=[{"url": "https://example.com/r", "title": "R"}])

    monkeypatch.setattr(chain_module, "search_sources", fake_search)
    rows = run(agent_tools._provider_search("query"))
    assert rows == [{"url": "https://example.com/r", "title": "R"}]


def test_chain_never_invents_a_source_when_every_adapter_returns_nothing():
    outcome = run(free_search("q", adapters=[FakeAdapter("a"), FakeAdapter("b")]))
    assert outcome.sources == []
    assert outcome.providers == []
    assert not outcome.ok
