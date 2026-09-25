"""Central Alex Gateway client: the only way this installation talks to Alex Cloud.

The installation credential (id + secret) is the client's cloud identity. It never
becomes a request token: it is exchanged for a short-lived gateway JWT that lives in
memory only, and the RunPod master key never exists on this machine in shared mode.

Nothing here can create, resume or stop provider compute except through the typed
Gateway operations, and nothing here logs or returns a credential.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)

# Gateway failures, in the vocabulary the rest of the product already uses, so the five
# status chips and the balance panel keep working unchanged.
LOCAL_CODES = {
    "gateway_unavailable": "gateway_unavailable",
    "gateway_busy": "gateway_busy",
    "gateway_queue_full": "gateway_queue_full",
    "gateway_auth_failed": "gateway_auth_failed",
    "gateway_rate_limited": "gateway_busy",
    "gateway_protocol_mismatch": "gateway_protocol_mismatch",
    "gateway_budget_denied": "gateway_budget_denied",
    "gateway_request_in_flight": "gateway_request_in_flight",
    "gateway_request_completed": "gateway_request_completed",
    "installation_revoked": "installation_revoked",
    "installation_unknown": "installation_revoked",
    "not_configured": "gateway_not_connected",
    "compute_offline": "offline",
    "malformed_response": "malformed_response",
}

TOKEN_MARGIN_SECONDS = 30


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def map_code(code: str | None, default: str = "gateway_unavailable") -> str:
    if not code:
        return default
    return LOCAL_CODES.get(code, default)


class CloudError(RuntimeError):
    """A Gateway failure with a stable code and a human-readable Russian message."""

    def __init__(self, code: str, message: str | None = None, status: int = 0):
        self.code = code
        self.status = status
        self.message = message
        super().__init__(message or code)


class GatewayClient:
    def __init__(self, settings: Settings, *, transport=None, clock=None):
        self.settings = settings
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._token: str | None = None
        self._token_expires: datetime | None = None
        self._lock = asyncio.Lock()
        self.token_requests = 0  # observable for tests

    # ------------------------------------------------------------------- configuration

    @property
    def url(self) -> str:
        return str(self.settings.alex_gateway_url or "").rstrip("/")

    @property
    def installation_id(self) -> str:
        return str(self.settings.alex_gateway_installation_id or "")

    @property
    def secret(self) -> str:
        return self.settings.alex_gateway_installation_secret.get_secret_value()

    @property
    def configured(self) -> bool:
        return bool(self.url and self.installation_id and len(self.secret) >= 16)

    @property
    def mode(self) -> str:
        return self.settings.alex_ai_mode

    @property
    def shared(self) -> bool:
        return self.settings.alex_ai_mode == "shared"

    def timeout(self, value: float | None = None) -> httpx.Timeout:
        return httpx.Timeout(value or self.settings.alex_gateway_timeout_seconds, connect=10)

    # -------------------------------------------------------------------------- tokens

    async def token(self, *, force: bool = False) -> str:
        """Short-lived gateway token, cached in memory and renewed before expiry."""
        if not self.configured:
            raise CloudError("gateway_not_connected")
        if not force and self._fresh():
            return self._token or ""
        async with self._lock:
            if not force and self._fresh():
                return self._token or ""
            await self._issue_token()
        return self._token or ""

    def _fresh(self) -> bool:
        if not self._token or not self._token_expires:
            return False
        return utc(self.clock()) + timedelta(seconds=TOKEN_MARGIN_SECONDS) < utc(self._token_expires)

    async def _issue_token(self) -> None:
        self.token_requests += 1
        payload = {
            "installation_id": self.installation_id,
            "installation_secret": self.secret,
            "gateway_protocol_version": self.settings.alex_gateway_protocol_version,
        }
        status, body = await self._send("POST", "/auth/token", json=payload, authorized=False)
        if status != 200 or not isinstance(body, dict) or not body.get("access_token"):
            raise CloudError(map_code(_code_of(body), "gateway_auth_failed"), _detail_of(body), status)
        self._token = str(body["access_token"])
        expires = body.get("expires_in")
        seconds = float(expires) if isinstance(expires, (int, float)) else 900.0
        self._token_expires = utc(self.clock()) + timedelta(seconds=seconds)

    def forget_token(self) -> None:
        self._token = None
        self._token_expires = None

    # ------------------------------------------------------------------------- requests

    async def _send(self, method: str, path: str, *, json=None, authorized=True, timeout=None):
        headers = {"X-Alex-Protocol-Version": str(self.settings.alex_gateway_protocol_version)}
        if authorized:
            headers["Authorization"] = "Bearer " + await self.token()
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout(timeout), follow_redirects=False, transport=self.transport
            ) as client:
                response = await client.request(method, self.url + path, json=json, headers=headers)
        except httpx.TimeoutException:
            raise CloudError("gateway_unavailable", "Canalla Cloud не ответил вовремя.") from None
        except httpx.HTTPError:
            raise CloudError("gateway_unavailable", "Canalla Cloud сейчас недоступен.") from None
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {}
        return response.status_code, body

    async def request(self, method: str, path: str, *, json=None, timeout=None):
        """One business request. A rejected token is renewed once, never retried twice."""
        status, body = await self._send(method, path, json=json, timeout=timeout)
        if status == 401:
            self.forget_token()
            status, body = await self._send(method, path, json=json, timeout=timeout)
        if status >= 400:
            raise CloudError(map_code(_code_of(body)), _detail_of(body), status)
        if not isinstance(body, dict):
            raise CloudError("malformed_response")
        return body

    # ---------------------------------------------------------------------- operations

    async def health(self) -> dict:
        status, body = await self._send("GET", "/health", authorized=False, timeout=10)
        if status != 200 or not isinstance(body, dict):
            raise CloudError("gateway_unavailable", "Canalla Cloud сейчас недоступен.", status)
        if body.get("gateway_protocol_version") != self.settings.alex_gateway_protocol_version:
            raise CloudError(
                "gateway_protocol_mismatch",
                "Версия Canalla Cloud несовместима с этой установкой Canalla LLM.",
            )
        return body

    async def compute_status(self) -> dict:
        return await self.request("GET", "/compute/status")

    async def ensure_compute(
        self,
        *,
        operation_id: str,
        task_id: str | None = None,
        caps: dict | None = None,
        origin: str | None = None,
    ):
        payload = {"operation_id": operation_id}
        if task_id:
            payload["task_id"] = task_id
        if origin:
            payload["origin"] = origin
        for key, value in (caps or {}).items():
            if value is not None:
                payload[key] = value
        return await self.request("POST", "/compute/ensure", json=payload, timeout=60)

    async def stop_compute(self, *, operation_id: str):
        return await self.request("POST", "/compute/stop", json={"operation_id": operation_id}, timeout=60)

    async def balance(self) -> dict:
        return await self.request("GET", "/balance")

    async def models(self) -> dict:
        return await self.request("GET", "/v1/models", timeout=15)

    async def revoke(self) -> dict:
        return await self.request("POST", "/auth/revoke", json={"reason": "client_disconnect"})

    def stream_headers(self, *, task_id: str | None = None, request_id: str | None = None) -> dict:
        headers = {
            "X-Alex-Protocol-Version": str(self.settings.alex_gateway_protocol_version),
            "Content-Type": "application/json",
        }
        if task_id:
            headers["X-Alex-Task-Id"] = task_id
        if request_id:
            headers["X-Alex-Request-Id"] = request_id
        return headers

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout(), follow_redirects=False, transport=self.transport)


def _code_of(body) -> str | None:
    return body.get("code") if isinstance(body, dict) else None


def _detail_of(body) -> str | None:
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail if isinstance(detail, str) and detail else None
