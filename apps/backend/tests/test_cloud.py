"""Client-side Canalla Cloud integration (shared mode).

The Gateway is doubled with ``httpx.MockTransport``: no test here can reach a real Gateway
or create paid compute. What is verified is the *client contract* — one short-lived token
in memory, inference and balance going through the Gateway, local compute refusing to act
in shared mode, and the five-chip vocabulary staying honest.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from app.cloud.client import CloudError, GatewayClient, map_code
from app.cloud.provider import GatewayBalanceSource, GatewayProvider
from app.cloud.state import CloudAi, CloudState
from app.config import get_settings
from app.models import User
from app.status.balance import RunPodBalanceService
from app.status.snapshot import ai_status


def run(coro):
    return asyncio.run(coro)


def stub_user(**fields) -> User:
    """A stand-in user for the status/compute calls; nothing here is persisted."""
    values = {"id": "u1", "email": "stub@example.com", "password_hash": "unused", "role": "user"}
    return User(**{**values, **fields})


def sse(payload: dict) -> bytes:
    return ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")


class FakeGateway:
    def __init__(self, *, protocol=1, compute=None, balance="7.77"):
        self.protocol = protocol
        self.balance = balance
        self.compute = compute or {
            "state": "offline",
            "ai": "off",
            "ai_label": "AI Off",
            "revision": 1,
            "error_code": None,
            "configured": True,
            "managed": False,
            "queue": {"depth": 0, "active": 0},
            "session": None,
            "idle_deadline": None,
        }
        self.token_calls = 0
        self.compute_calls = 0
        self.balance_calls = 0
        self.models_calls = 0
        self.chat_calls = 0
        self.rejects_token = False
        self.fail_status = None
        self.models_status = None
        self.time = datetime(2026, 9, 21, tzinfo=timezone.utc)
        self.token_ttl = 900
        self.stream_closed = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(
                200,
                json={
                    "product": "alex-llm-gateway",
                    "version": "1.2.0",
                    "gateway_protocol_version": self.protocol,
                    "ready": True,
                    "database": "ok",
                    "provider_configured": True,
                    "time": self.time.isoformat(),
                },
            )
        if path == "/auth/token":
            self.token_calls += 1
            body = json.loads(request.content)
            if body.get("installation_secret") != "installation-secret-" + "s" * 24:
                return httpx.Response(401, json={"detail": "nope", "code": "gateway_auth_failed"})
            return httpx.Response(
                200,
                json={
                    "access_token": f"gateway-token-{self.token_calls}",
                    "token_type": "bearer",
                    "expires_in": self.token_ttl,
                    "installation_id": body["installation_id"],
                    "gateway_protocol_version": self.protocol,
                },
            )
        if path == "/auth/revoke":
            return httpx.Response(200, json={"revoked": True})
        if self.rejects_token or request.headers.get("Authorization") == "Bearer gateway-token-expired":
            return httpx.Response(401, json={"detail": "expired", "code": "gateway_auth_failed"})
        if not request.headers.get("Authorization", "").startswith("Bearer gateway-token-"):
            return httpx.Response(401, json={"detail": "missing", "code": "gateway_auth_failed"})
        if self.fail_status is not None and path != "/health":
            return httpx.Response(self.fail_status, json={"detail": "gateway detail", "code": "gateway_busy"})
        if path == "/compute/status":
            self.compute_calls += 1
            return httpx.Response(200, json=self.compute)
        if path == "/balance":
            self.balance_calls += 1
            return httpx.Response(
                200,
                json={
                    "configured": True,
                    "available": True,
                    "balance_usd": self.balance,
                    "account_spend_per_hr": "0.79",
                    "stale": False,
                    "read_only": True,
                    "shared_account": True,
                },
            )
        if path == "/v1/models":
            self.models_calls += 1
            if self.models_status is not None:
                return httpx.Response(self.models_status, json={"detail": "upstream", "code": "gateway_busy"})
            return httpx.Response(200, json={"data": [{"id": "orcarouter-qwen38-27b-q5km"}]})
        if path == "/v1/chat/completions":
            self.chat_calls += 1
            body = json.loads(request.content)
            if not body.get("stream"):
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"role": "assistant", "content": "Привет из облака"}}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
                    },
                )
            owner = self

            async def events():
                try:
                    yield sse({"choices": [{"delta": {"content": "Привет"}}]})
                    yield sse({"choices": [{"delta": {"content": ", облако"}}]})
                    yield sse({"usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}})
                    yield b"data: [DONE]\n\n"
                finally:
                    owner.stream_closed = True

            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=events())
        raise AssertionError(f"unexpected gateway path {path}")


@pytest.fixture
def gateway():
    return FakeGateway()


def shared_settings(gateway_url="https://gateway.example", **extra):
    values = {
        "alex_ai_mode": "shared",
        "alex_gateway_url": gateway_url,
        "alex_gateway_installation_id": "11111111-2222-3333-4444-555555555555",
        "alex_gateway_installation_secret": SecretStr("installation-secret-" + "s" * 24),
        "gateway_status_idle_seconds": 15,
        "gateway_status_active_seconds": 5,
    }
    values.update(extra)
    return get_settings().model_copy(update=values)


def client_for(gateway, **extra):
    settings = shared_settings(**extra)
    client = GatewayClient(settings, transport=httpx.MockTransport(gateway.handle))
    client.clock = lambda: gateway.time
    return client


# --------------------------------------------------------------------------------- config


def test_remote_plaintext_gateway_is_rejected():
    with pytest.raises(ValueError):
        get_settings().model_copy(
            update={
                "alex_ai_mode": "shared",
                "alex_gateway_url": "http://gateway.example",
                "alex_gateway_installation_secret": SecretStr("installation-secret-" + "s" * 24),
            }
        ).model_validate(
            {
                **get_settings().model_dump(),
                "alex_ai_mode": "shared",
                "alex_gateway_url": "http://gateway.example",
            }
        )


def test_loopback_plaintext_gateway_is_allowed_for_development():
    settings = get_settings().model_copy(
        update={
            "alex_ai_mode": "shared",
            "alex_gateway_url": "http://127.0.0.1:9000",
            "alex_gateway_installation_id": "id-1",
            "alex_gateway_installation_secret": SecretStr("installation-secret-" + "s" * 24),
        }
    )
    # Re-validating the same values must not raise for a loopback Gateway.
    from app.config import Settings

    assert Settings(**settings.model_dump()) is not None


def test_shared_mode_without_a_url_reports_not_connected():
    """A production install is shared by default and may not be enrolled yet.

    That state must boot and say "Canalla Cloud не подключён" instead of failing to start or
    silently falling back to a local provider credential.
    """
    from app.config import Settings

    payload = get_settings().model_dump()
    payload["alex_ai_mode"] = "shared"
    payload["alex_gateway_url"] = ""
    settings = Settings(**payload)
    assert settings.alex_ai_mode == "shared"
    client = GatewayClient(settings)
    assert client.configured is False
    state = CloudState(settings, client=client)

    assert state.snapshot()["state"] == "not_connected"
    chip = ai_status(CloudAi(state), stub_user())
    assert chip["state"] == "not_configured"
    assert "Canalla Cloud" in chip["message"]
    run(state.refresh(force=True))
    assert state.snapshot()["detail_code"] == "gateway_not_connected"


# --------------------------------------------------------------------------------- tokens


def test_one_token_serves_many_requests(gateway):
    client = client_for(gateway)
    state = CloudState(client.settings, client=client, clock=lambda: gateway.time)
    run(state.refresh(force=True))
    run(state.refresh(force=True))
    assert gateway.token_calls == 1
    assert state.state() == "connected"
    assert gateway.compute_calls == 2


def test_token_is_renewed_after_expiry(gateway):
    client = client_for(gateway)
    run(client.compute_status())
    assert gateway.token_calls == 1
    gateway.time += timedelta(minutes=20)
    run(client.compute_status())
    assert gateway.token_calls == 2


def test_rejected_token_is_renewed_once(gateway):
    client = client_for(gateway)
    run(client.compute_status())
    gateway.rejects_token = True
    with pytest.raises(CloudError):
        run(client.compute_status())
    # Exactly one renewal attempt: a rejected token is not retried in a loop.
    assert gateway.token_calls == 2
    gateway.rejects_token = False
    client.forget_token()
    assert run(client.compute_status())["state"] == "offline"


def test_token_failure_is_reported_as_not_connected_or_auth_failure(gateway):
    settings = shared_settings()
    broken = GatewayClient(
        settings.model_copy(update={"alex_gateway_installation_secret": SecretStr("wrong-" + "w" * 30)}),
        transport=httpx.MockTransport(gateway.handle),
    )
    with pytest.raises(CloudError) as error:
        run(broken.compute_status())
    assert error.value.code == "gateway_auth_failed"


def test_unknown_installation_is_reported_as_revoked():
    assert map_code("installation_unknown") == "installation_revoked"
    assert map_code("gateway_rate_limited") == "gateway_busy"
    assert map_code(None) == "gateway_unavailable"


# -------------------------------------------------------------------------------- balance


def test_balance_comes_from_the_gateway_as_decimal(gateway):
    source = GatewayBalanceSource(client_for(gateway))
    assert source.configured is True
    payload = run(source.account_balance())
    assert payload["balance"] == Decimal("7.77")
    assert payload["current_spend_per_hr"] == Decimal("0.79")
    assert gateway.balance_calls == 1


def test_shared_balance_keeps_the_existing_cache_and_stale_semantics(gateway):
    source = GatewayBalanceSource(client_for(gateway))
    service = RunPodBalanceService(
        source, active=lambda: False, session=lambda: None, clock=lambda: gateway.time
    )
    first = run(service.snapshot())
    assert first["balance_usd"] == "7.77"
    assert first["shared_account"] is True
    assert gateway.balance_calls == 1
    run(service.snapshot())
    assert gateway.balance_calls == 1  # cached inside the client TTL

    gateway.fail_status = 503
    gateway.time += timedelta(seconds=20)
    stale = run(service.snapshot())
    assert stale["balance_usd"] == "7.77"  # last known value is preserved
    assert stale["stale"] is True
    assert stale["error_code"] == "gateway_unavailable"


def test_balance_is_not_faked_when_the_gateway_has_no_value(gateway):
    client = GatewayClient(
        shared_settings(alex_gateway_url="http://127.0.0.1:9000"),
        transport=httpx.MockTransport(gateway.handle),
    )
    client.forget_token()
    gateway.fail_status = 503
    source = GatewayBalanceSource(client)
    service = RunPodBalanceService(source, clock=lambda: gateway.time)
    payload = run(service.snapshot())
    assert payload["available"] is False
    assert payload["balance_usd"] is None
    assert payload["error_code"] == "gateway_unavailable"


# ------------------------------------------------------------------------------- provider


def test_streaming_inference_goes_through_the_gateway(gateway):
    provider = GatewayProvider(shared_settings(), client_for(gateway))

    async def scenario():
        return [part async for part in provider.stream_chat([{"role": "user", "content": "привет"}])]

    parts = run(scenario())
    assert "".join(parts) == "Привет, облако"
    assert gateway.chat_calls == 1
    assert gateway.stream_closed is True


def test_usage_is_reported_from_the_gateway_stream(gateway):
    provider = GatewayProvider(shared_settings(), client_for(gateway))
    usage: dict = {}

    async def scenario():
        return [part async for part in provider.stream_with_usage([{"role": "user", "content": "x"}], usage)]

    run(scenario())
    assert usage == {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}


def test_provider_maps_gateway_failures_to_user_codes(gateway):
    from app.providers import LLMError

    provider = GatewayProvider(shared_settings(), client_for(gateway))
    gateway.fail_status = 429

    async def scenario():
        return [part async for part in provider.stream_chat([{"role": "user", "content": "x"}])]

    with pytest.raises(LLMError) as error:
        run(scenario())
    assert error.value.code == "gateway_busy"


def test_provider_health_requires_the_configured_alias(gateway):
    provider = GatewayProvider(shared_settings(), client_for(gateway))
    assert run(provider.health()) is True
    other_settings = shared_settings(llm_model="another-model")
    other = GatewayProvider(
        other_settings, GatewayClient(other_settings, transport=httpx.MockTransport(gateway.handle))
    )
    assert run(other.health()) is False


def test_no_provider_credential_is_needed_on_the_client(gateway):
    """Shared mode works with an empty RunPod key: the master key is server-side."""
    settings = shared_settings()
    assert settings.runpod_api_key.get_secret_value() == ""
    provider = GatewayProvider(settings, client_for(gateway))
    assert run(provider.health()) is True


def test_provider_health_is_cached_instead_of_hot_looping(gateway):
    """`/health` is the desktop's liveness contract (400 ms) and the UI polls it.

    Probing the Gateway on every call made the local server answer slower than that
    budget, so the installed app never became ready and the Gateway saw a hot loop.
    Readiness is therefore cached, and a stale answer is served instead of waiting.
    """
    provider = GatewayProvider(shared_settings(), client_for(gateway))

    async def scenario() -> bool:
        assert await provider.health() is True  # the first caller waits for the real answer
        probes = gateway.models_calls
        for _ in range(20):
            assert await provider.health() is True
        cached = gateway.models_calls == probes

        # A Gateway that starts failing does not make `/health` slow: the stale answer is
        # served immediately and the background probe corrects it on the next call.
        gateway.models_status = 500
        provider.READY_TTL_SECONDS = 0.0
        assert await provider.health() is True
        await asyncio.sleep(0.2)
        assert await provider.health() is False
        gateway.models_status = None
        assert await provider.health() is False
        await asyncio.sleep(0.2)
        assert await provider.health() is True
        return cached

    assert run(scenario()) is True


def test_provider_health_never_raises_into_the_health_endpoint(gateway):
    """`/health` must stay a liveness signal even when every Gateway call fails."""
    provider = GatewayProvider(shared_settings(), client_for(gateway))
    gateway.rejects_token = True
    gateway.fail_status = 503
    assert run(provider.health()) is False
    assert run(provider.health()) is False


# ---------------------------------------------------------------------------- status/chip


def test_cloud_state_reports_connected_and_reads_only(gateway):
    client = client_for(gateway)
    state = CloudState(client.settings, client=client, clock=lambda: gateway.time)
    run(state.refresh(force=True))
    snapshot = state.snapshot()
    assert snapshot["state"] == "connected"
    assert snapshot["mode"] == "shared"
    assert snapshot["enrolled"] is True
    assert snapshot["balance_source"] == "gateway"
    assert snapshot["details"]["read_only"] is True
    assert "pod-" not in json.dumps(snapshot)


def test_cloud_state_reports_protocol_mismatch(gateway):
    gateway.protocol = 9
    client = client_for(gateway)
    state = CloudState(client.settings, client=client, clock=lambda: gateway.time)
    run(state.refresh(force=True))
    assert state.snapshot()["state"] == "protocol_mismatch"


def test_cloud_state_reports_unavailable_without_inventing_a_value(gateway):
    gateway.fail_status = 503
    client = client_for(gateway)
    state = CloudState(client.settings, client=client, clock=lambda: gateway.time)
    run(state.refresh(force=True))
    snapshot = state.snapshot()
    assert snapshot["state"] == "unavailable"
    assert snapshot["reachable"] is False
    assert snapshot["recoverable"] is True


def test_revoked_installation_is_reported_as_revoked(gateway):
    client = client_for(gateway)
    state = CloudState(client.settings, client=client, clock=lambda: gateway.time)
    gateway.rejects_token = True
    client._token = "gateway-token-expired"
    client._token_expires = gateway.time + timedelta(minutes=10)
    run(state.refresh(force=True))
    assert state.snapshot()["state"] in {"unavailable", "revoked"}


@pytest.mark.parametrize(
    "compute_state,compact,expected",
    [
        ({"state": "offline", "ai": "off"}, "off", "off"),
        ({"state": "loading_model", "ai": "starting"}, "starting", "starting"),
        ({"state": "ready", "ai": "ready"}, "ready", "ready"),
        ({"state": "create_unknown", "ai": "waiting", "error_code": "create_unknown"}, "waiting", "degraded"),
        ({"state": "multiple_compute", "ai": "error", "error_code": "multiple_compute"}, "error", "error"),
        ({"state": "external_compute", "ai": "waiting"}, "waiting", "degraded"),
    ],
)
def test_shared_mode_reuses_the_five_chip_vocabulary(gateway, compute_state, compact, expected):
    gateway.compute = {
        "state": compute_state["state"],
        "ai": compute_state["ai"],
        "ai_label": "label",
        "revision": 2,
        "error_code": compute_state.get("error_code"),
        "managed": True,
        "queue": {"depth": 0, "active": 0},
        "session": None,
        "idle_deadline": None,
    }
    client = client_for(gateway)
    state = CloudState(client.settings, client=client, clock=lambda: gateway.time)
    run(state.refresh(force=True))
    chip = ai_status(CloudAi(state), stub_user(use_memory=False))
    assert chip["state"] == expected
    assert chip["details"]["compute_state"] == compute_state["state"]


def test_gateway_outage_is_honest_in_the_chip(gateway):
    gateway.fail_status = 503
    client = client_for(gateway)
    state = CloudState(client.settings, client=client, clock=lambda: gateway.time)
    run(state.refresh(force=True))

    chip = ai_status(CloudAi(state), stub_user())
    assert chip["state"] == "unavailable"
    assert chip["recoverable"] is True
    assert chip["details"]["configured"] is True
    assert "RunPod" not in chip["message"]


def test_not_enrolled_installation_says_cloud_not_connected(gateway):
    settings = shared_settings(
        alex_gateway_installation_id="",
        alex_gateway_installation_secret=SecretStr(""),
    )
    client = GatewayClient(settings, transport=httpx.MockTransport(gateway.handle))
    state = CloudState(settings, client=client, clock=lambda: gateway.time)
    run(state.refresh(force=True))
    assert state.snapshot()["state"] == "not_connected"

    chip = ai_status(CloudAi(state), stub_user())
    assert chip["state"] == "not_configured"
    assert "Canalla Cloud" in chip["message"]


# ------------------------------------------------------------------- local compute guard


def test_shared_mode_refuses_local_compute_lifecycle():
    from app.compute.controller import RunPodController
    from app.compute.runpod_api import RunPodError
    from app.compute.schemas import StartRequest

    settings = shared_settings()
    controller = RunPodController(settings)
    with pytest.raises(RunPodError) as error:
        # The shared-mode guard fires before the request body is read.
        run(
            controller.start_compute(
                stub_user(),
                StartRequest(
                    quote_id="00000000-0000-0000-0000-000000000000",
                    gpu_id="gpu-48",
                    idempotency_key="test-idempotency-key",
                    confirmed=True,
                ),
            )
        )
    assert error.value.code == "gateway_managed_compute"

    result = run(controller.ensure_on_demand(stub_user(role="admin")))
    assert result["kind"] in {"unavailable", "waiting"}
    assert result["code"] == "gateway_managed_compute"


def test_shared_mode_refuses_the_local_compute_routes(gateway):
    """The route guard refuses before any controller is touched.

    Driven through ``ASGITransport`` (no lifespan) so this test cannot leave the shared
    application in shared mode for the rest of the suite.
    """
    import httpx

    from app.main import app

    settings = get_settings()
    original = settings.alex_ai_mode
    settings.alex_ai_mode = "shared"
    try:

        async def scenario():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://alex.test") as api:
                registered = await api.post(
                    "/auth/register", json={"email": "cloud@example.com", "password": "test-password-123"}
                )
                headers = {"Authorization": "Bearer " + registered.json()["access_token"]}
                start = await api.post(
                    "/compute/start",
                    json={
                        "quote_id": "x" * 36,
                        "gpu_id": "NVIDIA L40S",
                        "idempotency_key": "k" * 16,
                        "confirmed": True,
                    },
                    headers=headers,
                )
                stop = await api.post("/compute/stop", json={}, headers=headers)
                search = await api.post("/compute/search", json={}, headers=headers)
                return start, stop, search

        start, stop, search = run(scenario())
        for response in (start, stop, search):
            assert response.status_code == 409, response.text
            assert "Canalla Cloud" in response.json()["detail"]
    finally:
        settings.alex_ai_mode = original
