"""Free preflight fact: can the product's own API accept each placement's volume?

`RunPodAPI.volume()` is the read `_search` performs before it can quote a GPU. This script runs that
same call for both approved placements. It is read-only: no Pod, no volume, no write.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "apps" / "backend"))

import runpod_api_tools  # noqa: E402

from app.compute.runpod_api import RunPodAPI, RunPodError  # noqa: E402
from app.config import Settings  # noqa: E402

PLACEMENTS = (("US-TX-3", "uwgeaie5b0"), ("US-MO-1", "y92bt25ru8"))


async def main() -> int:
    key = runpod_api_tools.read_provider_secret()
    report: dict = {"credential": bool(key), "placements": []}
    for datacenter, volume_id in PLACEMENTS:
        settings = Settings(
            app_env="test",
            jwt_secret="x" * 48,
            runpod_api_key=key,
            runpod_network_volume_id=volume_id,
            runpod_datacenter=datacenter,
        )
        row = {"datacenter": datacenter, "volume_id": volume_id}
        try:
            volume = await RunPodAPI(settings).volume()
            row.update(
                {
                    "ok": True,
                    "volume_datacenter": volume.dataCenter,
                    "volume_type": volume.type,
                    "size_gb": volume.size,
                }
            )
        except RunPodError as error:
            row.update({"ok": False, "error": error.code, "status": error.status})
        report["placements"].append(row)
    report["verdict"] = (
        "READY" if all(item["ok"] for item in report["placements"]) else "BLOCKED"
    )
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["verdict"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
