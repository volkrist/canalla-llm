"""Model replicas: the copy must be *this* model, and the plan must say why when it is not.

A second placement is only useful if the model is there, and the dangerous failure is not a missing
volume but a stale, half-finished or unverified copy: the Pod starts, llama.cpp loads the file, and
the product answers confidently with the wrong weights. These tests pin the verdicts and the fact
that a refused placement is skipped with its reason kept — never substituted, never silently
dropped.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.compute import candidates as policy
from app.compute.replicas import (
    REPLICA_MISSING,
    REPLICA_PARTIAL,
    REPLICA_READY,
    REPLICA_UNVERIFIED,
    REPLICA_WRONG_HASH,
    REPLICA_WRONG_MODEL,
    REPLICA_WRONG_RUNTIME,
    ModelReplica,
    ReplicaExpectation,
    parse_replicas,
    placement_refusals,
    replica_verdict,
)

EXPECTED = ReplicaExpectation(
    model_id="orcarouter/Qwen3.8-27B-Uncensored",
    model_sha256="a" * 64,
    runtime_version="b4200",
    bootstrap_version="1.2.0",
    model_bytes=20_000_000_000,
)

READY = {
    "volume_id": "vol-secondary",
    "datacenter": "US-KS-2",
    "model_id": EXPECTED.model_id,
    "model_path": "/workspace/models/orcarouter-qwen38/model.gguf",
    "model_sha256": EXPECTED.model_sha256,
    "model_bytes": EXPECTED.model_bytes,
    "runtime_version": EXPECTED.runtime_version,
    "bootstrap_version": EXPECTED.bootstrap_version,
    "last_verified": "2026-09-25T10:00:00Z",
    "ready": True,
}


def replica(**overrides) -> ModelReplica:
    return ModelReplica.parse({**READY, **overrides})


def test_a_verified_replica_of_this_exact_model_is_ready():
    assert replica_verdict(replica(), EXPECTED) == REPLICA_READY


def test_no_record_at_all_is_missing_not_ready():
    assert replica_verdict(None, EXPECTED) == REPLICA_MISSING


def test_a_copy_that_never_finished_is_partial():
    assert replica_verdict(replica(ready=False), EXPECTED) == REPLICA_PARTIAL
    # Half the bytes with a finished flag is still half the bytes: the size is checked too.
    assert replica_verdict(replica(model_bytes=17), EXPECTED) == REPLICA_PARTIAL


def test_another_model_is_not_this_model():
    assert replica_verdict(replica(model_id="someone/other-model"), EXPECTED) == REPLICA_WRONG_MODEL


def test_a_different_hash_is_refused_whatever_the_case():
    assert replica_verdict(replica(model_sha256="b" * 64), EXPECTED) == REPLICA_WRONG_HASH
    assert replica_verdict(replica(model_sha256="A" * 64), EXPECTED) == REPLICA_READY


def test_the_runtime_and_the_bootstrap_are_part_of_the_identity():
    assert replica_verdict(replica(runtime_version="b5000"), EXPECTED) == REPLICA_WRONG_RUNTIME
    assert replica_verdict(replica(bootstrap_version="1.1.0"), EXPECTED) == REPLICA_WRONG_RUNTIME


@pytest.mark.parametrize("value", [None, "", "  ", "not-a-date", "2026-13-45T99:99:99Z"])
def test_a_copy_nobody_verified_is_not_usable(value):
    assert replica_verdict(replica(last_verified=value), EXPECTED) == REPLICA_UNVERIFIED


def test_the_record_may_grow_without_becoming_unusable():
    """The provisioning step owns the file; a new key in it must not invalidate a good replica."""
    from_next_phase = ModelReplica.parse({**READY, "nvidia_driver": "570.1", "copied_at": "yesterday"})
    assert replica_verdict(from_next_phase, EXPECTED) == REPLICA_READY


def test_junk_is_ignored_rather_than_trusted():
    assert ModelReplica.parse("nonsense") is None
    assert ModelReplica.parse({}) is None  # no volume id, so it cannot belong to a placement
    assert parse_replicas({"replicas": [READY, "junk", 42]}) == (replica(),)
    assert parse_replicas(None) == ()


def test_the_registry_keys_the_refusal_by_volume_so_the_audit_can_read_it():
    placements = (
        policy.Placement(datacenter="US-TX-3", volume_id="uwgeaie5b0"),
        policy.Placement(datacenter="US-KS-2", volume_id="vol-secondary"),
    )
    # The placement the product already runs on is proven by production: no record is not a refusal.
    assert placement_refusals(placements, [READY], EXPECTED) == {}
    # A record that exists is always honoured, for the primary as much as for an added placement.
    stale = {**READY, "volume_id": "uwgeaie5b0", "ready": False}
    assert placement_refusals(placements, [READY, stale], EXPECTED) == {"uwgeaie5b0": REPLICA_PARTIAL}


def test_an_added_placement_needs_its_own_verified_record():
    """An extra datacenter is a claim about storage, so it has to be provable before it is used."""
    placements = (
        policy.Placement(datacenter="US-TX-3", volume_id="uwgeaie5b0"),
        policy.Placement(datacenter="US-KS-2", volume_id="vol-secondary"),
    )
    assert placement_refusals(placements, [], EXPECTED, require_records=("vol-secondary",)) == {
        "vol-secondary": REPLICA_MISSING
    }
    assert placement_refusals(placements, [READY], EXPECTED, require_records=("vol-secondary",)) == {}


def test_a_placement_whose_copy_is_wrong_is_never_substituted():
    """The end-to-end shape: catalogue capacity in both datacenters, one usable copy of the model."""
    rows = [
        {
            "id": "NVIDIA L40S",
            "name": "NVIDIA L40S",
            "vram_gb": 48,
            "secure": True,
            "community": False,
            "price": {"secure": Decimal("1.09"), "community": None},
            "data_centers": {"US-TX-3": "LOW", "US-KS-2": "HIGH"},
        }
    ]
    placements = (
        policy.Placement(datacenter="US-TX-3", volume_id="uwgeaie5b0"),
        policy.Placement(datacenter="US-KS-2", volume_id="vol-secondary"),
    )
    replicas = [
        READY,
        {**READY, "volume_id": "uwgeaie5b0", "model_sha256": "c" * 64},
    ]
    plan = policy.build_plan(
        rows,
        min_vram_gb=48,
        max_hourly_price=Decimal("2.00"),
        placements=placements,
        replica_refusals=placement_refusals(placements, replicas, EXPECTED),
    )
    assert plan.reason is None
    assert [(item.datacenter, item.volume_id) for item in plan.candidates] == [("US-KS-2", "vol-secondary")]
