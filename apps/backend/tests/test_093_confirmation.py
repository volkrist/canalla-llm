"""0.9.3 confirmation envelope: bind Allow to the exact payload and consume once."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.database import SessionLocal
from app.models import Message, now
from app.tools.confirmation import (
    ALLOWED,
    CONSUMED,
    canonical_payload,
    consume_if_valid,
    payload_digest,
)
from app.tools.contracts import RiskLevel, ToolDefinition, ToolError, ToolRegistry
from app.tools.executor import ExecutionContext, ToolExecutor
from app.tools.models import ToolRun
from app.tools.policy import ToolLimits
from tests.db_helpers import require_row
from tests.test_tools_core import FakeProvider, public_dns


class SensitiveArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(default="notes.txt")
    amount: str = Field(default="1.00")
    recipient: str = Field(default="alice")
    note: str = Field(default="hello")


def sensitive_definition():
    return ToolDefinition(
        "test_sensitive",
        "Sensitive action",
        SensitiveArgs,
        "external_purchase",
        RiskLevel.SENSITIVE,
        "free",
        10,
        "fake",
    )


@pytest.fixture
def setup(client, auth):
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    with SessionLocal() as db:
        message = Message(chat_id=chat_id, role="assistant", content="")
        db.add(message)
        db.commit()
        generation_id = message.id

    async def emit(event, value):
        del event, value

    context = ExecutionContext(user_id, chat_id, generation_id, ToolLimits(), emit, resolver=public_dns)
    return headers, context, client, user_id


def _run(registry, context, arguments=None):
    return asyncio.run(
        ToolExecutor(registry).execute("test_sensitive", arguments or {"path": "notes.txt"}, context)
    )


def test_canonical_payload_is_order_independent():
    left = canonical_payload({"b": 1, "a": {"z": 2, "y": 3}})
    right = canonical_payload({"a": {"y": 3, "z": 2}, "b": 1})
    assert left == right
    assert payload_digest("tool", {"b": 1, "a": {"z": 2, "y": 3}}) == payload_digest(
        "tool", {"a": {"y": 3, "z": 2}, "b": 1}
    )


def test_same_payload_allowed_once(setup):
    headers, context, client, _user = setup
    provider, registry = FakeProvider(), ToolRegistry()
    registry.register(sensitive_definition(), provider)

    async def confirm(event, value):
        if value["status"] == "waiting_confirmation":
            route = f"/tools/runs/{value['id']}/confirm"
            first = client.post(route, headers=headers, json={"allow": True})
            assert first.status_code == 200
            replay = client.post(route, headers=headers, json={"allow": True})
            assert replay.status_code == 409
            assert "confirmation_already_used" in str(replay.json())

    context.emit = confirm
    result = _run(registry, context, {"path": "a.txt", "amount": "1.00", "recipient": "bob", "note": "hi"})
    assert provider.called == 1
    assert result.sources
    with SessionLocal() as db:
        row = require_row(db, ToolRun, context.run_id)
        envelope = (row.result_metadata or {}).get("confirmation") or {}
        assert envelope.get("consumption_state") == CONSUMED
        assert envelope.get("canonical_payload", {}).get("path") == "a.txt"


@pytest.mark.parametrize(
    "field,value",
    [("path", "other.txt"), ("amount", "9.99"), ("recipient", "mallory"), ("note", "mutated")],
)
def test_changed_field_rejected_before_side_effect(setup, field, value):
    headers, context, client, _user = setup
    provider, registry = FakeProvider(), ToolRegistry()
    registry.register(sensitive_definition(), provider)

    async def mutate(event, payload):
        if payload["status"] != "waiting_confirmation":
            return
        key = payload["id"]
        assert (
            client.post(f"/tools/runs/{key}/confirm", headers=headers, json={"allow": True}).status_code
            == 200
        )
        with SessionLocal() as db:
            row = require_row(db, ToolRun, key)
            meta = dict(row.result_metadata or {})
            envelope = dict(meta.get("confirmation") or {})
            canonical = dict(envelope.get("canonical_payload") or {})
            canonical[field] = value
            envelope["canonical_payload"] = canonical
            meta["confirmation"] = envelope
            meta["host_args"] = dict(canonical)
            row.result_metadata = meta
            db.commit()

    context.emit = mutate
    with pytest.raises(ToolError, match="confirmation_payload_changed"):
        _run(registry, context, {"path": "a.txt", "amount": "1.00", "recipient": "bob", "note": "hi"})
    assert provider.called == 0


def test_replay_same_confirmation_rejected(setup):
    headers, context, client, _user = setup
    provider, registry = FakeProvider(), ToolRegistry()
    registry.register(sensitive_definition(), provider)
    seen = {}

    async def confirm(event, value):
        if value["status"] == "waiting_confirmation":
            seen["id"] = value["id"]
            client.post(f"/tools/runs/{value['id']}/confirm", headers=headers, json={"allow": True})

    context.emit = confirm
    _run(registry, context)
    replay = client.post(f"/tools/runs/{seen['id']}/confirm", headers=headers, json={"allow": True})
    assert replay.status_code == 409


def test_expired_confirmation_rejected(setup):
    headers, context, client, _user = setup
    provider, registry = FakeProvider(), ToolRegistry()
    registry.register(sensitive_definition(), provider)

    async def expire(event, value):
        if value["status"] != "waiting_confirmation":
            return
        with SessionLocal() as db:
            row = require_row(db, ToolRun, value["id"])
            row.started_at = now() - timedelta(minutes=6)
            db.commit()
        response = client.post(f"/tools/runs/{value['id']}/confirm", headers=headers, json={"allow": True})
        assert response.status_code == 409

    context.emit = expire
    with pytest.raises(ToolError, match="confirmation_expired|confirmation_denied"):
        _run(registry, context)
    assert provider.called == 0


def test_wrong_user_rejected(setup, client, auth):
    headers, context, _client, _user = setup
    other = auth("other@example.com")
    provider, registry = FakeProvider(), ToolRegistry()
    registry.register(sensitive_definition(), provider)

    async def steal(event, value):
        if value["status"] == "waiting_confirmation":
            stolen = client.post(f"/tools/runs/{value['id']}/confirm", headers=other, json={"allow": True})
            assert stolen.status_code == 404
            client.post(f"/tools/runs/{value['id']}/confirm", headers=headers, json={"allow": False})

    context.emit = steal
    with pytest.raises(ToolError, match="confirmation_denied"):
        _run(registry, context)
    assert provider.called == 0


def test_parallel_consume_race(setup):
    _headers, context, _client, user_id = setup
    digest_value = payload_digest("test_sensitive", {"path": "race.txt"})
    with SessionLocal() as db:
        row = ToolRun(
            user_id=user_id,
            chat_id=context.chat_id,
            generation_id=context.generation_id,
            tool_name="test_sensitive",
            provider="fake",
            risk_level="SENSITIVE",
            status="approved",
            input_digest=digest_value,
            input_summary={},
            result_metadata={
                "confirmation": {
                    "consumption_state": ALLOWED,
                    "payload_digest": digest_value,
                    "canonical_payload": {"path": "race.txt"},
                }
            },
        )
        db.add(row)
        db.commit()
        run_id = row.id

    outcomes = []

    def worker():
        try:
            with SessionLocal() as db:
                consume_if_valid(db, run_id=run_id, user_id=user_id, expected_digest=digest_value)
                db.commit()
            outcomes.append("ok")
        except ToolError as error:
            outcomes.append(error.code)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: worker(), range(8)))
    assert outcomes.count("ok") == 1
    assert all(item in {"ok", "confirmation_already_used"} for item in outcomes)
    with SessionLocal() as db:
        row = require_row(db, ToolRun, run_id)
        assert row.status == "consumed"
