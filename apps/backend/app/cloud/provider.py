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

    supports_tools = False

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
        client = self.client.client()
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
                raise LLMError("offline")
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
