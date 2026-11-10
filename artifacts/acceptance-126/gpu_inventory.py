#!/usr/bin/env python
"""Free, read-only GPU inventory for the 1.2.6 completion.

Answers three questions the previous "no GPU" conclusion never separated:

1. which NVIDIA cards with >= 48 GB VRAM exist at all;
2. what each costs (hourly, secure and community);
3. which of them the provider will actually place next to the existing Network Volume,
   because a card that cannot mount the model volume cannot serve the configured model.

Only account/catalogue reads. Nothing is created, adopted, stopped or deleted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import runpod_api_tools as tools  # noqa: E402

VOLUME_ID = "uwgeaie5b0"
MIN_VRAM = 48
MAX_HOURLY = 1.79
OUT = Path(__file__).resolve().parent / "gpu-inventory.json"

GPU_TYPES = """
query Inventory {
  gpuTypes {
    id
    displayName
    manufacturer
    memoryInGb
    secureCloud
    communityCloud
    lowestPrice(input: {gpuCount: 1}) {
      minimumBidPrice
      uninterruptablePrice
    }
  }
}
"""

VOLUMES = """
query Volumes { myself { networkVolumes { id name dataCenterId size } } }
"""

# Per-datacenter price: an empty/null price is the provider saying "not placeable here".
GPU_BY_DC = """
query ByDc($dc: String!) {
  gpuTypes {
    id
    displayName
    memoryInGb
    lowestPrice(input: {gpuCount: 1, dataCenterId: $dc}) {
      minimumBidPrice
      uninterruptablePrice
    }
  }
}
"""


def price(row: dict, field: str) -> float | None:
    value = ((row.get("lowestPrice") or {}).get(field))
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def main() -> int:
    key = tools.read_provider_secret()
    if not key:
        print(json.dumps({"ok": False, "reason": "credential_not_configured"}))
        return 2

    status, payload = tools.call(
        "https://api.runpod.io/graphql", key, method="POST", payload={"query": VOLUMES}
    )
    volumes = []
    if status == 200 and isinstance(payload, dict):
        volumes = ((payload.get("data") or {}).get("myself") or {}).get("networkVolumes") or []
    volume = next((item for item in volumes if item.get("id") == VOLUME_ID), None)

    status, payload = tools.call(
        "https://api.runpod.io/graphql", key, method="POST", payload={"query": GPU_TYPES}
    )
    rows = []
    if status == 200 and isinstance(payload, dict):
        rows = ((payload.get("data") or {}).get("gpuTypes")) or []
    if not rows:
        print(json.dumps({"ok": False, "reason": "gpu_types_failed", "http": status,
                          "body": str(payload)[:400]}))
        return 1

    big = [
        row
        for row in rows
        if str(row.get("manufacturer") or "").lower().startswith("nvidia")
        and int(row.get("memoryInGb") or 0) >= MIN_VRAM
    ]

    datacenter = (volume or {}).get("dataCenterId")
    per_dc: dict[str, list[dict]] = {}
    if datacenter:
        status, payload = tools.call(
            "https://api.runpod.io/graphql",
            key,
            method="POST",
            payload={"query": GPU_BY_DC, "variables": {"dc": datacenter}},
        )
        if status == 200 and isinstance(payload, dict):
            for row in ((payload.get("data") or {}).get("gpuTypes")) or []:
                if int(row.get("memoryInGb") or 0) < MIN_VRAM:
                    continue
                secure = price(row, "uninterruptablePrice")
                community = price(row, "minimumBidPrice")
                if secure or community:
                    per_dc.setdefault(str(row.get("id")), []).append(
                        {
                            "datacenter": datacenter,
                            "secure_hourly_usd": secure,
                            "community_hourly_usd": community,
                        }
                    )

    inventory = []
    for row in sorted(big, key=lambda item: int(item.get("memoryInGb") or 0)):
        secure = price(row, "uninterruptablePrice")
        community = price(row, "minimumBidPrice")
        within = [value for value in (secure, community) if value and value <= MAX_HOURLY]
        inventory.append(
            {
                "id": row.get("id"),
                "display_name": row.get("displayName"),
                "vram_gb": int(row.get("memoryInGb") or 0),
                "secure_cloud": bool(row.get("secureCloud")),
                "community_cloud": bool(row.get("communityCloud")),
                "secure_hourly_usd": secure,
                "community_hourly_usd": community,
                "within_rate_cap": bool(within),
                "cheapest_within_cap_usd": min(within) if within else None,
                "placeable_on_volume_datacenter": per_dc.get(str(row.get("id")), []),
            }
        )

    usable = [
        item
        for item in inventory
        if item["within_rate_cap"] and item["placeable_on_volume_datacenter"]
    ]
    report = {
        "ok": True,
        "volume": volume,
        "volume_datacenter": datacenter,
        "min_vram_gb": MIN_VRAM,
        "max_hourly_usd": MAX_HOURLY,
        "gpu_type_count": len(rows),
        "nvidia_48gb_plus_count": len(inventory),
        "inventory": inventory,
        "placeable_now_on_volume_dc": usable,
        "verdict": "PLACEABLE" if usable else "NOT_PLACEABLE_ON_VOLUME_DATACENTER",
        "checked_at": tools.time.strftime("%Y-%m-%dT%H:%M:%SZ", tools.time.gmtime()),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "ok", "volume_datacenter", "gpu_type_count", "nvidia_48gb_plus_count", "verdict",
        "checked_at")}, indent=2))
    print("placeable_now:", json.dumps(
        [
            {
                "id": item["id"],
                "vram": item["vram_gb"],
                "secure": item["placeable_on_volume_datacenter"][0]["secure_hourly_usd"],
                "community": item["placeable_on_volume_datacenter"][0]["community_hourly_usd"],
            }
            for item in usable
        ],
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
