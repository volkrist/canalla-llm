"""Production inference proxy.

Shared mode never lets a client reach llama.cpp: the Gateway owns the only route to the
Pod's authenticated port. That is what makes the server-side money and ownership rules
unenforceable-by-bypass rather than advisory.

Properties kept from the product's direct mode:

* the model alias is the readiness contract (``/v1/models``);
* streaming stays incremental — chunks are forwarded as they arrive and are never
  buffered into a full response;
* ``--parallel 1`` means one generation at a time, so requests wait in a **bounded FIFO
  queue** instead of being silently dropped;
* stopping a generation is not stopping compute;
* a replayed request id never produces a second generation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from uuid import uuid4

import httpx
from fastapi.responses import JSONResponse, StreamingResponse

from .errors import GatewayError, error_body

logger = logging.getLogger(__name__)

REQUEST_MEMORY_SECONDS = 300
REQUEST_MEMORY_MAX = 256


class InferenceQueue:
    """Bounded admission control for the single generation slot (``--parallel 1``).

    ``asyncio.Semaphore`` wakes waiters in the order they blocked, so the queue is FIFO in
    practice; the bound is explicit, and a request that waits longer than
    ``inference_queue_timeout_seconds`` fails with ``gateway_busy`` instead of being
    silently dropped.
    """

    def __init__(self, size: int, timeout: float):
        self.size = max(0, int(size))
        self.timeout = float(timeout)
        self._slot = asyncio.Semaphore(1)
        self._waiting = 0
        self._running = 0

    def depth(self) -> int:
        return self._waiting

    def active(self) -> int:
        return self._waiting + self._running

    async def acquire(self) -> None:
        if self._slot.locked() or self._waiting:
            if self._waiting >= self.size:
                raise GatewayError("gateway_queue_full")
            self._waiting += 1
            try:
                await asyncio.wait_for(self._slot.acquire(), timeout=self.timeout)
            except TimeoutError:
                raise GatewayError("gateway_busy") from None
            finally:
                self._waiting = max(0, self._waiting - 1)
        else:
            await self._slot.acquire()
        self._running = 1

    def release(self) -> None:
        self._running = 0
        self._slot.release()


class InferenceProxy:
    def __init__(self, settings, authority, *, transport=None, queue: InferenceQueue | None = None):
        self.settings = settings
        self.authority = authority
        self.transport = transport
        self.queue = (
            queue
            if queue is not None
            else InferenceQueue(settings.inference_queue_size, settings.inference_queue_timeout_seconds)
        )
        self._recent: OrderedDict[str, dict] = OrderedDict()
        self._in_flight: dict[str, str] = {}

    # ------------------------------------------------------------------ request memory

    def _remember(self, request_id: str, state: str, body: dict | None = None) -> None:
        self._recent[request_id] = {"state": state, "body": body, "at": time.time()}
        while len(self._recent) > REQUEST_MEMORY_MAX:
            self._recent.popitem(last=False)

    def _lookup(self, request_id: str) -> dict | None:
        entry = self._recent.get(request_id)
        if entry is None:
            return None
        if time.time() - entry["at"] > REQUEST_MEMORY_SECONDS:
            self._recent.pop(request_id, None)
            return None
        return entry

    # --------------------------------------------------------------------------- models

    async def models(self) -> dict:
        target = await self.authority.upstream_target()
        if target is None:
            raise GatewayError("compute_offline")
        base_url, key = target
        try:
            async with self._client(timeout=10) as client:
                response = await client.get(
                    base_url + "/v1/models", headers={"Authorization": "Bearer " + key}
                )
        except httpx.HTTPError:
            raise GatewayError("gateway_unavailable") from None
        if response.status_code == 401:
            raise GatewayError("gateway_unavailable", detail="Pod отклонил ключ Alex Cloud.")
        if response.status_code >= 400:
            raise GatewayError("gateway_unavailable")
        try:
            payload = response.json()
        except ValueError:
            raise GatewayError("malformed_response") from None
        return payload

    def _client(self, timeout: float | None = None):
        return httpx.AsyncClient(
            timeout=timeout or self.settings.inference_upstream_timeout_seconds,
            follow_redirects=False,
            transport=self.transport,
        )

    # ---------------------------------------------------------------------- completions

    async def completions(
        self, body: dict, *, installation_id: str, task_id: str | None, request_id: str | None
    ) -> JSONResponse | StreamingResponse:
        request_id = request_id or str(uuid4())
        known = self._lookup(request_id)
        if known is not None:
            if known["state"] == "completed" and known.get("body") is not None and not body.get("stream"):
                return JSONResponse(content=known["body"], headers={"X-Alex-Request-Id": request_id})
            if known["state"] == "in_flight":
                raise GatewayError("gateway_request_in_flight")
            raise GatewayError("gateway_request_completed")
        target = await self.authority.upstream_target()
        if target is None:
            raise GatewayError("compute_offline")

        try:
            await asyncio.wait_for(self.queue.acquire(), timeout=self.queue.timeout)
        except TimeoutError:
            raise GatewayError("gateway_busy") from None
        except asyncio.CancelledError:
            raise

        base_url, key = target
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        if task_id:
            headers["X-Alex-Task-Id"] = task_id
        self._in_flight[request_id] = installation_id
        self._remember(request_id, "in_flight")
        self.authority.mark_activity()
        released = False

        def finish(state: str, result: dict | None = None):
            """Synchronous by design: the generation slot is never released across an await."""
            nonlocal released
            self._in_flight.pop(request_id, None)
            self._remember(request_id, state, result)
            if not released:
                released = True
                self.queue.release()

        stream = bool(body.get("stream"))
        client = self._client()
        try:
            request = client.build_request(
                "POST", base_url + "/v1/chat/completions", json=body, headers=headers
            )
            response = await client.send(request, stream=stream)
        except httpx.HTTPError:
            await client.aclose()
            finish("failed")
            raise GatewayError("gateway_unavailable") from None
        except BaseException:
            await client.aclose()
            finish("failed")
            raise

        if response.status_code >= 400:
            await response.aread()
            status = response.status_code
            await response.aclose()
            await client.aclose()
            finish("failed")
            if status == 401:
                raise GatewayError("gateway_unavailable", detail="Pod отклонил ключ Alex Cloud.")
            if status == 429:
                raise GatewayError("gateway_busy")
            raise GatewayError("gateway_unavailable", detail=f"AI ответил ошибкой {status}.")

        if not stream:
            try:
                payload = response.json()
            except ValueError:
                await response.aclose()
                await client.aclose()
                finish("failed")
                raise GatewayError("malformed_response") from None
            await response.aclose()
            await client.aclose()
            finish("completed", payload)
            return JSONResponse(content=payload, headers={"X-Alex-Request-Id": request_id})

        async def relay():
            status = "completed"
            try:
                async for chunk in response.aiter_bytes():
                    self.authority.mark_activity()
                    yield chunk
            except (asyncio.CancelledError, GeneratorExit):
                # The client went away (Stop button, closed window, cancelled request):
                # the upstream generation stops too.
                status = "cancelled"
                raise
            except httpx.HTTPError:
                status = "failed"
                yield sse_error(GatewayError("gateway_unavailable"))
                yield b"data: [DONE]\n\n"
            finally:
                # The slot is released synchronously first, so a cancelled or abandoned
                # stream can never leave the single generation slot occupied.
                finish(status)
                try:
                    await response.aclose()
                    await client.aclose()
                except BaseException:  # noqa: BLE001 - best effort while being cancelled
                    pass

        return StreamingResponse(
            relay(),
            media_type="text/event-stream",
            headers={
                "X-Alex-Request-Id": request_id,
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )


def sse_error(error: GatewayError) -> bytes:
    payload = error_body(error.code, error.detail)
    return ("data: " + json.dumps({"error": payload}, ensure_ascii=False) + "\n\n").encode()
