"""Read-only GPU selection for the owner-approved LIVE run.

Calls only catalogue and volume `GET`s, with the credential the installed product already stores.
It never creates, adopts, starts or stops compute, and it writes only the evidence file.

The selection rule is the product's own: NVIDIA, Secure Cloud, at least 48 GB of VRAM, the model's
Network Volume mountable in its datacenter, cheapest first. The hourly price it reports is the one
the provider returns *now*, not the price from an old report; `cost_policy` then derives whether the
approved budget leaves a meaningful work window.

Run:  python artifacts/acceptance-127/live-qwen/gpu_probe.py
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "apps" / "backend"
OUT = Path(__file__).resolve().parent / "final-live"
VOLUME_ID = "uwgeaie5b0"
MIN_VRAM_GB = 48
BUDGET_USD = 2.00

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(BACKEND))

import runpod_api_tools  # noqa: E402

from app.compute import cost_policy  # noqa: E402


def get(path: str, key: str) -> tuple[int, object]:
    return runpod_api_tools.call(path, key)


def main() -> int:
    key = runpod_api_tools.read_provider_secret()
    if not key:
        print(json.dumps({"ok": False, "reason": "credential_not_configured"}))
        return 2

    status, volume = get(f"/v2/network-volumes/{VOLUME_ID}", key)
    if status != 200 or not isinstance(volume, dict):
        print(json.dumps({"ok": False, "reason": "volume_read_failed", "status": status}))
        return 1
    datacenter = str(volume.get("dataCenter") or volume.get("dataCenterId") or "")

    query = urllib.parse.urlencode(
        {"include": "AVAILABILITY", "product": "POD", "count": 1, "minCudaVersion": "12.1"}
    )
    status, catalogue = get("/v2/catalog/gpus?" + query, key)
    rows = catalogue.get("gpus") if isinstance(catalogue, dict) else None
    if status != 200 or not isinstance(rows, list):
        print(json.dumps({"ok": False, "reason": "catalogue_read_failed", "status": status}))
        return 1

    offers = []
    elsewhere = []
    for row in rows:
        if not isinstance(row, dict) or row.get("manufacturer") != "NVIDIA" or not row.get("secure"):
            continue
        try:
            memory = int(row.get("memory") or 0)
            price = float((row.get("price") or {}).get("secure") or 0)
        except (TypeError, ValueError):
            continue
        centers = {
            str(center.get("id")): str(center.get("availability") or "NONE")
            for center in row.get("dataCenters") or []
            if isinstance(center, dict) and center.get("id")
        }
        stock = centers.get(datacenter, "NONE")
        if price <= 0 or memory < MIN_VRAM_GB:
            continue
        offer = {
            "gpu_id": str(row.get("id")),
            "name": str(row.get("name") or row.get("id")),
            "vram_gb": memory,
            "hourly_price_usd": price,
            "availability": stock,
            "datacenter": datacenter,
            "tier": "secure",
            "datacenters": centers,
        }
        if stock.upper() == "NONE":
            elsewhere.append(offer)
            continue
        offers.append(offer)
    offers.sort(key=lambda offer: (offer["hourly_price_usd"], -offer["vram_gb"], offer["name"]))
    elsewhere.sort(key=lambda offer: (offer["hourly_price_usd"], -offer["vram_gb"], offer["name"]))
    selected = offers[0] if offers else None
    reference = elsewhere[0] if elsewhere else None

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "read_only": True,
        "volume": {"id": VOLUME_ID, "datacenter": datacenter, "type": volume.get("type")},
        "minimum_vram_gb": MIN_VRAM_GB,
        "budget_usd": BUDGET_USD,
        "catalogue_offers": len(offers),
        "candidates": offers[:10],
        "selected": selected,
        "compatible_but_not_in_volume_datacenter": elsewhere[:10],
        "reference": reference,
        "ok": selected is not None,
    }
    if selected is None:
        report["verdict"] = "CAPACITY_UNAVAILABLE"
        report["reason"] = (
            "no NVIDIA Secure GPU with at least 48 GB is currently available in the volume's "
            "datacenter; no Pod was created and no capacity wait was started"
        )
        policy = None
        if reference is not None:
            report["reference_policy"] = cost_policy.plan(
                BUDGET_USD,
                reference["hourly_price_usd"],
                min_work_seconds=cost_policy.MIN_LIVE_WORK_SECONDS,
            ).to_dict()
    else:
        policy = cost_policy.plan(
            BUDGET_USD,
            selected["hourly_price_usd"],
            min_work_seconds=cost_policy.MIN_LIVE_WORK_SECONDS,
        )
        report["policy"] = policy.to_dict()
        report["verdict"] = (
            "READY"
            if policy.feasible and policy.sufficient
            else "PRICE_OR_TIME_INFEASIBLE"
        )
    (OUT).mkdir(parents=True, exist_ok=True)
    (OUT / "gpu-selection.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (OUT / "cost-policy.json").write_text(
        json.dumps(
            {
                "generated_at": report["generated_at"],
                "budget_usd": BUDGET_USD,
                "selected_rate_usd": (selected or {}).get("hourly_price_usd"),
                "policy": (policy.to_dict() if policy else None),
                "reference": report.get("reference"),
                "reference_policy": report.get("reference_policy"),
                "worst_case_within_budget": bool(policy and policy.worst_case_usd <= BUDGET_USD),
                "verdict": report["verdict"],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=1)[:4000])
    return 0 if report["verdict"] == "READY" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
