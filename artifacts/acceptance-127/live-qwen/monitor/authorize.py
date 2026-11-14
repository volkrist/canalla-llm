"""Store the owner's one-Pod permission for the automatic LIVE run.

The monitor will not spend a cent without this record, and prose is never enough: the exact phrase
must be supplied on the command line, and the record names the budget, the Pod count and an expiry.
It is written outside the repository so a grant is never committed, and it is consumed by the run.

    python .../monitor/authorize.py --confirm "I AUTHORIZE ONE PAID POD" --budget-usd 2.00
    python .../monitor/authorize.py --status
    python .../monitor/authorize.py --revoke
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from monitor import (
    BUDGET_USD,
    DATACENTER,
    GPU_ID,
    MODEL_ID,
    PHRASE,
    SCHEMA,
    VOLUME_ID,
    grant_path,
    read_grant,
    runtime_dir,
    save_json,
    utcnow,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Authorize exactly one paid LIVE Pod.")
    parser.add_argument("--confirm", default="", help=f'the exact phrase: "{PHRASE}"')
    parser.add_argument("--budget-usd", type=float, default=BUDGET_USD)
    parser.add_argument(
        "--days", type=int, default=30, help="how long the permission stays valid"
    )
    parser.add_argument("--revoke", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--state-dir", default=None)
    args = parser.parse_args(argv)
    rdir = runtime_dir(args.state_dir)

    if args.status:
        verdict = read_grant(rdir)
        print(
            json.dumps(
                {
                    "path": str(grant_path(rdir)),
                    "authorized": verdict["valid"],
                    "reason": verdict["reason"],
                    "grant": verdict["grant"],
                },
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0 if verdict["valid"] else 1

    if args.revoke:
        path = grant_path(rdir)
        if not path.is_file():
            print(json.dumps({"revoked": False, "reason": "no_grant"}))
            return 1
        grant = json.loads(path.read_text(encoding="utf-8"))
        grant["revoked"] = True
        grant["revoked_at"] = utcnow()
        save_json(path, grant)
        print(json.dumps({"revoked": True, "path": str(path)}, ensure_ascii=False))
        return 0

    if args.confirm != PHRASE:
        print(
            json.dumps(
                {
                    "authorized": False,
                    "reason": "wrong_phrase",
                    "expected": PHRASE,
                },
                ensure_ascii=False,
            )
        )
        return 2
    if not 0 < args.budget_usd <= BUDGET_USD + 1e-9:
        print(
            json.dumps(
                {
                    "authorized": False,
                    "reason": "budget_out_of_range",
                    "max_usd": BUDGET_USD,
                },
                ensure_ascii=False,
            )
        )
        return 2
    if not 1 <= args.days <= 365:
        print(json.dumps({"authorized": False, "reason": "days_out_of_range"}))
        return 2

    now = datetime.now(timezone.utc)
    grant = {
        "schema": SCHEMA,
        "grant_id": uuid.uuid4().hex,
        "phrase": PHRASE,
        "budget_usd": round(float(args.budget_usd), 2),
        "max_pods": 1,
        # The exact run this permission covers. A different card, volume or datacenter is not a
        # wider permission; the monitor refuses a record that names another scope.
        "allowed_model": MODEL_ID,
        "allowed_gpu": GPU_ID,
        "allowed_volume": VOLUME_ID,
        "allowed_datacenter": DATACENTER,
        "scope": "one LIVE Qwen acceptance: TaskDesk, one Pod, 90 billable minutes",
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(days=args.days)).isoformat(timespec="seconds"),
        "used": False,
        "used_at": None,
        "revoked": False,
    }
    save_json(grant_path(rdir), grant)
    print(
        json.dumps(
            {
                "authorized": True,
                "path": str(grant_path(rdir)),
                "budget_usd": grant["budget_usd"],
                "max_pods": 1,
                "scope": grant["scope"],
                "allowed_gpu": grant["allowed_gpu"],
                "allowed_volume": grant["allowed_volume"],
                "expires_at": grant["expires_at"],
                "note": "The monitor will start exactly one paid Pod when the GPU is available.",
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
