# ruff: noqa: F811
"""Finding A and Finding B, deterministically.

Finding A — the badge. An installed client sat in amber «Connecting… Ищем GPU» for 481 s and
still ~23 minutes later while no managed compute session, no Pod and no operation existed: the
Gateway kept answering `GET /compute/status → searching` and the client rendered it as a
transition. Amber is only honest while a *real, bounded* operation is in flight.

Finding B — the chat. A model-needing message never performed the logical equivalent of
`POST /compute/ensure`: the only path to it was the Settings button, so the user had to leave
the chat, press it and resend. The chat must ensure compute itself, bounded, and then execute
the original request exactly once.

Everything here runs against `httpx.MockTransport` fakes and a deterministic clock that only
moves when the code sleeps: no test can reach a real Gateway, a provider, or a GPU.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from test_cloud import FakeGateway, client_for, gateway, shared_settings, stub_user  # noqa: F401

from app.cloud.demand import (
    CAPACITY_WINDOW_SECONDS,
    MAX_SEARCH_ATTEMPTS,
    SharedDemand,
    capacity_code,
    outcome_of,
)
from app.cloud.state import CloudAi, CloudState, search_active
from app.compute.runtime import compact_ai
from app.config import get_settings
from app.status.snapshot import ai_status

# The fake Gateway's own clock (`FakeGateway.time`): every deadline in these tests is relative
# to the clock the code under test actually reads.
NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


class Clock:
    """A deterministic clock: it only moves when the code under test sleeps."""

    def __init__(self, start=NOW):
        self.now = start

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += timedelta(seconds=max(0.0, float(seconds)))
        await asyncio.sleep(0)


def compute_payload(
    state,
    *,
    ai=None,
    error_code=None,
    search=None,
    session=None,
    startup_deadline=None,
    **extra,
):
    return {
        "state": state,
        "ai": ai,
        "ai_label": None,
        "revision": 3,
        "error_code": error_code,
        "configured": True,
        "managed": True,
        "adopted": False,
        "queue": {"depth": 0, "active": 0},
        "session": session,
        "last_session": None,
        "idle_deadline": None,
        "startup_deadline": startup_deadline,
        "search": search,
        **extra,
    }


def search_window(*, started, active=True, timeout=CAPACITY_WINDOW_SECONDS):
    return {
        "operation_id": "op-1",
        "started_at": started.isoformat(),
        "deadline": (started + timedelta(seconds=timeout)).isoformat(),
        "timeout_seconds": timeout,
        "active": active,
        "reason": "gpu_unavailable",
    }


def cloud_for(gateway, *, clock=None):
    client = client_for(gateway)
    at = clock or (lambda: gateway.time)
    client.clock = at
    return CloudState(client.settings, client=client, clock=at)


def chip_for(cloud):
    return ai_status(CloudAi(cloud), stub_user(use_memory=False))


def refreshed(gateway, *, clock=None):
    cloud = cloud_for(gateway, clock=clock)
    run(cloud.refresh(force=True))
    return cloud


# --------------------------------------------------------------------- Finding A: the badge


def test_no_managed_session_is_disconnected(gateway):
    gateway.compute = compute_payload("offline", ai="off")
    chip = chip_for(refreshed(gateway))

    assert chip["state"] == "off"
    assert chip["state"] != "starting"


def test_a_terminated_pod_is_disconnected(gateway):
    gateway.compute = compute_payload("stopped", ai="off")
    assert chip_for(refreshed(gateway))["state"] == "off"


def test_a_stale_searching_gateway_is_never_connecting(gateway):
    """Exactly the production symptom: `searching` with no operation behind it at all."""
    gateway.compute = compute_payload("searching", ai="starting", error_code="gpu_unavailable", search=None)
    chip = chip_for(refreshed(gateway))

    assert chip["state"] == "unavailable"
    assert chip["detail_code"] == "gpu_unavailable"
    assert "GPU" in chip["message"]
    assert chip["recoverable"] is True
    assert chip["details"]["compute_search_active"] is False
    assert chip["details"]["compact_ai"] == "unavailable"


def test_an_expired_search_is_not_connecting(gateway):
    gateway.compute = compute_payload(
        "searching",
        ai="starting",
        error_code="gpu_unavailable",
        search=search_window(started=NOW - timedelta(minutes=23)),
    )
    chip = chip_for(refreshed(gateway))

    assert chip["state"] == "unavailable"
    assert chip["details"]["compute_search_active"] is False


def test_a_live_bounded_search_is_connecting(gateway):
    window = search_window(started=NOW)
    gateway.compute = compute_payload("searching", ai="starting", error_code="gpu_unavailable", search=window)
    chip = chip_for(refreshed(gateway))

    assert chip["state"] == "starting"
    assert chip["details"]["compute_search_active"] is True
    assert chip["details"]["compute_search_deadline"] == window["deadline"]


@pytest.mark.parametrize(
    "compute,expected",
    [
        ("creating", "starting"),
        ("starting_pod", "starting"),
        ("loading_model", "starting"),
        ("ready", "ready"),
        ("generating", "ready"),
        ("stopped", "off"),
        ("offline", "off"),
    ],
)
def test_every_real_stage_keeps_its_own_state(gateway, compute, expected):
    payloads = {
        "creating": compute_payload("creating", ai="starting"),
        "starting_pod": compute_payload("starting_pod", ai="starting"),
        "loading_model": compute_payload("loading_model", ai="starting"),
        "ready": compute_payload("ready", ai="ready"),
        "generating": compute_payload("generating", ai="ready"),
        "stopped": compute_payload("stopped", ai="off"),
        "offline": compute_payload("offline", ai="off"),
    }
    gateway.compute = payloads[compute]
    assert chip_for(refreshed(gateway))["state"] == expected


def test_a_provider_error_is_disconnected_with_its_reason(gateway):
    gateway.compute = compute_payload("error", ai="error", error_code="startup_failed")
    chip = chip_for(refreshed(gateway))

    assert chip["state"] == "error"
    assert chip["detail_code"] == "startup_failed"
    assert chip["recoverable"] is True


@pytest.mark.parametrize(
    "state,code,active,expected",
    [
        # A live bounded capacity search is a transition; a dead one is not.
        ("searching", "gpu_unavailable", True, "starting"),
        ("searching", "gpu_unavailable", False, "unavailable"),
        # A catalogue conclusion never becomes a transition by waiting.
        ("searching", "price_limit", True, "unavailable"),
        ("searching", "no_compatible_gpu", True, "unavailable"),
        # The collapsed search: over, with its typed reason kept.
        ("offline", "gpu_unavailable", False, "unavailable"),
        ("error", "gpu_unavailable", False, "unavailable"),
        # Unrelated states keep their own meaning.
        ("creating", None, False, "starting"),
        ("starting_pod", None, False, "starting"),
        ("loading_model", None, False, "starting"),
        ("ready", None, False, "ready"),
        ("offline", None, False, "off"),
        ("stopped", None, False, "off"),
        ("error", "runpod_unavailable", False, "error"),
    ],
)
def test_the_shared_mapping_only_calls_a_transition_a_transition(state, code, active, expected):
    ai, _ = compact_ai(
        provider="llamacpp",
        app_env="production",
        configured=True,
        compute_state=state,
        error_code=code,
        search_active=active,
    )
    assert ai == expected


def test_search_active_needs_both_an_identity_and_a_live_deadline():
    live = search_window(started=NOW)
    assert search_active({"state": "searching", "search": live}, at=NOW) is True
    # No operation at all, an explicitly inactive one, and an expired deadline are all "no".
    assert search_active({"state": "searching", "search": None}, at=NOW) is False
    assert search_active({"state": "searching", "search": {**live, "operation_id": None}}, at=NOW) is False
    assert search_active({"state": "searching", "search": {**live, "active": False}}, at=NOW) is False
    assert search_active({"state": "searching", "search": live}, at=NOW + timedelta(minutes=5)) is False
    assert search_active({"state": "searching"}, at=NOW) is False


def test_capacity_reasons_are_typed():
    assert capacity_code({"error_code": "gpu_unavailable"}) == "gpu_capacity_unavailable"
    assert capacity_code({}) == "gpu_capacity_unavailable"
    assert capacity_code({"error_code": "price_limit"}) == "price_limit"


# -------------------------------------------------------------- Finding B: the chat path


class Wiring:
    """The shared-mode installation under test: a fake Gateway, a moving clock, no network."""

    def __init__(self, gateway, cloud, demand, clock):
        self.gateway = gateway
        self.cloud = cloud
        self.demand = demand
        self.clock = clock


@pytest.fixture
def shared_demand(gateway):
    """A shared-mode cloud plus the one lifecycle, on a deterministic clock."""
    clock = Clock()
    cloud = cloud_for(gateway, clock=clock)
    return Wiring(gateway, cloud, SharedDemand(cloud, sleep=clock.sleep, clock=clock), clock)


@pytest.fixture
def shared_app(monkeypatch, shared_demand):
    """A real app in shared mode, wired to the fake Gateway. The chat path is real.

    The shared-mode lifespan installs process-wide state (the Gateway provider, the Cloud
    adapter), exactly as it does in production. This fixture therefore restores what it knows the
    lifespan overwrites, so no later test can inherit a shared-mode provider with a fake
    transport — the same care ``test_shared_mode_refuses_the_local_compute_routes`` takes.
    """
    from app.main import app

    settings = get_settings()
    monkeypatch.setattr(settings, "alex_ai_mode", "shared")
    monkeypatch.setattr(settings, "llm_provider", "llamacpp")
    monkeypatch.setattr(settings, "llm_connection_mode", "runpod")
    monkeypatch.setattr(app.state, "cloud_override", shared_demand.cloud, raising=False)
    monkeypatch.setattr(app.state, "cloud_demand_override", shared_demand.demand, raising=False)
    monkeypatch.setattr(app.state, "provider_name", "llamacpp")
    previous = {
        name: getattr(app.state, name, None)
        for name in ("provider", "ai_status_source", "cloud", "cloud_demand", "balance")
    }
    try:
        yield shared_demand
    finally:
        for name, value in previous.items():
            setattr(app.state, name, value)


def start_chat(client, headers, content="Привет, посчитай 2+2"):
    """Create a chat, then stream one message into it."""
    created = client.post("/chats", json={}, headers=headers)
    assert created.status_code == 201, created.text
    chat_id = created.json()["id"]
    response = client.post(f"/chats/{chat_id}/stream", json={"content": content}, headers=headers)
    assert response.status_code == 200, response.text
    return chat_id, response


class AttemptCounter:
    """Counts *attempts*, which is what "one lifecycle" means: two would be two searches."""

    def __init__(self):
        self.count = 0


def count_attempts(demand):
    counter = AttemptCounter()
    original = demand._attempt

    async def spy(*args, **kwargs):
        counter.count += 1
        return await original(*args, **kwargs)

    demand._attempt = spy
    return counter


def messages(client, headers, chat_id):
    body = client.get(f"/chats/{chat_id}/messages", headers=headers)
    assert body.status_code == 200, body.text
    return body.json()


def generations(chat_id):
    """One row per billed generation: the count is what "executed twice" would show up as."""
    from sqlalchemy import select

    from app.compute.models import GenerationUsage
    from app.database import SessionLocal

    with SessionLocal() as db:
        return list(db.scalars(select(GenerationUsage).where(GenerationUsage.chat_id == chat_id)))


def sse_events(text):
    events = []
    for frame in text.split("\n\n"):
        if "event:" not in frame:
            continue
        name = frame.split("event:", 1)[1].split("\n", 1)[0].strip()
        payload = [line for line in frame.split("\n") if line.startswith("data:")]
        if payload:
            events.append((name, json.loads(payload[0][5:].strip())))
    return events


def test_a_chat_with_no_model_ensures_once_and_then_generates(shared_app, client, auth):
    """The request starts the model itself: one bounded ensure, then one generation."""
    headers = auth()
    shared_app.gateway.ensure_results = [compute_payload("starting_pod", ai="starting")]
    shared_app.gateway.compute_steps = [
        compute_payload("starting_pod", ai="starting"),
        compute_payload("loading_model", ai="starting"),
        compute_payload("ready", ai="ready"),
    ]

    chat_id, response = start_chat(client, headers)

    assert shared_app.gateway.ensure_calls == 1
    assert shared_app.gateway.chat_calls == 1
    assert shared_app.demand.ready() is True
    events = sse_events(response.text)
    assert [name for name, _ in events].count("done") == 1
    saved = messages(client, headers, chat_id)
    assert [row["role"] for row in saved] == ["user", "assistant"]
    assert saved[1]["status"] == "complete"
    assert "облако" in saved[1]["content"]
    # Executed once: one generation row, so nothing was billed twice.
    assert len(generations(chat_id)) == 1


def test_a_chat_with_a_ready_model_generates_without_an_ensure(shared_app, client, auth):
    headers = auth()
    shared_app.gateway.compute = compute_payload("ready", ai="ready")
    # The badge's own polling already proved readiness. Nothing is *assumed* ready: an unknown
    # cache still performs one ensure, which the Gateway answers by reusing the live session.
    run(shared_app.cloud.refresh(force=True))
    assert shared_app.demand.ready() is True

    chat_id, _ = start_chat(client, headers)

    assert shared_app.gateway.ensure_calls == 0
    assert shared_app.gateway.chat_calls == 1
    saved = messages(client, headers, chat_id)
    assert [row["role"] for row in saved] == ["user", "assistant"]


def test_a_model_that_is_not_ready_yet_never_polls_the_model_endpoint(shared_app, client, auth):
    """The order is ensure → Gateway readiness → model, never a `GET /v1/models` hot loop."""
    headers = auth()
    shared_app.gateway.ensure_results = [compute_payload("starting_pod", ai="starting")]
    shared_app.gateway.compute_steps = [
        compute_payload("loading_model", ai="starting"),
        compute_payload("loading_model", ai="starting"),
        compute_payload("ready", ai="ready"),
    ]

    start_chat(client, headers)

    assert shared_app.gateway.chat_calls == 1
    assert shared_app.gateway.models_calls == 0


def test_an_ensure_that_is_already_active_is_reused(shared_app, client, auth):
    """A concurrent chat event must never start a second search (or a second Pod)."""
    shared_app.gateway.ensure_results = [
        compute_payload(
            "searching", ai="starting", error_code="gpu_unavailable", search=search_window(started=NOW)
        ),
        compute_payload("starting_pod", ai="starting"),
    ]
    shared_app.gateway.compute_steps = [compute_payload("ready", ai="ready")]

    async def scenario():
        first = asyncio.ensure_future(shared_app.demand.ensure())
        await asyncio.sleep(0)
        second = asyncio.ensure_future(shared_app.demand.ensure())
        return await asyncio.gather(first, second)

    outcomes = run(scenario())

    assert [item["kind"] for item in outcomes] == ["starting", "starting"]
    # One attempt's worth of catalogue reads, shared by both callers.
    assert shared_app.gateway.ensure_calls == 2
    assert shared_app.gateway.ensure_bodies[0]["operation_id"].startswith("local-")


def test_manual_prewarm_and_a_chat_share_one_lifecycle(shared_app, client, auth):
    """The Settings button is an optional prewarm: same state machine, one search."""
    headers = auth()
    shared_app.gateway.ensure_results = [
        compute_payload(
            "searching", ai="starting", error_code="gpu_unavailable", search=search_window(started=NOW)
        ),
        compute_payload("starting_pod", ai="starting"),
    ]
    shared_app.gateway.compute_steps = [compute_payload("ready", ai="ready")]
    attempts = count_attempts(shared_app.demand)

    prewarm = client.post("/cloud/compute/ensure", json={}, headers=headers)
    assert prewarm.status_code == 200, prewarm.text
    assert prewarm.json()["compute"] is not None
    assert prewarm.json()["compute"]["state"] in {"searching", "starting_pod"}
    # The button's own answer arrived after the Gateway's first reply; its background attempt
    # (and only it) owns the rest of the bounded window.
    assert 1 <= shared_app.gateway.ensure_calls <= MAX_SEARCH_ATTEMPTS

    _, response = start_chat(client, headers)

    # One attempt for both callers: the chat attached to the prewarm's lifecycle.
    assert attempts.count == 1
    assert shared_app.gateway.ensure_calls == 2
    assert shared_app.gateway.chat_calls == 1, sse_events(response.text)
    assert [name for name, _ in sse_events(response.text)].count("done") == 1


def test_capacity_unavailable_fails_typed_and_never_generates(shared_app, client, auth):
    headers = auth()
    searching = compute_payload(
        "searching", ai="starting", error_code="gpu_unavailable", search=search_window(started=NOW)
    )
    shared_app.gateway.ensure_results = [searching]

    chat_id, response = start_chat(client, headers)

    # Bounded: one immediate check plus at most two retries inside the 60-second window.
    assert shared_app.gateway.ensure_calls == MAX_SEARCH_ATTEMPTS
    assert shared_app.clock.now <= NOW + timedelta(seconds=CAPACITY_WINDOW_SECONDS)
    assert shared_app.gateway.chat_calls == 0
    assert shared_app.gateway.models_calls == 0
    events = sse_events(response.text)
    errors = [payload for name, payload in events if name == "error"]
    assert [item["code"] for item in errors] == ["gpu_capacity_unavailable"]
    assert "GPU" in errors[0]["detail"]
    # The user's message stays, there is exactly one assistant message, and nothing is queued
    # to run the same request again later.
    saved = messages(client, headers, chat_id)
    assert [row["role"] for row in saved] == ["user", "assistant"]
    assert saved[0]["content"].startswith("Привет")
    assert saved[1]["status"] == "error"
    assert generations(chat_id) == []  # nothing generated, so nothing billed
    assert client.get(f"/tasks?chat_id={chat_id}", headers=headers).json() == []
    assert shared_app.gateway.stop_calls == 0  # nothing was created, so nothing is stopped


def test_compute_that_becomes_ready_after_the_ensure_executes_the_message_once(shared_app, client, auth):
    headers = auth()
    shared_app.gateway.ensure_results = [
        compute_payload(
            "searching", ai="starting", error_code="gpu_unavailable", search=search_window(started=NOW)
        ),
        compute_payload("starting_pod", ai="starting"),
    ]
    shared_app.gateway.compute_steps = [
        compute_payload("loading_model", ai="starting"),
        compute_payload("ready", ai="ready"),
    ]

    chat_id, response = start_chat(client, headers)

    events = sse_events(response.text)
    assert [name for name, _ in events].count("done") == 1
    assert shared_app.gateway.chat_calls == 1
    saved = messages(client, headers, chat_id)
    assert len(saved) == 2
    assert saved[1]["status"] == "complete"
    assert saved[1]["content"]


def test_a_loading_failure_ends_typed_without_generating(shared_app, client, auth):
    """The Gateway owns the Pod and its own cleanup: the client fails typed and stops there."""
    headers = auth()
    shared_app.gateway.ensure_results = [compute_payload("error", ai="error", error_code="startup_failed")]

    chat_id, response = start_chat(client, headers)

    assert shared_app.gateway.chat_calls == 0
    errors = [payload for name, payload in sse_events(response.text) if name == "error"]
    assert [item["code"] for item in errors] == ["startup_failed"]
    saved = messages(client, headers, chat_id)
    assert saved[1]["status"] == "error"
    # No second Pod, no local stop: D-9 keeps ownership of a failed start on the server.
    assert shared_app.gateway.stop_calls == 0
    assert shared_app.gateway.ensure_calls == 1
    chip = chip_for(shared_app.cloud)
    assert chip["state"] == "error"


def test_stop_ai_then_a_new_chat_starts_one_new_bounded_ensure(shared_app, client, auth):
    headers = auth()
    shared_app.gateway.compute = compute_payload("ready", ai="ready")

    stopped = client.post("/cloud/compute/stop", json={}, headers=headers)
    assert stopped.status_code == 200, stopped.text
    assert chip_for(shared_app.cloud)["state"] == "off"

    shared_app.gateway.ensure_results = [compute_payload("starting_pod", ai="starting")]
    shared_app.gateway.compute_steps = [compute_payload("ready", ai="ready")]
    chat_id, _ = start_chat(client, headers)

    assert shared_app.gateway.stop_calls == 1
    assert shared_app.gateway.ensure_calls == 1
    saved = messages(client, headers, chat_id)
    assert saved[1]["status"] == "complete"


def test_a_search_window_that_never_settles_fails_bounded(shared_app):
    """No unbounded wait: the client's own window closes and the reason is typed."""
    searching = compute_payload(
        "searching", ai="starting", error_code="gpu_unavailable", search=search_window(started=NOW)
    )
    shared_app.gateway.ensure_results = [searching]

    with pytest.raises(Exception) as error:
        run(_drain(shared_app.demand))

    assert getattr(error.value, "code", "") == "gpu_capacity_unavailable"
    assert shared_app.gateway.ensure_calls == MAX_SEARCH_ATTEMPTS
    assert shared_app.clock.now <= NOW + timedelta(seconds=CAPACITY_WINDOW_SECONDS)


async def _drain(demand):
    async for _ in demand.wait_until_ready():
        pass


def test_a_startup_that_never_becomes_ready_fails_at_the_gateway_deadline(shared_app):
    """The D-9 deadline is the Gateway's; the client waits for it and then fails typed."""
    deadline = NOW + timedelta(seconds=120)
    starting = compute_payload("loading_model", ai="starting", startup_deadline=deadline.isoformat())
    shared_app.gateway.compute = starting

    async def scenario():
        return [item async for item in shared_app.demand.wait_until_ready()]

    with pytest.raises(Exception) as error:
        run(scenario())

    assert getattr(error.value, "code", "") == "startup_timeout"
    # The last attempt bounded by the Gateway's own deadline, never an unbounded poll.
    assert shared_app.clock.now >= deadline
    assert shared_app.clock.now <= deadline + timedelta(seconds=60)


def test_outcome_classification_covers_the_gateway_vocabulary():
    assert outcome_of({"state": "ready"}, now=NOW)["kind"] == "ready"
    assert outcome_of({"state": "generating"}, now=NOW)["kind"] == "ready"
    assert outcome_of({"state": "creating"}, now=NOW)["kind"] == "starting"
    assert outcome_of({"state": "starting_pod"}, now=NOW)["kind"] == "starting"
    assert outcome_of({"state": "loading_model"}, now=NOW)["kind"] == "starting"
    assert outcome_of({"state": "offline"}, now=NOW)["kind"] == "unavailable"
    assert outcome_of({"state": "stopped"}, now=NOW)["kind"] == "unavailable"
    assert outcome_of({"state": "stopping"}, now=NOW)["kind"] == "stopped"
    assert outcome_of({"state": "create_unknown"}, now=NOW)["kind"] == "blocked"
    assert outcome_of({"state": "multiple_compute"}, now=NOW)["kind"] == "blocked"
    assert outcome_of({"state": "external_compute"}, now=NOW)["kind"] == "blocked"
    assert outcome_of({"state": "not_configured"}, now=NOW)["kind"] == "blocked"
    assert outcome_of({"state": "error", "error_code": "runpod_unavailable"}, now=NOW)["kind"] == "error"
    # A search is only "still searching" inside this request's own window.
    searching = {"state": "searching", "error_code": "gpu_unavailable"}
    assert outcome_of(searching, now=NOW, search_open=True)["kind"] == "searching"
    dead = outcome_of(searching, now=NOW, search_open=False)
    assert dead["kind"] == "unavailable"
    assert dead["code"] == "gpu_capacity_unavailable"
