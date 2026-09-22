"""Minimal Marionette client. No geckodriver; talks to Tor Browser's own Firefox."""

from __future__ import annotations

import asyncio
import json

from ..contracts import ToolError


class MarionetteClient:
    def __init__(self, host="127.0.0.1", port=2828, timeout=45):
        self.host, self.port, self.timeout = host, port, timeout
        self.reader = None
        self.writer = None
        self._msgid = 0
        self.last_error = None

    async def connect(self):
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout
            )
        except (OSError, asyncio.TimeoutError) as error:
            raise ToolError("tor_browser_not_ready") from error
        hello = await self._read()
        if not isinstance(hello, dict):
            raise ToolError("tor_browser_not_ready")
        await self.call(
            "WebDriver:NewSession",
            {
                "capabilities": {
                    "alwaysMatch": {
                        "acceptInsecureCerts": True,
                        "unhandledPromptBehavior": "dismiss",
                    }
                }
            },
        )
        await self._focus_window()

    async def _focus_window(self):
        try:
            handles = await self.call("WebDriver:GetWindowHandles")
        except ToolError:
            return
        if isinstance(handles, dict):
            handles = handles.get("value") or handles.get("handles") or []
        if not handles:
            return
        handle = handles[0]
        try:
            await self.call("WebDriver:SwitchToWindow", {"handle": handle, "name": handle})
        except ToolError:
            try:
                await self.call("WebDriver:SwitchToWindow", {"name": handle})
            except ToolError:
                return

    async def call(self, command: str, params=None, _retried=False):
        self._msgid += 1
        msgid = self._msgid
        await self._write([0, msgid, command, params or {}])
        reply = await self._read()
        if not isinstance(reply, list) or len(reply) < 4:
            raise ToolError("tor_browser_not_ready")
        if reply[2]:
            error = reply[2] if isinstance(reply[2], dict) else {"error": reply[2]}
            self.last_error = error
            code = str(error.get("error") or error.get("message") or "tor_browser_not_ready")
            if "timeout" in code.lower():
                raise ToolError("timeout")
            alert = "unexpected alert" in code.lower() or "unexpectedalertopen" in code.lower().replace(
                " ", ""
            )
            if alert and not _retried:
                try:
                    await self.call("WebDriver:DismissAlert", {}, _retried=True)
                except ToolError:
                    pass
                return await self.call(command, params, _retried=True)
            raise ToolError("tor_browser_not_ready")
        self.last_error = None
        return reply[3]

    async def navigate(self, url: str):
        return await self.call("WebDriver:Navigate", {"url": url})

    async def current_url(self) -> str:
        value = await self.call("WebDriver:GetCurrentURL")
        if isinstance(value, dict):
            return str(value.get("value") or "")
        return str(value or "")

    async def title(self) -> str:
        value = await self.call("WebDriver:GetTitle")
        if isinstance(value, dict):
            return str(value.get("value") or "")
        return str(value or "")

    async def page_source(self) -> str:
        value = await self.call("WebDriver:GetPageSource")
        if isinstance(value, dict):
            return str(value.get("value") or "")
        return str(value or "")

    async def inner_text(self) -> str:
        script = (
            "return (document.body && document.body.innerText) ? document.body.innerText : "
            "(document.documentElement ? document.documentElement.innerText : '');"
        )
        try:
            value = await self.call("WebDriver:ExecuteScript", {"script": script, "args": []})
        except ToolError:
            return ""
        if isinstance(value, dict):
            return str(value.get("value") or "")
        return str(value or "")

    async def back(self):
        return await self.call("WebDriver:Back")

    async def set_timeouts(self, ms: int):
        try:
            await self.call(
                "WebDriver:SetTimeouts", {"pageLoad": ms, "script": min(ms, 30000), "implicit": 0}
            )
        except ToolError:
            return

    async def close(self):
        try:
            if self.writer:
                try:
                    await self.call("WebDriver:DeleteSession")
                except ToolError:
                    pass
        finally:
            if self.writer:
                self.writer.close()
                try:
                    await self.writer.wait_closed()
                except Exception:
                    pass
            self.reader = self.writer = None

    async def _write(self, message):
        writer = self.writer
        if writer is None:  # pragma: no cover - the client only writes on a connected session
            raise ToolError("tor_browser_not_ready")
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        writer.write(f"{len(payload)}:".encode("ascii") + payload)
        await writer.drain()

    async def _read(self):
        reader = self.reader
        if reader is None:  # pragma: no cover - the client only reads on a connected session
            raise ToolError("tor_browser_not_ready")
        raw = b""
        while b":" not in raw:
            chunk = await asyncio.wait_for(reader.read(1), self.timeout)
            if not chunk:
                raise ToolError("tor_browser_not_ready")
            raw += chunk
            if len(raw) > 16:
                raise ToolError("tor_browser_not_ready")
        length = int(raw.split(b":", 1)[0])
        body = b""
        while len(body) < length:
            chunk = await asyncio.wait_for(reader.read(length - len(body)), self.timeout)
            if not chunk:
                raise ToolError("tor_browser_not_ready")
            body += chunk
        return json.loads(body.decode("utf-8"))
