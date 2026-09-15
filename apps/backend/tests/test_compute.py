"""All supplier traffic is intercepted: these tests cannot create paid resources."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.compute.controller import RunPodController, estimate
from app.compute.models import ComputeSession
from app.compute.runpod_api import RunPodAPI, RunPodError
from app.compute.schemas import ComputePreferences, StartRequest, StopRequest
from app.config import get_settings
from app.database import SessionLocal
from app.models import Chat, User


class Supplier:
    def __init__(self):
        self.time = datetime(2026, 9, 14, tzinfo=timezone.utc)
        self.pods = []
        self.creates = []
        self.actions = []
        self.failure = None
        self.price = 0.8

    def handle(self, request):
        path = request.url.path
        assert request.url.host == "api.runpod.io"
        assert request.headers["Authorization"] == "Bearer test-only-fake-key"
        if path.endswith("/catalog/gpus"):
            return httpx.Response(
                200,
                json={
                    "gpus": [
                        {
                            "id": gid,
                            "name": gid,
                            "manufacturer": manufacturer,
                            "secure": True,
                            "memory": memory,
                            "price": {"secure": price},
                            "dataCenters": [{"id": "US-TX-3", "availability": availability}],
                        }
                        for gid, manufacturer, memory, price, availability in [
                            ("cheap-unavailable", "NVIDIA", 48, 0.4, "NONE"),
                            ("small", "NVIDIA", 24, 0.3, "HIGH"),
                            ("amd", "AMD", 192, 0.2, "HIGH"),
                            ("gpu-48", "NVIDIA", 48, self.price, "LOW"),
                            ("gpu-80", "NVIDIA", 80, 1.6, "HIGH"),
                        ]
                    ]
                },
            )
        if "/network-volumes/" in path:
            return httpx.Response(
                200,
                json={
                    "id": "uwgeaie5b0",
                    "name": "storage",
                    "size": 50,
                    "dataCenter": "US-TX-3",
                    "type": "STANDARD",
                },
            )
        if "/catalog/datacenters/" in path:
            return httpx.Response(200, json={"networkVolumeTypes": ["STANDARD"]})
        if path.endswith("/pods") and request.method == "POST":
            body = json.loads(request.content)
            self.creates.append(body)
            if self.failure == "timeout":
                raise httpx.ReadTimeout("upstream secret must never be exposed")
            if isinstance(self.failure, int):
                return httpx.Response(self.failure, json={"detail": "private upstream details"})
            pod = {
                "id": "pod-test",
                "name": body["name"],
                "status": "RUNNING",
                "cost": self.price,
                "startedAt": self.time.isoformat(),
                "mounts": body["mounts"],
                "gpu": body["gpu"],
            }
            self.pods.append(pod)
            return httpx.Response(201, json=pod)
        if path.endswith("/pods"):
            return httpx.Response(200, json={"pods": self.pods})
        if path.endswith("/action"):
            self.actions.append(json.loads(request.content))
            return httpx.Response(204)
        if path.endswith("/logs"):
            marker = {"line": "ALEX_LLM_PHASE=ready", "ts": datetime.now(timezone.utc).isoformat()}
            return httpx.Response(200, text="data: " + json.dumps(marker) + "\n\n")
        if path.endswith("/pods/pod-test"):
            return httpx.Response(200, json=self.pods[0])
        raise AssertionError(f"Unexpected supplier request {request.method} {path}")


@pytest.fixture
def compute():
    supplier = Supplier()
    settings = get_settings().model_copy(
        update={"runpod_api_key": __import__("pydantic").SecretStr("test-only-fake-key")}
    )
    api = RunPodAPI(settings, httpx.MockTransport(supplier.handle))
    controller = RunPodController(settings, api=api, clock=lambda: supplier.time)
    with SessionLocal() as db:
        user = User(email="compute@example.com", password_hash="unused", role="admin")
        db.add(user)
        db.commit()
    return controller, supplier, user


async def start(controller, user, preferences=None):
    quote = await controller.search_gpu(user, preferences or ComputePreferences())
    request = StartRequest(
        quote_id=quote["quote_id"], gpu_id="gpu-48", idempotency_key=str(uuid4()), confirmed=True
    )
    return request, await controller.start_compute(user, request)


def test_catalog_filters_and_price_cap(compute):
    controller, supplier, _ = compute
    options = asyncio.run(controller.api.gpu_options(ComputePreferences()))
    assert [g.id for g in options if g.selectable] == ["gpu-48"]
    assert "amd" not in [g.id for g in options]
    assert next(g for g in options if g.id == "gpu-80").reason == "price_limit"
    assert next(g for g in options if g.id == "small").reason == "insufficient_vram"
    assert supplier.creates == []


def test_duplicate_and_restart_never_create_twice(compute):
    controller, supplier, user = compute

    async def scenario():
        request, result = await start(controller, user)
        assert result["state"] == "starting_pod"
        restarted = RunPodController(controller.settings, api=controller.api, clock=controller.clock)
        await restarted.recover()
        await asyncio.gather(controller.start_compute(user, request), controller.start_compute(user, request))
        assert restarted.get_compute_status(user)["state"] == "ready"

    asyncio.run(scenario())
    assert len(supplier.creates) == 1
    body = supplier.creates[0]
    assert body["dataCenterIds"] == ["US-TX-3"]
    assert body["mounts"] == {"network": [{"volumeId": "uwgeaie5b0", "path": "/workspace"}]}
    assert body["ports"] == []


@pytest.mark.parametrize(
    "failure, expected",
    [("timeout", "create_unknown"), (500, "create_unknown"), (402, "error"), (400, "searching")],
)
def test_creation_failure_is_sanitized_and_not_retried(compute, failure, expected):
    controller, supplier, user = compute
    supplier.failure = failure

    async def scenario():
        request, result = await start(controller, user)
        assert result["state"] == expected
        await controller.start_compute(user, request)
        assert "private" not in json.dumps(result)
        assert "secret" not in json.dumps(result)

    asyncio.run(scenario())
    assert len(supplier.creates) == 1


def test_price_change_requires_new_confirmation(compute):
    controller, supplier, user = compute

    async def scenario():
        quote = await controller.search_gpu(user, ComputePreferences())
        supplier.price = 0.9
        with pytest.raises(RunPodError, match="Цена"):
            await controller.start_compute(
                user,
                StartRequest(
                    quote_id=quote["quote_id"], gpu_id="gpu-48", idempotency_key=str(uuid4()), confirmed=True
                ),
            )

    asyncio.run(scenario())
    assert not supplier.creates


def test_budget_stops_even_during_generation(compute):
    controller, supplier, user = compute

    async def scenario():
        await start(controller, user, ComputePreferences(session_budget=Decimal("0.01")))
        await controller.tick()
        with SessionLocal() as db:
            chat = Chat(user_id=user.id, title="budget")
            db.add(chat)
            db.commit()
        usage = await controller.begin_generation(user.id, chat.id, "mock")
        supplier.time += timedelta(seconds=60)
        await controller.tick()
        assert supplier.actions == [{"action": "terminate"}]
        assert controller.get_compute_status(user)["session"] is None
        with pytest.raises(HTTPException):
            await controller.begin_generation(user.id, chat.id, "mock")
        controller.finish_generation(usage, "complete")
        await controller.tick()
        assert controller.get_compute_status(user)["state"] == "stopped"

    asyncio.run(scenario())
    assert supplier.actions == [{"action": "terminate"}]
    with SessionLocal() as db:
        row = db.scalar(select(ComputeSession))
        assert row.stop_reason == "session_budget"
        assert estimate(row, supplier.time)[1] == Decimal("0.013333")


def test_idle_stop_preserves_volume(compute):
    controller, supplier, user = compute

    async def scenario():
        await start(controller, user)
        await controller.tick()
        supplier.time += timedelta(minutes=11)
        await controller.tick()
        assert controller.get_compute_status(user)["state"] == "stopped"

    asyncio.run(scenario())
    assert supplier.actions == [{"action": "terminate"}]


def test_missing_key_and_admin_permissions(client, auth):
    headers = auth()
    assert client.get("/compute/status", headers=headers).json()["state"] == "not_configured"
    assert client.get("/llm/status", headers=headers).json()["state"] == "mock"
    assert client.get("/admin/users", headers=headers).status_code == 403
    assert client.post("/compute/search", headers=headers, json={}).status_code == 403
    response = client.get("/compute/options", headers=headers)
    assert response.status_code == 503
    assert response.json()["code"] == "not_configured"


@pytest.mark.parametrize("payload", [{"gpus": None}, {"gpus": [{}]}, {"gpus": "invalid"}])
def test_malformed_catalog_is_safe(compute, payload):
    controller, _, _ = compute
    controller.api.transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    with pytest.raises(RunPodError) as error:
        asyncio.run(controller.api.gpu_options(ComputePreferences()))
    assert error.value.code == "malformed_response"


def test_existing_pod_is_not_duplicated_or_automatically_stopped(compute):
    controller, supplier, user = compute
    supplier.pods = [
        {
            "id": "pod-test",
            "name": "existing",
            "status": "RUNNING",
            "cost": 0.8,
            "startedAt": supplier.time.isoformat(),
            "gpu": {"id": "gpu-48", "memory": 256},
            "mounts": {"network": [{"volumeId": "uwgeaie5b0", "path": "/workspace"}]},
        }
    ]

    async def scenario():
        _, result = await start(controller, user)
        assert result["state"] == "external_compute"
        assert result["session"]["gpu_vram_mb"] == 0
        supplier.time += timedelta(days=2)
        await controller.tick()
        assert not supplier.actions
        with pytest.raises(HTTPException):
            await controller.stop_compute(user, StopRequest())
        await controller.stop_compute(user, StopRequest(confirm_external=True))

    asyncio.run(scenario())
    assert not supplier.creates and supplier.actions == [{"action": "terminate"}]


def test_manual_selection_does_not_fallback(compute):
    controller, supplier, user = compute
    supplier.failure = 400
    asyncio.run(start(controller, user, ComputePreferences(selection="manual")))
    assert len(supplier.creates) == 1


def test_startup_timeout_terminates_managed_compute(compute):
    controller, supplier, user = compute

    async def scenario():
        await start(controller, user)
        supplier.pods[0]["status"] = "STARTING"
        supplier.time += timedelta(seconds=901)
        await controller.tick()
        assert controller.get_compute_status(user)["state"] == "stopped"

    asyncio.run(scenario())
    with SessionLocal() as db:
        assert db.scalar(select(ComputeSession)).stop_reason == "startup_timeout"


def test_search_cancel_and_other_user_quote_access(compute):
    controller, supplier, user = compute
    supplier.price = 2
    with SessionLocal() as db:
        other = User(email="other@example.com", password_hash="unused", role="user")
        db.add(other)
        db.commit()

    async def scenario():
        quote = await controller.search_gpu(user, ComputePreferences(auto_search=True))
        assert controller.get_compute_status(user)["state"] == "searching"
        with pytest.raises(HTTPException):
            controller.quote(other, quote["quote_id"])
        with pytest.raises(HTTPException):
            await controller.cancel_gpu_search(other)
        await controller.cancel_gpu_search(user)
        supplier.time += timedelta(minutes=1)
        await controller.tick()
        assert controller.get_compute_status(user)["state"] == "offline"

    asyncio.run(scenario())
    assert not supplier.creates


@pytest.mark.parametrize("payload", [{"records": []}, {"records": [{"podId": "other", "gpuAmount": 1}]}])
def test_missing_actual_billing_is_unknown_not_zero(compute, payload):
    controller, supplier, _ = compute
    controller.api.transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    assert asyncio.run(controller.api.actual_cost("pod-test", supplier.time, supplier.time)) is None


def test_malformed_supplier_timestamp_is_rejected(compute):
    controller, _, _ = compute
    with pytest.raises(RunPodError):
        controller.api.parse_pod(
            {"id": "pod", "name": "x", "status": "RUNNING", "cost": 1, "startedAt": "invalid"}
        )


def test_auto_selection_cannot_choose_more_expensive_quote(compute):
    controller, supplier, user = compute
    from app.compute.models import ComputeQuote

    async def scenario():
        quote = await controller.search_gpu(user, ComputePreferences())
        with SessionLocal() as db:
            row = db.get(ComputeQuote, quote["quote_id"])
            row.options = row.options + [
                {
                    **next(g for g in row.options if g["id"] == "gpu-48"),
                    "id": "expensive",
                    "hourly_rate": "1.10",
                }
            ]
            db.commit()
        with pytest.raises(RunPodError):
            await controller.start_compute(
                user,
                StartRequest(
                    quote_id=quote["quote_id"],
                    gpu_id="expensive",
                    idempotency_key=str(uuid4()),
                    confirmed=True,
                ),
            )

    asyncio.run(scenario())
    assert not supplier.creates


def test_auto_search_repeats_without_restart_and_never_creates(compute):
    controller, supplier, user = compute

    async def scenario():
        supplier.price = 2
        await controller.search_gpu(user, ComputePreferences(auto_search=True))
        supplier.time += timedelta(seconds=30)
        await controller.tick()
        assert controller.get_compute_status(user)["state"] == "searching"
        supplier.price = 0.8
        supplier.time += timedelta(seconds=30)
        await controller.tick()
        assert controller.get_compute_status(user)["state"] == "gpu_found"
        assert not supplier.creates
        await controller.cancel_gpu_search(user)
        supplier.time += timedelta(seconds=30)
        await controller.tick()
        assert controller.get_compute_status(user)["state"] == "offline"

    asyncio.run(scenario())


def test_disappeared_gpu_resumes_search_without_create(compute):
    controller, supplier, user = compute

    async def scenario():
        quote = await controller.search_gpu(user, ComputePreferences())
        supplier.price = 2
        result = await controller.start_compute(
            user,
            StartRequest(
                quote_id=quote["quote_id"],
                gpu_id="gpu-48",
                idempotency_key=str(uuid4()),
                confirmed=True,
            ),
        )
        assert result["state"] == "searching"
        assert result["quote_id"] is None
        assert result["next_search_at"] is not None
        assert not supplier.creates

    asyncio.run(scenario())


def test_rejected_placement_resumes_search(compute):
    controller, supplier, user = compute
    supplier.failure = 409

    async def scenario():
        _, result = await start(controller, user)
        assert result["state"] == "searching"
        assert result["session"] is None
        assert result["next_search_at"] is not None
        assert len(supplier.creates) == 1

    asyncio.run(scenario())


def test_auto_connect_exact_gpu_once_and_recovery(compute):
    controller, supplier, user = compute

    async def scenario():
        prefs = ComputePreferences(auto_connect=True, gpu_id="gpu-48")
        result = await controller.search_gpu(user, prefs)
        assert result["status"]["session"]["gpu_type"] == "gpu-48"
        await controller.search_gpu(user, prefs)
        restarted = RunPodController(controller.settings, api=controller.api, clock=controller.clock)
        await restarted.recover()
        assert len(supplier.creates) == 1

    asyncio.run(scenario())


def test_auto_connect_waits_for_exact_gpu_not_cheaper_fallback(compute):
    controller, supplier, user = compute

    async def scenario():
        # gpu-80 is available but forbidden by the exact target, even under a higher limit.
        supplier.price = 4
        prefs = ComputePreferences(auto_connect=True, gpu_id="gpu-48", max_hourly_price=2)
        await controller.search_gpu(user, prefs)
        assert controller.get_compute_status(user)["state"] == "searching"
        assert not supplier.creates
        supplier.price = 1.5
        supplier.time += timedelta(seconds=31)
        await controller.tick()
        assert len(supplier.creates) == 1
        assert supplier.creates[0]["gpu"]["id"] == "gpu-48"

    asyncio.run(scenario())


def test_auto_connect_rechecks_price_and_cancel_disarms(compute):
    controller, supplier, user = compute
    original = controller.api.gpu_options
    count = 0

    async def changing(prefs):
        nonlocal count
        count += 1
        if count > 1:
            supplier.price = 1.1
        return await original(prefs)

    controller.api.gpu_options = changing

    async def scenario():
        await controller.search_gpu(user, ComputePreferences(auto_connect=True, gpu_id="gpu-48"))
        assert not supplier.creates
        assert controller.get_compute_status(user)["state"] == "searching"
        await controller.cancel_gpu_search(user)
        supplier.time += timedelta(minutes=1)
        await controller.tick()
        assert not supplier.creates

    asyncio.run(scenario())


def test_auto_connect_rechecks_permissions_after_wait(compute):
    controller, supplier, user = compute

    async def scenario():
        supplier.price = 4
        await controller.search_gpu(user, ComputePreferences(auto_connect=True, gpu_id="gpu-48"))
        with SessionLocal() as db:
            db.get(User, user.id).role = "user"
            db.commit()
        supplier.price = 0.8
        supplier.time += timedelta(seconds=31)
        await controller.tick()
        assert not supplier.creates
        assert controller.get_compute_status(user)["next_search_at"] is None

    asyncio.run(scenario())


def test_live_preferences_ignore_legacy_env_cap_and_survive_restart(compute):
    controller, supplier, user = compute
    controller.settings.runpod_max_session_budget = Decimal("0.82")
    controller.settings.runpod_max_hourly_price = Decimal("1.09")

    async def scenario():
        await start(controller, user)
        prefs = ComputePreferences(session_budget=5, max_hourly_price=1.6, auto_stop_minutes=5)
        await controller.update_preferences(user, prefs)
        current = controller.get_compute_status(user)
        assert current["session"]["session_budget"] == 5
        assert current["session"]["max_hourly_price"] == 1.6
        assert current["session"]["auto_stop_minutes"] == 5
        restarted = RunPodController(controller.settings, api=controller.api, clock=controller.clock)
        assert restarted.preferences(user.id).session_budget == 5
        assert len(supplier.creates) == 1

    asyncio.run(scenario())


def test_changed_preferences_invalidate_quote(compute):
    controller, supplier, user = compute

    async def scenario():
        quote = await controller.search_gpu(user, ComputePreferences())
        await controller.update_preferences(user, ComputePreferences(max_hourly_price=0.5))
        with pytest.raises(RunPodError):
            await controller.start_compute(
                user,
                StartRequest(
                    quote_id=quote["quote_id"], gpu_id="gpu-48", idempotency_key=str(uuid4()), confirmed=True
                ),
            )
        assert not supplier.creates

    asyncio.run(scenario())


def test_live_search_preferences_restrict_next_attempt(compute):
    controller, supplier, user = compute

    async def scenario():
        supplier.price = 4
        await controller.search_gpu(user, ComputePreferences(auto_connect=True, gpu_id="gpu-48"))
        await controller.update_preferences(
            user, ComputePreferences(auto_connect=True, gpu_id="gpu-80", max_hourly_price=1.7)
        )
        await controller.tick()
        assert len(supplier.creates) == 1
        assert supplier.creates[0]["gpu"]["id"] == "gpu-80"

    asyncio.run(scenario())


def test_preferences_route_requires_compute_permission(client, auth):
    assert client.put("/compute/preferences", headers=auth(), json={"session_budget": 5}).status_code == 403


def test_auto_manual_requires_exact_gpu():
    from app.config import get_settings

    with pytest.raises(ValueError):
        ComputePreferences(selection="manual", auto_connect=True).enforce(get_settings())
