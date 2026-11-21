"""Free, official search APIs, one adapter each.

The paid provider is an optimisation, not a requirement: research has to work on an installation
that has no search key at all. Every adapter here speaks a public API that documents a free tier
with no key — Wikipedia's MediaWiki API, arXiv's Atom API, the Stack Exchange API and the GitHub
REST API — returns the same `Hit` shape, and never invents a row.

Each adapter is deliberately small. A provider that cannot answer raises `AdapterError` with a
short typed code, so the chain can say *which* provider was unavailable instead of pretending the
query had no results.
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from ...agent.webclient import USER_AGENT

MAX_BODY = 1_000_000
TAG = re.compile(r"<[^>]+>")
SPACE = re.compile(r"\s+")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


class AdapterError(Exception):
    """A typed provider failure. `code` is short and non-secret."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Hit:
    url: str
    title: str
    excerpt: str = ""
    publisher: str = ""
    published_at: str | None = None
    position: int | None = None
    provider: str = ""


def plain(markup: str, limit: int = 1500) -> str:
    text = TAG.sub(" ", markup or "")
    text = html.unescape(text)
    return SPACE.sub(" ", text).strip()[:limit]


def query_language(query: str, language: str | None) -> str:
    """The Wikipedia subdomain to ask.

    An explicit `language` wins. Otherwise Cyrillic asks the Russian edition and everything else
    asks English — a wrong guess returns nothing, which the chain reports honestly rather than
    silently substituting a different question.
    """
    if language:
        return language.split("-", 1)[0].lower()
    return "ru" if CYRILLIC.search(query or "") else "en"


class SearchAdapter:
    name = "adapter"
    kinds: frozenset[str] = frozenset({"web"})
    timeout = 8.0

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    def available(self) -> bool:
        return True

    async def search(self, query: str, *, top: int, language: str | None = None) -> list[Hit]:
        raise NotImplementedError

    async def _get(self, url: str, params: dict, headers: dict | None = None):
        request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json, application/atom+xml"}
        request_headers.update(headers or {})
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=5),
                follow_redirects=False,
                transport=self.transport,
                headers=request_headers,
            ) as client:
                response = await client.get(url, params=params)
        except httpx.TimeoutException:
            raise AdapterError("timeout") from None
        except httpx.HTTPError:
            raise AdapterError("unavailable") from None
        if response.status_code == 429:
            raise AdapterError("rate_limited")
        if response.status_code in {401, 403}:
            raise AdapterError("forbidden")
        if response.status_code != 200:
            raise AdapterError(f"http_{response.status_code}")
        if len(response.content) > MAX_BODY:
            raise AdapterError("too_large")
        return response


class WikipediaAdapter(SearchAdapter):
    name = "wikipedia"
    kinds = frozenset({"web", "research_paper", "news"})

    async def search(self, query: str, *, top: int, language: str | None = None) -> list[Hit]:
        lang = query_language(query, language)
        response = await self._get(
            f"https://{lang}.wikipedia.org/w/api.php",
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": str(top),
                "srprop": "snippet|timestamp|wordcount",
                "format": "json",
                "formatversion": "2",
                "utf8": "1",
            },
        )
        try:
            payload = response.json()
        except ValueError:
            raise AdapterError("malformed") from None
        rows = ((payload.get("query") or {}).get("search") or []) if isinstance(payload, dict) else []
        hits: list[Hit] = []
        for index, row in enumerate(rows[:top]):
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            hits.append(
                Hit(
                    url=f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='')}",
                    title=title,
                    excerpt=plain(str(row.get("snippet") or "")),
                    publisher="wikipedia.org",
                    published_at=row.get("timestamp"),
                    position=index + 1,
                    provider=self.name,
                )
            )
        return hits


class ArxivAdapter(SearchAdapter):
    """arXiv asks for one request every three seconds; the cool-down is process-wide."""

    name = "arxiv"
    kinds = frozenset({"research_paper"})
    timeout = 12.0
    _next_call = 0.0

    async def search(self, query: str, *, top: int, language: str | None = None) -> list[Hit]:
        now = time.monotonic()
        wait = ArxivAdapter._next_call - now
        if wait > 0:
            import asyncio

            await asyncio.sleep(min(wait, 3.0))
        ArxivAdapter._next_call = time.monotonic() + 3.0
        response = await self._get(
            "https://export.arxiv.org/api/query",
            {
                "search_query": f"all:{query}",
                "start": "0",
                "max_results": str(max(1, min(top, 20))),
                "sortBy": "relevance",
            },
            headers={"Accept": "application/atom+xml"},
        )
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError:
            raise AdapterError("malformed") from None
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        hits: list[Hit] = []
        for index, entry in enumerate(root.findall("atom:entry", namespace)[:top]):
            identifier = (entry.findtext("atom:id", "", namespace) or "").strip()
            title = SPACE.sub(" ", (entry.findtext("atom:title", "", namespace) or "")).strip()
            if not identifier or not title:
                continue
            hits.append(
                Hit(
                    url=identifier.replace("http://", "https://", 1),
                    title=title[:300],
                    excerpt=SPACE.sub(" ", (entry.findtext("atom:summary", "", namespace) or ""))[:1500],
                    publisher="arxiv.org",
                    published_at=(entry.findtext("atom:published", "", namespace) or "")[:10] or None,
                    position=index + 1,
                    provider=self.name,
                )
            )
        return hits


class StackExchangeAdapter(SearchAdapter):
    name = "stackexchange"
    kinds = frozenset({"web"})

    async def search(self, query: str, *, top: int, language: str | None = None) -> list[Hit]:
        response = await self._get(
            "https://api.stackexchange.com/2.3/search/advanced",
            {
                "order": "desc",
                "sort": "relevance",
                "q": query,
                "site": "stackoverflow",
                "pagesize": str(max(1, min(top, 10))),
                "filter": "default",
            },
        )
        try:
            payload = response.json()
        except ValueError:
            raise AdapterError("malformed") from None
        rows = (payload.get("items") or []) if isinstance(payload, dict) else []
        hits: list[Hit] = []
        for index, row in enumerate(rows[:top]):
            if not isinstance(row, dict):
                continue
            url = str(row.get("link") or "")
            title = plain(str(row.get("title") or ""), 300)
            if not url or not title:
                continue
            tags = ",".join(str(tag) for tag in (row.get("tags") or [])[:5])
            created = row.get("creation_date")
            published = None
            if isinstance(created, (int, float)):
                published = datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%d")
            hits.append(
                Hit(
                    url=url,
                    title=title,
                    excerpt=f"score={row.get('score')}; answered={bool(row.get('is_answered'))}; tags={tags}",
                    publisher="stackoverflow.com",
                    published_at=published,
                    position=index + 1,
                    provider=self.name,
                )
            )
        return hits


class GitHubAdapter(SearchAdapter):
    name = "github"
    kinds = frozenset({"web"})

    async def search(self, query: str, *, top: int, language: str | None = None) -> list[Hit]:
        response = await self._get(
            "https://api.github.com/search/repositories",
            {
                "q": query,
                "per_page": str(max(1, min(top, 10))),
                "order": "desc",
            },
            headers={"Accept": "application/vnd.github+json"},
        )
        try:
            payload = response.json()
        except ValueError:
            raise AdapterError("malformed") from None
        rows = (payload.get("items") or []) if isinstance(payload, dict) else []
        hits: list[Hit] = []
        for index, row in enumerate(rows[:top]):
            if not isinstance(row, dict):
                continue
            url = str(row.get("html_url") or "")
            if not url:
                continue
            hits.append(
                Hit(
                    url=url,
                    title=str(row.get("full_name") or url)[:300],
                    excerpt=(
                        f"{str(row.get('description') or '')[:400]} "
                        f"(stars={row.get('stargazers_count')}, language={row.get('language')})"
                    ).strip(),
                    publisher="github.com",
                    published_at=str(row.get("updated_at") or "")[:10] or None,
                    position=index + 1,
                    provider=self.name,
                )
            )
        return hits


ADAPTERS: tuple[type[SearchAdapter], ...] = (
    WikipediaAdapter,
    ArxivAdapter,
    StackExchangeAdapter,
    GitHubAdapter,
)
