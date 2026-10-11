"""Durable claim/lease protocol for local computer jobs (A05).

The backend hands a local job to a paired device and the device's native host runs
it. Two failures used to be indistinguishable and both re-ran a side effect:

* the host delivered the result but the POST was lost, so the job stayed
  ``waiting_host`` and the next poll re-ran the command; and
* the host crashed after the side effect but before it recorded the result.

This module is the vocabulary and the derivation that make the states explicit and
survive a restart. It deliberately does **not** promise exactly-once for an
arbitrary shell: a crash between the effect and the result is reported as
``unknown`` and needs a human decision, never a silent replay.

States
------

``pending``            the backend created the job and nobody claimed it (DB status
                       ``waiting_host``)
``claimed``            a device took a lease on it and is about to run it
``started``            the device began the effect (a crash after this point is a
                       side effect of unknown outcome)
``completed``          the executor consumed the delivered result
``result_awaiting_ack`` the backend durably stored the result and is waiting for the
                       device to confirm receipt
``acknowledged``       the device confirmed; its local journal may be cleared
``unknown``            the device cannot tell whether the effect ran; never retried
                       automatically

Only :data:`PRESERVED_ON_RESTART` survives the lifespan reconcile step: a run that
never left the backend is failed, a run that may already have done something is not.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone

PENDING = "waiting_host"
CLAIMED = "claimed"
STARTED = "started"
COMPLETED = "completed"
RESULT_AWAITING_ACK = "result_awaiting_ack"
ACKNOWLEDGED = "acknowledged"
UNKNOWN = "unknown"

# The protocol states, in the order a healthy job visits them.
ORDERED_STATES = (PENDING, CLAIMED, STARTED, RESULT_AWAITING_ACK, ACKNOWLEDGED)

# ``host_ready`` is the pre-1.2.1 spelling of "the host delivered a result"; it stays
# accepted (and is reported as ``result_awaiting_ack``) so an installation upgrading
# mid-flight does not lose a run.
LEGACY_DELIVERED = "host_ready"

# Runs whose side effect may already have happened. A back-end restart preserves them
# instead of failing them: the device can still deliver, decline or resolve the result.
PRESERVED_ON_RESTART = frozenset({CLAIMED, STARTED, RESULT_AWAITING_ACK, UNKNOWN, LEGACY_DELIVERED})

# The states the executor treats as "the host delivered a result". It stops waiting on
# any of them; ``acknowledged`` is only ever reached after delivery.
DELIVERED = frozenset({LEGACY_DELIVERED, RESULT_AWAITING_ACK, ACKNOWLEDGED, COMPLETED})

# A job this device has taken but not finished delivering. It stays visible to the same
# device so a lost result POST is retried without re-running anything.
IN_FLIGHT = frozenset({CLAIMED, STARTED, RESULT_AWAITING_ACK})

# The device-side phases of the native journal, shared verbatim with the Rust host.
PHASE_STARTED = "started"
PHASE_DONE = "done"

CLAIM_TTL_SECONDS = 300

_STATE_BY_STATUS = {
    PENDING: "pending",
    CLAIMED: CLAIMED,
    STARTED: STARTED,
    COMPLETED: "completed",
    RESULT_AWAITING_ACK: RESULT_AWAITING_ACK,
    LEGACY_DELIVERED: RESULT_AWAITING_ACK,
    ACKNOWLEDGED: ACKNOWLEDGED,
    UNKNOWN: UNKNOWN,
}


def utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def new_claim_id() -> str:
    """A fresh, unguessable lease id. Not a credential: it only binds a delivery."""
    return secrets.token_hex(16)


def claim_expired(expires_at: datetime | None, current: datetime) -> bool:
    moment = utc(expires_at)
    return moment is not None and moment <= current


def result_digest(
    *, status: str, exit_code: int | None, stdout: str, stderr: str, text: str, metadata
) -> str:
    """A digest of the whole delivered result.

    It makes a re-delivery of the *same* result idempotent and a *different* result a
    conflict, so a device that keeps retrying after a lost ACK can never smuggle a
    second outcome past the backend.
    """
    payload = {
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "text": text,
        "metadata": metadata,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def job_state(row) -> str:
    """Normalise a ``ToolRun`` row to its protocol state.

    Falls back to the raw status for non-host runs (web, Tor) so the field stays
    meaningful everywhere it is published.
    """
    status = getattr(row, "status", "") or ""
    if status in _STATE_BY_STATUS:
        return _STATE_BY_STATUS[status]
    # A row whose status was reset but still carries a live claim is claimed.
    if status == PENDING and getattr(row, "claim_id", None):
        return CLAIMED
    return status
