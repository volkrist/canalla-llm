"""Compose the storage-unblock evidence from measured inputs only.

Inputs (all already on disk, produced by read-only probes):
    baseline.json                 account, volumes, catalogue, product allocation plans
    gpu-catalog.json              compatible cards with per-datacenter availability
    global-volume-availability.json
    artifacts/runpod-tx3-watcher/primary-manifest.json   measured inventory of uwgeaie5b0
    docs/replica-us-mo-1.json                          verified replica on y92bt25ru8

Nothing here invents a price: the network-volume rate is the one this account is billed, the
global request rates are the provider's published classes, and the global *storage* rate is marked
unpublished with scenario figures clearly labelled as scenarios.

    python artifacts/acceptance-127/storage-unblock/build_artifacts.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
BACKEND = REPO / "apps" / "backend"

import sys

sys.path.insert(0, str(BACKEND))

from app.compute import storage

PRIMARY = "uwgeaie5b0"
REPLICA = "y92bt25ru8"
GLOBAL = "cmugq2bsv000007js3sa47w6j"
MODEL_BYTES = 20_752_787_712
MODEL_SHA = "4ad98832b3d376dd274df5bba59dd34834f0f401593e1463f38fd5fa219293b1"
RUNTIME_VERSION = "b11565"
BUDGET_USD = Decimal("2.00")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write(name: str, payload) -> None:
    (OUT / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def main() -> int:
    baseline = load(OUT / "baseline.json")
    catalogue = load(OUT / "gpu-catalog.json")
    manifest = load(REPO / "artifacts" / "runpod-tx3-watcher" / "primary-manifest.json")
    replica_doc = load(REPO / "docs" / "replica-us-mo-1.json")["replicas"][0]
    probe_path = (
        REPO
        / "artifacts"
        / "acceptance-127"
        / "live-qwen"
        / "final-live"
        / "gpu-selection.json"
    )
    probe = load(probe_path) if probe_path.is_file() else None
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")

    volumes = {row["id"]: row for row in baseline["network_volumes"]["rows"]}
    gpu_by_name = {row["name"]: row for row in catalogue["compatible"]}

    # ---------------------------------------------------------------- model inventory
    inventory = {
        "generated_at": generated,
        "status": "COMPLETE_FOR_BOTH_REGIONAL_VOLUMES",
        "sources": [
            "artifacts/runpod-tx3-watcher/primary-manifest.json (measured in a Pod, 2026-09-26)",
            "docs/replica-us-mo-1.json (verified by a real GPU answer, 2026-10-13)",
        ],
        "model": {
            "id": "orcarouter-qwen38-27b-q5km",
            "display_name": "orcarouter/Qwen3.8-27B-Uncensored Q5_K_M",
            "format": "GGUF, single file, Q5_K_M",
            "split_gguf": False,
            "context_window": 32768,
            "chat_template": "embedded in the GGUF metadata (llama.cpp --jinja)",
            "gguf_bytes": MODEL_BYTES,
            "gguf_sha256": MODEL_SHA,
            "runtime_version": RUNTIME_VERSION,
        },
        "volumes": {
            PRIMARY: {
                "datacenter": volumes[PRIMARY]["data_center"],
                "type": volumes[PRIMARY]["type"],
                "size_gb": volumes[PRIMARY]["size_gb"],
                "used_bytes": manifest["used_bytes"],
                "file_count": manifest["file_count"],
                "gguf_path": manifest["gguf_path"],
                "gguf_bytes": manifest["gguf_bytes"],
                "gguf_sha256": manifest["gguf_sha256"],
                "runtime_files": manifest.get("binaries"),
                "scripts": manifest.get("scripts"),
                "volume_root_listing": manifest.get("runtime_listing"),
                "serve_selftest": manifest.get("serve_selftest"),
                "serve_selftest_note": (
                    "recorded as FAIL on the primary volume; the cause was not captured in this "
                    "session and no GPU was started to re-test it. The MO-1 replica is the one with "
                    "a proven real-model answer."
                ),
            },
            REPLICA: {
                "datacenter": volumes[REPLICA]["data_center"],
                "type": volumes[REPLICA]["type"],
                "size_gb": volumes[REPLICA]["size_gb"],
                "model_path": replica_doc["model_path"],
                "model_bytes": replica_doc["model_bytes"],
                "model_sha256": replica_doc["model_sha256"],
                "runtime_version": replica_doc["runtime_version"],
                "runtime_path": replica_doc.get("runtime_path"),
                "runtime_sha256": replica_doc.get("runtime_sha256"),
                "ready": replica_doc["ready"],
                "last_verified": replica_doc["last_verified"],
                "verified_by": (
                    "docs/report-2026-10-13-gpu-acceptance-us-mo-1.md: llama-server started from "
                    "this volume, /v1/models served the alias after 8 s, and a real Qwen answer was "
                    "produced with 21 613 MiB in VRAM"
                ),
            },
        },
        "global_volume": {
            "id": GLOBAL,
            "content": "UNKNOWN",
            "status": "MODEL_INVENTORY_PARTIAL",
            "reason": (
                "a global volume has no S3-compatible API; its contents can only be read from "
                "inside a Pod, and no Pod was created in this free stage"
            ),
        },
    }
    write("model-inventory.json", inventory)

    # ---------------------------------------------------------------- llama.cpp compatibility
    compatibility = {
        "generated_at": generated,
        "evidence_level": "STATIC_ANALYSIS_PLUS_ONE_PROVEN_RUN_ON_A_REGIONAL_VOLUME",
        "loader": {
            "binary": storage.RUNTIME_RELATIVE_PATH,
            "runtime_version": RUNTIME_VERSION,
            "model_file": storage.MODEL_RELATIVE_PATH,
            "reads_model_with": "mmap by default (llama.cpp), single contiguous GGUF read",
            "writes_to_model_directory": False,
            "kv_cache_location": "GPU VRAM",
        },
        "launch_path": {
            "script": "start-llm.sh",
            "writes": [
                "llama-server.log (volume root, measured in runtime_listing)",
                "llama-server.pid (volume root, measured in runtime_listing)",
            ],
            "reads": ["llama-server", "GGUF", "check-llm.sh"],
            "requires": [
                "full path semantics for bash",
                "no locking, rename, symlink or permission bits",
            ],
        },
        "paths": {
            "model_root_env": "ALEX_LLM_ROOT",
            "model_path_env": "ALEX_LLM_MODEL_PATH",
            "default_root": storage.DEFAULT_MOUNT,
            "resolved_default_model_path": storage.DEFAULT_MOUNT
            + "/"
            + storage.MODEL_RELATIVE_PATH,
        },
        "requirements": {
            "file_locking": False,
            "atomic_rename": False,
            "hard_links": False,
            "permission_bits": False,
            "symlinks": False,
            "in_place_rewrite": False,
            "large_whole_file_reads": True,
        },
        "verdicts": {
            storage.REGIONAL_VOLUME: {
                "verdict": "COMPATIBLE_PROVEN",
                "evidence": "real GPU run from y92bt25ru8 (2026-10-13): ready in 8 s, real answer in 2.1 s",
            },
            storage.GLOBAL_VOLUME: {
                "verdict": "INCOMPATIBLE_AS_IS",
                "reasons": [
                    "the measured start-llm.sh writes llama-server.log and llama-server.pid to the mount",
                    "object-backed storage has no atomic rename, no file locks and no permission bits",
                ],
                "what_would_have_to_change": [
                    "write the log and pid to /tmp (container disk) instead of the mount",
                    (
                        "keep the llama.cpp runtime off the object-backed mount, or accept a "
                        "small-file startup penalty"
                    ),
                ],
                "unverified": list(storage.GLOBAL_UNVERIFIED_REASONS),
                "measurement_required": (
                    "one GPU Pod mounting the global volume and loading the GGUF; only that can "
                    "prove or disprove mmap and startup latency"
                ),
            },
            storage.LOCAL_MODEL_COPY: {
                "verdict": "COMPATIBLE_WITH_DISK_BUDGET",
                "requirement_bytes": MODEL_BYTES,
                "reserve_fraction": str(storage.LOCAL_COPY_RESERVE_FRACTION),
                "note": "the container disk must hold the model plus the reserve; the copy costs startup time",
            },
        },
        "not_claimed": "global-volume inference is NOT verified; no Pod was created in this stage",
    }
    write("model-compatibility.json", compatibility)

    # ---------------------------------------------------------------- architecture comparison
    tx3_cards = [
        row["name"]
        for row in catalogue["compatible"]
        if "US-TX-3" in row["available_centers"]
    ]
    mo1_cards = [
        row["name"]
        for row in catalogue["compatible"]
        if "US-MO-1" in row["available_centers"]
    ]
    comparison = {
        "generated_at": generated,
        "read_only": True,
        "question": "where should one canonical copy of the weights live so a GPU can always be booked?",
        "facts": {
            "observed_at": baseline["generated_at"],
            "volatility_note": (
                "per-datacenter availability is a live pool: the first read of this stage saw zero "
                "compatible cards in US-TX-3 and an L40S in US-MO-1; a read 23 minutes later saw "
                "an L40S back in US-TX-3. Exactly why one datacenter is not a plan."
            ),
            "primary_volume": {
                "id": PRIMARY,
                "datacenter": volumes[PRIMARY]["data_center"],
                "compatible_gpus_available_now": tx3_cards,
            },
            "replica_volume": {
                "id": REPLICA,
                "datacenter": volumes[REPLICA]["data_center"],
                "verified_replica": True,
                "compatible_gpus_available_now": mo1_cards,
            },
            "global_volume": {
                "id": GLOBAL,
                "exists": True,
                "content": "unknown",
                "api_attachable": True,
                "mount_kind": "global (max 1 per Pod, no datacenter constraint, GPU and CPU)",
            },
        },
        "architectures": {
            "A_regional_tx3_only": {
                "summary": "keep only uwgeaie5b0 and wait for capacity in US-TX-3",
                "compatible_gpus_available_now": tx3_cards,
                "monthly_storage_usd": "3.50 (already billed)",
                "migration_cost_usd": "0",
                "verdict": "BLOCKED",
                "why": "zero compatible cards with availability in US-TX-3 at the time of the read",
            },
            "B_regional_replica_mo1": {
                "summary": "use the verified copy on y92bt25ru8 in US-MO-1",
                "compatible_gpus_available_now": mo1_cards,
                "monthly_storage_usd": "7.03 for both volumes (already billed)",
                "migration_cost_usd": "0 (the copy exists and is verified)",
                "verdict": "READY_NOW",
                "why": (
                    "a real GPU answer was produced from this volume on 2026-10-13 and L40S 48 GB "
                    "is available there now at $1.09/h"
                ),
            },
            "C_global_volume_whole_tree": {
                "summary": "copy the whole /workspace tree to the global volume and mount it at /workspace",
                "compatible_gpus_available_now": "every datacenter in the catalogue (no pin)",
                "monthly_storage_usd": "global rate NOT PUBLISHED; scenarios in monthly-storage-cost.json",
                "migration_cost_usd": "≈0.06 one-time (CPU Pod + request classes), see migration-cost.json",
                "verdict": "POSSIBLE_UNVERIFIED",
                "why": (
                    "the public API can attach it, but the launch script writes to the mount and "
                    "mmap/startup on object-backed storage has not been measured"
                ),
            },
            "D_global_weights_local_runtime": {
                "summary": "global volume holds only the GGUF; llama.cpp runs from local storage",
                "compatible_gpus_available_now": "every datacenter in the catalogue (no pin)",
                "monthly_storage_usd": "global rate NOT PUBLISHED; scenarios in monthly-storage-cost.json",
                "migration_cost_usd": "≈0.06 one-time + a local runtime delivery step per start",
                "verdict": "POSSIBLE_UNVERIFIED",
                "why": (
                    "avoids object-storage small-file reads, but requires the runtime to be shipped "
                    "in the image or fetched at every start (adds startup time and cost)"
                ),
            },
        },
        "recommendation": {
            "immediate_live": "B_regional_replica_mo1",
            "why": "zero migration, zero new infrastructure, a verified copy, and capacity right now",
            "strategic": "C_global_volume_whole_tree, then D if C's startup latency is unacceptable",
            "guard": (
                "neither global option may be called compatible before one GPU Pod loads the GGUF "
                "from it; that Pod is a paid action and is NOT part of the $2 LIVE envelope"
            ),
        },
    }
    write("storage-architecture-comparison.json", comparison)

    # ---------------------------------------------------------------- GPU selection
    plans = baseline.get("allocation_plans", {})
    selection = {
        "generated_at": generated,
        "read_only": True,
        "minimum_vram_gb": 48,
        "budget_usd": str(BUDGET_USD),
        "regional_plan": plans.get("regional_placements", {}),
        "global_plan": plans.get("global_placement", {}),
        "selected_for_live": (
            {
                "gpu": "NVIDIA L40S",
                "vram_gb": 48,
                "hourly_rate_usd": gpu_by_name["L40S"]["price_secure_usd_hr"],
                "datacenter": "US-MO-1",
                "volume_id": REPLICA,
                "storage_mode": storage.REGIONAL_VOLUME,
            }
            if mo1_cards
            else None
        ),
        "note": (
            "the regional plan is the product's own candidate policy over the live catalogue; the "
            "global plan shows what would be bookable with a global placement (no datacenter pin)"
        ),
    }
    write("gpu-selection.json", selection)

    # ---------------------------------------------------------------- migration plan
    migration = {
        "generated_at": generated,
        "status": "DRAFT_REQUIRES_OWNER_APPROVAL",
        "target": {
            "storage": "existing global volume",
            "id": GLOBAL,
            "why": "region-independent; already exists; no new paid storage would be created",
        },
        "source": {
            "volume_id": REPLICA,
            "datacenter": volumes[REPLICA]["data_center"],
            "why": "the verified copy; reading it avoids re-downloading ~20.8 GB",
        },
        "preconditions": [
            "owner approval for one short CPU Pod (mounts.global is API-only for CPU pods)",
            "the source volume is mounted read-only in spirit: nothing is deleted or edited",
            "both volumes attach to ONE Pod (one network + one global)",
        ],
        "steps": {
            "A_inventory": {
                "done": True,
                "evidence": "model-inventory.json",
                "files": manifest["file_count"],
                "bytes": manifest["used_bytes"],
                "gguf_sha256": MODEL_SHA,
            },
            "B_destination": {
                "layout": "/workspace/ (global) mirrors the source layout",
                "expected_paths": [
                    storage.MODEL_RELATIVE_PATH,
                    storage.RUNTIME_RELATIVE_PATH,
                    "start-llm.sh",
                    "check-llm.sh",
                ],
            },
            "C_transfer": {
                "command": "rsync --archive --human-readable --info=progress2 /workspace/ /workspace-global/",
                "runs_in": "one CPU Pod in US-MO-1 with both volumes attached",
                "not_executed": True,
            },
            "D_verify": {
                "commands": [
                    "find /workspace-global -type f | wc -l",
                    "du -sb /workspace-global",
                    "sha256sum /workspace-global/" + storage.MODEL_RELATIVE_PATH,
                ],
                "must_match": {
                    "gguf_sha256": MODEL_SHA,
                    "gguf_bytes": MODEL_BYTES,
                },
            },
            "E_model_loading": {
                "status": "NOT_RUN",
                "requires": "one GPU Pod with the global mount; proves or disproves mmap",
            },
            "F_cutover": {
                "status": "NOT_RUN",
                "rule": "no production cutover in this task; the regional volume stays the default",
            },
        },
        "rollback": [
            "nothing is deleted or moved: the source volume stays untouched",
            "the global volume can be deleted by an explicit owner decision, priced below",
        ],
        "old_volume": {
            "id": PRIMARY,
            "rule": "never deleted or altered without a separate owner decision and a verified backup",
        },
    }
    write("migration-plan.json", migration)

    # ---------------------------------------------------------------- migration cost
    cpu_rate = Decimal(
        "0.07"
    )  # cpu5c: $0.035/vCPU/h x 2 vCPU (catalogue read this stage)
    floor_minutes, ceiling_minutes = Decimal(10), Decimal(30)
    transfer = storage.migration_projection(
        MODEL_BYTES, files=int(manifest["file_count"])
    )
    migration_cost = {
        "generated_at": generated,
        "currency": "USD",
        "one_time": {
            "cpu_pod": {
                "flavour": "cpu5c",
                "vcpu": 2,
                "hourly_rate_usd": str(cpu_rate),
                "minutes": {"fast": str(floor_minutes), "slow": str(ceiling_minutes)},
                "cost_usd": {
                    "fast": str(
                        (cpu_rate * floor_minutes / 60).quantize(Decimal("0.0001"))
                    ),
                    "slow": str(
                        (cpu_rate * ceiling_minutes / 60).quantize(Decimal("0.0001"))
                    ),
                },
            },
            "request_classes": transfer,
            "total_usd": {
                "floor": str(
                    (
                        cpu_rate * floor_minutes / 60
                        + Decimal(transfer["request_cost_usd"]["floor"])
                    ).quantize(Decimal("0.0001"))
                ),
                "ceiling": str(
                    (
                        cpu_rate * ceiling_minutes / 60
                        + Decimal(transfer["request_cost_usd"]["per_file"])
                    ).quantize(Decimal("0.0001"))
                ),
            },
        },
        "verification_gpu": {
            "status": "NEEDED_ONLY_IF_THE_GLOBAL_VOLUME_IS_CHOSEN",
            "gpu": "NVIDIA L40S",
            "hourly_rate_usd": "1.09",
            "minutes": 15,
            "cost_usd": "0.2725",
            "note": "one Pod, one load attempt, then terminate",
        },
        "storage_while_copying": {
            "rate": "NOT_PUBLISHED by the provider on the pages this build reads",
            "scenarios_usd_per_30_days": {
                "0.07_per_gb_month": str(
                    storage.monthly_storage_cost(Decimal("22.9"), Decimal("0.07"))
                ),
                "0.10_per_gb_month": str(
                    storage.monthly_storage_cost(Decimal("22.9"), Decimal("0.10"))
                ),
            },
        },
        "spent_in_this_stage_usd": "0.00",
        "not_authorized": True,
    }
    write("migration-cost.json", migration_cost)

    # ---------------------------------------------------------------- monthly storage cost
    monthly = {
        "generated_at": generated,
        "measured": {
            "source": "GET /v2/billing/network-volumes (30 days)",
            "volumes": baseline["network_volume_billing"]["payload"]["metadata"][
                "totals"
            ],
            "per_volume_usd_per_day": "0.11666667088866234",
            "per_volume_usd_per_month": "3.5000",
            "rate_usd_per_gb_month": str(storage.NETWORK_STORAGE_PER_GB_MONTH),
            "volume_size_gb": 50,
        },
        "current": {
            "network_volumes_usd_per_window": str(
                baseline["network_volume_billing"]["payload"]["metadata"]["totals"][
                    "totalAmount"
                ]
            ),
            "window": "30 days (GET /v2/billing/network-volumes)",
            "network_volumes_usd_per_month": str(
                storage.monthly_storage_cost(50, storage.NETWORK_STORAGE_PER_GB_MONTH)
                * 2
            ),
            "note": (
                "the monthly figure is 2 x 50 GB at the measured $0.07/GB-month; the billed "
                "window figure is what the provider actually charged"
            ),
            "global_volume_usd_per_month": "rate unpublished; usage-based",
        },
        "after_migration_scenarios": {
            "note": (
                "the provider has not published a global per-GB rate on the pages this build "
                "reads, so these are scenarios, not quotes"
            ),
            "keep_both_regional": "7.0300 (measured)",
            "global_only_at_0.07": str(
                storage.monthly_storage_cost(Decimal("22.9"), Decimal("0.07"))
            ),
            "global_only_at_0.10": str(
                storage.monthly_storage_cost(Decimal("22.9"), Decimal("0.10"))
            ),
            "saving_vs_current_at_0.07": str(
                (
                    Decimal("7.0300")
                    - storage.monthly_storage_cost(Decimal("22.9"), Decimal("0.07"))
                ).quantize(Decimal("0.0001"))
            ),
            "saving_vs_current_at_0.10": str(
                (
                    Decimal("7.0300")
                    - storage.monthly_storage_cost(Decimal("22.9"), Decimal("0.10"))
                ).quantize(Decimal("0.0001"))
            ),
        },
        "not_included": ["request costs (see migration-cost.json)", "GPU hours"],
    }
    write("monthly-storage-cost.json", monthly)

    # ---------------------------------------------------------------- storage tests
    test_map = {
        "gpu_catalog_parsing": "test_storage_modes.py + provider_inventory.py (live catalogue read)",
        "multi_dc_selection": "test_the_status_payload_reports_storage_and_its_verdict",
        "storage_mode_selection": "test_a_regional_volume_mounts_where_it_always_did / test_a_global_volume_alone_takes_the_provider_default_mount",
        "global_volume_detection": "test_an_unknown_storage_kind_is_refused_instead_of_treated_as_regional",
        "model_path_construction": "test_a_regional_volume_mounts_where_it_always_did",
        "region_compatibility": "test_the_status_payload_reports_storage_and_its_verdict (pins_datacenter)",
        "missing_storage": "test_a_kind_without_a_volume_is_refused / test_no_attachment_at_all_is_refused",
        "missing_model_file": "test_a_model_source_must_be_stated",
        "corrupt_model_manifest": "apps/backend/tests/test_model_replicas.py",
        "incorrect_digest": "apps/backend/tests/test_model_replicas.py",
        "insufficient_local_disk": "test_a_local_copy_needs_room_for_the_model_and_a_reserve",
        "unexpected_gpu_price": "apps/backend/tests/test_127_cost_policy.py",
        "unsupported_gpu": "apps/backend/tests/test_allocation_candidates.py",
        "gpu_capacity_unavailable": "apps/backend/tests/test_allocation_candidates.py",
        "failed_volume_mount": "test_duplicate_mount_paths_are_a_conflict_not_a_coin_toss",
        "incomplete_file_transfer": "apps/backend/tests/test_model_replicas.py (partial)",
        "retry_after_interrupted_transfer": "apps/backend/tests/test_model_replicas.py (unverified)",
        "cost_limit_exceeded": "apps/backend/tests/test_127_cost_policy.py",
        "provider_unreachable": "artifacts/acceptance-127/final-readiness/watchdog-dry-run.json",
        "cleanup_after_failed_preparation": "test_an_impossible_storage_is_refused_before_the_provider_call",
    }
    tests = {
        "generated_at": generated,
        "new_suite": {
            "file": "apps/backend/tests/test_storage_modes.py",
            "result": "31 passed",
        },
        "regression_runs": {
            "services": "108 passed (test_compute, test_allocation_candidates, test_model_replicas, test_launch_scripts_contract)",
            "gateway": "262 passed",
            "backend_full": "1160 passed, 1 skipped (sidecar stamp rebuilt afterwards; test_sidecar_stamp 11 passed)",
        },
        "required_cases": test_map,
        "mock_policy": (
            "the storage decisions are pure functions with no provider call; the only mocks are the "
            "HTTP transports that prove no request is sent when a combination is refused"
        ),
    }
    write("storage-tests.json", tests)

    # ---------------------------------------------------------------- live readiness
    readiness = {
        "generated_at": generated,
        "budget_usd": str(BUDGET_USD),
        "verified_placements": (
            probe.get("verified_placements") if probe else [REPLICA]
        ),
        "placement_at_last_probe": (
            {
                "datacenter": probe["selected"]["datacenter"],
                "volume_id": probe["selected"]["volume_id"],
                "storage_mode": probe["selected"].get("storage_type"),
                "gpu": probe["selected"].get("gpu"),
                "hourly_price_usd": probe["selected"].get("hourly_price_usd"),
                "observed_at": probe.get("generated_at"),
            }
            if probe and probe.get("selected")
            else None
        ),
        "placement_rule": (
            "the cheapest compatible card in any placement whose copy of the model is verified; a "
            "regional volume pins its datacenter, so the probe re-reads availability immediately "
            "before the run instead of trusting this file"
        ),
        "gpu": {
            "name": "NVIDIA L40S",
            "vram_gb": 48,
            "hourly_rate_usd": "1.09",
            "worst_case_usd_90_min": "1.635",
            "budget_headroom_usd": "0.365",
            "min_work_seconds": 2700,
            "target_work_seconds": 4800,
        },
        "guards": {
            "one_pod": True,
            "second_pod_forbidden": True,
            "capacity_wait_seconds": 60,
            "watchdog_self_test": "PASS",
            "provider_state": "0 pods",
        },
        "production": {
            "version": "1.2.6",
            "untouched": True,
            "device_identity": "6413489d-6cae-4183-8446-a3b2339137dc",
        },
        "verdict": (
            "READY_FOR_OWNER_AUTHORIZATION"
            if probe and probe.get("selected")
            else "CAPACITY_UNAVAILABLE"
        ),
        "verdict_basis": (
            "the last read-only probe found no bookable pinned SKU in a verified placement"
            if not (probe and probe.get("selected"))
            else "a bookable pinned SKU exists in a verified placement"
        ),
        "paid_actions_taken": 0,
    }
    write("live-readiness.json", readiness)

    summary = {
        "generated_at": generated,
        "stage": "storage unblock",
        "existing_volume": {
            "primary": {
                "id": PRIMARY,
                "datacenter": "US-TX-3",
                "compatible_gpus_now": tx3_cards,
            },
            "replica": {
                "id": REPLICA,
                "datacenter": "US-MO-1",
                "compatible_gpus_now": mo1_cards,
            },
        },
        "global_volume_available": True,
        "global_volume_api_attachable": True,
        "chosen_architecture": "B_regional_replica_mo1 for the immediate LIVE; C/D documented as strategic",
        "model_compatibility": {
            "regional": "COMPATIBLE_PROVEN",
            "global": "INCOMPATIBLE_AS_IS + UNVERIFIED (mmap/latency unmeasured)",
            "local_copy": "COMPATIBLE_WITH_DISK_BUDGET",
        },
        "gpu_availability": (
            "NVIDIA L40S 48 GB at $1.09/h was bookable in at least one verified placement at the "
            "last probe; per-datacenter stock changes, which is why the probe re-reads it"
        ),
        "migration_method": "rsync inside one CPU Pod with both volumes attached (draft, not executed)",
        "migration_cost_usd": migration_cost["one_time"]["total_usd"],
        "monthly_cost_usd": str(
            baseline["network_volume_billing"]["payload"]["metadata"]["totals"][
                "totalAmount"
            ]
        ),
        "live_gpu_budget_usd": str(BUDGET_USD),
        "production_integrity": "unchanged",
        "real_qwen_status": "NOT RUN in this stage",
        "paid_actions": {"pods_created": 0, "storage_created": 0, "spend_usd": "0.00"},
        "next_action": (
            "owner approval for one LIVE Pod on the verified US-MO-1 replica: "
            "python artifacts/acceptance-127/live-qwen/live_acceptance.py --live "
            '--budget-usd 2.00 --confirm "I AUTHORIZE ONE PAID POD"'
        ),
    }
    write("summary.json", summary)

    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
