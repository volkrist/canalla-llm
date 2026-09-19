import asyncio
import json
import re
import time
from contextlib import suppress
from dataclasses import dataclass
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


def absolute_http_url(base: str, href: str) -> str:
    raw = (href or "").strip()
    if not raw or raw.startswith(("#", "javascript:", "mailto:", "data:")):
        return ""
    value = urljoin(base or "", raw)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return value


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
        try:
            value = await self.client.request(
                "DELETE", BROWSER + "/" + quote(session_id, safe=""), timeout=10
            )
            return value.get("terminated") is True
        except ToolError:
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
            count = min(await locators.count(), 40) if current else 0
        except Exception:
            count = 0
        links = []
        for index in range(count):
            href = absolute_http_url(current, await locators.nth(index).get_attribute("href") or "")
            if not href:
                continue
            try:
                await validate_url(href, resolver)
            except ToolError:
                continue
            label = sanitized(await locators.nth(index).inner_text(timeout=2000), (), 120)
            token = f"L{len(links) + 1}"
            links.append({"id": token, "url": href[:2048], "text": label, "index": index})
            if len(links) >= 20:
                break
        session.links = links
        session.last_activity = time.monotonic()
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
                        metadata={"session_id": existing.session_id, "links": self._public_links(links)[:12]},
                    )
            started = await self.execute(BrowserStartArgs(url=args.url), context)
            session = self.owned(started.metadata["session_id"], context.user_id)
            title, snapshot, links, url = await self._snapshot(session, context.resolver)
            return ToolResult(
                text=sanitized(f"{title}\n{snapshot}", context.secrets, 6000),
                sources=[self._source(url, title, snapshot, links, session.session_id)],
                provider_run_id=session.session_id,
                metadata={
                    **started.metadata,
                    "session_id": session.session_id,
                    "links": self._public_links(links)[:12],
                    "started_by_alex": True,
                },
            )
        if args.operation == "close":
            session = self._owned_session(context.user_id)
            if not session:
                raise ToolError("not_found")
            return ToolResult(
                text="Browser session closed.", metadata=await self.stop(session.session_id, context.user_id)
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
                await validate_url(match["url"], context.resolver)
                session.allow_write = True
                session.write_budget = 1
                try:
                    await session.page.locator("a[href]").nth(int(match.get("index", 0))).click(timeout=10000)
                finally:
                    session.allow_write = False
                    session.write_budget = 0
            title, snapshot, links, url = await self._snapshot(session, context.resolver)
            text = snapshot if args.operation != "links" else json.dumps(links, ensure_ascii=False)
            return ToolResult(
                text=sanitized(text, context.secrets, 6000),
                sources=[self._source(url, title, snapshot, links, session.session_id)],
                provider_run_id=session.session_id,
                metadata={"session_id": session.session_id, "links": self._public_links(links)[:12]},
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
