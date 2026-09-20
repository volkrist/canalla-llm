"""Canonical confirmation envelope: bind Allow to the exact payload, consume once."""

from __future__ import annotations

import json
import secrets
from datetime import timedelta

from sqlalchemy import update

from ..models import now
from .contracts import ToolError
from .models import ToolRun
from .security import digest

PENDING = "PENDING"
ALLOWED = "ALLOWED"
CONSUMED = "CONSUMED"
TTL = timedelta(minutes=5)

STATUS_PENDING = "waiting_confirmation"
STATUS_ALLOWED = "approved"
STATUS_CONSUMED = "consumed"


def canonical_payload(payload: dict) -> dict:
    return json.loads(json.dumps(payload or {}, sort_keys=True, ensure_ascii=False, default=str))


def payload_digest(tool_name: str, payload: dict) -> str:
    return digest(canonical_payload(payload), tool_name)


def make_envelope(
    *, user_id: str, task_id: str | None, tool_name: str, risk_level: str, payload: dict, confirmation_id: str
) -> dict:
    canonical = canonical_payload(payload)
    created = now()
    return {
        "user_id": user_id,
        "task_id": task_id,
        "tool_name": tool_name,
        "risk_level": risk_level,
        "canonical_payload": canonical,
        "payload_digest": digest(canonical, tool_name),
        "created_at": created.isoformat(),
        "expires_at": (created + TTL).isoformat(),
        "nonce": secrets.token_hex(16),
        "confirmation_id": confirmation_id,
        "consumption_state": PENDING,
    }


def envelope_of(row: ToolRun) -> dict:
    return dict((row.result_metadata or {}).get("confirmation") or {})


def store_envelope(row: ToolRun, envelope: dict) -> None:
    row.result_metadata = {**(row.result_metadata or {}), "confirmation": envelope}


def _expired(row: ToolRun) -> bool:
    started = row.started_at
    if started is None:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=now().tzinfo)
    return started < now() - TTL


def confirm_allow(db, *, run_id: str, user_id: str, digest_value: str | None = None) -> ToolRun:
    """PENDING → ALLOWED. Second allow is confirmation_already_used."""
    row = db.get(ToolRun, run_id)
    if not row or row.user_id != user_id:
        raise ToolError("not_found")
    if digest_value and digest_value != row.input_digest:
        raise ToolError("confirmation_payload_changed")
    if _expired(row):
        raise ToolError("confirmation_expired")
    if row.status == STATUS_CONSUMED:
        raise ToolError("confirmation_already_used")
    if row.status not in {STATUS_PENDING, STATUS_ALLOWED}:
        raise ToolError("confirmation_already_used")
    envelope = envelope_of(row)
    envelope["consumption_state"] = ALLOWED
    metadata = {**(row.result_metadata or {}), "confirmation": envelope}
    changed = db.execute(
        update(ToolRun)
        .where(
            ToolRun.id == run_id,
            ToolRun.user_id == user_id,
            ToolRun.status == STATUS_PENDING,
        )
        .values(status=STATUS_ALLOWED, confirmed_at=now(), result_metadata=metadata)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        raise ToolError("confirmation_already_used")
    db.refresh(row)
    return row


def confirm_deny(db, *, run_id: str, user_id: str) -> ToolRun:
    row = db.get(ToolRun, run_id)
    if not row or row.user_id != user_id:
        raise ToolError("not_found")
    envelope = envelope_of(row)
    envelope["consumption_state"] = CONSUMED
    metadata = {
        **(row.result_metadata or {}),
        "confirmation": envelope,
        "reserved_budget": 0,
    }
    changed = db.execute(
        update(ToolRun)
        .where(
            ToolRun.id == run_id,
            ToolRun.user_id == user_id,
            ToolRun.status == STATUS_PENDING,
        )
        .values(
            status="stopped",
            cancelled_at=now(),
            finished_at=now(),
            error_code="confirmation_denied",
            result_metadata=metadata,
        )
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        raise ToolError("confirmation_already_used")
    db.refresh(row)
    return row


def consume_if_valid(db, *, run_id: str, user_id: str, expected_digest: str) -> ToolRun:
    """ALLOWED → CONSUMED. Exactly one caller wins. Must run immediately before side effect."""
    row = db.get(ToolRun, run_id)
    if not row or row.user_id != user_id:
        raise ToolError("not_found")
    if _expired(row):
        raise ToolError("confirmation_expired")
    if row.input_digest != expected_digest:
        raise ToolError("confirmation_payload_changed")
    if row.status == STATUS_CONSUMED:
        raise ToolError("confirmation_already_used")
    if row.status != STATUS_ALLOWED:
        raise ToolError("confirmation_required")
    envelope = envelope_of(row)
    if envelope.get("payload_digest") and envelope["payload_digest"] != expected_digest:
        raise ToolError("confirmation_payload_changed")
    envelope["consumption_state"] = CONSUMED
    metadata = {**(row.result_metadata or {}), "confirmation": envelope}
    changed = db.execute(
        update(ToolRun)
        .where(
            ToolRun.id == run_id,
            ToolRun.user_id == user_id,
            ToolRun.status == STATUS_ALLOWED,
            ToolRun.input_digest == expected_digest,
        )
        .values(status=STATUS_CONSUMED, result_metadata=metadata)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        raise ToolError("confirmation_already_used")
    db.refresh(row)
    return row
