import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

CHITCHAT = re.compile(r"(?i)^(привет|здравствуйте|здравствуй|hello|hi|hey|thanks|спасибо|ок|ok|пока)[\s!.]*$")
NO_WEB = re.compile(
    r"(?i)без интернета|не (ищи|искать|смотри) в интернет|don't search|do not search|offline only"
)
REWRITE = re.compile(
    r"(?i)^(перепиши|переведи|исправь|отформатируй|rewrite|translate|format|summarize this)\b"
)
EXPLICIT_WEB = re.compile(
    r"(?i)("
    r"найд[иьё].{0,48}интернет|посмотри.{0,48}интернет|посмотр[ие].{0,48}сети|"
    r"проверь.{0,48}сети|актуальн\w*\s+информац|search the web|look(?:\s+it)?\s+up\s+online|"
    r"найди в сети|в интернете|google\b|browse the web|"
    r"посмотри в интернете|найди в интернете|проверь актуальн|проверь прямо сейчас|"
    r"актуальн\w+\s+документац|актуальн\w+\s+верси"
    r")"
)
FRESH = re.compile(
    r"(?i)сегодня|сейчас|последн|latest|today|current|price|availability|news|"
    r"version|weather|актуальн|свеж|\blive\b|verify|провер|прямо сейчас|документац"
)
URL = re.compile(r"https?://[^\s<>]+")


def looks_like_tor(prompt: str) -> bool:
    from .tor.router import looks_like_tor as tor_intent

    return tor_intent(prompt)


@dataclass(frozen=True)
class WebIntent:
    required: bool
    fresh: bool
    query: str


def classify_web(prompt: str, web_mode: str) -> WebIntent:
    text = (prompt or "").strip()
    query = text[:500] or "current information"
    if web_mode == "off":
        return WebIntent(False, False, query)
    if NO_WEB.search(text):
        return WebIntent(False, False, query)
    fresh = bool(FRESH.search(text) or URL.search(text))
    explicit = bool(EXPLICIT_WEB.search(text) or fresh)
    if web_mode == "auto":
        return WebIntent(explicit, fresh or bool(EXPLICIT_WEB.search(text)), query)
    if CHITCHAT.match(text) or (REWRITE.search(text) and not explicit):
        return WebIntent(False, False, query)
    return WebIntent(True, fresh, query)


def canonical_url(value: str) -> str:
    parsed = urlsplit((value or "").strip())
    host = (parsed.hostname or "").rstrip(".").lower()
    if not parsed.scheme or not host:
        return (value or "").strip()
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    drop = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in drop and not key.lower().startswith("utm_")
        ],
        doseq=True,
    )
    port = parsed.port
    netloc = host
    if port and port not in {80, 443}:
        netloc = f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))


def pick_fetch_urls(sources, limit=3):
    seen, urls = set(), []
    for source in sources:
        url = source.get("final_url") or source.get("url") or ""
        key = canonical_url(url)
        if not key or source.get("kind") == "fetch" or key in seen:
            continue
        seen.add(key)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


@dataclass(frozen=True)
class TinyFishDecision:
    paid: str | None
    reason: str
    classification: str | None = None
    url: str = ""


def sources_need_browser(sources) -> bool:
    return any(
        (item.get("details") or {}).get("needs_browser") or item.get("needs_browser")
        for item in sources or []
    )


def first_source_url(sources) -> str:
    for item in sources or []:
        url = item.get("final_url") or item.get("url") or ""
        if url:
            return url
    return ""


def select_tinyfish_route(prompt: str, context) -> TinyFishDecision:
    from .local.plan import looks_like_computer
    from .local.workspace import looks_like_coding
    from .tinyfish.classify import (
        classify_agent_goal,
        implied_start_url,
        looks_like_agent_task,
        looks_like_browser_task,
        looks_like_simple_math,
    )
    from .tor.router import classify_tor, effective_tor_mode

    text = prompt or getattr(context, "user_prompt", "") or ""
    settings = getattr(context, "settings", None)
    agent_mode = getattr(settings, "agent_mode", "auto") if settings else "auto"
    browser_mode = getattr(settings, "browser_mode", "auto") if settings else "auto"
    classified = classify_agent_goal(text, text)
    url = first_source_url(getattr(context, "sources", None)) or implied_start_url(text)
    if looks_like_tor(text) or classify_tor(text, effective_tor_mode(context)).allowed:
        return TinyFishDecision(None, "tor_stack_only", classified.kind, url)
    if looks_like_simple_math(text) or CHITCHAT.match((text or "").strip()):
        return TinyFishDecision(None, "no_web_needed", classified.kind, url)
    if (looks_like_coding(text) or looks_like_computer(text)) and not classify_web(text, "auto").required:
        return TinyFishDecision(None, "local_computer_only", classified.kind, url)
    if classified.kind != "READ_ONLY":
        if (
            looks_like_browser_task(text)
            and browser_mode != "off"
            and getattr(context, "mode", "off") != "off"
        ):
            return TinyFishDecision("browser", "side_effect_controlled_browser", classified.kind, url)
        return TinyFishDecision(None, "agent_side_effect_blocked", classified.kind, url)
    if browser_mode != "off" and (
        looks_like_browser_task(text) or sources_need_browser(getattr(context, "sources", None))
    ):
        return TinyFishDecision("browser", "js_or_explicit_browser", classified.kind, url)
    if agent_mode != "off" and looks_like_agent_task(text):
        return TinyFishDecision("agent", "complex_readonly_web", classified.kind, url)
    return TinyFishDecision(None, "search_fetch", classified.kind, url)
