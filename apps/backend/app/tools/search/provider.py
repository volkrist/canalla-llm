"""`web_search` and `web_fetch` as tools that work without a paid key.

The registry used to bind both tools to the TinyFish provider, so an installation with no key had
two advertised tools that could only answer `provider_not_configured`. The model then had to
explain a credential problem to the user on a question the product could have answered. The
providers here keep the paid path when it exists and give the free path real work to do when it
does not.
"""

from __future__ import annotations

import asyncio
import re

from ...agent.research import plain_text
from ...agent.webclient import FetchError, fetch_page
from ..contracts import ToolError, ToolProvider, ToolResult
from ..security import validate_url
from ..tinyfish.web import FetchArgs, SearchArgs
from .chain import search_sources

TITLE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
TEXT_LIMIT = 6000


def _html_title(markup: str) -> str:
    found = TITLE.search(markup or "")
    return plain_text(found.group(1))[:300] if found else ""


def _is_html(content_type: str) -> bool:
    lowered = (content_type or "").lower()
    return "html" in lowered or "xml" in lowered


class WebSearchProvider(ToolProvider[SearchArgs]):
    async def execute(self, args: SearchArgs, context):
        outcome = await search_sources(
            args.query,
            top=args.top_results,
            language=args.language,
            domain_type=args.domain_type,
            include=args.include_domains,
            exclude=args.exclude_domains,
        )
        resolver = getattr(context, "resolver", None) if context is not None else None
        sources, errors = [], list(outcome.errors)
        for source in outcome.sources:
            try:
                await validate_url(str(source.get("url") or ""), resolver)
            except ToolError:
                errors.append("unsafe_source")
                continue
            sources.append(source)
        if not sources:
            detail = "; ".join(errors[:4]) or "no provider returned a result"
            raise ToolError("search_unavailable", detail=detail)
        return ToolResult(
            sources=sources,
            errors=errors,
            cost_estimate=None if outcome.paid else 0,
            metadata={"providers": outcome.providers, "paid": outcome.paid},
        )


class WebFetchProvider(ToolProvider[FetchArgs]):
    """The paid reader when a key exists, the product's own bounded reader otherwise."""

    def __init__(self):
        self._tinyfish = None

    async def execute(self, args: FetchArgs, context):
        from ..tinyfish.client import get_tinyfish_client

        if get_tinyfish_client().credentials.configured("tinyfish"):
            from ..tinyfish.web import TinyFishFetchProvider

            if self._tinyfish is None:
                self._tinyfish = TinyFishFetchProvider()
            return await self._tinyfish.execute(args, context)

        resolver = getattr(context, "resolver", None) if context is not None else None
        for url in args.urls:
            await validate_url(url, resolver)
        sources, errors = [], []
        for url in args.urls:
            try:
                page = await asyncio.to_thread(fetch_page, url, timeout=25)
            except FetchError as error:
                errors.append(f"fetch_failed:{error}")
                continue
            text = plain_text(page.text) if _is_html(page.content_type) else page.text
            sources.append(
                {
                    "url": url,
                    "final_url": page.url,
                    "title": _html_title(page.text) if _is_html(page.content_type) else "",
                    "excerpt": text[:TEXT_LIMIT],
                    "publisher": "",
                    "published_at": None,
                    "retrieval": "fetch",
                    "needs_browser": _is_html(page.content_type) and len(text.strip()) < 200,
                }
            )
        if not sources:
            raise ToolError("fetch_failed", detail="; ".join(errors[:3]) or "empty page")
        return ToolResult(sources=sources, errors=errors, cost_estimate=0)
