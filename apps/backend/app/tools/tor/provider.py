import html
import logging
import re
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

from ...config import get_settings
from ..contracts import ToolError, ToolProvider, ToolResult
from ..tinyfish.web import bounded_excerpt
from .classify import classify_authority
from .router import blocked_link, extract_page_links
from .service import active_endpoint, active_service
from .snapshot import fetch_needs_browser
from .socks import TorTransportProvider
from .urls import validate_tor_url

logger = logging.getLogger(__name__)

# The codes the SOCKS5h transport raises when the route itself is the problem: these, and only
# these, are what a fresh ``ensure()`` can honestly help with.
TOR_TRANSPORT_ERRORS = frozenset({"tor_not_configured", "tor_unavailable"})
# One bounded recovery per action for a Tor-required call: long enough to re-probe the endpoint and
# restart the process Canalla owns, short enough that a down Tor fails the action instead of hanging
# it. The supervisor keeps working in the background either way.
TOR_RECOVER_SECONDS = 20.0


def route_proof() -> dict:
    """The circuit proof the action travelled on: read from the service, never assumed.

    The tool run — not just the status chip — has to carry what proved the route, so a result that
    came back over Tor is auditable on its own.
    """
    read = getattr(active_service(), "snapshot", None)
    if read is None:
        return {}
    try:
        snapshot = read()
    except Exception as error:  # pragma: no cover - a proof must never break a fetched page
        logger.warning("tor_route_proof_unavailable type=%s", type(error).__name__)
        return {}
    return {
        "route": "TOR_ONLY",
        "verified_chain": bool(snapshot.get("verified_chain")),
        "verified_at": snapshot.get("verified_at"),
        "endpoint": snapshot.get("endpoint"),
        "managed": bool(snapshot.get("managed")),
        "proof_method": snapshot.get("method"),
    }


async def reestablish_route() -> bool:
    """One bounded recovery attempt through the running service. False means: fail closed."""
    attempt = getattr(active_service(), "reestablish", None)
    if attempt is None:
        return False
    try:
        return bool(await attempt(deadline_seconds=TOR_RECOVER_SECONDS))
    except Exception as error:
        logger.warning("tor_reestablish_failed type=%s", type(error).__name__)
        return False


def typed_route_failure(code: str) -> str:
    """``tor_unavailable`` for anything the route itself failed on, the code itself otherwise.

    A Tor-required action that the route could not serve is one honest answer for the caller, no
    matter which level noticed (no SOCKS proxy at all, or a proxy that refused the circuit). The
    transport's own code stays in the log; the caller never gets a clearnet retry.
    """
    if code in TOR_TRANSPORT_ERRORS:
        logger.warning("tor_route_failure code=%s", code)
        return "tor_unavailable"
    return code


class TorRoute:
    """The transport of one Tor-required action: proven endpoint, at most one recovery.

    A transport failure is the one case where asking the service again is honest — it re-probes,
    restarts the process Canalla owns and re-proves the route — and it happens **once** per action
    inside a fixed budget, so a down Tor produces a typed failure rather than a retry loop, and
    never a direct request.
    """

    def __init__(self, build):
        self._build = build
        self.transport = build()
        self.recovery_used = False

    async def fetch(self, url: str, *, timeout: int = 45) -> dict:
        try:
            return await self.transport.fetch(url, timeout=timeout)
        except ToolError as error:
            if error.code not in TOR_TRANSPORT_ERRORS or self.recovery_used:
                raise
            self.recovery_used = True
            if not await reestablish_route():
                raise
            # The recovery may have moved the endpoint, so the retry uses a transport built from
            # the endpoint that was just proven, not the object that failed.
            self.transport = self._build()
            return await self.transport.fetch(url, timeout=timeout)


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


HIDDEN_INPUT = re.compile(r"""<input\b[^>]*\btype=["']hidden["'][^>]*>""", re.I)
INPUT_ATTR = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*["']([^"']*)["']""")


def extract_hidden_fields(html: str) -> dict[str, str]:
    fields = {}
    for tag in HIDDEN_INPUT.findall(html or ""):
        attrs = {key.lower(): value for key, value in INPUT_ATTR.findall(tag)}
        name = (attrs.get("name") or "").strip()
        if name:
            fields[name] = attrs.get("value") or ""
    return fields


def apply_search_form_fields(search_url: str, html: str) -> str:
    fields = extract_hidden_fields(html)
    if not fields:
        return search_url
    parsed = urlsplit(search_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(fields)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def extract_search_hits(base_url: str, text: str, limit=8):
    found, seen = [], set()
    base_host = (urlsplit(base_url).hostname or "").rstrip(".").lower()

    def add(url: str, label: str):
        value = (url or "").strip()
        host = (urlsplit(value).hostname or "").rstrip(".").lower()
        if not value or value in seen or host == base_host:
            return False
        seen.add(value)
        found.append((value, re.sub(r"\s+", " ", label).strip()[:200]))
        return len(found) >= limit

    for href, label in re.findall(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text):
        abs_url = urljoin(base_url, html.unescape(href.strip()))
        parsed = urlsplit(abs_url)
        query = parse_qs(parsed.query)
        for key in ("redirect_url", "redirect", "url"):
            if query.get(key):
                abs_url = html.unescape(query[key][0])
                break
        if not abs_url.startswith("http"):
            host = (urlsplit(abs_url).hostname or abs_url.split("/")[0]).lower()
            if host.endswith(".onion"):
                abs_url = "http://" + abs_url.lstrip("/")
        host = (urlsplit(abs_url).hostname or "").rstrip(".").lower()
        if not host.endswith(".onion"):
            continue
        label_text = re.sub(r"<[^>]+>", "", label)
        if add(abs_url, label_text):
            return found
    for cite in re.findall(r"(?is)<cite[^>]*>(.*?)</cite>", text):
        host = re.sub(r"<[^>]+>", "", cite).strip().lower()
        if host.endswith(".onion") and add(f"http://{host}/", host):
            return found
    for host in re.findall(r"\b([a-z2-7]{56}\.onion|[a-z2-7]{16}\.onion)\b", text.lower()):
        if add(f"http://{host}/", host):
            break
    return found


def _links(text: str, limit=8, base_url=""):
    return extract_search_hits(base_url, text, limit)


class TorSearchProvider(ToolProvider[TorSearchArgs]):
    def __init__(self, transport=None):
        self.transport = transport

    def _transport(self, settings):
        host, port = active_endpoint(settings.tor_socks_host, settings.tor_socks_port)
        return self.transport or TorTransportProvider(host=host, port=port)

    async def execute(self, args: TorSearchArgs, context):
        settings = get_settings()
        providers = list(settings.tor_search_providers or [])
        if not providers:
            raise ToolError("tor_search_not_configured")
        route = TorRoute(lambda: self._transport(settings))
        sources, errors, handshake = [], [], {}
        for item in providers[:3]:
            if not isinstance(item, dict) or not item.get("url_template"):
                continue
            url = (
                str(item["url_template"])
                .replace("{query}", quote(args.query))
                .replace("__QUERY__", quote(args.query))
            )
            await validate_tor_url(url)
            form_url = str(item.get("form_url") or "").strip()
            if form_url:
                await validate_tor_url(form_url)
                try:
                    form_page = await route.fetch(form_url, timeout=45)
                except ToolError as error:
                    errors.append(error.code)
                    continue
                handshake = form_page.get("socks") or handshake
                url = apply_search_form_fields(url, form_page.get("text") or "")
            try:
                page = await route.fetch(url, timeout=45)
            except ToolError as error:
                errors.append(error.code)
                continue
            if page.get("status") and page["status"] >= 400:
                errors.append("tor_search_failed")
                continue
            provider_host = (urlsplit(page.get("url") or url).hostname or "").rstrip(".").lower()
            for href, label in extract_search_hits(page.get("url") or url, page.get("text", "")):
                try:
                    await validate_tor_url(href)
                except ToolError:
                    continue
                host = (urlsplit(href).hostname or "").rstrip(".").lower()
                if not host.endswith(".onion") or host == provider_host:
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
            handshake = page.get("socks") or handshake
            if len(sources) >= 5:
                break
        if not sources:
            raise ToolError(typed_route_failure(errors[0]) if errors else "tor_search_failed")
        return ToolResult(
            sources=sources,
            errors=errors,
            metadata={"transport": "tor-socks5h", "socks": handshake or {}, **route_proof()},
        )


class TorFetchProvider(ToolProvider[TorFetchArgs]):
    def __init__(self, transport=None):
        self.transport = transport

    def _transport(self, settings):
        host, port = active_endpoint(settings.tor_socks_host, settings.tor_socks_port)
        return self.transport or TorTransportProvider(host=host, port=port)

    async def execute(self, args: TorFetchArgs, context):
        settings = get_settings()
        route = TorRoute(lambda: self._transport(settings))
        sources, errors, handshake = [], [], {}
        attempted = reached = 0
        transport_errors: list[str] = []
        for url in args.urls[:3]:
            if blocked_link(url):
                errors.append("unsafe_url")
                continue
            await validate_tor_url(url)
            attempted += 1
            try:
                page = await route.fetch(url)
            except ToolError as error:
                errors.append(error.code)
                if error.code in TOR_TRANSPORT_ERRORS:
                    transport_errors.append(error.code)
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
            reached += 1 if reachable else 0
            raw_html = page.get("text") or ""
            links = extract_page_links(page.get("url") or url, raw_html)
            sources.append(
                {
                    "url": url,
                    "final_url": page.get("url") or url,
                    "title": _title(raw_html) or urlsplit(url).hostname or url,
                    "excerpt": bounded_excerpt(
                        re.sub(r"(?is)<script.*?</script>", " ", raw_html), args.purpose
                    ),
                    "authority": classify_authority(url, reachable, settings),
                    "links": links,
                    "reachable": reachable,
                    "transport": "tor",
                    "needs_browser": fetch_needs_browser(raw_html),
                    "retrieval": "http",
                    "rendered": False,
                }
            )
            handshake = page.get("socks") or handshake
        if transport_errors and len(transport_errors) == attempted and not reached:
            # Every URL died on the route and no page answered: this is one failed Tor action, not a
            # set of "unreachable sources". A Tor-required call fails closed with the route's typed
            # code instead of handing the model a fabricated source.
            logger.warning("tor_fetch_route_failed code=%s urls=%d", transport_errors[0], attempted)
            raise ToolError("tor_unavailable")
        return ToolResult(
            sources=sources,
            errors=errors,
            metadata={"transport": "tor-socks5h", "socks": handshake or {}, **route_proof()},
        )


def register_tor_tools(registry):
    from ..contracts import RiskLevel, ToolDefinition

    registry.register(
        ToolDefinition(
            "tor_search",
            "Search configured Tor search providers through SOCKS5h. Not TinyFish.",
            TorSearchArgs,
            "tor_search",
            RiskLevel.READ,
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
            RiskLevel.READ,
            "free",
            90,
            "tor",
        ),
        TorFetchProvider(),
    )
    from .browser import TorBrowserArgs, TorBrowserProvider

    browser = TorBrowserProvider()
    registry.register(
        ToolDefinition(
            "tor_browser",
            "Read-only Tor Browser fallback: open/render/click L-ids through isolated Tor Browser. "
            "Use only when HTTP fetch is a JS shell or the user asks for Tor Browser. Never submit forms.",
            TorBrowserArgs,
            "tor_browser",
            RiskLevel.READ,
            "free",
            90,
            "tor",
        ),
        browser,
    )
