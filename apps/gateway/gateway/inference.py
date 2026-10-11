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
* a replayed request id never produces a second generation, and the replay identity is the
  installation *plus* the request id *plus* the request body — one installation can never be
  served another's answer, and a concurrent copy of the same request never queues up to
  generate twice;
* the single generation slot is released even when the client never reads the body at all.
"""

from __future__ import annotations

import asyncio
import hashlib
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


class GenerationBody:
    """The streaming body of one generation, with a guaranteed finalizer.

    Starlette may close the body *before* it ever starts — a client that drops the connection
    between the response headers and the first chunk, or a cancelled request that never
    iterated. A bare async generator would then never enter its own ``finally``, and the single
    generation slot (``--parallel 1``) would stay occupied forever: every later request would
    queue until it reported ``gateway_busy``. ``aclose`` is therefore the release point,
    whether or not the iteration ever began; the finalizer is idempotent, so a generation that
    really finished keeps the outcome it recorded.
    """

    def __init__(self, inner, finish):
        self._inner = inner
        self._finish = finish

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._inner.__anext__()

    async def aclose(self) -> None:
        try:
            await self._inner.aclose()
        finally:
            self._finish("cancelled")


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

    @staticmethod
    def _key(installation_id: str, request_id: str) -> str:
        """Request identity is the *installation plus* its request id.

        The id is chosen by the client, so a shared id space would let one installation read
        another's answer out of the replay cache. Identity is therefore part of the key.
        """
        return f"{installation_id}\x00{request_id}"

    @staticmethod
    def _digest(body: dict) -> str:
        """The request body is part of the identity too: the same id with a different request
        is a new generation, never a replay of the answer to another question."""
        canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _remember(self, key: str, state: str, digest: str, body: dict | None = None) -> None:
        self._recent[key] = {"state": state, "body": body, "digest": digest, "at": time.time()}
        while len(self._recent) > REQUEST_MEMORY_MAX:
            self._recent.popitem(last=False)

    def _lookup(self, key: str, digest: str) -> dict | None:
        entry = self._recent.get(key)
        if entry is None:
            return None
        if time.time() - entry["at"] > REQUEST_MEMORY_SECONDS:
            self._recent.pop(key, None)
            return None
        if entry.get("digest") != digest:
            # Same id, different request: the stale record is dropped so the new request owns
            # the id from here on, instead of being answered with someone else's question.
            self._recent.pop(key, None)
            return None
        return entry

    def _forget(self, key: str) -> None:
        """An admission failure leaves no trace, so a retry with the same id is possible."""
        self._recent.pop(key, None)
        self._in_flight.pop(key, None)

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
        key = self._key(installation_id, request_id)
        digest = self._digest(body)
        known = self._lookup(key, digest)
        if known is not None:
            if known["state"] == "completed" and known.get("body") is not None and not body.get("stream"):
                return JSONResponse(content=known["body"], headers={"X-Alex-Request-Id": request_id})
            if known["state"] == "in_flight":
                raise GatewayError("gateway_request_in_flight")
            raise GatewayError("gateway_request_completed")
        target = await self.authority.upstream_target()
        if target is None:
            raise GatewayError("compute_offline")

        # A13: the id is claimed *before* any await, so two simultaneous copies of the same
        # request can never both reach the model — one request stays one generation. The claim
        # is released again if admission itself fails, so a legitimate retry still works.
        self._in_flight[key] = installation_id
        self._remember(key, "in_flight", digest)
        try:
            await asyncio.wait_for(self.queue.acquire(), timeout=self.queue.timeout)
        except TimeoutError:
            self._forget(key)
            raise GatewayError("gateway_busy") from None
        except GatewayError:
            self._forget(key)
            raise
        except asyncio.CancelledError:
            self._forget(key)
            raise

        base_url, key_value = target
        headers = {"Authorization": "Bearer " + key_value, "Content-Type": "application/json"}
        if task_id:
            headers["X-Alex-Task-Id"] = task_id
        self.authority.mark_activity()
        released = False

        def finish(state: str, result: dict | None = None):
            """Synchronous by design, and single-outcome: the generation slot is never released
            across an await, and one generation records exactly one result."""
            nonlocal released
            if released:
                return
            released = True
            self._in_flight.pop(key, None)
            self._remember(key, state, digest, result)
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
            GenerationBody(relay(), finish),
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
