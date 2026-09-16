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
