#!/usr/bin/env python
"""Read-only scan of >=48 GB NVIDIA availability across every placeable datacenter."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import runpod_api_tools as tools  # noqa: E402

MAX_HOURLY = 1.79
MIN_VRAM = 48
OUT = Path(__file__).resolve().parent / "gpu-availability.json"

QUERY = """
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

DCS = """
query { dataCenters { id name location } }
"""


def num(value):
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
        "https://api.runpod.io/graphql", key, method="POST", payload={"query": DCS}
    )
    centers = ((payload or {}).get("data") or {}).get("dataCenters") or []
    if not centers:
        print(json.dumps({"ok": False, "reason": "no_datacenters", "http": status}))
        return 1

    rows: dict[str, list[dict]] = {}
    for center in centers:
        dc = center["id"]
        status, payload = tools.call(
            "https://api.runpod.io/graphql",
            key,
            method="POST",
            payload={"query": QUERY, "variables": {"dc": dc}},
        )
        found = []
        for gpu in ((payload or {}).get("data") or {}).get("gpuTypes") or []:
            if int(gpu.get("memoryInGb") or 0) < MIN_VRAM:
                continue
            secure, community = (
                num((gpu.get("lowestPrice") or {}).get("uninterruptablePrice")),
                num((gpu.get("lowestPrice") or {}).get("minimumBidPrice")),
            )
            usable = [v for v in (secure, community) if v and v <= MAX_HOURLY]
            if usable:
                found.append(
                    {
                        "id": gpu["id"],
                        "vram_gb": int(gpu.get("memoryInGb") or 0),
                        "secure_hourly_usd": secure,
                        "community_hourly_usd": community,
                    }
                )
        if found:
            rows[dc] = sorted(found, key=lambda item: min(
                v for v in (item["secure_hourly_usd"], item["community_hourly_usd"]) if v
            ))

    report = {
        "ok": True,
        "checked_at": tools.time.strftime("%Y-%m-%dT%H:%M:%SZ", tools.time.gmtime()),
        "datacenters_checked": len(centers),
        "datacenters_with_a_48gb_card_under_cap": sorted(rows),
        "by_datacenter": rows,
        "configured_placements": {"US-TX-3": "uwgeaie5b0", "US-MO-1": "y92bt25ru8"},
        "configured_placements_availability": {
            dc: rows.get(dc, []) for dc in ("US-TX-3", "US-MO-1")
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in (
        "ok", "checked_at", "datacenters_checked", "configured_placements_availability")},
        ensure_ascii=False, indent=2))
    print("dcs_with_stock:", len(report["datacenters_with_a_48gb_card_under_cap"]))
    for dc in report["datacenters_with_a_48gb_card_under_cap"]:
        print(" ", dc, json.dumps(report["by_datacenter"][dc][:3], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
