"""Balance and inference adapters for production shared mode.

``GatewayBalanceSource`` speaks the same tiny interface the existing
``RunPodBalanceService`` already consumes (``configured`` + ``account_balance()``), so the
shared balance keeps the product's cache, stale-never-zero and Decimal semantics without a
second implementation. ``GatewayProvider`` implements the existing ``LLMProvider``
contract, so the chat pipeline is unchanged: only the transport moves behind the Gateway.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx

from ..config import Settings
from ..providers import LLMError, LLMProvider
from .client import CloudError, GatewayClient

# Compute states in which a model endpoint can exist — ready to serve, or on its way there.
# Deliberately written out rather than imported from `demand`: that module imports this one, and a
# cycle to save eight strings is a poor trade. `test_cloud_demand.py` asserts this set equals
# `READY_STATES | STARTING_STATES`, so the two cannot drift apart silently.
MODEL_BEARING_STATES = frozenset(
    {
        "ready",
        "generating",
        "gpu_found",
        "creating",
        "starting_pod",
        "mounting_storage",
        "loading_model",
    }
)

logger = logging.getLogger(__name__)

# Balance failures that describe the Gateway state rather than the provider account.
BALANCE_CODES = {
    "gateway_not_connected",
    "gateway_unavailable",
    "gateway_auth_failed",
    "gateway_protocol_mismatch",
    "installation_revoked",
    "malformed_response",
}

# Transport failures that describe a moment in time, not a conclusion. A planning turn has no
# side effects, so it may be retried; auth, protocol, policy and malformed answers may not.
PLANNER_RETRY_CODES = frozenset(
    {"gateway_unavailable", "gateway_busy", "gateway_queue_full", "gateway_rate_limited"}
)


class GatewayBalanceSource:
    def __init__(self, client: GatewayClient):
        self.client = client

    @property
    def configured(self) -> bool:
        return bool(self.client.configured)

    async def account_balance(self) -> dict:
        try:
            payload = await self.client.balance()
        except CloudError as error:
            # The balance surface reports reachability of Alex Cloud, not AI capacity, so a
            # busy queue is not a different balance failure.
            code = error.code if error.code in BALANCE_CODES else "gateway_unavailable"
            raise CloudError(code, error.message, error.status) from None
        balance = _decimal(payload.get("balance_usd"))
        if balance is None:
            raise CloudError("malformed_response", "Canalla Cloud не вернул баланс.")
        return {"balance": balance, "current_spend_per_hr": _decimal(payload.get("account_spend_per_hr"))}


class GatewayProvider(LLMProvider):
    """Production shared-mode inference: this installation never sees llama.cpp."""

    # The Gateway forwards the request body unchanged, so the same planner contract llama.cpp
    # serves works through it. This used to be `False`, which made every model-driven tool call
    # impossible in production and injected "tool calling unsupported" into the model context.
    supports_tools = True

    # `/health` is the desktop's liveness contract (the runtime probe allows 400 ms) and the
    # UI polls it, so readiness is cached: at most one upstream probe per TTL and never a
    # Gateway round trip on every call. Probing per request made the local server answer
    # `/health` slower than the desktop's budget (the app then never became ready) and turned
    # the fingerprint of a healthy client into a hot loop against the shared Gateway.
    READY_TTL_SECONDS = 8.0

    def __init__(
        self,
        settings: Settings,
        client: GatewayClient | None = None,
        compute_state=None,
    ):
        self.settings = settings
        self.client = client or GatewayClient(settings)
        # A callable returning the last known compute state, or ``None`` when unknown. The probe
        # below uses it to decide whether asking for a model list could possibly be answered.
        self._compute_state = compute_state or (lambda: None)
        self._ready_value = False
        self._ready_at: float | None = None
        self._probe = None

    async def health(self) -> bool:
        """Cheap, honest readiness: cached, refreshed in the background (single flight).

        When compute cannot contain a model — no session at all, a concluded capacity failure, a
        stopped or terminated Pod, a provider error — the answer is derived from that state and
        **no model endpoint request is made**. The probe used to run every
        ``READY_TTL_SECONDS`` regardless, so a disconnected AI asked the Gateway for a model list
        that could not exist and collected a 409 every eight seconds, indefinitely.
        """
        if not self._could_have_a_model():
            self._ready_value = False
            self._ready_at = time.monotonic()
            return False
        if self._ready_at is not None and time.monotonic() - self._ready_at < self.READY_TTL_SECONDS:
            return self._ready_value
        if self._probe is None:
            self._probe = asyncio.create_task(self._probe_ready())
        if self._ready_at is None:
            # Nothing is known yet, so the very first caller waits for the real answer.
            return await asyncio.shield(self._probe)
        # A stale answer is better than a slow `/health`: the refresh runs in the background.
        return self._ready_value

    def _could_have_a_model(self) -> bool:
        """Is a model-bearing state known right now?

        ``None`` means the state has not been read yet, and the probe is allowed: an unknown state
        must not be turned into a permanent "not ready" by a caller that simply has not looked yet.
        """
        state = self._compute_state()
        if state is None:
            return True
        return state in MODEL_BEARING_STATES

    async def _probe_ready(self) -> bool:
        ready = False
        try:
            payload = await self.client.models()
            ready = any(
                isinstance(item, dict) and item.get("id") == self.settings.llm_model
                for item in payload.get("data", [])
            )
        except CloudError:
            ready = False
        except Exception:  # pragma: no cover - a probe must never break the caller
            ready = False
        finally:
            self._ready_value = ready
            self._ready_at = time.monotonic()
            self._probe = None
        return ready

    async def stream_chat(self, messages):
        iterator = self.stream_with_usage(messages, {})
        try:
            async for part in iterator:
                yield part
        finally:
            await iterator.aclose()

    async def plan_tools(self, messages, tools, usage):
        """One tool-planning turn, through the Gateway.

        A server that was not started with `--jinja` rejects the `tools` field. That rejection
        is a *server* fact and is raised as `tools_unsupported`, never turned into a sentence
        the model would repeat to the user.

        The turn streams. A non-streaming completion is bounded by the Gateway's per-request
        upstream timeout *and* by the client's own read budget, so a queued or slow planner turn
        died at 90 s while the generation was still healthy. SSE makes the Gateway's read timeout
        a per-chunk bound and the client budget (`alex_gateway_plan_timeout_seconds`) the only
        total one. A transport failure before a tool call is complete is retried a bounded number
        of times: planning has no side effects, so this cannot repeat a file change or a command.
        """
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "chat_template_kwargs": {"enable_thinking": False},
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": 1200,
        }
        attempts = 3
        message: dict | None = None
        error: LLMError | CloudError | None = None
        for attempt in range(attempts):
            error = None
            attempt_usage: dict = {}
            started = time.monotonic()
            try:
                message = await self._plan_stream(body, attempt_usage)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    if key in attempt_usage:
                        usage[key] = usage.get(key, 0) + attempt_usage[key]
                if attempt_usage.get("_planner_usage_incomplete"):
                    usage["_planner_usage_incomplete"] = True
                break
            except (LLMError, CloudError) as failure:
                error = failure
                retryable = (isinstance(failure, CloudError) and failure.code in PLANNER_RETRY_CODES) or (
                    isinstance(failure, LLMError)
                    and failure.code in {"llm_unavailable", "stream_interrupted", "gateway_busy"}
                )
                # A full-budget timeout is a conclusion, not a blip: retrying it would only
                # spend another 300 s of the task's window. Fast transport failures are
                # retried; slow ones park the task for the queue monitor's backoff.
                if not retryable or attempt == attempts - 1 or time.monotonic() - started > 30:
                    break
                await asyncio.sleep(0.5 * (2**attempt))
            except Exception:
                raise LLMError("llm_unavailable") from None
        if message is None:
            logger.warning(
                "planner_turn_failed code=%s status=%s attempts=%s",
                getattr(error, "code", type(error).__name__),
                getattr(error, "status", 0),
                attempt + 1,
            )
            if isinstance(error, CloudError):
                detail = (error.message or "").lower()
                rejected_tools = error.status in (400, 422) or error.code == "gateway_upstream_rejected"
                if rejected_tools and any(
                    token in detail for token in ("tool", "jinja", "template", "unsupported")
                ):
                    raise LLMError("tools_unsupported") from None
                if error.code in {"gateway_protocol_mismatch", "installation_revoked"}:
                    raise LLMError(error.code) from None
            if isinstance(error, LLMError) and error.code in {
                "tools_unsupported",
                "malformed_response",
                "gateway_protocol_mismatch",
                "installation_revoked",
                "gateway_auth_failed",
                "gateway_request_conflict",
                "offline",
                "stream_interrupted",
            }:
                raise error
            raise LLMError("llm_unavailable") from None
        usage["_planner_calls"] = usage.get("_planner_calls", 0) + 1
        return message

    async def _plan_stream(self, body: dict, usage: dict) -> dict:
        """One streaming planner turn, accumulating tool-call deltas into one message."""
        headers = self.client.stream_headers()
        headers["Authorization"] = "Bearer " + await self.client.token()
        client = self.client.stream_client(self.settings.alex_gateway_plan_timeout_seconds)
        try:
            request = client.build_request(
                "POST", self.client.url + "/v1/chat/completions", json=body, headers=headers
            )
            response = await client.send(request, stream=True)
        except CloudError:
            await client.aclose()
            raise
        except httpx.TimeoutException:
            await client.aclose()
            raise CloudError("gateway_unavailable", "Canalla Cloud не ответил вовремя.") from None
        except httpx.HTTPError:
            await client.aclose()
            raise CloudError("gateway_unavailable", "Canalla Cloud сейчас недоступен.") from None
        try:
            if response.status_code in (401, 403):
                raise LLMError("gateway_auth_failed")
            if response.status_code == 409:
                code = "offline"
                try:
                    payload = json.loads(await response.aread())
                    if isinstance(payload, dict) and payload.get("code") == "gateway_request_conflict":
                        code = "gateway_request_conflict"
                except (ValueError, httpx.HTTPError):
                    pass
                raise LLMError(code)
            if response.status_code == 429:
                raise CloudError("gateway_busy", "Canalla Cloud занят.", 429)
            if response.status_code == 503:
                raise CloudError("gateway_unavailable", "Canalla Cloud сейчас недоступен.", 503)
            if not response.is_success:
                detail = ""
                try:
                    detail = (await response.aread()).decode("utf-8", "replace")[:400]
                except httpx.HTTPError:
                    detail = ""
                if response.status_code in (400, 422) and any(
                    token in detail.lower() for token in ("tool", "jinja", "template", "unsupported")
                ):
                    raise LLMError("tools_unsupported")
                raise CloudError(
                    "gateway_unavailable",
                    f"AI ответил ошибкой {response.status_code}. {detail}".strip(),
                    response.status_code,
                )
            text_parts: list[str] = []
            calls: dict[int, dict] = {}
            completed = False
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    completed = True
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    raise LLMError("malformed_response") from None
                if not isinstance(event, dict):
                    raise LLMError("malformed_response")
                if "error" in event:
                    code = (
                        (event.get("error") or {}).get("code") if isinstance(event["error"], dict) else None
                    )
                    raise LLMError(code or "llm_unavailable")
                reported = event.get("usage")
                if isinstance(reported, dict):
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        count = reported.get(key)
                        if isinstance(count, int) and count >= 0:
                            usage[key] = usage.get(key, 0) + count
                        else:
                            usage["_planner_usage_incomplete"] = True
                choices = event.get("choices", [])
                if not isinstance(choices, list):
                    raise LLMError("malformed_response")
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                if not isinstance(delta, dict):
                    raise LLMError("malformed_response")
                content = delta.get("content")
                if isinstance(content, str) and content:
                    text_parts.append(content)
                for raw in delta.get("tool_calls") or []:
                    if not isinstance(raw, dict):
                        continue
                    index = raw.get("index")
                    if not isinstance(index, int):
                        index = 0
                    slot = calls.setdefault(
                        index,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if raw.get("id"):
                        slot["id"] = str(raw["id"])
                    if raw.get("type"):
                        slot["type"] = str(raw["type"])
                    function = raw.get("function") or {}
                    if isinstance(function, dict):
                        if function.get("name"):
                            slot["function"]["name"] = str(function["name"])
                        if function.get("arguments"):
                            slot["function"]["arguments"] += str(function["arguments"])
            if not completed and not calls and not text_parts:
                raise LLMError("stream_interrupted")
        finally:
            try:
                await response.aclose()
                await client.aclose()
            except Exception:
                logger.debug("gateway_plan_close_failure")
        message = {"role": "assistant", "content": "".join(text_parts) or None}
        message["tool_calls"] = [calls[index] for index in sorted(calls)]
        for position, call in enumerate(message["tool_calls"]):
            if not call.get("id"):
                call["id"] = f"call_plan_{position}"
            if not call["function"].get("name"):
                raise LLMError("malformed_response")
        return message

    async def stream_with_usage(self, messages, usage):
        iterator = self._stream(messages, usage)
        try:
            async for part in iterator:
                yield part
        finally:
            await iterator.aclose()

    async def _stream(self, messages, usage):
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        headers = self.client.stream_headers()
        headers["Authorization"] = "Bearer " + await self.client.token()
        # A32: the stream uses the longer read budget so a legitimate queue wait is not turned into
        # a dead-model error before the Gateway's admitted window ends.
        client = self.client.stream_client()
        try:
            request = client.build_request(
                "POST", self.client.url + "/v1/chat/completions", json=body, headers=headers
            )
            response = await client.send(request, stream=True)
        except CloudError as error:
            await client.aclose()
            raise LLMError(error.code) from None
        except httpx.TimeoutException:
            await client.aclose()
            raise LLMError("llm_timeout") from None
        except httpx.HTTPError:
            await client.aclose()
            raise LLMError("llm_unavailable") from None
        try:
            if response.status_code in (401, 403):
                raise LLMError("gateway_auth_failed")
            if response.status_code == 409:
                # A 409 is either `compute_offline` or an A03 identifier conflict. The product
                # never sends a request id, but if one is ever reused the reason must be named,
                # not dressed as "AI выключен".
                code = "offline"
                try:
                    body = json.loads(await response.aread())
                    if isinstance(body, dict) and body.get("code") == "gateway_request_conflict":
                        code = "gateway_request_conflict"
                except (ValueError, httpx.HTTPError):
                    pass
                raise LLMError(code)
            if response.status_code == 429:
                raise LLMError("gateway_busy")
            if response.status_code == 503:
                raise LLMError("llm_unavailable")
            if not response.is_success:
                raise LLMError("llm_unavailable")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    event = json.loads(data)
                except ValueError:
                    raise LLMError("malformed_response") from None
                if not isinstance(event, dict):
                    raise LLMError("malformed_response")
                if "error" in event:
                    code = (
                        (event.get("error") or {}).get("code") if isinstance(event["error"], dict) else None
                    )
                    raise LLMError(code or "llm_unavailable")
                reported = event.get("usage")
                if isinstance(reported, dict):
                    for upstream, column in (
                        ("prompt_tokens", "input_tokens"),
                        ("completion_tokens", "output_tokens"),
                        ("total_tokens", "total_tokens"),
                    ):
                        value = reported.get(upstream)
                        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**31 - 1:
                            usage[column] = value
                choices = event.get("choices", [])
                if not isinstance(choices, list):
                    raise LLMError("malformed_response")
                if choices:
                    content = choices[0].get("delta", {}).get("content")
                    if content:
                        if not isinstance(content, str):
                            raise LLMError("malformed_response")
                        yield content
            raise LLMError("stream_interrupted")
        finally:
            try:
                await response.aclose()
                await client.aclose()
            except Exception:
                logger.debug("gateway_stream_close_failure")


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        return None
    return result if result.is_finite() else None


def now() -> datetime:
    return datetime.now(timezone.utc)
