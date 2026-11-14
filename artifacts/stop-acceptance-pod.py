"""Stop the acceptance Pod that is currently billing, and confirm it through the provider.

The monitor is not allowed to leave a paid Pod behind, and neither is the operator. This is the
bounded manual stop for exactly one test Pod: it reads the key from the same OS store the product
uses, asks the provider to terminate that one id, then re-reads the account until it is empty.

    python artifacts/stop-acceptance-pod.py <pod-id>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import runpod_api_tools as tools  # noqa: E402


def account(key: str) -> dict:
    status, payload = tools.call("/v2/pods", key)
    rows = (payload or {}).get("pods") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(rows, list):
        return {"ok": False, "reason": "pod_list_failed", "status": status}
    return {"ok": True, "pod_count": len(rows), "pods": rows}


def main() -> int:
    pod_id = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not pod_id:
        print(json.dumps({"ok": False, "reason": "pod_id_required"}))
        return 2
    key = tools.read_provider_secret()
    if not key:
        print(json.dumps({"ok": False, "reason": "credential_not_configured"}))
        return 2
    before = account(key)
    result = tools.terminate(key, pod_id)
    deadline = time.monotonic() + 180
    after = before
    while time.monotonic() < deadline:
        time.sleep(6)
        after = account(key)
        if after.get("ok") and after.get("pod_count") == 0:
            break
    print(
        json.dumps(
            {
                "ok": bool(after.get("ok") and after.get("pod_count") == 0),
                "pod_id": pod_id,
                "terminate": result,
                "pod_count_before": before.get("pod_count"),
                "pod_count_after": after.get("pod_count"),
                "pods_after": [
                    {"id": row.get("id"), "status": row.get("status")}
                    for row in after.get("pods") or []
                ],
            },
            indent=1,
        )
    )
    return 0 if after.get("pod_count") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
