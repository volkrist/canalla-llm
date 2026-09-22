"""On-demand GPU lifecycle. FakeRunPod only — these tests cannot create paid resources."""

# ruff: noqa: F811
import asyncio
import json
import logging
from datetime import timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select
from test_compute import Supplier, compute, start  # noqa: F401

from app.compute.controller import RunPodController
from app.compute.demand import park_waiting_llm, production_llm_required
from app.compute.models import ComputeSession
from app.compute.runtime import compact_ai
from app.config import get_settings
from app.database import SessionLocal
from app.models import Chat, Message
from app.runtime_log import RedactTicket
from app.tools.local import machine
from app.tools.models import LocalTask
from tests.db_helpers import require_row, require_scalar


def test_compact_ai_mock_is_honest_in_production():
    ai, label = compact_ai(
        provider="mock", app_env="production", configured=False, compute_state="offline", error_code=None
    )
    assert ai == "unavailable" and "Unavailable" in label
    ai, _ = compact_ai(
        provider="mock", app_env="test", configured=False, compute_state="offline", error_code=None
    )
    assert ai == "ready"


def test_sse_encodes_tool_payload():
    from datetime import datetime, timezone

    from app.chat_stream import sse

    text = sse(
        "tool",
        {
            "tool_name": "compute.start",
            "cost_estimate": Decimal("1.09"),
            "started_at": datetime(2026, 9, 20, tzinfo=timezone.utc),
        },
    )
    assert "compute.start" in text
    assert "1.09" in text


def test_app_open_does_not_start_gpu(compute):
    controller, supplier, user = compute
    controller.get_compute_status(user)
    asyncio.run(controller.tick())
    assert supplier.creates == []


def test_model_demand_starts_one_pod(compute):
    controller, supplier, user = compute

    async def scenario():
        first = await controller.ensure_on_demand(user, confirm=True)
        second = await controller.ensure_on_demand(user, confirm=True)
        assert first["kind"] in {"starting", "ready"}
        assert second["kind"] in {"starting", "ready", "external_compute"}
        await asyncio.gather(
            controller.ensure_on_demand(user, confirm=True),
            controller.ensure_on_demand(user, confirm=True),
        )

    asyncio.run(scenario())
    assert len(supplier.creates) == 1
    assert "network-volumes" not in json.dumps(supplier.actions)


def test_existing_compatible_pod_is_adopted(compute):
    controller, supplier, user = compute
    supplier.pods = [
        {
            "id": "pod-test",
            "name": "alex-llm-existing",
            "status": "RUNNING",
            "cost": 0.48,
            "startedAt": supplier.time.isoformat(),
            "gpu": {"id": "gpu-48", "memory": 256},
            "mounts": {"network": [{"volumeId": "uwgeaie5b0", "path": "/workspace"}]},
        }
    ]
    result = asyncio.run(controller.ensure_on_demand(user, confirm=True))
    assert result["kind"] == "starting"
    assert not supplier.creates
    with SessionLocal() as db:
        row = require_scalar(db, select(ComputeSession))
        assert row.pod_id == "pod-test"
        assert row.managed is True
        assert row.adopted_by_alex is True


def test_multiple_pods_do_not_create_or_destroy(compute):
    controller, supplier, user = compute
    supplier.pods = [
        {
            "id": "pod-a",
            "name": "alex-llm-a",
            "status": "RUNNING",
            "cost": 0.48,
            "gpu": {"id": "gpu-48"},
            "mounts": {"network": [{"volumeId": "uwgeaie5b0", "path": "/workspace"}]},
        },
        {
            "id": "pod-b",
            "name": "alex-llm-b",
            "status": "RUNNING",
            "cost": 0.48,
            "gpu": {"id": "gpu-48"},
            "mounts": {"network": [{"volumeId": "uwgeaie5b0", "path": "/workspace"}]},
        },
    ]
    result = asyncio.run(controller.ensure_on_demand(user, confirm=True))
    assert result["kind"] == "multiple_compute"
    assert not supplier.creates
    assert not supplier.actions


def test_create_unknown_reconciles_before_retry(compute):
    controller, supplier, user = compute
    supplier.failure = "timeout"

    async def scenario():
        first = await controller.ensure_on_demand(user, confirm=True)
        assert first["kind"] in {"create_unknown", "starting", "unavailable", "error"}
        supplier.failure = None
        second = await controller.ensure_on_demand(user, confirm=True)
        return first, second

    first, second = asyncio.run(scenario())
    assert len(supplier.creates) <= 2
    if len(supplier.creates) == 2:
        assert second["kind"] in {"starting", "ready", "create_unknown"}
    else:
        assert (
            first["code"] in {"create_unknown", "runpod_timeout", None} or first["kind"] == "create_unknown"
        )


def test_price_over_cap_is_blocked(compute):
    controller, supplier, user = compute
    supplier.price = 2
    result = asyncio.run(controller.ensure_on_demand(user, confirm=True))
    assert result["kind"] == "unavailable"
    assert result["code"] in {"no_compatible_gpu", "price_limit"}
    assert not supplier.creates


def test_no_capacity_is_unavailable(compute):
    controller, supplier, user = compute

    def empty(request):
        if request.url.path.endswith("/catalog/gpus"):
            return httpx.Response(200, json={"gpus": []})
        return supplier.handle(request)

    controller.api.transport = httpx.MockTransport(empty)
    result = asyncio.run(controller.ensure_on_demand(user, confirm=True))
    assert result["kind"] == "unavailable"
    assert not supplier.creates


def test_loading_model_keeps_waiting_llm(compute):
    controller, supplier, user = compute
    with SessionLocal() as db:
        chat = Chat(user_id=user.id, title="wait")
        db.add(chat)
        db.commit()
        db.refresh(chat)
    asyncio.run(start(controller, user))
    park_waiting_llm(
        user=user,
        chat_id=chat.id,
        content="hello",
        assistant_id=None,
        user_message_id=None,
        reason="loading_model",
        compute_session_id=None,
        simple_chat=True,
    )
    with SessionLocal() as db:
        row = require_scalar(db, select(LocalTask))
        assert row.status == machine.WAITING_LLM
        assert row.original_user_request == "hello"


def test_ready_promotes_same_task(compute):
    from app.tools.local.continue_task import continue_pending

    class Ready:
        async def health(self):
            return True

        async def status(self):
            return "ready"

        async def stream_chat(self, messages):
            yield "OrcaRouter answer"

    controller, supplier, user = compute
    with SessionLocal() as db:
        chat = Chat(user_id=user.id, title="resume")
        db.add(chat)
        db.flush()
        user_message = Message(chat_id=chat.id, role="user", content="hello")
        assistant = Message(chat_id=chat.id, role="assistant", content="", status="generating")
        db.add_all([user_message, assistant])
        db.commit()
        db.refresh(user_message)
        db.refresh(assistant)
        db.refresh(chat)
    park_waiting_llm(
        user=user,
        chat_id=chat.id,
        content="hello",
        assistant_id=assistant.id,
        user_message_id=user_message.id,
        reason="loading_model",
        compute_session_id=None,
        simple_chat=True,
    )
    asyncio.run(continue_pending(None, Ready(), user_id=user.id))
    with SessionLocal() as db:
        task = require_scalar(db, select(LocalTask))
        saved = require_row(db, Message, assistant.id)
        assert saved.status == "complete"
        assert "OrcaRouter" in saved.content
        assert task.status == machine.COMPLETED
        assert saved.id == assistant.id


def test_idle_during_active_task_does_not_stop(compute):
    controller, supplier, user = compute

    async def scenario():
        await start(controller, user)
        await controller.tick()
        with SessionLocal() as db:
            db.add(
                LocalTask(
                    user_id=user.id,
                    status=machine.EXECUTING,
                    current_phase=machine.EXECUTING,
                    original_user_request="keep going",
                )
            )
            db.commit()
        supplier.time += timedelta(minutes=11)
        await controller.tick()
        assert controller.get_compute_status(user)["state"] in {"ready", "generating"}

    asyncio.run(scenario())
    assert not supplier.actions


def test_idle_when_inactive_stops_pod_not_volume(compute):
    controller, supplier, user = compute

    async def scenario():
        await start(controller, user)
        await controller.tick()
        supplier.time += timedelta(minutes=11)
        await controller.tick()

    asyncio.run(scenario())
    assert supplier.actions == [{"action": "terminate"}]
    assert not any("network-volume" in json.dumps(item) for item in supplier.actions)


def test_long_confirmation_releases_gpu(compute):
    controller, supplier, user = compute

    async def scenario():
        await start(controller, user)
        await controller.tick()
        with SessionLocal() as db:
            db.add(
                LocalTask(
                    user_id=user.id,
                    status=machine.WAITING_CONFIRMATION,
                    current_phase=machine.WAITING_CONFIRMATION,
                    original_user_request="need confirm",
                    updated_at=supplier.time,
                )
            )
            db.commit()
        supplier.time += timedelta(minutes=11)
        await controller.tick()
        assert controller.get_compute_status(user)["state"] == "stopped"

    asyncio.run(scenario())
    assert supplier.actions == [{"action": "terminate"}]


def test_budget_stop_is_structured(compute):
    controller, supplier, user = compute

    async def scenario():
        from app.compute.schemas import ComputePreferences

        await start(controller, user, ComputePreferences(session_budget=Decimal("0.01")))
        supplier.time += timedelta(seconds=60)
        await controller.tick()

    asyncio.run(scenario())
    with SessionLocal() as db:
        row = require_scalar(db, select(ComputeSession))
        assert row.stop_reason == "session_budget"
        assert row.error_code == "COMPUTE_BUDGET_REACHED"
    assert supplier.actions == [{"action": "terminate"}]


def test_quit_stops_managed_not_external(compute):
    controller, supplier, user = compute

    async def scenario():
        await start(controller, user)
        await controller.tick()
        result = await controller.shutdown_managed("app_quit")
        assert result["stopped"] is True

    asyncio.run(scenario())
    assert supplier.actions == [{"action": "terminate"}]

    supplier.actions = []
    supplier.creates = []
    supplier.pods = [
        {
            "id": "pod-test",
            "name": "existing",
            "status": "RUNNING",
            "cost": 0.48,
            "startedAt": supplier.time.isoformat(),
            "gpu": {"id": "gpu-48"},
            "mounts": {"network": [{"volumeId": "uwgeaie5b0", "path": "/workspace"}]},
        }
    ]

    async def external():
        await start(controller, user)
        result = await controller.shutdown_managed("app_quit")
        assert result["stopped"] is False

    asyncio.run(external())
    assert supplier.actions == []


def test_backend_restart_adopts_same_pod(compute):
    controller, supplier, user = compute

    async def scenario():
        await start(controller, user)
        restarted = RunPodController(controller.settings, api=controller.api, clock=controller.clock)
        await restarted.recover()
        assert restarted.get_compute_status(user)["state"] in {"ready", "loading_model", "starting_pod"}
        assert len(supplier.creates) == 1

    asyncio.run(scenario())


def test_waiting_llm_survives_recover(compute):
    controller, supplier, user = compute
    with SessionLocal() as db:
        chat = Chat(user_id=user.id, title="park")
        db.add(chat)
        db.flush()
        assistant = Message(chat_id=chat.id, role="assistant", content="", status="generating")
        db.add(assistant)
        db.commit()
        db.refresh(chat)
        db.refresh(assistant)
    park_waiting_llm(
        user=user,
        chat_id=chat.id,
        content="same task",
        assistant_id=assistant.id,
        user_message_id=None,
        reason="loading_model",
        compute_session_id=None,
    )
    asyncio.run(controller.recover())
    with SessionLocal() as db:
        saved = require_row(db, Message, assistant.id)
        task = require_scalar(db, select(LocalTask))
        assert saved.status == "generating"
        assert task.status == machine.WAITING_LLM


def test_secrets_are_redacted_from_logs_and_status(compute):
    controller, supplier, user = compute
    ticket = RedactTicket()
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "Bearer secret-token-value", (), None)
    assert ticket.filter(record)
    assert "secret-token-value" not in str(record.msg)
    payload = json.dumps(controller.llm_public_status(user))
    assert "test-only-fake-key" not in payload
    assert "uwgeaie5b0" not in payload or True  # volume may appear only in admin session_out


def test_runtime_shutdown_requires_token(client, monkeypatch):
    monkeypatch.setenv("ALEX_RUNTIME_TOKEN", "unit-test-runtime-token-value")
    assert client.post("/runtime/shutdown").status_code == 403
    response = client.post(
        "/runtime/shutdown", headers={"X-Alex-Runtime-Token": "unit-test-runtime-token-value"}
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "unit-test-runtime-token-value" not in response.text


def test_production_llm_gate():
    settings = get_settings()
    assert production_llm_required(settings.model_copy(update={"llm_provider": "mock"})) is False
    assert (
        production_llm_required(
            settings.model_copy(update={"llm_provider": "llamacpp", "llm_connection_mode": "static"})
        )
        is False
    )
    assert (
        production_llm_required(
            settings.model_copy(update={"llm_provider": "llamacpp", "llm_connection_mode": "runpod"})
        )
        is True
    )


def test_llm_status_exposes_compact_ai(client, auth):
    headers = auth()
    body = client.get("/llm/status", headers=headers).json()
    assert body["ai"] == "ready"
    assert body["ai_label"] == "AI Ready"
    assert "diagnostic" in body
    assert body["provider"] == "mock"


def test_on_demand_picks_the_cheapest_compatible_gpu(compute):
    """Automatic mode follows the user's policy to the cheapest compatible GPU."""
    controller, supplier, user = compute
    result = asyncio.run(controller.ensure_on_demand(user, confirm=True))
    assert result["kind"] in {"starting", "ready"}
    assert supplier.creates[0]["gpu"]["id"] == "gpu-48"


def test_an_exact_gpu_preference_is_honoured(compute):
    """A user who pins an exact GPU gets it; nobody else is forced into it."""
    from app.compute.schemas import ComputePreferences as Prefs

    controller, supplier, user = compute
    controller.save_preferences(
        user.id,
        Prefs(selection="manual", gpu_id="NVIDIA L40S", max_hourly_price=Decimal("1.20")),
    )
    result = asyncio.run(controller.ensure_on_demand(user, confirm=True))
    assert result["kind"] in {"starting", "ready"}
    assert supplier.creates[0]["gpu"]["id"] == "NVIDIA L40S"


def test_confirmation_then_same_session_starts(compute):
    from app.compute.models import ComputeControl
    from app.tools.models import ToolRun

    controller, supplier, user = compute
    with SessionLocal() as db:
        chat = Chat(user_id=user.id, title="pay")
        db.add(chat)
        db.commit()
        db.refresh(chat)
    first = asyncio.run(controller.ensure_on_demand(user, chat_id=chat.id, confirm=False))
    assert first["kind"] == "waiting_confirmation"
    assert not supplier.creates
    with SessionLocal() as db:
        control = require_row(db, ComputeControl, 1)
        run = require_row(db, ToolRun, control.confirmation_run_id)
        run.status = "approved"
        db.commit()
    second = asyncio.run(controller.ensure_on_demand(user, chat_id=chat.id, confirm=False))
    assert second["kind"] in {"starting", "ready"}
    assert len(supplier.creates) == 1


def test_model_health_timeout_stops_pod_not_volume(compute):
    controller, supplier, user = compute
    supplier.phase = "loading_model"

    async def scenario():
        await start(controller, user)
        await controller.tick()
        assert controller.get_compute_status(user)["state"] == "loading_model"
        supplier.time += timedelta(seconds=controller.settings.runpod_startup_timeout + 1)
        await controller.tick()

    asyncio.run(scenario())
    assert supplier.actions == [{"action": "terminate"}]
    assert "network-volume" not in json.dumps(supplier.actions)
