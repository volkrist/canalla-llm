"""One search contract, two provider modes: paid key or the free public APIs.

`web_search` must not be a dead tool on an installation without a TinyFish key. The chain keeps
the paid provider as an optimisation and falls back to the free adapters, and it reports *what
answered* and *what failed* instead of returning an empty list that looks like "no such topic".

Selection is deterministic and query-driven, so a technical question asks Stack Overflow and
GitHub, a research question asks arXiv, and a general question asks Wikipedia. A domain that has
no free provider is not faked: the chain says so.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from ..contracts import ToolError
from ..web_router import canonical_url
from .providers import (
    AdapterError,
    ArxivAdapter,
    GitHubAdapter,
    Hit,
    SearchAdapter,
    StackExchangeAdapter,
    WikipediaAdapter,
)

TECHNICAL = re.compile(
    r"(?i)\b(python|javascript|typescript|rust|golang|java|sql|http|https|api|json|yaml|docker|"
    r"git|regex|exception|traceback|fastapi|django|flask|react|vue|node|npm|pip|cargo|sqlite|"
    r"powershell|bash|linux|windows|async|class|function|library|framework|bug|error|install)\b"
)


@dataclass
class SearchOutcome:
    sources: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    paid: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.sources)


def adapters_for(query: str, domain_type: str) -> list[SearchAdapter]:
    if domain_type == "research_paper":
        return [ArxivAdapter(), WikipediaAdapter()]
    if domain_type == "news":
        # No free keyless news index exists; Wikipedia is the only honest fallback and the chain
        # will say `no_free_provider_for_news` when it returns nothing.
        return [WikipediaAdapter()]
    if TECHNICAL.search(query or ""):
        return [StackExchangeAdapter(), GitHubAdapter(), WikipediaAdapter()]
    return [WikipediaAdapter()]


def _host_allowed(url: str, include: list[str], exclude: list[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False

    def matches(domain: str) -> bool:
        item = domain.strip().lower()
        return bool(item) and (host == item or host.endswith("." + item))

    if exclude and any(matches(item) for item in exclude):
        return False
    if include and not any(matches(item) for item in include):
        return False
    return True


def _interleave(groups: list[list[Hit]], limit: int) -> list[Hit]:
    """Take one hit from each provider per round: relevance first, diversity preserved."""
    seen: set[str] = set()
    picked: list[Hit] = []
    for index in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if index >= len(group):
                continue
            hit = group[index]
            key = canonical_url(hit.url)
            if not key or key in seen:
                continue
            seen.add(key)
            picked.append(hit)
            if len(picked) >= limit:
                return picked
    return picked


def to_source(hit: Hit) -> dict:
    return {
        "url": hit.url,
        "final_url": hit.url,
        "title": hit.title,
        "excerpt": hit.excerpt,
        "publisher": hit.publisher,
        "published_at": hit.published_at,
        "position": hit.position,
        "retrieval": "search",
        "provider": hit.provider,
    }


async def free_search(
    query: str,
    *,
    top: int = 5,
    language: str | None = None,
    domain_type: str = "web",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    adapters: list[SearchAdapter] | None = None,
) -> SearchOutcome:
    chosen = adapters if adapters is not None else adapters_for(query, domain_type)
    chosen = [adapter for adapter in chosen if adapter.available()]
    if not chosen:
        return SearchOutcome(errors=["no_free_provider"])

    results = await asyncio.gather(
        *(adapter.search(query, top=top, language=language) for adapter in chosen),
        return_exceptions=True,
    )
    outcome = SearchOutcome()
    groups: list[list[Hit]] = []
    for adapter, result in zip(chosen, results):
        if isinstance(result, AdapterError):
            outcome.errors.append(f"{adapter.name}:{result.code}")
            continue
        if isinstance(result, BaseException):
            outcome.errors.append(f"{adapter.name}:unavailable")
            continue
        hits = [hit for hit in result if _host_allowed(hit.url, include or [], exclude or [])]
        if hits:
            outcome.providers.append(adapter.name)
            groups.append(hits)
    outcome.sources = [to_source(hit) for hit in _interleave(groups, max(1, top))]
    return outcome


async def search_sources(
    query: str,
    *,
    top: int = 5,
    language: str | None = None,
    domain_type: str = "web",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> SearchOutcome:
    """TinyFish when a key exists, the free public APIs otherwise.

    A paid failure is not fatal: the free chain still answers, and the paid error stays in
    `errors` so the caller can say what happened.
    """
    paid_error: str | None = None
    try:
        from ..tinyfish.client import get_tinyfish_client

        if get_tinyfish_client().credentials.configured("tinyfish"):
            from ..tinyfish.web import SearchArgs, TinyFishSearchProvider

            result = await TinyFishSearchProvider().execute(
                SearchArgs(
                    query=query,
                    language=language,
                    domain_type=domain_type,  # type: ignore[arg-type]
                    include_domains=include or [],
                    exclude_domains=exclude or [],
                    top_results=max(1, min(top, 10)),
                ),
                None,
            )
            outcome = SearchOutcome(
                sources=[public_source(source) for source in result.sources or []],
                errors=list(result.errors or []),
                providers=["tinyfish"],
                paid=True,
            )
            if outcome.sources:
                return outcome
            paid_error = "tinyfish:empty"
    except (ToolError, ValueError) as error:
        paid_error = f"tinyfish:{getattr(error, 'code', 'invalid_arguments')}"

    outcome = await free_search(
        query,
        top=top,
        language=language,
        domain_type=domain_type,
        include=include,
        exclude=exclude,
    )
    if paid_error:
        outcome.errors.insert(0, paid_error)
    return outcome


def public_source(source: dict) -> dict:
    """Copy a provider row without leaking provider-internal fields into our source contract."""
    allowed = {
        "url",
        "final_url",
        "title",
        "excerpt",
        "publisher",
        "published_at",
        "position",
        "retrieval",
        "etag",
        "last_modified",
    }
    return {key: value for key, value in dict(source).items() if key in allowed}
