"""One bounded capacity confirmation for the approved placement.

The rule for this run is a single immediate read plus at most two short retries inside a 60-second
window - never a queue. This script is that window and nothing else: read-only catalogue calls, no
Pod, no adoption, no wait beyond the bound.

It answers two questions with facts:
    * is the pinned SKU bookable in the verified replica's datacenter right now?
    * which datacenter does the provider actually offer the pinned SKU in?

Run:  python artifacts/acceptance-127/live-qwen/capacity_check.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "final-live"
VOLUME_ID = "y92bt25ru8"
DATACENTER = "US-MO-1"
GPU_ID = "NVIDIA L40S"
MIN_VRAM_GB = 48
READS = 3
GAP_SECONDS = 20

sys.path.insert(0, str(REPO / "scripts"))

import runpod_api_tools


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read(key: str) -> dict:
    query = urllib.parse.urlencode(
        {
            "include": "AVAILABILITY",
            "product": "POD",
            "count": 1,
            "minCudaVersion": "12.1",
        }
    )
    status, payload = runpod_api_tools.call("/v2/catalog/gpus?" + query, key)
    if status != 200 or not isinstance(payload, dict):
        return {"ok": False, "status": status, "error": "catalogue_read_failed"}
    row = next(
        (
            item
            for item in payload.get("gpus") or []
            if isinstance(item, dict) and item.get("id") == GPU_ID
        ),
        None,
    )
    if row is None:
        return {"ok": True, "found": False, "skus_available_in_datacenter": []}
    centers = {
        str(center.get("id")): str(center.get("availability") or "NONE")
        for center in row.get("dataCenters") or []
        if isinstance(center, dict) and center.get("id")
    }
    return {
        "ok": True,
        "found": True,
        "price_secure_usd_hr": (row.get("price") or {}).get("secure"),
        "memory_gb": int(row.get("memory") or 0),
        "datacenter_availability": centers.get(DATACENTER, "NONE"),
        "bookable_datacenters": sorted(
            name for name, value in centers.items() if value.upper() not in {"NONE", ""}
        ),
        "skus_available_in_datacenter": sorted(
            str(item.get("id"))
            for item in payload.get("gpus") or []
            if isinstance(item, dict)
            and item.get("manufacturer") == "NVIDIA"
            and int(item.get("memory") or 0) >= MIN_VRAM_GB
            and any(
                str(center.get("id")) == DATACENTER
                and str(center.get("availability") or "NONE").upper()
                not in {"NONE", ""}
                for center in item.get("dataCenters") or []
                if isinstance(center, dict)
            )
        ),
    }


def main() -> int:
    key = runpod_api_tools.read_provider_secret()
    if not key:
        print(json.dumps({"ok": False, "reason": "credential_not_configured"}))
        return 2

    reads = []
    started = time.monotonic()
    for index in range(READS):
        if index:
            time.sleep(GAP_SECONDS)
        reads.append(
            {
                "at": utcnow(),
                "elapsed_s": round(time.monotonic() - started, 1),
                **read(key),
            }
        )

    last = reads[-1]
    ready_now = bool(
        last.get("found")
        and last.get("datacenter_availability", "NONE").upper() not in {"NONE", ""}
    )
    report = {
        "generated_at": utcnow(),
        "read_only": True,
        "volume_id": VOLUME_ID,
        "datacenter": DATACENTER,
        "gpu_id": GPU_ID,
        "window_seconds": round(time.monotonic() - started, 1),
        "reads": reads,
        "verdict": "READY" if ready_now else "CAPACITY_UNAVAILABLE",
        "reason": None
        if ready_now
        else (
            f"{GPU_ID} has no availability in {DATACENTER} for the verified replica "
            f"{VOLUME_ID}; no Pod was created and no capacity wait was started"
        ),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "capacity-check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if ready_now else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
