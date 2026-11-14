"""Free preflight: the exact GPU quote the isolated backend performs before it can start.

`_search` = `RunPodAPI.volume()` + `RunPodAPI.gpu_options(prefs)`. This runs both for the placement
the monitor chose, with the user's policy, and reports what the product would see. Read-only.
"""

from __future__ import annotations

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "apps" / "backend"))

import runpod_api_tools  # noqa: E402

from app.compute.runpod_api import RunPodAPI, RunPodError  # noqa: E402
from app.compute.schemas import ComputePreferences  # noqa: E402
from app.config import Settings  # noqa: E402


async def quote(datacenter: str, volume_id: str, key: str) -> dict:
    settings = Settings(
        app_env="test",
        jwt_secret="x" * 48,
        runpod_api_key=key,
        runpod_network_volume_id=volume_id,
        runpod_datacenter=datacenter,
    )
    api = RunPodAPI(settings)
    prefs = ComputePreferences(
        selection="automatic",
        strategy="cheapest",
        min_vram_gb=48,
        max_hourly_price=Decimal("1.09"),
        session_budget=Decimal("2.00"),
        auto_connect=False,
        auto_search=True,
    )
    row: dict = {"datacenter": datacenter, "volume_id": volume_id}
    try:
        await api.volume()
        row["volume_ok"] = True
        options = await api.gpu_options(prefs)
        selectable = [
            {
                "id": item.id,
                "name": item.name,
                "hourly_rate": float(item.hourly_rate),
                "availability": item.availability,
                "compatible": item.compatible,
            }
            for item in options
            if item.selectable
        ]
        row["options"] = len(options)
        row["selectable"] = selectable
        row["l40s"] = [
            {
                "id": item.id,
                "hourly_rate": float(item.hourly_rate),
                "availability": item.availability,
                "compatible": item.compatible,
                "selectable": item.selectable,
                "vram_gb": item.vram_gb,
            }
            for item in options
            if "L40S" in f"{item.id} {item.name}"
        ]
        row["ok"] = bool(selectable)
        if not selectable:
            row["reason"] = "no_selectable_gpu"
    except RunPodError as error:
        row.update({"ok": False, "error": error.code, "status": error.status})
    return row


async def main() -> int:
    key = runpod_api_tools.read_provider_secret()
    if not key:
        print(json.dumps({"ok": False, "reason": "credential_not_configured"}))
        return 2
    report = {
        "placements": [
            await quote("US-TX-3", "uwgeaie5b0", key),
            await quote("US-MO-1", "y92bt25ru8", key),
        ]
    }
    report["any_selectable"] = any(item.get("ok") for item in report["placements"])
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["any_selectable"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
