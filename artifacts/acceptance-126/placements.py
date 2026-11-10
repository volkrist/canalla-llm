"""One read-only answer to "where could the configured model actually run right now?".

The model, the llama.cpp runtime and the volume state live on a RunPod Network Volume, and a
volume is bound to one datacenter. So "is there a GPU" is never a global question: a card that
cannot mount one of the account's volumes cannot serve this product, no matter how much VRAM it
has. Both the historical "no GPU" conclusions were wrong for exactly this reason — one looked at a
single card type, the other at a single datacenter.

Everything here is a catalogue read. Nothing is created, adopted, stopped or deleted.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import runpod_api_tools as tools  # noqa: E402

MIN_VRAM_GB = 48
MAX_HOURLY_USD = 1.79

VOLUMES = "query { myself { networkVolumes { id name dataCenterId size } } }"

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


def _float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def volumes(key: str) -> list[dict]:
    """Every Network Volume the account owns, with the datacenter that pins it."""
    status, payload = tools.call(
        "https://api.runpod.io/graphql", key, method="POST", payload={"query": VOLUMES}
    )
    if status != 200 or not isinstance(payload, dict):
        return []
    found = ((payload.get("data") or {}).get("myself") or {}).get("networkVolumes") or []
    return [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "datacenter": item.get("dataCenterId"),
            "size_gb": item.get("size"),
        }
        for item in found
        if item.get("id") and item.get("dataCenterId")
    ]


def gpus_in(key: str, datacenter: str, *, min_vram_gb: int = MIN_VRAM_GB) -> list[dict]:
    """Cards the provider will place in `datacenter` right now, cheapest first.

    `lowestPrice(dataCenterId: …)` returning null is the provider saying "not here", which is
    different from "no such card".
    """
    status, payload = tools.call(
        "https://api.runpod.io/graphql",
        key,
        method="POST",
        payload={"query": GPU_BY_DC, "variables": {"dc": datacenter}},
    )
    if status != 200 or not isinstance(payload, dict):
        return []
    rows = []
    for gpu in ((payload.get("data") or {}).get("gpuTypes")) or []:
        if int(gpu.get("memoryInGb") or 0) < min_vram_gb:
            continue
        secure = _float((gpu.get("lowestPrice") or {}).get("uninterruptablePrice"))
        community = _float((gpu.get("lowestPrice") or {}).get("minimumBidPrice"))
        rates = [value for value in (secure, community) if value]
        if not rates:
            continue
        rows.append(
            {
                "id": gpu["id"],
                "display_name": gpu.get("displayName"),
                "vram_gb": int(gpu.get("memoryInGb") or 0),
                "secure_hourly_usd": secure,
                "community_hourly_usd": community,
                "cheapest_hourly_usd": min(rates),
                "datacenter": datacenter,
            }
        )
    return sorted(rows, key=lambda item: item["cheapest_hourly_usd"])


def candidates(
    key: str,
    *,
    max_hourly_usd: float = MAX_HOURLY_USD,
    min_vram_gb: int = MIN_VRAM_GB,
    allow_community: bool = False,
    volume_rows: list[dict] | None = None,
) -> list[dict]:
    """Every `(card, datacenter, volume)` the account could start right now inside the caps.

    A placement is only offered for a volume the account already owns: a card in a datacenter with
    no volume cannot mount the model, and creating a second permanent volume is a cost the release
    budget does not authorise.
    """
    rows = volume_rows if volume_rows is not None else volumes(key)
    usable = []
    for volume in rows:
        for gpu in gpus_in(key, volume["datacenter"], min_vram_gb=min_vram_gb):
            secure = gpu["secure_hourly_usd"]
            hourly = gpu["secure_hourly_usd"]
            if hourly is None or hourly > max_hourly_usd:
                if allow_community and gpu["community_hourly_usd"]:
                    if gpu["community_hourly_usd"] <= max_hourly_usd:
                        hourly = gpu["community_hourly_usd"]
                    else:
                        continue
                else:
                    continue
            usable.append(
                {
                    **gpu,
                    "selected_hourly_usd": hourly,
                    "secure_only_rate": secure is not None,
                    "volume_id": volume["id"],
                    "volume_name": volume.get("name"),
                }
            )
    return sorted(usable, key=lambda item: item["selected_hourly_usd"])


def snapshot(
    key: str,
    *,
    max_hourly_usd: float = MAX_HOURLY_USD,
    min_vram_gb: int = MIN_VRAM_GB,
    allow_community: bool = False,
) -> dict:
    """A full, dated statement of what is and is not placeable — the monitor's check unit."""
    tested_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows = volumes(key)
    if not rows:
        return {
            "ok": False,
            "tested_at": tested_at,
            "reason": "no_network_volumes",
            "volumes": [],
            "candidates": [],
        }
    found = candidates(
        key,
        max_hourly_usd=max_hourly_usd,
        min_vram_gb=min_vram_gb,
        allow_community=allow_community,
        volume_rows=rows,
    )
    scanned = []
    for volume in rows:
        scanned.append(
            {
                "volume_id": volume["id"],
                "datacenter": volume["datacenter"],
                "gpus_at_least_48gb": [
                    {
                        "id": gpu["id"],
                        "vram_gb": gpu["vram_gb"],
                        "secure_hourly_usd": gpu["secure_hourly_usd"],
                        "community_hourly_usd": gpu["community_hourly_usd"],
                    }
                    for gpu in gpus_in(key, volume["datacenter"], min_vram_gb=min_vram_gb)
                ],
            }
        )
    return {
        "ok": True,
        "tested_at": tested_at,
        "max_hourly_usd": max_hourly_usd,
        "min_vram_gb": min_vram_gb,
        "allow_community": allow_community,
        "volumes": rows,
        "scanned": scanned,
        "candidates": found,
        "verdict": "PLACEABLE" if found else "NOT_PLACEABLE",
    }
