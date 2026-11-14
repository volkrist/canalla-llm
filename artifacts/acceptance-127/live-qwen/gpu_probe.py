"""Read-only GPU selection across every storage placement this build can actually serve from.

The 1.2.6 harness knew one Network Volume and therefore one datacenter, so it answered
`CAPACITY_UNAVAILABLE` whenever that single datacenter had no compatible card - even while a second
volume, holding a *verified* copy of the same model, had an available GPU. The provider scopes a
Network Volume to its own datacenter; it does not scope the model to one volume.

So this probe:

1. reads the account's Network Volumes (``GET /v2/network-volumes``), each with its datacenter;
2. reads the replica registry (``docs/replica-*.json``) and asks ``app.compute.replicas`` whether
   each *added* placement provably holds this exact model, hash and runtime;
3. reads the live GPU catalogue once and lets ``app.compute.candidates`` - the product's own policy -
   order the candidates, with a refused replica skipped and its reason kept.

It never creates, adopts, starts or stops compute, and it writes only the evidence file. The price
it reports is the provider's price *now*, and ``cost_policy`` decides whether the approved budget
leaves a work window that is worth starting.

    python artifacts/acceptance-127/live-qwen/gpu_probe.py
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "apps" / "backend"
OUT = Path(__file__).resolve().parent / "final-live"
PRIMARY_VOLUME = "uwgeaie5b0"
MIN_VRAM_GB = 48
BUDGET_USD = Decimal("2.00")

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(BACKEND))

import runpod_api_tools
from app.compute import candidates as policy
from app.compute import cost_policy
from app.compute import storage as storage_layout
from app.compute.replicas import (
    ReplicaExpectation,
    parse_replicas,
    placement_refusals,
)

# What this build's model actually is. Read from the deployment's own pin, never from a replica.
EXPECTATION = ReplicaExpectation(
    model_id="orcarouter-qwen38-27b-q5km",
    model_sha256="4ad98832b3d376dd274df5bba59dd34834f0f401593e1463f38fd5fa219293b1",
    model_bytes=20_752_787_712,
    runtime_version="b11565",
    # An unstated expectation is silence and never contradicts a verified record: the desktop
    # release version must not be compared with the Gateway service version that gated the copy.
    bootstrap_version="",
)


def get(path: str, key: str):
    return runpod_api_tools.call(path, key)


def rows_of(payload, *keys) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for name in keys:
            value = payload.get(name)
            if isinstance(value, list):
                return value
    return []


def replica_records() -> list[dict]:
    """Every replica record the repository carries, flattened and deduplicated by volume."""
    by_volume: dict[str, dict] = {}
    for path in sorted((REPO / "docs").glob("replica-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for replica in parse_replicas(payload):
            by_volume[replica.volume_id] = {
                "volume_id": replica.volume_id,
                "datacenter": replica.datacenter,
                "model_id": replica.model_id,
                "model_path": replica.model_path,
                "model_bytes": replica.model_bytes,
                "model_sha256": replica.model_sha256,
                "runtime_version": replica.runtime_version,
                "bootstrap_version": replica.bootstrap_version,
                "last_verified": replica.last_verified,
                "ready": replica.ready,
                "source": path.name,
            }
    return list(by_volume.values())


def main() -> int:
    key = runpod_api_tools.read_provider_secret()
    if not key:
        print(json.dumps({"ok": False, "reason": "credential_not_configured"}))
        return 2

    status, volumes_payload = get("/v2/network-volumes", key)
    volume_rows = rows_of(volumes_payload, "networkVolumes", "volumes", "data")
    if status != 200 or not volume_rows:
        print(
            json.dumps({"ok": False, "reason": "volumes_read_failed", "status": status})
        )
        return 1

    volumes = []
    for row in volume_rows:
        if not isinstance(row, dict):
            continue
        datacenter = str(row.get("dataCenterId") or row.get("dataCenter") or "")
        if not datacenter or not row.get("id"):
            continue
        volumes.append(
            {
                "id": str(row.get("id")),
                "name": row.get("name"),
                "size_gb": row.get("size"),
                "type": row.get("type"),
                "datacenter": datacenter,
                "primary": str(row.get("id")) == PRIMARY_VOLUME,
            }
        )
    volumes.sort(key=lambda item: (not item["primary"], item["datacenter"], item["id"]))
    if not any(item["primary"] for item in volumes):
        print(json.dumps({"ok": False, "reason": "primary_volume_missing"}))
        return 1

    records = replica_records()
    placements = tuple(
        policy.Placement(
            datacenter=item["datacenter"],
            volume_id=item["id"],
            storage_type=policy.REGIONAL_VOLUME,
        )
        for item in volumes
    )
    # The volume production already runs on is proven by production; every added one must carry a
    # verified record, or it is skipped with its reason kept in the plan audit.
    require_records = tuple(item["id"] for item in volumes if not item["primary"])
    refusals = placement_refusals(
        placements, records, EXPECTATION, require_records=require_records
    )

    query = urllib.parse.urlencode(
        {
            "include": "AVAILABILITY",
            "product": "POD",
            "count": 1,
            "minCudaVersion": "12.1",
        }
    )
    status, catalogue = get("/v2/catalog/gpus?" + query, key)
    catalogue_rows = rows_of(catalogue, "gpus")
    if status != 200 or not catalogue_rows:
        print(
            json.dumps(
                {"ok": False, "reason": "catalogue_read_failed", "status": status}
            )
        )
        return 1

    offers = []
    elsewhere = []
    for row in catalogue_rows:
        if not isinstance(row, dict) or row.get("manufacturer") != "NVIDIA":
            continue
        try:
            memory = int(row.get("memory") or 0)
            secure = float((row.get("price") or {}).get("secure") or 0)
            community = float((row.get("price") or {}).get("community") or 0)
        except (TypeError, ValueError):
            continue
        if memory < MIN_VRAM_GB:
            continue
        centers = {
            str(center.get("id")): str(center.get("availability") or "NONE")
            for center in row.get("dataCenters") or []
            if isinstance(center, dict) and center.get("id")
        }
        offer = {
            "id": str(row.get("id")),
            "name": str(row.get("name") or row.get("id")),
            "vram_gb": memory,
            "price": {"secure": secure, "community": community},
            "secure": bool(row.get("secure")),
            "community": bool(community > 0),
            "data_centers": centers,
        }
        catalogued = {
            "gpu_id": offer["id"],
            "name": offer["name"],
            "vram_gb": memory,
            "hourly_price_usd": secure,
            "tier": "secure",
            "datacenters": centers,
        }
        if secure <= 0:
            continue
        offers.append(offer)
        if not any(value.upper() in policy.USABLE_STOCK for value in centers.values()):
            elsewhere.append(catalogued)

    plan = policy.build_plan(
        offers,
        min_vram_gb=MIN_VRAM_GB,
        max_hourly_price=BUDGET_USD,
        placements=placements,
        replica_refusals=refusals,
        strategy="cheapest",
    )

    candidates = [
        {
            **candidate.audit(),
            "name": candidate.gpu_name,
            "hourly_price_usd": float(candidate.hourly_rate),
            "storage_mode": storage_layout.REGIONAL_VOLUME,
        }
        for candidate in plan.candidates
    ]
    selected = candidates[0] if candidates else None
    reference = None
    if not selected:
        reference = (
            min(elsewhere, key=lambda item: item["hourly_price_usd"])
            if elsewhere
            else None
        )

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "read_only": True,
        "budget_usd": float(BUDGET_USD),
        "minimum_vram_gb": MIN_VRAM_GB,
        "model": {
            "id": EXPECTATION.model_id,
            "sha256": EXPECTATION.model_sha256,
            "bytes": EXPECTATION.model_bytes,
            "runtime_version": EXPECTATION.runtime_version,
            "context_window": 32768,
        },
        "volumes": volumes,
        "replicas": records,
        "placements": [item.audit() for item in placements],
        "replica_refusals": dict(refusals),
        "verified_placements": [
            item.volume_id for item in placements if item.volume_id not in refusals
        ],
        "catalogue_offers": len(offers),
        "candidates": candidates[:10],
        "selected": selected,
        "volume": next(
            (
                {
                    "id": item["id"],
                    "datacenter": item["datacenter"],
                    "type": item["type"],
                    "size_gb": item["size_gb"],
                }
                for item in volumes
                if selected and item["id"] == selected["volume_id"]
            ),
            None,
        ),
        "compatible_but_not_in_any_verified_placement": elsewhere[:10],
        "reference": reference,
        "plan_reason": plan.reason,
        "ok": selected is not None,
    }

    if selected is None:
        if not report["verified_placements"]:
            report["verdict"] = "NO_VERIFIED_MODEL_COPY"
            report["reason"] = (
                "no placement holds a verified copy of this model; nothing was created"
            )
        else:
            report["verdict"] = "CAPACITY_UNAVAILABLE"
            report["reason"] = (
                "no compatible GPU is available in any verified placement ("
                + ", ".join(report["verified_placements"])
                + "); no Pod was created and no capacity wait was started"
            )
        if reference is not None:
            report["reference_policy"] = cost_policy.plan(
                float(BUDGET_USD),
                reference["hourly_price_usd"],
                min_work_seconds=cost_policy.MIN_LIVE_WORK_SECONDS,
            ).to_dict()
        report["policy"] = None
    else:
        chosen = cost_policy.plan(
            float(BUDGET_USD),
            float(selected["hourly_price_usd"]),
            min_work_seconds=cost_policy.MIN_LIVE_WORK_SECONDS,
        )
        report["policy"] = chosen.to_dict()
        report["verdict"] = (
            "READY"
            if chosen.feasible and chosen.sufficient
            else "PRICE_OR_TIME_INFEASIBLE"
        )
        if report["verdict"] != "READY":
            report["reason"] = (
                "the current price leaves no work window that justifies a paid run; no Pod was created"
            )

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "gpu-selection.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    effective = report["policy"] or report.get("reference_policy")
    (OUT / "cost-policy.json").write_text(
        json.dumps(
            {
                "generated_at": report["generated_at"],
                "budget_usd": float(BUDGET_USD),
                "selected_rate_usd": (selected or {}).get("hourly_price_usd"),
                "policy": report["policy"],
                "reference": report.get("reference"),
                "reference_policy": report.get("reference_policy"),
                "worst_case_within_budget": bool(
                    effective and effective["worst_case_usd"] <= float(BUDGET_USD)
                ),
                "worst_case_basis": "selected" if report["policy"] else "reference",
                "verdict": report["verdict"],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=1)[:5000])
    return 0 if report["verdict"] == "READY" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
