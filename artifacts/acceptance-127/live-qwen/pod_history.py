"""Read-only: what Pods this account created and when. Never creates, stops or adopts anything."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))

import runpod_api_tools  # noqa: E402

QUERY = """
query AlexHistory { myself { id clientBalance currentSpendPerHr
  pods { id name desiredStatus lastStatusChange costPerHr createdAt }
} }
"""


def main() -> int:
    key = runpod_api_tools.read_provider_secret()
    if not key:
        print(json.dumps({"ok": False, "reason": "credential_not_configured"}))
        return 2
    status, payload = runpod_api_tools.call(
        "https://api.runpod.io/graphql", key, method="POST", payload={"query": QUERY}
    )
    myself = ((payload or {}).get("data") or {}).get("myself") or {}
    pods = myself.get("pods") or []
    print(
        json.dumps(
            {
                "ok": status == 200,
                "status": status,
                "balance_usd": myself.get("clientBalance"),
                "current_spend_per_hr": myself.get("currentSpendPerHr"),
                "pod_count": len(pods),
                "pods": sorted(
                    (
                        {
                            "id": row.get("id"),
                            "name": row.get("name"),
                            "status": row.get("desiredStatus"),
                            "last_status_change": row.get("lastStatusChange"),
                            "created_at": row.get("createdAt"),
                            "cost_per_hr": row.get("costPerHr"),
                        }
                        for row in pods
                        if isinstance(row, dict)
                    ),
                    key=lambda row: row.get("created_at") or "",
                ),
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
