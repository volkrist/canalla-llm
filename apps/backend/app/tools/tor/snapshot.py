"""Bounded rendered-page snapshot. Model sees L-ids, never a raw DOM dump."""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .router import blocked_link, extract_page_links, normalize_http_url

APP_SHELL = re.compile(
    r'(?is)id=["\'](root|app|__next|__nuxt)["\']|data-reactroot|ng-app|id=["\']application["\']'
)
SCRIPT_TAG = re.compile(r"(?is)<script\b")
VISIBLE = re.compile(r"(?is)<(script|style|noscript|template)\b.*?</\1>|<[^>]+>")
HEADING = re.compile(r"(?is)<(h[1-6])\b[^>]*>(.*?)</\1>")
BUTTON = re.compile(r"(?is)<(button|input)\b([^>]*)>(.*?)</button>|<input\b([^>]*)>")
FORM = re.compile(r"(?is)<form\b([^>]*)>(.*?)</form>")
LOADING = re.compile(r"(?i)\b(loading|загрузка|please wait|enable javascript)\b")
MAX_TEXT = 8000
MAX_HTML = 200_000


def visible_text(html_text: str) -> str:
    text = VISIBLE.sub(" ", html_text or "")
    text = html_lib.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:MAX_TEXT]


def headings(html_text: str):
    found = []
    for tag, inner in HEADING.findall(html_text or ""):
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", inner)).strip()
        if label:
            found.append({"level": tag.lower(), "text": label[:200]})
        if len(found) >= 12:
            break
    return found


def buttons(html_text: str):
    found, seen = [], set()
    for match in BUTTON.findall(html_text or ""):
        attrs = " ".join(part for part in match if part)
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match[2] if len(match) > 2 else "")).strip()
        if not label:
            name = re.search(r'(?i)\b(?:aria-label|value|name)=["\']([^"\']+)["\']', attrs)
            label = name.group(1).strip() if name else ""
        kind = "submit" if re.search(r'(?i)type=["\']submit["\']', attrs) else "button"
        key = (kind, label.lower())
        if not label or key in seen:
            continue
        seen.add(key)
        found.append({"kind": kind, "text": label[:120]})
        if len(found) >= 12:
            break
    return found


def forms_summary(html_text: str):
    rows = []
    for attrs, body in FORM.findall(html_text or ""):
        fields = re.findall(r'(?i)<input\b[^>]*\bname=["\']([^"\']+)["\']', body)
        action = re.search(r'(?i)\baction=["\']([^"\']*)["\']', attrs)
        rows.append(
            {
                "action": (action.group(1) if action else "")[:200],
                "fields": [name[:80] for name in fields[:8]],
                "submit": bool(re.search(r'(?i)type=["\']submit["\']', body)),
            }
        )
        if len(rows) >= 4:
            break
    return rows


def fetch_needs_browser(html_text: str, *, explicit_browser=False) -> bool:
    if explicit_browser:
        return True
    html_text = html_text or ""
    text = visible_text(html_text)
    if APP_SHELL.search(html_text) and len(text) < 160:
        return True
    if SCRIPT_TAG.search(html_text) and len(text) < 80:
        return True
    if LOADING.search(text) and len(text) < 200 and SCRIPT_TAG.search(html_text):
        return True
    return False


def looks_like_js_marker(text: str, marker: str) -> bool:
    return marker in (text or "")


@dataclass
class BrowserPageSnapshot:
    url: str
    title: str
    visible_text: str
    links: list[dict] = field(default_factory=list)
    buttons: list[dict] = field(default_factory=list)
    forms_present: bool = False
    forms: list[dict] = field(default_factory=list)
    rendered_at: str = ""
    transport: str = "tor"
    retrieval: str = "browser"
    rendered: bool = True
    depth: int = 1
    parent_source: str | None = None
    html: str = ""

    def public(self, *, include_html=False):
        payload = {
            "url": self.url,
            "title": self.title[:400],
            "visible_text": self.visible_text[:MAX_TEXT],
            "links": self.links[:40],
            "buttons": self.buttons[:12],
            "forms_present": self.forms_present,
            "forms": self.forms[:4],
            "rendered_at": self.rendered_at,
            "transport": self.transport,
            "retrieval": self.retrieval,
            "rendered": self.rendered,
            "depth": self.depth,
            "parent_source": self.parent_source,
        }
        if include_html:
            payload["html"] = self.html[:MAX_HTML]
        return payload


def assign_link_ids(links):
    labeled = []
    for index, item in enumerate(links, start=1):
        row = dict(item)
        row["id"] = f"L{index}"
        labeled.append(row)
    return labeled


def snapshot_from_html(
    url: str,
    html_text: str,
    *,
    title="",
    text="",
    depth=1,
    parent_source=None,
    transport="tor",
):
    html_text = (html_text or "")[:MAX_HTML]
    if not title:
        match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html_text)
        title = re.sub(r"\s+", " ", match.group(1)).strip() if match else urlsplit(url).hostname or url
    body = text.strip() if text else visible_text(html_text)
    links = assign_link_ids(extract_page_links(url, html_text, limit=40))
    form_rows = forms_summary(html_text)
    return BrowserPageSnapshot(
        url=normalize_http_url(url) or url,
        title=title[:400],
        visible_text=body[:MAX_TEXT],
        links=links,
        buttons=buttons(html_text),
        forms_present=bool(form_rows),
        forms=form_rows,
        rendered_at=datetime.now(timezone.utc).isoformat(),
        transport=transport,
        depth=depth,
        parent_source=parent_source,
        html=html_text,
    )


def resolve_link_id(snapshot: BrowserPageSnapshot | None, link_id: str) -> str:
    token = (link_id or "").strip().upper()
    if not snapshot or not re.fullmatch(r"L\d{1,3}", token):
        return ""
    for item in snapshot.links:
        if str(item.get("id") or "").upper() == token:
            url = item.get("url") or ""
            if url and not blocked_link(url):
                return url
    return ""
