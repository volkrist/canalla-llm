import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import ToolError, ToolProvider, ToolResult
from ..security import validate_url
from .classify import markdown_needs_browser
from .client import FETCH, SEARCH, get_tinyfish_client


def bounded_excerpt(text, purpose, limit=6000):
    text = str(text)
    if len(text) <= limit:
        return text
    terms = set(re.findall(r"[^\W_]{3,}", (purpose or "").casefold()))
    if not terms:
        return text[: limit - 22] + "\n[excerpt truncated]"
    blocks = [
        part
        for paragraph in text.split("\n\n")
        for start in range(0, len(paragraph), 1800)
        if (part := paragraph[start : start + 1800])
    ]
    scored = sorted(
        enumerate(blocks),
        key=lambda pair: (-len(terms & set(re.findall(r"[^\W_]{3,}", pair[1].casefold()))), pair[0]),
    )
    selected, size = [], 22
    for index, block in scored:
        if size + len(block) + 2 <= limit:
            selected.append((index, block))
            size += len(block) + 2
    return "\n\n".join(block for _, block in sorted(selected)) + "\n[excerpt truncated]"


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=500)
    purpose: str | None = Field(default=None, max_length=2000)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    location: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    recency_minutes: int | None = Field(default=None, ge=1, le=5256000)
    after_date: date | None = None
    before_date: date | None = None
    domain_type: Literal["web", "news", "research_paper"] = "web"
    include_domains: list[str] = Field(default_factory=list, max_length=10)
    exclude_domains: list[str] = Field(default_factory=list, max_length=10)
    page: int = Field(default=0, ge=0, le=10)
    top_results: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def dates(self):
        if self.recency_minutes and (self.after_date or self.before_date):
            raise ValueError("Choose recency or date range")
        if self.after_date and self.before_date and self.after_date > self.before_date:
            raise ValueError("Invalid date range")
        return self


class FetchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    urls: list[str] = Field(min_length=1, max_length=3)
    purpose: str | None = Field(default=None, max_length=2000)
    fresh: bool = False


class TinyFishSearchProvider(ToolProvider):
    def __init__(self, client=None):
        self.client = client or get_tinyfish_client()

    async def execute(self, args: SearchArgs, context):
        params = args.model_dump(
            mode="json", exclude_none=True, exclude={"top_results", "language", "location"}
        )
        for key in ("include_domains", "exclude_domains"):
            for domain in params[key]:
                if "/" in domain or ":" in domain:
                    raise ToolError("unsafe_url")
                await validate_url("https://" + domain, context.resolver)
            params[key] = ",".join(params[key])
            if not params[key]:
                del params[key]
        if args.language:
            params["language"] = args.language
        if args.location:
            params["location"] = args.location
        value = await self.client.request("GET", SEARCH, params=params)
        rows = value.get("results", [])
        if not isinstance(rows, list):
            raise ToolError("malformed_provider_result")
        sources, errors = [], []
        for row in rows[: args.top_results]:
            if not isinstance(row, dict):
                errors.append("malformed_search_result")
                continue
            try:
                await validate_url(row.get("url", ""), context.resolver)
            except ToolError:
                errors.append("unsafe_source")
                continue
            sources.append(
                {
                    "url": row["url"],
                    "final_url": row["url"],
                    "title": row.get("title", ""),
                    "excerpt": str(row.get("snippet", ""))[:1500],
                    "publisher": row.get("publisher") or row.get("site_name"),
                    "published_at": row.get("date"),
                    "position": row.get("position"),
                    "retrieval": "search",
                }
            )
        return ToolResult(
            sources=sources,
            errors=errors,
            cost_estimate=0 if self.client.settings.tinyfish_search_fetch_free else None,
        )


class TinyFishFetchProvider(ToolProvider):
    def __init__(self, client=None):
        self.client = client or get_tinyfish_client()

    async def execute(self, args: FetchArgs, context):
        for url in args.urls:
            await validate_url(url, context.resolver)
        body = {
            "urls": args.urls,
            "format": "markdown",
            "ttl": 0 if args.fresh else 3600,
            "links": False,
            "image_links": False,
            "per_url_timeout_ms": 25000,
            "include_etag_and_last_modified": True,
        }
        if args.purpose:
            body["purpose"] = args.purpose
        value = await self.client.request("POST", FETCH, body=body, timeout=35)
        rows = value.get("results", [])
        if not isinstance(rows, list):
            raise ToolError("malformed_provider_result")
        sources, errors = [], ["fetch_failed"] * min(10, len(value.get("errors") or []))
        for row in rows[:10]:
            if not isinstance(row, dict) or row.get("url") not in args.urls:
                errors.append("malformed_fetch_result")
                continue
            try:
                await validate_url(row.get("final_url") or row["url"], context.resolver)
            except ToolError:
                errors.append("unsafe_redirect")
                continue
            excerpt = bounded_excerpt(row.get("text", ""), args.purpose)
            sources.append(
                {
                    "url": row["url"],
                    "final_url": row.get("final_url") or row["url"],
                    "title": row.get("title", ""),
                    "excerpt": excerpt,
                    "publisher": row.get("author"),
                    "published_at": row.get("published_date"),
                    "etag": row.get("etag"),
                    "last_modified": row.get("last_modified"),
                    "retrieval": "fetch",
                    "needs_browser": markdown_needs_browser(excerpt),
                }
            )
        return ToolResult(
            sources=sources,
            errors=errors,
            cost_estimate=0 if self.client.settings.tinyfish_search_fetch_free else None,
        )
