"""Operator-side compute inspection for acceptance runs (read-mostly, never prints a secret).

Copied to the Gateway host by `scripts/acceptance-post-release-1.0.0.py`, which uses it to prove
provider truth that a client must never see: how many managed Pods exist, whether the Network
Volume still exists, and — as a last resort — to stop a managed session the product path could
not stop. It talks to the *deployed* Gateway code and settings.

    python gateway-compute-ops.py status
    python gateway-compute-ops.py stop [reason]
    python gateway-compute-ops.py volume

Every mode prints exactly one JSON document on stdout. The RunPod key is read from
`/etc/alex-gateway/runpod.env` (whose lines may carry CRLF) and is never echoed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ENV_FILES = (Path("/etc/alex-gateway/alex-gateway.env"), Path("/etc/alex-gateway/runpod.env"))


def load_env() -> list[str]:
    """Load the deployed env files, normalising Windows line endings. Values are never printed."""
    names: list[str] = []
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            if name:
                os.environ[name] = value.strip().strip('"').strip("'")
                names.append(name)
    return names


load_env()


def authority():
    """Build the deployed ComputeAuthority (import happens after the env is loaded)."""
    from gateway.compute import ComputeAuthority
    from gateway.config import get_settings

    return ComputeAuthority(get_settings())


def pod_row(pod) -> dict[str, Any]:
    return {
        "id": getattr(pod, "id", None),
        "name": getattr(pod, "name", None),
        "status": getattr(pod, "status", None),
        "cost": getattr(pod, "cost", None),
        "started_at": getattr(pod, "started_at", None),
    }


async def status_payload(compute) -> dict[str, Any]:
    payload = await compute.reconcile()
    out: dict[str, Any] = {
        "provider_configured": payload.get("configured"),
        "state": payload.get("state"),
        "error_code": payload.get("error_code"),
        "create_attempts": payload.get("create_attempts"),
        "session": payload.get("session"),
        "last_session": payload.get("last_session"),
        "queue": payload.get("queue"),
    }
    try:
        pods = await compute.api.list_pods()
        out["pods"] = [pod_row(pod) for pod in pods]
        out["active_pods"] = [pod_row(pod) for pod in compute.active_pods(pods)]
    except Exception as error:  # a provider read failure is reported, never hidden
        out["pods_error"] = f"{type(error).__name__}: {error}"
    try:
        out["balance"] = (await compute.status()).get("balance")
    except Exception as error:
        out["balance_error"] = f"{type(error).__name__}: {error}"
    return out


async def stop(compute, reason: str) -> dict[str, Any]:
    """Stop the managed session through the Gateway authority, tolerating a busy lease."""
    operation_id = f"acceptance-stop-{uuid.uuid4().hex[:12]}"
    last = ""
    for _ in range(6):
        try:
            payload = await compute.stop("operator-acceptance", operation_id=operation_id, reason=reason)
            return {
                "state": payload.get("state"),
                "error_code": payload.get("error_code"),
                "session": payload.get("session"),
            }
        except Exception as error:
            last = f"{type(error).__name__}: {error}"
            if getattr(error, "code", None) != "gateway_busy":
                break
            time.sleep(5)
    return {"error": last or "stop failed"}


async def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    compute = authority()
    if mode == "status":
        print(json.dumps(await status_payload(compute), ensure_ascii=False, default=str))
        return 0
    if mode == "stop":
        reason = sys.argv[2] if len(sys.argv) > 2 else "manual"
        stopped = await stop(compute, reason)
        print(
            json.dumps(
                {"stop": stopped, "after": await status_payload(compute)},
                ensure_ascii=False,
                default=str,
            )
        )
        return 0
    if mode == "volume":
        print(json.dumps(await compute.api.volume(), ensure_ascii=False, default=str))
        return 0
    print(json.dumps({"error": f"unknown mode {mode}"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
