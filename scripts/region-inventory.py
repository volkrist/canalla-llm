"""Read-only RunPod inventory for a future second regional placement.

Never creates, never deletes, never mutates. Prints volumes, the GPU catalogue per datacenter and
the compatible (>= 48 GB) offers with their availability, so a second region can be chosen from
evidence instead of one snapshot.

**One snapshot is not a reliability ranking.** Run it on a schedule and pass ``--history`` so each
measurement is appended with its UTC timestamp; only a series of snapshots can support a claim
about which datacenter is actually usable over time. Nothing here decides a region.

Run on the Gateway host (reads the master key from /etc/alex-gateway/runpod.env) or anywhere with
RUNPOD_API_KEY exported.

Global Volumes: the beta public API does not expose attachment (tested 2026-09-25), so this script
only *reports* whether the account has any and never tries to create or attach one.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENV = Path("/etc/alex-gateway/runpod.env")
API = "https://api.runpod.io/v2"
GRAPHQL = "https://api.runpod.io/graphql"


def api_key() -> str:
    value = os.environ.get("RUNPOD_API_KEY", "").strip()
    if value:
        return value
    for line in ENV.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*(?:export\s+)?RUNPOD_API_KEY\s*=\s*(.+?)\s*$", line)
        if match:
            return match.group(1).strip().strip('"').strip("'")
    raise SystemExit(f"no RunPod key found in {ENV}")


def request(
    url: str, key: str, *, method: str = "GET", payload: dict | None = None, params: dict | None = None
) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode()
    if params:
        from urllib.parse import urlencode

        url = url + "?" + urlencode(params)
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "canalla-region-inventory/1.2.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            body = response.read().decode()
            return response.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as error:
        return error.code, {"error": error.read().decode()[:200]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history",
        default="",
        help="append a timestamped JSON measurement to this file (read-only elsewhere)",
    )
    args = parser.parse_args()
    key = api_key()
    measured_at = datetime.now(timezone.utc).isoformat()
    print(f"measured_at={measured_at}")
    print("== Network Volumes (REST v2) ==")
    status, payload = request(f"{API}/network-volumes", key)
    print(f"http={status}")
    volumes_seen = payload.get("networkVolumes") or payload.get("data") or []
    for volume in volumes_seen:
        print(
            f"  id={volume.get('id')} name={volume.get('name')!r} size={volume.get('size')}GB "
            f"dc={volume.get('dataCenterId') or volume.get('dataCenter')} "
            f"type={volume.get('type')} createdAt={volume.get('createdAt')}"
        )

    print("== Global Volumes (GraphQL, read-only) ==")
    status, payload = request(
        GRAPHQL,
        key,
        method="POST",
        payload={"query": "query { myself { globalVolumes { id name size dataCenterId } } }"},
    )
    if status != 200 or payload.get("errors"):
        print(f"  http={status} errors={json.dumps(payload.get('errors'))[:200]}")
    else:
        volumes = ((payload.get("data") or {}).get("myself") or {}).get("globalVolumes") or []
        print(f"  count={len(volumes)}")
        for volume in volumes:
            print(f"  id={volume.get('id')} name={volume.get('name')!r} size={volume.get('size')}")

    print("== Compatible GPU offers per datacenter (REST v2 catalogue) ==")
    status, payload = request(
        f"{API}/catalog/gpus",
        key,
        # The same slice the allocator reads: without ``include=AVAILABILITY`` the catalogue
        # reports no per-datacenter availability at all.
        params={"include": "AVAILABILITY", "product": "POD", "count": 1, "minCudaVersion": "12.8"},
    )
    if status != 200:
        print(f"  http={status} — catalogue unavailable")
        return 0
    rows: list[dict] = []
    for gpu in payload.get("gpus") or []:
        memory = gpu.get("memory") or gpu.get("vramGb") or 0
        if int(memory or 0) < 48:
            continue
        secure = (gpu.get("price") or {}).get("secure")
        for center in gpu.get("dataCenters") or []:
            availability = center.get("availability")
            if availability in (None, "NONE"):
                continue
            rows.append(
                {
                    "gpu": gpu.get("id"),
                    "name": gpu.get("name"),
                    "vram_gb": memory,
                    "dc": center.get("id"),
                    "price": secure,
                    "availability": availability,
                    "community": bool(gpu.get("community")),
                }
            )
    rows.sort(key=lambda row: (row["price"] if row["price"] is not None else 99, str(row["dc"])))
    for row in rows:
        print(
            f"  {row['dc']:<12} {row['gpu']:<24} vram={row['vram_gb']}GB "
            f"${row['price']}/h availability={row['availability']} community={row['community']}"
        )
    by_dc: dict[str, int] = {}
    for row in rows:
        by_dc[str(row["dc"])] = by_dc.get(str(row["dc"]), 0) + 1
    print("== compatible SKUs per datacenter (this snapshot only) ==")
    for dc, count in sorted(by_dc.items(), key=lambda item: -item[1]):
        print(f"  {dc}: {count}")

    if args.history:
        # A series, not a snapshot: each measurement keeps its own timestamp so a later ranking can
        # be argued from history ("was this datacenter usable over these days?") instead of from a
        # single lucky moment.
        record = {
            "measured_at": measured_at,
            "source": "runpod-rest-v2:/catalog/gpus?include=AVAILABILITY&product=POD",
            "volumes": [
                {
                    "id": volume.get("id"),
                    "name": volume.get("name"),
                    "size_gb": volume.get("size"),
                    "data_center": volume.get("dataCenterId") or volume.get("dataCenter"),
                    "type": volume.get("type"),
                }
                for volume in volumes_seen
            ],
            "offers": rows,
            "per_datacenter_sku_count": by_dc,
            "note": "availability is a moment-in-time signal, not a capacity guarantee",
        }
        path = Path(args.history)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"history appended: {path}")

    print("NOTE: one snapshot is not a reliability ranking; availability is a moment-in-time signal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
