"""Read-only RunPod inventory for the 1.2.7 storage decision.

Only ``GET`` requests, with the credential the installed product already stores. Nothing is
created, adopted, started or stopped, and the key is never printed or written.

The output is the factual baseline the architecture comparison rests on:

    baseline.json                     account, pods, balance, billing
    global-volume-availability.json   what global volumes exist and what the API says about them
    gpu-catalog.json                  every compatible card and the datacenters that report stock

    python artifacts/acceptance-127/storage-unblock/provider_inventory.py
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
OUT = Path(__file__).resolve().parent
MIN_VRAM_GB = 48
CANDIDATE_NAMES = (
    "A40",
    "L40",
    "L40S",
    "RTX 6000 Ada",
    "RTX A6000",
    "A100",
    "H100",
)

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(BACKEND))

import runpod_api_tools
from app.compute import candidates as policy


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def main() -> int:
    key = runpod_api_tools.read_provider_secret()
    if not key:
        print(json.dumps({"ok": False, "reason": "credential_not_configured"}))
        return 2

    report: dict = {
        "generated_at": utcnow(),
        "read_only": True,
        "min_vram_gb": MIN_VRAM_GB,
    }

    status, pods = get("/v2/pods", key)
    pod_rows = rows_of(pods, "pods")
    report["pods"] = {
        "status": status,
        "count": len(pod_rows),
        "rows": [
            {
                "id": row.get("id"),
                "status": row.get("status"),
                "dataCenterId": row.get("dataCenterId"),
                "cloud": row.get("cloud"),
                "mounts": row.get("mounts"),
            }
            for row in pod_rows
            if isinstance(row, dict)
        ],
    }

    status, balance = get("/v2/account/balance", key)
    report["balance"] = {"status": status, "payload": balance}

    status, network = get("/v2/network-volumes", key)
    network_rows = rows_of(network, "networkVolumes", "volumes", "data")
    report["network_volumes"] = {
        "status": status,
        "count": len(network_rows),
        "rows": [
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "size_gb": row.get("size"),
                "type": row.get("type"),
                "data_center": row.get("dataCenterId") or row.get("dataCenter"),
            }
            for row in network_rows
            if isinstance(row, dict)
        ],
    }

    status, global_volumes = get("/v2/global-volumes", key)
    global_rows = rows_of(global_volumes, "globalVolumes", "volumes", "data")
    global_report = {
        "generated_at": report["generated_at"],
        "source": "/v2/global-volumes",
        "status": status,
        "count": len(global_rows),
        "rows": global_rows,
        "raw_keys": sorted(global_volumes)
        if isinstance(global_volumes, dict)
        else None,
        "note": (
            "A global volume has no datacenter by design. Its file inventory is only readable "
            "from inside a Pod (no S3 API), so content stays unverified until a Pod is allowed."
        ),
    }
    report["global_volumes"] = {
        "status": status,
        "count": len(global_rows),
        "rows": [
            {
                key_name: row.get(key_name)
                for key_name in (
                    "id",
                    "name",
                    "sizeBytes",
                    "usedBytes",
                    "createdAt",
                    "updatedAt",
                    "status",
                )
                if isinstance(row, dict) and key_name in row
            }
            for row in global_rows
            if isinstance(row, dict)
        ],
    }

    status, network_billing = get("/v2/billing/network-volumes", key)
    report["network_volume_billing"] = {"status": status, "payload": network_billing}

    status, aggregated = get("/v2/billing", key)
    report["billing"] = {"status": status, "payload": aggregated}

    query = urllib.parse.urlencode(
        {
            "include": "AVAILABILITY",
            "product": "POD",
            "count": 1,
            "minCudaVersion": "12.1",
        }
    )
    status, catalogue = get("/v2/catalog/gpus?" + query, key)
    offers = rows_of(catalogue, "gpus")
    compatible = []
    for row in offers:
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
        compatible.append(
            {
                "gpu_id": str(row.get("id")),
                "name": str(row.get("name") or row.get("id")),
                "vram_gb": memory,
                "price_secure_usd_hr": secure,
                "price_community_usd_hr": community,
                "secure": bool(row.get("secure")),
                "available_centers": sorted(
                    name
                    for name, value in centers.items()
                    if value.upper() in policy.USABLE_STOCK
                ),
                "datacenters": centers,
            }
        )
    compatible.sort(key=lambda row: (row["price_secure_usd_hr"] or 99, row["name"]))

    report["gpu_catalog"] = {
        "status": status,
        "compatible_count": len(compatible),
        "offers": compatible,
    }
    report["gpu_catalog_compact"] = [
        {
            "name": row["name"],
            "vram_gb": row["vram_gb"],
            "price_secure_usd_hr": row["price_secure_usd_hr"],
            "available_centers": row["available_centers"],
        }
        for row in compatible
    ]

    # The plan the product itself would build, once per storage mode, from this same catalogue read.
    # The exact row shape ``RunPodAPI.gpu_offers()`` produces, so the plan below is the
    # product's own policy over a real catalogue read, not a second implementation.
    rows_by_id = {
        row["gpu_id"]: {
            "id": row["gpu_id"],
            "name": row["name"],
            "vram_gb": row["vram_gb"],
            "price": {
                "secure": row["price_secure_usd_hr"],
                "community": row["price_community_usd_hr"],
            },
            "secure": row["secure"],
            "community": row["price_community_usd_hr"] > 0,
            "data_centers": row["datacenters"],
        }
        for row in compatible
    }
    plans = {}
    regional = tuple(
        policy.Placement(datacenter=str(row["data_center"]), volume_id=str(row["id"]))
        for row in report["network_volumes"]["rows"]
        if row.get("data_center")
    )
    if regional:
        plan = policy.build_plan(
            list(rows_by_id.values()),
            min_vram_gb=MIN_VRAM_GB,
            max_hourly_price=Decimal("2.00"),
            placements=regional,
        )
        plans["regional_placements"] = {
            "placements": [placement.audit() for placement in regional],
            "plan": plan.audit(),
            "reason": plan.reason,
        }
    if global_rows:
        global_placement = (
            policy.Placement(
                datacenter="",
                volume_id=str(global_rows[0].get("id")),
                storage_type=policy.GLOBAL_VOLUME,
            ),
        )
        plan = policy.build_plan(
            list(rows_by_id.values()),
            min_vram_gb=MIN_VRAM_GB,
            max_hourly_price=Decimal("2.00"),
            placements=global_placement,
        )
        plans["global_placement"] = {
            "placements": [placement.audit() for placement in global_placement],
            "plan": plan.audit(),
            "reason": plan.reason,
        }
    report["allocation_plans"] = plans

    (OUT / "baseline.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (OUT / "global-volume-availability.json").write_text(
        json.dumps(global_report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (OUT / "gpu-catalog.json").write_text(
        json.dumps(
            {
                "generated_at": report["generated_at"],
                "read_only": True,
                "minimum_vram_gb": MIN_VRAM_GB,
                "compatible": compatible,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
