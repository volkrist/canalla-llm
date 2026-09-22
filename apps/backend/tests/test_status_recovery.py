"""Deterministic status/recovery tests. No supplier traffic leaves the process."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from app.compute.runpod_api import RunPodAPI, RunPodError
from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import Memory, User, now
from app.status import snapshot as snap
from app.status.balance import RunPodBalanceService
from app.tools.models import PairedDevice
from tests.db_helpers import require_row


class Clock:
    def __init__(self, start=None):
        self.value = start or datetime(2026, 9, 21, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class FakeAccountAPI:
    """Stands in for RunPodAPI. `configured` mirrors the real client's property."""

    def __init__(self, key="test-only-fake-key"):
        self.key = key
        self.balance = Decimal("8.73")
        self.failure: RunPodError | None = None
        self.delay = 0.0
        self.calls = 0

    @property
    def configured(self):
        return bool(self.key)

    async def account_balance(self):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failure is not None:
            raise self.failure
        return {"balance": self.balance, "current_spend_per_hr": Decimal("0.12")}


class FakeController:
    def __init__(self, ai, compute_state=None, error_code=None, configured=True, provider="llamacpp"):
        self.ai = ai
        self.compute_state = compute_state or ai
        self.error_code = error_code
        self.provider = provider
        self.api = type("Api", (), {"configured": configured})()

    def llm_public_status(self, user):
        return {
            "provider": self.provider,
            "model": "orcarouter-qwen38-27b-q5km",
            "available": self.ai == "ready",
            "state": self.compute_state,
            "ai": self.ai,
            "ai_label": self.ai,
            "diagnostic": {
                "compute_state": self.compute_state,
                "last_error": self.error_code,
                "managed": True,
                "gpu": "NVIDIA L40S",
                "price_per_hour": 1.09,
                "datacenter": "US-TX-3",
            },
        }


def new_user(email="status@example.com", **fields):
    with SessionLocal() as db:
        user = User(email=email, password_hash="unused", **fields)
        db.add(user)
        db.commit()
        return user.id


def chip(state, action, recoverable):
    return state, action, recoverable


@pytest.mark.parametrize(
    "ai, compute_state, error_code, configured, expected",
    [
        ("off", "offline", None, True, chip("off", None, False)),
        ("starting", "creating", None, True, chip("starting", None, True)),
        ("ready", "ready", None, True, chip("ready", None, False)),
        ("waiting", "generate", None, True, chip("degraded", "retry", True)),
        ("unavailable", "not_configured", None, False, chip("not_configured", "configure", True)),
        ("unavailable", "offline", "no_compatible_gpu", True, chip("unavailable", "retry", True)),
        ("unavailable", "offline", "price_limit", True, chip("unavailable", "configure", True)),
        ("unavailable", "offline", "runpod_timeout", True, chip("unavailable", "retry", True)),
        ("error", "multiple_compute", "multiple_compute", True, chip("error", None, False)),
        ("error", "error", "COMPUTE_BUDGET_REACHED", True, chip("error", "configure", False)),
        ("error", "create_unknown", "create_unknown", True, chip("degraded", "retry", True)),
        ("error", "error", "startup_timeout", True, chip("error", "retry", True)),
        ("error", "error", "runpod_auth", True, chip("error", "configure", False)),
        ("waiting", "stopping", None, True, chip("off", None, False)),
    ],
)
def test_ai_chip_mapping_is_truthful(ai, compute_state, error_code, configured, expected):
    controller = FakeController(ai, compute_state, error_code, configured)
    result = snap.ai_status(controller, User(email="a@example.com", password_hash="x"))
    assert (result["state"], result["action"], result["recoverable"]) == expected
    assert result["message"]
    assert result["state"] in snap.STATES
    assert result["details"]["compute_state"] == compute_state


def test_ai_chip_reports_needs_setup_and_keeps_error_code():
    controller = FakeController("unavailable", "not_configured", None, False)
    result = snap.ai_status(controller, User(email="a@example.com", password_hash="x"))
    assert result["state"] == "not_configured"
    assert result["action"] == "configure"
    assert result["details"]["configured"] is False


def test_computer_chip_uses_device_heartbeat_window():
    user_id = new_user()
    with SessionLocal() as db:
        user = require_row(db, User, user_id)
        prefs = snap.preferences(db, user_id)
        assert snap.computer_status(db, user, prefs)["state"] == "not_configured"
        row = PairedDevice(
            user_id=user_id,
            display_name="Windows device",
            platform="windows",
            capabilities=["fs"],
            credential_hash="0" * 64,
            last_seen=now(),
        )
        db.add(row)
        db.commit()
        ready = snap.computer_status(db, user, prefs)
        assert ready["state"] == "ready"
        assert ready["details"]["device"]["display_name"] == "Windows device"
        row.last_seen = now() - timedelta(seconds=120)
        db.commit()
        offline = snap.computer_status(db, user, prefs)
        assert offline["state"] == "unavailable"
        assert offline["action"] == "reconnect"
        assert offline["detail_code"] == "host_offline"


def test_computer_chip_respects_disabled_mode():
    user_id = new_user(email="computer-off@example.com")
    with SessionLocal() as db:
        user = require_row(db, User, user_id)
        prefs = snap.preferences(db, user_id).model_copy(update={"computer_mode": "off"})
        result = snap.computer_status(db, user, prefs)
    assert result["state"] == "off"
    assert result["action"] == "configure"


def test_web_chip_reports_configuration_state():
    base = get_settings()
    configured = base.model_copy(update={"tinyfish_api_key": SecretStr("tinyfish-test-key")})
    from app.tools.policy import WebSettings

    settings = WebSettings()
    missing = snap.web_status(base, settings)
    assert missing["state"] == "not_configured"
    assert missing["action"] == "configure"
    only_configured = snap.web_status(configured, settings)
    # A configured provider is not a healthy provider: no request has been made.
    assert only_configured["state"] == "configured"
    assert only_configured["state"] != "ready"
    assert only_configured["recoverable"] is False
    assert only_configured["action"] is None
    assert "настроен" in only_configured["message"].lower()
    assert only_configured["details"]["probe"] == "configuration"
    disabled = WebSettings.model_validate(
        {"search_enabled": False, "fetch_enabled": False, "agent_mode": "off", "browser_mode": "off"}
    )
    assert snap.web_status(configured, disabled)["state"] == "off"


@pytest.mark.parametrize("mode, listening", [("auto", True), ("on", True), ("auto", False), ("on", False)])
def test_tor_chip_never_claims_ready_without_a_verified_chain(monkeypatch, mode, listening):
    """An open SOCKS port is not a verified Tor route, and no proof store exists."""
    from app.tools.policy import WebSettings

    monkeypatch.setattr(snap, "socks_listening", lambda *args, **kwargs: listening)
    result = snap.tor_status(get_settings(), WebSettings.model_validate({"tor_mode": mode}))
    assert result["state"] != "ready"
    assert result["details"]["verified_chain"] is False
    assert result["details"]["proof_store"] == "none"
    assert result["details"]["fallback"] == "none"
    if listening:
        assert result["state"] == "configured"
        assert "цепь ещё не проверена" in result["message"]
    else:
        assert result["state"] == "unavailable"
        assert result["action"] == "retry"


@pytest.mark.parametrize("mode, listening, expected", [("off", True, "off"), ("auto", True, "configured")])
def test_tor_chip_is_fail_closed(monkeypatch, mode, listening, expected):
    from app.tools.policy import WebSettings

    monkeypatch.setattr(snap, "socks_listening", lambda *args, **kwargs: listening)
    settings = get_settings()
    prefs = WebSettings.model_validate({"tor_mode": mode})
    assert snap.tor_status(settings, prefs)["state"] == expected


def test_tor_chip_never_reports_ready_without_socks(monkeypatch):
    from app.tools.policy import WebSettings

    monkeypatch.setattr(snap, "socks_listening", lambda *args, **kwargs: False)
    settings = get_settings()
    for mode in ("auto", "on"):
        result = snap.tor_status(settings, WebSettings.model_validate({"tor_mode": mode}))
        assert result["state"] == "unavailable"
        assert result["details"]["fallback"] == "none"
        assert result["details"]["verified_chain"] is False
        assert result["details"]["required"] is (mode == "on")


def test_memory_chip_reflects_user_preference_and_never_downloads():
    user_id = new_user(email="memory@example.com")
    settings = get_settings()
    with SessionLocal() as db:
        user = require_row(db, User, user_id)
        off = snap.memory_status(db, user, settings)
        assert off["state"] == "ready" and off["details"]["items"] == 0
        db.add(Memory(user_id=user_id, category="note", content="hello"))
        db.commit()
        assert snap.memory_status(db, user, settings)["details"]["items"] == 1
        user.use_memory = False
        db.commit()
        disabled = snap.memory_status(db, user, settings)
    assert disabled["state"] == "off"
    assert disabled["details"]["retrieval"] == "lexical"


# --------------------------------------------------------------------------------------
# Shared RunPod balance


def run(coro):
    return asyncio.run(coro)


def test_balance_not_configured_never_calls_upstream():
    api = FakeAccountAPI(key="")
    payload = run(RunPodBalanceService(api).snapshot())
    assert payload["configured"] is False
    assert payload["available"] is False
    assert payload["balance_usd"] is None
    assert payload["error_code"] == "not_configured"
    assert api.calls == 0


def test_balance_success_keeps_decimal_precision():
    api = FakeAccountAPI()
    api.balance = Decimal("8.731234")
    service = RunPodBalanceService(api, clock=Clock())
    payload = run(service.snapshot())
    assert payload["balance_usd"] == "8.731234"
    assert payload["account_spend_per_hr"] == "0.12"
    assert payload["available"] is True and payload["stale"] is False
    assert payload["error_code"] is None
    assert payload["shared_account"] is True and payload["read_only"] is True
    assert isinstance(payload["balance_usd"], str)


def test_balance_cache_is_reused_within_the_interval():
    api = FakeAccountAPI()
    clock = Clock()
    service = RunPodBalanceService(api, clock=clock, idle_interval=15.0)
    run(service.snapshot())
    run(service.snapshot())
    run(service.snapshot())
    assert api.calls == 1
    clock.advance(16)
    run(service.snapshot())
    assert api.calls == 2


def test_balance_interval_follows_active_gpu():
    api = FakeAccountAPI()
    idle = RunPodBalanceService(api, clock=Clock(), active=lambda: False)
    active = RunPodBalanceService(api, clock=Clock(), active=lambda: True)
    assert idle.payload()["refresh_seconds"] == 15.0
    assert active.payload()["refresh_seconds"] == 5.0


def test_balance_refresh_is_single_flight():
    api = FakeAccountAPI()
    api.delay = 0.05
    service = RunPodBalanceService(api, clock=Clock())

    async def scenario():
        return await asyncio.gather(*[service.snapshot() for _ in range(5)])

    payloads = run(scenario())
    assert api.calls == 1
    assert {payload["balance_usd"] for payload in payloads} == {"8.73"}


@pytest.mark.parametrize(
    "code",
    [
        "runpod_timeout",
        "runpod_auth",
        "runpod_rate_limit",
        "runpod_unavailable",
        "malformed_response",
    ],
)
def test_balance_failure_without_snapshot_is_availability_not_zero(code):
    api = FakeAccountAPI()
    api.failure = RunPodError(code, 502)
    service = RunPodBalanceService(api, clock=Clock())
    payload = run(service.snapshot())
    assert payload["error_code"] == code
    assert payload["available"] is False
    assert payload["balance_usd"] is None
    assert payload["stale"] is False
    assert payload["message"]


def test_balance_failure_keeps_last_value_and_marks_stale():
    api = FakeAccountAPI()
    clock = Clock()
    service = RunPodBalanceService(api, clock=clock, idle_interval=15.0)
    assert run(service.snapshot())["balance_usd"] == "8.73"
    api.failure = RunPodError("runpod_timeout", 504)
    clock.advance(16)
    payload = run(service.snapshot())
    assert payload["balance_usd"] == "8.73"
    assert payload["stale"] is True
    assert payload["available"] is True
    assert payload["error_code"] == "runpod_timeout"
    assert payload["last_success_at"] is not None
    # A broken provider is polled at the interval, not once per user request.
    assert api.calls == 2
    run(service.snapshot())
    assert api.calls == 2


def test_balance_recovery_clears_stale():
    api = FakeAccountAPI()
    clock = Clock()
    service = RunPodBalanceService(api, clock=clock, idle_interval=15.0)
    run(service.snapshot())
    api.failure = RunPodError("runpod_unavailable", 502)
    clock.advance(16)
    assert run(service.snapshot())["stale"] is True
    api.failure = None
    api.balance = Decimal("4.50")
    clock.advance(16)
    payload = run(service.snapshot())
    assert payload["stale"] is False and payload["error_code"] is None
    assert payload["balance_usd"] == "4.50"


def test_balance_payload_serializes_as_json():
    api = FakeAccountAPI()
    service = RunPodBalanceService(api, clock=Clock())
    payload = run(service.snapshot())
    text = json.dumps(payload)  # must not raise on Decimal/datetime
    assert "8.73" in text


# --------------------------------------------------------------------------------------
# RunPod client: read-only GraphQL balance


def graphql_client(handler):
    settings = get_settings().model_copy(
        update={
            "runpod_api_key": SecretStr("test-only-fake-key"),
            "runpod_graphql_url": "https://api.runpod.io/graphql",
        }
    )
    return RunPodAPI(settings, httpx.MockTransport(handler))


def test_client_balance_reads_client_balance_only():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"myself": {"id": "u1", "clientBalance": 8.73}}})

    api = graphql_client(handler)
    result = run(api.account_balance())
    assert result["balance"] == Decimal("8.73")
    assert result["current_spend_per_hr"] is None
    assert seen["url"] == "https://api.runpod.io/graphql"
    assert seen["auth"] == "Bearer test-only-fake-key"
    assert "myself" in seen["body"]["query"]
    assert "clientBalance" in seen["body"]["query"]
    # No mutating GraphQL surface is ever used for a balance read.
    for verb in ("mutation", "podRentInterruptable", "podTerminate", "podResume"):
        assert verb not in seen["body"]["query"]


@pytest.mark.parametrize(
    "response, code",
    [
        (httpx.Response(200, json={"data": {"myself": {"id": "u1"}}}), "malformed_response"),
        (httpx.Response(200, json={"data": {"myself": None}}), "malformed_response"),
        (httpx.Response(200, json={"errors": [{"message": "nope"}]}), "malformed_response"),
        (httpx.Response(200, json={"data": {}}), "malformed_response"),
        (httpx.Response(401, json={"detail": "private"}), "runpod_auth"),
        (httpx.Response(403, json={"detail": "private"}), "runpod_auth"),
        (httpx.Response(429, json={}), "runpod_rate_limit"),
        (httpx.Response(500, json={}), "runpod_unavailable"),
    ],
)
def test_client_balance_fails_closed(response, code):
    api = graphql_client(lambda request: response)
    with pytest.raises(RunPodError) as error:
        run(api.account_balance())
    assert error.value.code == code
    assert "private" not in str(error.value)


def test_client_balance_timeout_is_reported_not_guessed():
    def handler(request):
        raise httpx.ReadTimeout("upstream secret must never be exposed")

    api = graphql_client(handler)
    with pytest.raises(RunPodError) as error:
        run(api.account_balance())
    assert error.value.code == "runpod_timeout"
    assert "secret" not in str(error.value)


def test_client_balance_requires_a_configured_key():
    settings = get_settings().model_copy(update={"runpod_api_key": SecretStr("")})
    api = RunPodAPI(settings, httpx.MockTransport(lambda request: httpx.Response(500)))
    with pytest.raises(RunPodError) as error:
        run(api.account_balance())
    assert error.value.code == "not_configured"


# --------------------------------------------------------------------------------------
# API surface


@pytest.fixture
def fake_balance():
    api = FakeAccountAPI()
    clock = Clock()
    service = RunPodBalanceService(
        api, active=lambda: False, clock=clock, active_interval=5.0, idle_interval=15.0
    )
    app.state.balance_override = service
    yield service, api, clock
    app.state.balance_override = None


def test_status_requires_authentication(client):
    assert client.get("/status").status_code == 401


def test_status_exposes_five_chips_and_shared_balance(client, auth, fake_balance):
    headers = auth("chips@example.com")
    response = client.get("/status", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body["subsystems"]) == {"ai", "computer", "web", "tor", "memory"}
    for name, value in body["subsystems"].items():
        assert value["state"] in snap.STATES, name
        assert isinstance(value["message"], str) and value["message"]
        assert isinstance(value["recoverable"], bool)
        assert "balance" not in json.dumps(value["details"])
    assert body["balance"]["balance_usd"] == "8.73"
    assert body["balance"]["configured"] is True
    assert body["generated_at"]


def test_status_balance_is_identical_for_every_user_and_uses_one_upstream_call(client, auth, fake_balance):
    service, api, _clock = fake_balance
    first = client.get("/status", headers=auth("user-a@example.com"))
    second = client.get("/status", headers=auth("user-b@example.com"))
    assert first.status_code == second.status_code == 200
    # One installation-global provider credential serves every local user: both see
    # `configured` and exactly the same shared account balance, with one supplier read.
    assert first.json()["balance"]["configured"] is True
    assert second.json()["balance"]["configured"] is True
    assert first.json()["balance"]["balance_usd"] == second.json()["balance"]["balance_usd"] == "8.73"
    assert api.calls == 1


def test_provider_credential_is_shared_not_per_user(client, auth, fake_balance):
    _service, api, _clock = fake_balance
    seen = []
    user_scoped_flags = []
    for header in (auth("shared-a@example.com"), auth("shared-b@example.com")):
        payload = client.get("/status", headers=header).json()
        seen.append((payload["balance"]["configured"], payload["balance"]["balance_usd"]))
        # Session identity differs per user; the provider configuration does not.
        user_scoped_flags.append(payload["subsystems"]["ai"]["details"]["configured"])
    assert seen[0] == seen[1] == (True, "8.73")
    assert user_scoped_flags[0] == user_scoped_flags[1]
    assert api.calls == 1


def test_no_provider_credential_configures_nobody(client, auth):
    """Without a credential every user is honestly not configured, and no supplier
    call is made (there is nothing to read a balance with)."""
    for email in ("nokey-a@example.com", "nokey-b@example.com"):
        payload = client.get("/status", headers=auth(email)).json()
        assert payload["balance"]["configured"] is False
        assert payload["balance"]["available"] is False
        assert payload["balance"]["balance_usd"] is None
        assert payload["balance"]["error_code"] == "not_configured"
        assert payload["subsystems"]["ai"]["details"]["configured"] is False


def test_status_never_leaks_the_provider_key(client, auth, fake_balance):
    response = client.get("/status", headers=auth("secrets@example.com"))
    text = response.text
    for needle in ("test-only-fake-key", "api_key", "runpod_api_key", "Authorization"):
        assert needle not in text


def test_status_never_starts_compute(monkeypatch, client, auth, fake_balance):
    compute = app.state.compute

    async def forbidden(*args, **kwargs):
        raise AssertionError("status must never start or stop compute")

    for name in ("start_compute", "search_gpu", "_ensure_on_demand_locked", "stop_compute"):
        monkeypatch.setattr(compute, name, forbidden)
    monkeypatch.setattr(compute.api, "create_pod", forbidden)
    response = client.get("/status", headers=auth("gpu@example.com"))
    assert response.status_code == 200
    assert response.json()["subsystems"]["ai"]["state"] in snap.STATES


def test_status_ai_chip_follows_mock_provider_semantics(client, auth, fake_balance):
    """The test provider is `mock` in a non-production environment, which is ready by design."""
    body = client.get("/status", headers=auth("mock@example.com")).json()
    ai = body["subsystems"]["ai"]
    assert ai["state"] == "ready"
    assert ai["details"]["provider"] == "mock"
    assert ai["details"]["configured"] is False
