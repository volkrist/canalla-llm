"""Read-only: the live US-TX-3 catalogue rows in full, plus pods and volumes. GETs only."""

from __future__ import annotations

import json
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "scripts"))

from runpod_api_tools import call, key_or_exit  # noqa: E402

KEY = key_or_exit()
DC = "US-TX-3"

status, payload = call("/v2/catalog/gpus?include=AVAILABILITY&product=POD&count=1", KEY)
rows = (payload or {}).get("gpus") if isinstance(payload, dict) else None
print(f"catalog/gpus HTTP {status} · rows={len(rows or [])}")

dcs = sorted(
    {
        str(center.get("id"))
        for row in rows or []
        for center in row.get("dataCenters") or []
    }
)
print(f"distinct datacenters named anywhere in the catalogue: {len(dcs)}")
print("  " + ", ".join(dcs))
raw = json.dumps(payload)
print(f"raw payload mentions \"{DC}\": {raw.count(DC)}")

seen = []
for row in rows or []:
    for center in row.get("dataCenters") or []:
        if str(center.get("id")) != DC:
            continue
        seen.append(
            {
                "gpu": row.get("id"),
                "vram_gb": row.get("memory"),
                "secure": bool(row.get("secure")),
                "price_secure": (row.get("price") or {}).get("secure"),
                "price_community": (row.get("price") or {}).get("community"),
                "stock": str(center.get("availability") or "NONE").upper(),
            }
        )
print(f"\nUS-TX-3 rows in the catalogue: {len(seen)} (any price, any stock)")
for item in sorted(seen, key=lambda r: (r["price_secure"] or 99)):
    within = "<=1.00/h" if (item["price_secure"] or 99) <= 1.00 else "over budget"
    bookable = item["stock"] in {"LOW", "MEDIUM", "HIGH"}
    print(
        f"  {item['gpu'][:44]:44} {item['vram_gb'] or 0:>3} GB  ${item['price_secure']}  "
        f"stock={item['stock']:6} secure={item['secure']}  {within}  bookable={bookable}"
    )

status, payload = call("/v2/pods", KEY)
pods = (payload or {}).get("pods") if isinstance(payload, dict) else None
print(f"\n/v2/pods HTTP {status}")
for pod in pods or []:
    print(
        f"  {pod.get('id')}  {pod.get('name')}  status={pod.get('status')}  "
        f"dc={pod.get('dataCenterId')}  created={pod.get('createdAt')}"
    )

status, payload = call("/v2/network-volumes", KEY)
volumes = payload if isinstance(payload, list) else (payload or {}).get("volumes")
print(f"\n/v2/network-volumes HTTP {status}")
print(" " + json.dumps(payload, ensure_ascii=False)[:1500])
