"""Stdlib HTTP + SSE client. No third-party imports. Secrets never logged."""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from typing import Any


class HttpError(Exception):
    def __init__(self, status: int, body: str = ""):
        self.status = status
        self.body = body
        super().__init__(f"http_{status}")


class Client:
    def __init__(self, base: str, token: str | None = None, timeout: float = 60.0):
        self.base = base.rstrip("/") + "/"
        self.token = token
        self.timeout = timeout
        self._opener = urllib.request.build_opener()

    def headers(self, extra: dict | None = None, json_body: bool = True) -> dict[str, str]:
        value = {"Accept": "application/json"}
        if json_body:
            value["Content-Type"] = "application/json"
        if self.token:
            value["Authorization"] = f"Bearer {self.token}"
        if extra:
            value.update(extra)
        return value

    def request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        timeout: float | None = None,
        extra_headers: dict | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> Any:
        url = self.base.rstrip("/") + "/" + path.lstrip("/")
        body = data
        headers = self.headers(extra_headers, json_body=payload is not None or data is not None)
        if content_type:
            headers["Content-Type"] = content_type
        if payload is not None and body is None:
            body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, method=method.upper(), headers=headers)
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                ctype = resp.headers.get("Content-Type", "")
                text = raw.decode("utf-8", "replace")
                if "json" in ctype or text[:1] in "{[":
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return text
                return text
        except urllib.error.HTTPError as error:
            raw = error.read() if error.fp else b""
            text = raw.decode("utf-8", "replace") if raw else ""
            raise HttpError(error.code, text[:500]) from None

    def get(self, path: str, **kw) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, payload=None, **kw) -> Any:
        return self.request("POST", path, payload=payload, **kw)

    def put(self, path: str, payload=None, **kw) -> Any:
        return self.request("PUT", path, payload=payload, **kw)

    def patch(self, path: str, payload=None, **kw) -> Any:
        return self.request("PATCH", path, payload=payload, **kw)

    def stream_sse(self, path: str, payload: dict, timeout: float = 180.0):
        url = self.base.rstrip("/") + "/" + path.lstrip("/")
        body = json.dumps(payload).encode("utf-8")
        headers = self.headers()
        headers["Accept"] = "text/event-stream"
        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        started = time.time()
        try:
            resp = self._opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as error:
            raw = error.read() if error.fp else b""
            raise HttpError(error.code, raw.decode("utf-8", "replace")[:500]) from None
        event = "message"
        data_lines: list[str] = []
        buf = ""
        try:
            while True:
                if time.time() - started > timeout:
                    raise TimeoutError("sse_timeout")
                chunk = resp.read(256)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", "replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.rstrip("\r")
                    if line == "":
                        if data_lines:
                            payload_text = "\n".join(data_lines)
                            try:
                                parsed = json.loads(payload_text)
                            except json.JSONDecodeError:
                                parsed = payload_text
                            yield event, parsed
                        event = "message"
                        data_lines = []
                        continue
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
        finally:
            resp.close()
