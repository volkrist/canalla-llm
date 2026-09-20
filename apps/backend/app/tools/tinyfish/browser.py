import asyncio
import json
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import quote, urljoin, urlsplit

import anyio
from pydantic import BaseModel, ConfigDict, Field

from ...database import SessionLocal
from ...models import now
from ..contracts import ToolError, ToolProvider, ToolResult
from ..models import ToolRun
from ..security import digest, sanitized, validate_url
from .client import BROWSER, get_tinyfish_client

UNSAFE_HREF_PREFIXES = ("#", "javascript:", "mailto:", "data:", "file:", "vbscript:")
DOCS_TEXT = re.compile(r"(?i)^\s*(documentation|docs|python docs|документ(аци[яи])?)\s*$")
DOCS_HREF = re.compile(r"(?i)(/docs?(/|$|\?)|docs\.python|documentation)")


def absolute_http_url(base: str, href: str) -> str:
    raw = (href or "").strip()
    if not raw or raw.lower().startswith(UNSAFE_HREF_PREFIXES):
        return ""
    value = urljoin(base or "", raw)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return value


def looks_like_docs_target(text: str = "", url: str = "", href: str = "") -> bool:
    if DOCS_TEXT.search(text or ""):
        return True
    return bool(DOCS_HREF.search(f"{text or ''} {url or ''} {href or ''}"))


def is_docs_page(url: str = "", title: str = "") -> bool:
    return looks_like_docs_target(title or "", url or "", "")


def normalize_browser_link(page_url: str, raw_href: str, text: str, index: int, token: str) -> dict | None:
    resolved = absolute_http_url(page_url, raw_href)
    if not resolved:
        return None
    parsed = urlsplit(resolved)
    page = urlsplit(page_url or "")
    return {
        "id": token,
        "text": text or "",
        "raw_href": (raw_href or "")[:2048],
        "href": (raw_href or "")[:2048],
        "resolved_url": resolved[:2048],
        "url": resolved[:2048],
        "scheme": parsed.scheme,
        "same_origin": bool(page.netloc and parsed.netloc.lower() == page.netloc.lower()),
        "source_page_url": page_url or "",
        "index": index,
    }


def docs_link_candidates(links, prompt="", current_url=""):
    exact, hrefs, seen = [], [], set()

    def add(bucket, token, url):
        target = (url or "").strip()
        if not target or target in seen:
            return
        seen.add(target)
        bucket.append((str(token or ""), target))

    for link in links or []:
        token = str(link.get("id") or "")
        url = link.get("resolved_url") or link.get("url") or ""
        href = link.get("raw_href") or link.get("href") or ""
        text = str(link.get("text") or "")
        if not url:
            continue
        if looks_like_docs_target(text, url, href):
            add(exact if DOCS_TEXT.search(text) else hrefs, token, url)
    if re.search(r"(?i)doc|документ", prompt or "") and current_url:
        for rel in ("/doc/", "/docs/", "/documentation/"):
            add(hrefs, "", urljoin(current_url, rel))
    return exact + hrefs


class BrowserStartArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=2048)


class BrowserReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(max_length=160)
    action: Literal["navigate", "read", "wait", "screenshot", "extract"]
    url: str | None = Field(default=None, max_length=2048)
    selector: str = Field(default="body", max_length=300)
    seconds: float = Field(default=1, ge=0, le=5)


class BrowserWriteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(max_length=160)
    action: Literal["click", "type"]
    selector: str = Field(min_length=1, max_length=300)
    text: str = Field(default="", max_length=1000)


class WebBrowserArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["open", "read", "links", "click", "back", "wait", "close"]
    url: str | None = Field(default=None, max_length=2048)
    link_id: str | None = Field(default=None, max_length=8)
    seconds: float = Field(default=1, ge=0, le=5)


@dataclass
class BrowserSession:
    session_id: str
    user_id: str
    run_id: str
    cdp_url: str
    base_url: str
    started: float
    max_seconds: float
    playwright: object
    browser: object
    page: object
    task: object = None
    active: bool = True
    allow_write: bool = False
    write_budget: int = 0
    image: bytes | None = None
    stopped: float | None = None
    task_id: str | None = None
    started_by_alex: bool = True
    last_activity: float = 0.0
    links: list | None = None


@dataclass
class BrowserTaskState:
    session_id: str = ""
    current_url: str = ""
    current_title: str = ""
    available_links: list = field(default_factory=list)
    navigation_history: list = field(default_factory=list)
    goal_remaining: list = field(default_factory=list)
    source_ids: list = field(default_factory=list)


class BrowserController:
    """Typed Playwright only. Page/model input is never evaluated as JavaScript."""

    async def execute(self, session, args, resolver=None):
        page = session.page
        if not session.active:
            raise ToolError("browser_stopped")
        if args.action == "navigate":
            if not args.url:
                raise ToolError("invalid_arguments")
            await validate_url(args.url, resolver)
            await page.goto(args.url, wait_until="domcontentloaded", timeout=20000)
            await validate_url(page.url, resolver)
            return await page.locator("body").inner_text(timeout=5000)
        if args.action in {"read", "extract"}:
            await validate_url(page.url, resolver)
            return await page.locator(args.selector).first.inner_text(timeout=5000)
        if args.action == "wait":
            await asyncio.sleep(args.seconds)
            return "Wait completed."
        if args.action == "screenshot":
            session.image = await page.screenshot(type="png", full_page=False, timeout=10000)
            return "Screenshot available in the application."
        locator = page.locator(args.selector).first
        if args.action == "type":
            attributes = " ".join(
                [(await locator.get_attribute(key) or "") for key in ("type", "name", "autocomplete")]
            )
            if re.search(r"(?i)password|passwd|token|secret|one-time-code|cc-number|credit", attributes):
                raise ToolError("credentials_not_supported")
        try:
            session.allow_write = True
            session.write_budget = 1
            if args.action == "click":
                await locator.click(timeout=10000)
            elif args.action == "type":
                await locator.fill(args.text, timeout=10000)
            else:
                raise ToolError("invalid_arguments")
        finally:
            session.allow_write = False
            session.write_budget = 0
        return "Confirmed action completed."


class TinyFishBrowserProvider(ToolProvider):
    def __init__(self, client=None, playwright_factory=None):
        self.client = client or get_tinyfish_client()
        self.playwright_factory = playwright_factory
        self.sessions = {}
        self.controller = BrowserController()
        self.locks = {}
        self.start_lock = asyncio.Lock()

    def owned(self, session_id, user_id):
        session = self.sessions.get(session_id)
        if not session or session.user_id != user_id:
            raise ToolError("not_found")
        if not session.active:
            raise ToolError("browser_stopped")
        return session

    async def execute(self, args, context):
        if isinstance(args, WebBrowserArgs):
            return await self.web_execute(args, context)
        async with self.start_lock:
            return await self.start(args, context)

    async def start(self, args: BrowserStartArgs, context):
        await validate_url(args.url, context.resolver)
        if any(s.active and s.user_id == context.user_id for s in self.sessions.values()):
            raise ToolError("browser_already_active")
        if not self.playwright_factory:
            from playwright.async_api import async_playwright

            factory = async_playwright
        else:
            factory = self.playwright_factory
        price = self.client.settings.tinyfish_browser_minute_price
        seconds = min(
            context.settings.agent_max_runtime,
            context.settings.agent_run_budget / price * 60 if price else context.settings.agent_max_runtime,
        )
        if seconds < 5:
            raise ToolError("run_budget")
        # Start blank so the local controller installs network guards before any navigation.
        try:
            value = await self.client.request(
                "POST", BROWSER, body={"timeout_seconds": max(5, int(seconds))}, retry=False, timeout=60
            )
        except (asyncio.CancelledError, ToolError):
            await context.progress(supplier_state="UNKNOWN", supplier_stop_confirmed=False)
            raise
        session_id, cdp_url, base_url = value.get("session_id"), value.get("cdp_url"), value.get("base_url")
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", session_id):
            raise ToolError("malformed_browser_session")
        playwright = browser = None
        attached = False
        try:
            if not isinstance(cdp_url, str) or urlsplit(cdp_url).scheme != "wss":
                raise ToolError("malformed_browser_session")
            await validate_url(cdp_url.replace("wss://", "https://", 1), context.resolver)
            await validate_url(base_url, context.resolver)
            playwright = await factory().start()
            browser = await playwright.chromium.connect_over_cdp(cdp_url, timeout=30000)
            browser_context = await browser.new_context(service_workers="block", accept_downloads=False)
            session = BrowserSession(
                session_id,
                context.user_id,
                context.run_id,
                cdp_url,
                base_url,
                time.monotonic(),
                seconds,
                playwright,
                browser,
                None,
                task_id=getattr(context, "task_id", None),
                last_activity=time.monotonic(),
            )

            async def guard(route):
                # TinyFish CDP sessions are proxied. route.continue_ breaks the tunnel
                # (net::ERR_TUNNEL_CONNECTION_FAILED). Safe methods use fallback.
                try:
                    method = route.request.method
                    if method in {"GET", "HEAD", "OPTIONS"}:
                        await validate_url(route.request.url, context.resolver)
                        await route.fallback()
                        return
                    await validate_url(route.request.url, context.resolver)
                    if not session.active or not session.allow_write or session.write_budget <= 0:
                        raise ToolError("browser_write_not_confirmed")
                    session.write_budget -= 1
                    await route.fallback()
                except ToolError:
                    await route.abort()

            if self.playwright_factory is not None:
                await browser_context.route("**/*", guard)
                await browser_context.route_web_socket("**/*", lambda socket: socket.close())
            page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
            session.page = page
            self.sessions[session_id] = session
            self.locks[session_id] = asyncio.Lock()
            try:
                await page.goto(args.url, wait_until="domcontentloaded", timeout=20000)
            except ToolError:
                raise
            except Exception:
                raise ToolError("browser_navigation_failed") from None
            await validate_url(page.url, context.resolver)
            session.task = asyncio.create_task(self.watchdog(session_id, context.user_id, seconds))
            attached = True
            await context.progress(
                event_kind="BROWSER_STARTED",
                session_id=session_id,
                supplier_state="RUNNING",
            )
            await context.progress(event_kind="BROWSER_CONNECTED", session_id=session_id)
            return ToolResult(
                text="Advanced browser session started.",
                provider_run_id=session_id,
                metadata={
                    "session_id": session_id,
                    "supplier_state": "RUNNING",
                    "started_by_alex": True,
                    "session_created": True,
                    "max_runtime_seconds": int(seconds),
                    "budget_enforcement": "local_soft",
                },
            )
        finally:
            if not attached:
                with anyio.CancelScope(shield=True):
                    if browser:
                        with suppress(Exception):
                            await browser.close()
                    if playwright:
                        with suppress(Exception):
                            await playwright.stop()
                    try:
                        terminated = await self.terminate_supplier(session_id)
                        await context.progress(
                            supplier_stop_confirmed=terminated,
                            supplier_state="TERMINATED" if terminated else "UNKNOWN",
                        )
                    finally:
                        self.sessions.pop(session_id, None)
                        self.locks.pop(session_id, None)

    async def terminate_supplier(self, session_id):
        if not self.client.settings.tinyfish_browser_delete_supported:
            return False
        for _ in range(3):
            try:
                value = await self.client.request(
                    "DELETE", BROWSER + "/" + quote(session_id, safe=""), timeout=10
                )
                if value.get("terminated") is True:
                    return True
            except ToolError:
                continue
        return False

    async def watchdog(self, session_id, owner, seconds):
        deadline = time.monotonic() + max(5, seconds)
        idle = min(120.0, max(5.0, seconds))
        while time.monotonic() < deadline:
            await asyncio.sleep(5)
            session = self.sessions.get(session_id)
            if not session or not session.active:
                return
            if time.monotonic() - (session.last_activity or session.started) > idle:
                break
        await self.stop(session_id, owner)

    async def stop(self, session_id, owner):
        session = self.sessions.get(session_id)
        if not session or session.user_id != owner:
            raise ToolError("not_found")
        session.active = False
        session.stopped = session.stopped or time.monotonic()
        if session.task and session.task is not asyncio.current_task():
            session.task.cancel()
        with anyio.CancelScope(shield=True):
            with suppress(Exception):
                await session.browser.close()
            with suppress(Exception):
                await session.playwright.stop()
            terminated = await self.terminate_supplier(session_id)
        duration = (session.stopped - session.started) if session.stopped else 0
        cost = duration / 60 * self.client.settings.tinyfish_browser_minute_price
        with suppress(Exception):
            with SessionLocal() as db:
                row = db.get(ToolRun, session.run_id)
                if row:
                    row.cost_estimate = cost
                    row.result_metadata = {
                        **row.result_metadata,
                        "supplier_stop_confirmed": terminated,
                        "supplier_state": "TERMINATED" if terminated else "UNKNOWN",
                        "local_controller_stopped": True,
                        "duration_seconds": duration,
                        "estimated_provider_cost": cost,
                    }
                    row.finished_at = now()
                    db.commit()
        session.image = None
        session.cdp_url = ""
        self.sessions.pop(session_id, None)
        self.locks.pop(session_id, None)
        return {
            "local_controller_stopped": True,
            "supplier_stop_confirmed": terminated,
            "supplier_state": "TERMINATED" if terminated else "UNKNOWN",
            "cost_estimate": cost,
            "duration_seconds": duration,
            "session_status": "CLOSED",
            "delete_attempted": True,
            "delete_status": "terminated" if terminated else "unknown",
            "registry_removed": True,
        }

    async def action(self, args, context):
        session = self.owned(args.session_id, context.user_id)
        async with self.locks[args.session_id]:
            try:
                text = await self.controller.execute(session, args, context.resolver)
                return ToolResult(
                    text=sanitized(text, context.secrets, 6000),
                    metadata={
                        "session_id": session.session_id,
                        "screenshot_available": args.action == "screenshot",
                    },
                )
            except asyncio.CancelledError:
                await self.stop(session.session_id, context.user_id)
                raise

    async def close_all(self):
        for session in list(self.sessions.values()):
            if session.active:
                await self.stop(session.session_id, session.user_id)

    def _owned_session(self, user_id):
        for session in self.sessions.values():
            if session.active and session.user_id == user_id:
                return session
        return None

    async def _snapshot(self, session, resolver=None):
        page = session.page
        current = page.url
        try:
            await validate_url(current, resolver)
        except ToolError:
            current = ""
        title = sanitized(await page.title(), (), 400)
        text = sanitized(await page.locator("body").inner_text(timeout=5000), (), 6000) if current else ""
        locators = page.locator("a[href]")
        try:
            count = min(await locators.count(), 120) if current else 0
        except Exception:
            count = 0
        docs, other = [], []
        for index in range(count):
            raw = await locators.nth(index).get_attribute("href") or ""
            label = sanitized(await locators.nth(index).inner_text(timeout=2000), (), 120)
            item = normalize_browser_link(current, raw, label, index, "L0")
            if not item:
                continue
            try:
                await validate_url(item["resolved_url"], resolver)
            except ToolError:
                continue
            (docs if looks_like_docs_target(item["text"], item["url"], item["raw_href"]) else other).append(
                item
            )
            if len(docs) + len(other) >= 80:
                break
        ranked = docs + other[: max(0, 32 - len(docs))]
        links = []
        for item in ranked:
            item = {**item, "id": f"L{len(links) + 1}"}
            links.append(item)
        session.links = links
        session.last_activity = time.monotonic()
        session.task_state = BrowserTaskState(
            session_id=session.session_id,
            current_url=current,
            current_title=title,
            available_links=self._public_links(links),
            navigation_history=[*(getattr(session.task_state, "navigation_history", None) or []), current]
            if current
            else list(getattr(getattr(session, "task_state", None), "navigation_history", None) or []),
        )
        return title, text, links, current

    def _public_links(self, links):
        return [{key: value for key, value in item.items() if key != "index"} for item in links or []]

    def _source(self, url, title, text, links, session_id):
        return {
            "url": url,
            "final_url": url,
            "title": title,
            "excerpt": text,
            "retrieval": "browser",
            "rendered": True,
            "links": self._public_links(links),
            "browser_session_id": session_id,
        }

    def _result_meta(self, session, extra=None, *, status="READY", error_code=None):
        payload = {
            "session_id": session.session_id if session else "",
            "session_status": status,
            "error_code": error_code,
        }
        if extra:
            payload.update(extra)
        return {key: value for key, value in payload.items() if value is not None}

    async def web_execute(self, args: WebBrowserArgs, context):
        if args.operation == "open":
            if not args.url:
                raise ToolError("invalid_arguments")
            existing = self._owned_session(context.user_id)
            if existing:
                async with self.locks[existing.session_id]:
                    text = await self.controller.execute(
                        existing,
                        BrowserReadArgs(session_id=existing.session_id, action="navigate", url=args.url),
                        context.resolver,
                    )
                    title, snapshot, links, url = await self._snapshot(existing, context.resolver)
                    return ToolResult(
                        text=sanitized(f"{title}\n{snapshot}", context.secrets, 6000),
                        sources=[self._source(url, title, snapshot, links, existing.session_id)],
                        provider_run_id=existing.session_id,
                        metadata=self._result_meta(
                            existing,
                            {
                                "links": self._public_links(links)[:32],
                                "current_url": url,
                                "title": title,
                                "page_text_excerpt": snapshot[:1500],
                                "navigation_history": [url] if url else [],
                            },
                        ),
                    )
            started = await self.execute(BrowserStartArgs(url=args.url), context)
            session = self.owned(started.metadata["session_id"], context.user_id)
            title, snapshot, links, url = await self._snapshot(session, context.resolver)
            return ToolResult(
                text=sanitized(f"{title}\n{snapshot}", context.secrets, 6000),
                sources=[self._source(url, title, snapshot, links, session.session_id)],
                provider_run_id=session.session_id,
                metadata=self._result_meta(
                    session,
                    {
                        **started.metadata,
                        "links": self._public_links(links)[:32],
                        "started_by_alex": True,
                        "current_url": url,
                        "title": title,
                        "page_text_excerpt": snapshot[:1500],
                        "navigation_history": [url] if url else [],
                    },
                ),
            )
        if args.operation == "close":
            session = self._owned_session(context.user_id)
            if not session:
                raise ToolError("not_found")
            stopped = await self.stop(session.session_id, context.user_id)
            return ToolResult(
                text="Browser session closed.",
                metadata={**stopped, "session_status": "CLOSED", "error_code": None},
            )
        session = self._owned_session(context.user_id)
        if not session:
            raise ToolError("not_found")
        async with self.locks[session.session_id]:
            if args.operation == "wait":
                await asyncio.sleep(args.seconds)
            elif args.operation == "back":
                await session.page.go_back(wait_until="domcontentloaded", timeout=20000)
            elif args.operation == "click":
                token = (args.link_id or "").upper()
                match = next((item for item in session.links or [] if item.get("id") == token), None)
                if not match:
                    raise ToolError("invalid_arguments")
                target = (
                    match.get("resolved_url")
                    or match.get("url")
                    or absolute_http_url(session.page.url, match.get("raw_href") or match.get("href") or "")
                )
                if not target:
                    raise ToolError("invalid_arguments")
                await validate_url(target, context.resolver)
                session.allow_write = True
                session.write_budget = 1
                method = "click"
                try:
                    try:
                        await (
                            session.page.locator("a[href]")
                            .nth(int(match.get("index", 0)))
                            .click(timeout=10000)
                        )
                    except Exception:
                        method = "goto"
                        await session.page.goto(target, wait_until="domcontentloaded", timeout=20000)
                finally:
                    session.allow_write = False
                    session.write_budget = 0
            title, snapshot, links, url = await self._snapshot(session, context.resolver)
            text = snapshot if args.operation != "links" else json.dumps(links, ensure_ascii=False)
            extra = {
                "links": self._public_links(links)[:32],
                "current_url": url,
                "title": title,
                "page_text_excerpt": snapshot[:1500],
                "navigation_history": list(
                    getattr(getattr(session, "task_state", None), "navigation_history", None) or [url]
                ),
            }
            if args.operation == "click":
                extra.update(
                    {
                        "navigation_method": method,
                        "requested_url": target,
                        "final_url": url,
                    }
                )
            return ToolResult(
                text=sanitized(text, context.secrets, 6000),
                sources=[self._source(url, title, snapshot, links, session.session_id)],
                provider_run_id=session.session_id,
                metadata=self._result_meta(session, extra),
            )


class BrowserActionProvider(ToolProvider):
    def __init__(self, browser):
        self.browser = browser

    async def preview(self, args, context):
        session = self.browser.owned(args.session_id, context.user_id)
        target = {"url": session.page.url, "selector": args.selector, "action": args.action}
        if isinstance(args, BrowserWriteArgs):
            locator = session.page.locator(args.selector).first
            target["label"] = sanitized(await locator.inner_text(timeout=5000), context.secrets, 200)
            target["href"] = await locator.get_attribute("href")
            target["type"] = await locator.get_attribute("type")
        context.action_fingerprint = digest(target)
        return {"site": urlsplit(session.page.url).netloc, "target": target.get("label", args.selector)}

    async def execute(self, args, context):
        expected = context.action_fingerprint
        await self.preview(args, context)
        if expected != context.action_fingerprint:
            raise ToolError("browser_target_changed")
        return await self.browser.action(args, context)
