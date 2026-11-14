"""Store the owner's one-Pod permission for the automatic LIVE run.

The monitor will not spend a cent without this record, and prose is never enough: the exact phrase
must be supplied on the command line, and the record names the budget, the Pod count, an expiry and
an explicit placement list.

The placement list is not decoration. Both paid Network Volumes are candidates, so a grant that
names one of them is a different (narrower) permission than one that names both. A scope change is
therefore allowed only as an *in-place upgrade of the same grant*: same ``grant_id``, same creation
time, same expiry, still ``used: false``, written through a validated temporary file that atomically
replaces the original. There is never a moment - and never a crash - that leaves two valid
permissions on disk, and a consumed grant is never revived.

    python .../monitor/authorize.py --confirm "I AUTHORIZE ONE PAID POD" --budget-usd 2.00
    python .../monitor/authorize.py --confirm "I AUTHORIZE ONE PAID POD" --scope both
    python .../monitor/authorize.py --status
    python .../monitor/authorize.py --revoke
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from monitor import (  # noqa: E402
    BUDGET_USD,
    GPU_ID,
    GRANT_SCHEMA,
    MODEL_ID,
    PHRASE,
    FileLock,
    Busy,
    grant_path,
    read_grant,
    runtime_dir,
    save_json,
    utcnow,
    validate_grant,
)
from placement_records import PLACEMENTS  # noqa: E402

SCOPES = {
    "both": tuple((item["datacenter"], item["volume_id"]) for item in PLACEMENTS),
    "mo1": tuple(
        (item["datacenter"], item["volume_id"])
        for item in PLACEMENTS
        if item["volume_id"] == "y92bt25ru8"
    ),
    "tx3": tuple(
        (item["datacenter"], item["volume_id"])
        for item in PLACEMENTS
        if item["volume_id"] == "uwgeaie5b0"
    ),
}


def placements_payload(pairs) -> list[dict]:
    return [
        {"datacenter": datacenter, "volume_id": volume_id, "gpu": GPU_ID}
        for datacenter, volume_id in pairs
    ]


def _existing(path: Path) -> tuple[dict | None, str]:
    if not path.is_file():
        return None, "absent"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "malformed"
    return (payload if isinstance(payload, dict) else None), "present"


def _atomic_replace(path: Path, payload: dict) -> dict:
    """Prove the new file valid, then swap it into place under the monitor's own lock."""
    verdict = validate_grant(payload)
    if not verdict["valid"]:
        raise SystemExit(f"refusing to store an invalid grant: {verdict['reason']}")
    temporary = path.with_suffix(path.suffix + ".new")
    save_json(temporary, payload)
    try:
        written = json.loads(temporary.read_text(encoding="utf-8"))
        if not validate_grant(written)["valid"]:
            raise SystemExit("refusing: the written grant did not survive validation")
        lock = FileLock(path.parent / "monitor.lock")
        # A scheduled pass may hold the lock for ~35 seconds (two checks 30 s apart). Waiting for
        # it is correct: the swap must never race a check that is reading the grant.
        for attempt in range(90):
            try:
                lock.acquire()
                break
            except Busy:
                if attempt == 89:
                    raise SystemExit("refusing: the monitor lock is still held") from None
                time.sleep(1)
        try:
            os.replace(temporary, path)
        finally:
            lock.release()
    finally:
        temporary.unlink(missing_ok=True)
    return verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Authorize exactly one paid LIVE Pod.")
    parser.add_argument("--confirm", default="", help=f'the exact phrase: "{PHRASE}"')
    parser.add_argument(
        "--budget-usd",
        type=float,
        default=None,
        help=f"at most {BUDGET_USD:.2f}; defaults to the existing grant's budget on an upgrade",
    )
    parser.add_argument(
        "--scope",
        choices=sorted(SCOPES),
        default="both",
        help="placements this permission covers (default: both paid volumes)",
    )
    parser.add_argument(
        "--days", type=int, default=30, help="how long a new permission stays valid"
    )
    parser.add_argument("--revoke", action="store_true")
    parser.add_argument(
        "--replace-consumed",
        action="store_true",
        help="archive a consumed permission and store a new one for a new owner decision",
    )
    parser.add_argument(
        "--consume",
        action="store_true",
        help="record that a Pod was created: the one paid attempt is spent",
    )
    parser.add_argument("--reason", default="", help="why the permission is being consumed")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--state-dir", default=None)
    args = parser.parse_args(argv)
    rdir = runtime_dir(args.state_dir)
    path = grant_path(rdir)

    if args.status:
        verdict = read_grant(rdir)
        grant = verdict.get("grant") or {}
        print(
            json.dumps(
                {
                    "path": str(path),
                    "authorized": verdict["valid"],
                    "reason": verdict["reason"],
                    "grant_id": grant.get("grant_id"),
                    "budget_usd": grant.get("budget_usd"),
                    "max_pods": grant.get("max_pods"),
                    "used": grant.get("used"),
                    "expires_at": grant.get("expires_at"),
                    "placements": verdict.get("placements") or [],
                    "grant": grant,
                },
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0 if verdict["valid"] else 1

    if args.consume:
        if not path.is_file():
            print(json.dumps({"consumed": False, "reason": "no_grant"}))
            return 1
        grant = json.loads(path.read_text(encoding="utf-8"))
        if grant.get("used"):
            print(
                json.dumps(
                    {"consumed": True, "already": True, "grant_id": grant.get("grant_id")}
                )
            )
            return 0
        grant["used"] = True
        grant["used_at"] = utcnow()
        grant["consumed_reason"] = args.reason or "paid Pod observed"
        save_json(path, grant)
        print(
            json.dumps(
                {
                    "consumed": True,
                    "already": False,
                    "grant_id": grant.get("grant_id"),
                    "used_at": grant["used_at"],
                    "reason": grant["consumed_reason"],
                    "note": "The monitor can no longer create a Pod. A new attempt needs a new owner decision.",
                },
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0

    if args.revoke:
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
                {"authorized": False, "reason": "wrong_phrase", "expected": PHRASE},
                ensure_ascii=False,
            )
        )
        return 2
    if args.days < 1 or args.days > 365:
        print(json.dumps({"authorized": False, "reason": "days_out_of_range"}))
        return 2

    existing, presence = _existing(path)
    if presence == "malformed":
        print(json.dumps({"authorized": False, "reason": "existing_grant_malformed"}))
        return 3

    budget = args.budget_usd
    if budget is None:
        budget = float((existing or {}).get("budget_usd") or BUDGET_USD)
    if not 0 < budget <= BUDGET_USD + 1e-9:
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

    pairs = SCOPES[args.scope]
    now = datetime.now(timezone.utc)

    if args.replace_consumed:
        # A consumed permission is archived, never edited. The new record is a new owner decision
        # with its own id and lifetime; exactly one `authorization-grant.json` ever exists, so the
        # monitor can never see two valid permissions.
        if existing is None:
            print(json.dumps({"authorized": False, "reason": "no_grant"}))
            return 3
        if not existing.get("used"):
            print(
                json.dumps(
                    {
                        "authorized": False,
                        "reason": "grant_not_consumed",
                        "note": "use the normal upgrade path; nothing was replaced",
                    }
                )
            )
            return 3
        archive = path.with_name(
            f"authorization-grant.consumed-{existing.get('grant_id') or 'unknown'}.json"
        )
        save_json(archive, existing)
        grant = {
            "schema": GRANT_SCHEMA,
            "grant_id": uuid.uuid4().hex,
            "phrase": PHRASE,
            "budget_usd": round(float(budget), 2),
            "max_pods": 1,
            "allowed_model": MODEL_ID,
            "allowed_gpu": GPU_ID,
            "allowed_placements": placements_payload(pairs),
            "scope": "one LIVE Qwen acceptance: TaskDesk, one Pod, 90 billable minutes",
            "created_at": now.isoformat(timespec="seconds"),
            "expires_at": (now + timedelta(days=args.days)).isoformat(timespec="seconds"),
            "used": False,
            "used_at": None,
            "revoked": False,
            "replaces": existing.get("grant_id"),
        }
        verdict = _atomic_replace(path, grant)
        mode = "replaced_consumed"
    elif existing is None:
        grant = {
            "schema": GRANT_SCHEMA,
            "grant_id": uuid.uuid4().hex,
            "phrase": PHRASE,
            "budget_usd": round(float(budget), 2),
            "max_pods": 1,
            "allowed_model": MODEL_ID,
            "allowed_gpu": GPU_ID,
            "allowed_placements": placements_payload(pairs),
            "scope": "one LIVE Qwen acceptance: TaskDesk, one Pod, 90 billable minutes",
            "created_at": now.isoformat(timespec="seconds"),
            "expires_at": (now + timedelta(days=args.days)).isoformat(timespec="seconds"),
            "used": False,
            "used_at": None,
            "revoked": False,
        }
        verdict = _atomic_replace(path, grant)
        mode = "created"
    else:
        state = validate_grant(existing, now)
        reason = state["reason"]
        if reason == "already_used":
            print(
                json.dumps(
                    {
                        "authorized": False,
                        "reason": "already_consumed",
                        "grant_id": existing.get("grant_id"),
                        "note": "A second paid Pod needs its own separate owner decision.",
                    },
                    ensure_ascii=False,
                )
            )
            return 4
        if reason in {"revoked", "expired", "malformed", "wrong_phrase", "missing_expiry"}:
            print(
                json.dumps(
                    {
                        "authorized": False,
                        "reason": reason,
                        "grant_id": existing.get("grant_id"),
                        "note": "Monitoring continues; no paid Pod is created.",
                    },
                    ensure_ascii=False,
                )
            )
            return 4
        if reason != "ok":
            print(
                json.dumps(
                    {"authorized": False, "reason": reason, "grant_id": existing.get("grant_id")}
                )
            )
            return 4
        # Same grant, new scope: identity, creation time and expiry are preserved so the record
        # stays one permission rather than becoming a second one with a fresh lifetime.
        grant = dict(existing)
        grant["allowed_placements"] = placements_payload(pairs)
        grant["budget_usd"] = round(float(budget), 2)
        grant["max_pods"] = 1
        grant["used"] = False
        grant["used_at"] = None
        grant["revoked"] = False
        # Schema 2 expresses the scope once, as a list. Leaving the schema-1 single-placement keys
        # next to it would invite a reader (or a future validator) to think the narrower value is
        # still in force.
        grant.pop("allowed_volume", None)
        grant.pop("allowed_datacenter", None)
        grant["schema"] = GRANT_SCHEMA
        verdict = _atomic_replace(path, grant)
        mode = "upgraded"

    effective = verdict.get("grant") or {}
    print(
        json.dumps(
            {
                "authorized": True,
                "mode": mode,
                "path": str(path),
                "grant_id": effective.get("grant_id"),
                "budget_usd": effective.get("budget_usd"),
                "max_pods": effective.get("max_pods"),
                "allowed_gpu": effective.get("allowed_gpu"),
                "placements": verdict.get("placements"),
                "created_at": effective.get("created_at"),
                "expires_at": effective.get("expires_at"),
                "used": effective.get("used"),
                "archived": mode == "replaced_consumed",
                "note": "The monitor starts exactly one paid Pod when an authorized placement is bookable.",
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
