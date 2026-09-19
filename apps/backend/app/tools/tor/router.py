"""Deterministic Tor intent and research helpers. Not a second agent stack."""

import html
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from ..web_router import CHITCHAT, REWRITE, canonical_url
from .urls import is_onion

NO_TOR = re.compile(
    r"(?i)("
    r"не используй(\s+\w+){0,3}\s+tor|без tor|don't use tor|do not use tor|"
    r"без луков|не (ищи|искать) (в|через) tor"
    r")"
)
EXPLICIT_TOR = re.compile(
    r"(?i)("
    r"\.onion|\btor\b|\bтор(а|ом|у|е)?\b|"
    r"луков|ahmia|hidden service|dark web|"
    r"onion[- ]?(сайт|адрес|ресурс|service|site|link|url)|"
    r"через tor|в tor|tor browser|поиск в tor|найди onion|"
    r"search(ing)? (via |through |on )?tor|search tor|"
    r"follow onion|продолжи.{0,40}onion|проверь через tor"
    r")"
)
CONTINUE = re.compile(
    r"(?i)("
    r"продолжи.{0,48}(onion|tor|поиск|ссылк)|"
    r"ещё (два|2|несколько) источник|"
    r"follow( the)? (found |those )?onion|continue.{0,24}(onion|tor|search)"
    r")"
)
RESEARCH = re.compile(
    r"(?i)("
    r"найд[иьё]|поищи|search|find|verify|research|проверь|источник|"
    r"look up|документац"
    r")"
)
MATH = re.compile(r"(?i)^(сколько будет|what('?s| is) \d|[\d\s+\-*/().=?,!]+)$")
ONION_IN_PROMPT = re.compile(
    r"https?://(?:[a-z2-7]{56}|[a-z2-7]{16})\.onion(?:/[^\s<>\"')\]]*)?",
    re.I,
)
LOCAL_ONLY = re.compile(r"(?i)(pytest|локальн\w* проект|тестов\w* папк|workspace|calculator\.py)")
BLOCKED_SCHEMES = {"mailto", "javascript", "data", "file", "magnet", "blob", "about", "chrome"}
DOWNLOAD_SUFFIXES = (
    ".exe",
    ".msi",
    ".dmg",
    ".pkg",
    ".zip",
    ".rar",
    ".7z",
    ".iso",
    ".img",
    ".bat",
    ".cmd",
    ".ps1",
    ".scr",
    ".apk",
    ".deb",
    ".rpm",
)


def effective_tor_mode(context) -> str:
    mode = getattr(context, "tor_mode", None)
    if mode in {"auto", "on"}:
        return mode
    if getattr(context, "tor_enabled", False):
        return "auto"
    return "off"


def looks_like_tor(prompt: str) -> bool:
    return bool(EXPLICIT_TOR.search(prompt or ""))


@dataclass(frozen=True)
class TorIntent:
    allowed: bool
    required: bool
    continue_research: bool
    query: str
    mode: str


def classify_tor(prompt: str, tor_mode: str) -> TorIntent:
    text = (prompt or "").strip()
    query = text[:500] or "onion search"
    mode = tor_mode if tor_mode in {"off", "auto", "on"} else "off"
    if mode == "off" or NO_TOR.search(text):
        return TorIntent(False, False, False, query, "off" if mode == "off" else mode)
    explicit = bool(EXPLICIT_TOR.search(text))
    continue_research = bool(CONTINUE.search(text) and (explicit or mode != "off"))
    if mode == "auto":
        needed = explicit or continue_research
        return TorIntent(needed, needed, continue_research, query, mode)
    if CHITCHAT.match(text) or MATH.match(text) or (REWRITE.search(text) and not explicit):
        return TorIntent(False, False, False, query, mode)
    if LOCAL_ONLY.search(text) and not explicit:
        return TorIntent(False, False, False, query, mode)
    research = bool(RESEARCH.search(text) or explicit or continue_research)
    return TorIntent(research, explicit or continue_research, continue_research, query, mode)


def blocked_link(url: str) -> bool:
    parsed = urlsplit((url or "").strip())
    scheme = (parsed.scheme or "").lower()
    path = (parsed.path or "").lower()
    if scheme in BLOCKED_SCHEMES:
        return True
    return any(path.endswith(suffix) for suffix in DOWNLOAD_SUFFIXES)


def normalize_http_url(value: str) -> str:
    return canonical_url(value or "")


def onion_urls_from_prompt(prompt: str) -> list[str]:
    found, seen = [], set()
    for match in ONION_IN_PROMPT.finditer(prompt or ""):
        url = match.group(0).rstrip(").,];")
        host = (urlsplit(url).hostname or "").rstrip(".").lower()
        if blocked_link(url) or not is_onion(host):
            continue
        key = normalize_http_url(url) or url.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(url)
    return found


def extract_page_links(base_url: str, html_text: str, limit=50):
    found, seen = [], set()
    base_host = (urlsplit(base_url).hostname or "").rstrip(".").lower()
    for href, label in re.findall(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_text or ""):
        raw = html.unescape((href or "").strip())
        if raw.startswith("#") or raw.lower().startswith("javascript:"):
            continue
        abs_url = urljoin(base_url, raw)
        parsed = urlsplit(abs_url)
        if parsed.scheme not in {"http", "https"} or blocked_link(abs_url):
            continue
        host = (parsed.hostname or "").rstrip(".").lower()
        if not host:
            continue
        key = normalize_http_url(abs_url)
        if not key or key in seen:
            continue
        seen.add(key)
        found.append(
            {
                "url": key,
                "text": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", label)).strip()[:200],
                "source_page": base_url,
                "is_onion": is_onion(host),
                "is_clearnet": not is_onion(host),
                "same_host": host == base_host,
            }
        )
        if len(found) >= limit:
            break
    return found


def pick_tor_fetch_urls(sources, limit=3, visited=None):
    ranked = []
    blocked = {normalize_http_url(item) for item in (visited or ()) if item}
    for source in sources:
        url = source.get("final_url") or source.get("url") or ""
        host = (urlsplit(url).hostname or "").rstrip(".").lower()
        if not url or blocked_link(url) or not is_onion(host):
            continue
        key = normalize_http_url(url)
        if key in blocked:
            continue
        authority = source.get("authority") or ""
        score = 0
        if authority == "OFFICIAL_AND_REACHABLE":
            score += 4
        if authority == "OFFICIAL_UNREACHABLE":
            score += 2
        if source.get("kind") != "fetch":
            score += 1
        ranked.append((score, url))
    seen, urls = set(), []
    for _score, url in sorted(ranked, key=lambda item: -item[0]):
        key = normalize_http_url(url)
        if not key or key in seen:
            continue
        seen.add(key)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def pick_follow_urls(candidates, visited, *, max_depth, limit=1):
    seen, urls = set(visited), []
    ordered = sorted(
        candidates,
        key=lambda item: (
            0 if item.get("authority") == "OFFICIAL_AND_REACHABLE" else 1,
            0 if item.get("same_host") else 1,
            0 if item.get("is_onion") else 1,
            item.get("depth", 0),
        ),
    )
    for item in ordered:
        url = item.get("url") or ""
        depth = int(item.get("depth") or 0)
        host = (urlsplit(url).hostname or "").rstrip(".").lower()
        key = normalize_http_url(url)
        if (
            not key
            or key in seen
            or not item.get("is_onion")
            or not is_onion(host)
            or blocked_link(url)
            or depth > max_depth
        ):
            continue
        seen.add(key)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls
