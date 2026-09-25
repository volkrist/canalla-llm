"""Model replicas: is the copy on a placement's storage the model this build expects?

A second placement only helps if the model is *there*. The dangerous failure is not a missing
volume — that is loud — but a **stale or half-finished copy**: a Pod starts, llama.cpp loads a
file that is not the pinned model, and the product answers confidently with the wrong weights. So
each placement publishes a small, non-secret metadata record and the allocator refuses a placement
whose record is absent, incomplete, unverified or does not name this exact model, hash and runtime.

The record is a fact the provisioning step writes after a verified copy (see
``docs/report-2026-09-25-storage-multi-datacenter-decision.md`` §18) — never a claim the runtime
makes about itself:

    {"datacenter": "US-KS-2", "volume_id": "vol_secondary", "model_id": "orcarouter/…",
     "model_path": "/workspace/models/…gguf", "model_sha256": "…", "model_bytes": 20123456789,
     "runtime_version": "b4200", "bootstrap_version": "1.2.0", "last_verified": "2026-09-25T…Z",
     "ready": true}

Nothing here performs I/O or reads a credential: it is a pure function over one metadata record
and the expectation the deployment already holds, so both provider modes share it.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime

# The verdicts. Each is a code the operator reads in the audit trail, never a sentence.
REPLICA_READY = "ready"
REPLICA_MISSING = "missing"
REPLICA_PARTIAL = "partial"
REPLICA_UNVERIFIED = "unverified"
REPLICA_WRONG_MODEL = "wrong_model"
REPLICA_WRONG_HASH = "wrong_hash"
REPLICA_WRONG_RUNTIME = "wrong_runtime"

# The typed reason an allocation ends with when every placement was refused for this one.
REPLICA_NOT_READY = "replica_not_ready"

_STATUSES = (
    REPLICA_READY,
    REPLICA_PARTIAL,
    REPLICA_UNVERIFIED,
    REPLICA_WRONG_MODEL,
    REPLICA_WRONG_HASH,
    REPLICA_WRONG_RUNTIME,
)


@dataclass(frozen=True)
class ReplicaExpectation:
    """What this build's model actually is. Read from the deployment, never from the replica."""

    model_id: str
    model_sha256: str
    runtime_version: str
    bootstrap_version: str
    model_bytes: int | None = None


@dataclass(frozen=True)
class ModelReplica:
    """One placement's claim about the copy it holds."""

    volume_id: str
    datacenter: str = ""
    model_id: str = ""
    model_path: str = ""
    model_sha256: str = ""
    model_bytes: int = 0
    runtime_version: str = ""
    bootstrap_version: str = ""
    last_verified: str | None = None
    ready: bool = False

    @classmethod
    def parse(cls, raw) -> "ModelReplica | None":
        """One metadata record, or ``None`` when it is not a record at all.

        Unknown keys are ignored on purpose: the file is written by the provisioning step and may
        grow, and a new key must not make an otherwise valid replica unusable.
        """
        if not isinstance(raw, dict):
            return None
        known = {field.name for field in fields(cls)}
        values = {key: value for key, value in raw.items() if key in known}
        try:
            replica = cls(**values)
        except TypeError:
            return None
        return replica if replica.volume_id else None


def replica_verdict(replica: ModelReplica | None, expected: ReplicaExpectation) -> str:
    """Whether this replica may serve the configured model, as one typed code.

    Every comparison is made only when the expectation can actually state the value. A deployment
    that does not know the model's hash must not turn a record that *does* state one into a
    mismatch: an unstated expectation is silence, and silence never contradicts a verified copy.
    """
    if replica is None:
        return REPLICA_MISSING
    if not replica.ready:
        return REPLICA_PARTIAL
    if expected.model_id and replica.model_id != expected.model_id:
        return REPLICA_WRONG_MODEL
    if expected.model_sha256 and replica.model_sha256.lower() != expected.model_sha256.lower():
        return REPLICA_WRONG_HASH
    if expected.model_bytes is not None and replica.model_bytes != expected.model_bytes:
        return REPLICA_PARTIAL
    if expected.runtime_version and replica.runtime_version != expected.runtime_version:
        return REPLICA_WRONG_RUNTIME
    if expected.bootstrap_version and replica.bootstrap_version != expected.bootstrap_version:
        return REPLICA_WRONG_RUNTIME
    if not _verified(replica.last_verified):
        return REPLICA_UNVERIFIED
    return REPLICA_READY


def placement_refusals(
    placements, replicas, expected: ReplicaExpectation, *, require_records=()
) -> dict[str, str]:
    """The placements whose replica cannot serve this model, keyed by volume id.

    A placement is never silently dropped: the reason travels with the volume id into the plan's
    audit, so "why did it start in the second datacenter" (or "why did it not") is a field.

    ``require_records`` names the volumes that **must** carry a verified record — the placements an
    operator adds. The placement the product already runs on is deliberately not in it: it is
    proven by production, and demanding a record it has never had would take a working
    installation offline. A record that *does* exist is always honoured, so writing a bad record
    for any volume refuses that volume rather than being ignored.
    """
    by_volume: dict[str, ModelReplica] = {}
    for raw in replicas or ():
        replica = raw if isinstance(raw, ModelReplica) else ModelReplica.parse(raw)
        if replica is not None:
            by_volume[replica.volume_id] = replica
    required = {str(volume) for volume in require_records}
    refused: dict[str, str] = {}
    for placement in placements:
        replica = by_volume.get(placement.volume_id)
        if replica is None and placement.volume_id not in required:
            continue
        verdict = replica_verdict(replica, expected)
        if verdict != REPLICA_READY:
            refused[placement.volume_id] = verdict
    return refused


def parse_replicas(raw) -> tuple[ModelReplica, ...]:
    """A registry document (``{"replicas": [...]}`` or a bare list) as records, ignoring junk."""
    if isinstance(raw, dict):
        raw = raw.get("replicas") or ()
    if not isinstance(raw, (list, tuple)):
        return ()
    parsed = (ModelReplica.parse(item) for item in raw)
    return tuple(replica for replica in parsed if replica is not None)


def _verified(value) -> bool:
    """A verification timestamp that a human can read and that parses as an instant."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True
