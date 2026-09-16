import re
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from ...config import get_settings
from ..contracts import ToolError, ToolProvider, ToolResult
from ..tinyfish.web import bounded_excerpt
from .classify import classify_authority
from .socks import TorTransportProvider
from .urls import validate_tor_url


class TorSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=500)
    purpose: str | None = Field(default=None, max_length=2000)


class TorFetchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    urls: list[str] = Field(min_length=1, max_length=3)
    purpose: str | None = Field(default=None, max_length=2000)
    fresh: bool = True


def _title(text: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
    return re.sub(r"\s+", " ", match.group(1)).strip()[:400] if match else ""


def _links(text: str, limit=8):
    found = []
    for href, label in re.findall(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text):
        if href.startswith("http"):
            found.append((href, re.sub(r"<[^>]+>", "", label).strip()[:200]))
        if len(found) >= limit:
            break
    return found


class TorSearchProvider(ToolProvider):
    def __init__(self, transport=None):
        self.transport = transport

    def _transport(self, settings):
        return self.transport or TorTransportProvider(
            host=settings.tor_socks_host, port=settings.tor_socks_port
        )

    async def execute(self, args: TorSearchArgs, context):
        settings = get_settings()
        providers = list(settings.tor_search_providers or [])
        if not providers:
            raise ToolError("tor_search_not_configured")
        transport = self._transport(settings)
        sources, errors = [], []
        for item in providers[:3]:
            if not isinstance(item, dict) or not item.get("url_template"):
                continue
            url = str(item["url_template"]).replace("{query}", quote(args.query))
            await validate_tor_url(url)
            try:
                page = await transport.fetch(url)
            except ToolError as error:
                errors.append(error.code)
                continue
            if page.get("status") and page["status"] >= 400:
                errors.append("tor_search_failed")
                continue
            for href, label in _links(page.get("text", "")):
                try:
                    await validate_tor_url(href)
                except ToolError:
                    continue
                sources.append(
                    {
                        "url": href,
                        "final_url": href,
                        "title": label or _title(page.get("text", "")),
                        "excerpt": f"Tor search hit via {item.get('name') or 'provider'}: {label}"[:1500],
                        "authority": classify_authority(href, True, settings),
                    }
                )
                if len(sources) >= 5:
                    break
        if not sources:
            raise ToolError(errors[0] if errors else "tor_search_not_configured")
        return ToolResult(sources=sources, errors=errors, metadata={"transport": "tor-socks5h"})


class TorFetchProvider(ToolProvider):
    def __init__(self, transport=None):
        self.transport = transport

    def _transport(self, settings):
        return self.transport or TorTransportProvider(
            host=settings.tor_socks_host, port=settings.tor_socks_port
        )

    async def execute(self, args: TorFetchArgs, context):
        settings = get_settings()
        transport = self._transport(settings)
        sources, errors = [], []
        for url in args.urls[:3]:
            await validate_tor_url(url)
            try:
                page = await transport.fetch(url)
            except ToolError as error:
                errors.append(error.code)
                sources.append(
                    {
                        "url": url,
                        "final_url": url,
                        "title": urlsplit(url).hostname or url,
                        "excerpt": "Source was not reachable through Tor.",
                        "authority": classify_authority(url, False, settings),
                    }
                )
                continue
            reachable = bool(page.get("text"))
            sources.append(
                {
                    "url": url,
                    "final_url": url,
                    "title": _title(page.get("text", "")) or urlsplit(url).hostname or url,
                    "excerpt": bounded_excerpt(
                        re.sub(r"(?is)<script.*?</script>", " ", page.get("text", "")), args.purpose
                    ),
                    "authority": classify_authority(url, reachable, settings),
                }
            )
        return ToolResult(sources=sources, errors=errors, metadata={"transport": "tor-socks5h"})


def register_tor_tools(registry):
    from ..contracts import RiskLevel, ToolDefinition

    registry.register(
        ToolDefinition(
            "tor_search",
            "Search configured Tor search providers through SOCKS5h. Not TinyFish.",
            TorSearchArgs,
            "tor_search",
            RiskLevel.READ_ONLY,
            "free",
            90,
            "tor",
        ),
        TorSearchProvider(),
    )
    registry.register(
        ToolDefinition(
            "tor_fetch",
            "Fetch http(s) URLs including .onion through local Tor SOCKS5h.",
            TorFetchArgs,
            "tor_fetch",
            RiskLevel.READ_ONLY,
            "free",
            90,
            "tor",
        ),
        TorFetchProvider(),
    )
