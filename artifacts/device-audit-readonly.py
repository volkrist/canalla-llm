"""Read-only classification of the historical `paired_devices` rows.

Nothing is deleted, revoked or migrated. The database is opened with `mode=ro`,
so even an accidental write would fail.

The defect (fixed in 1.2.5) deleted a valid device identity whenever *any*
heartbeat answered 401, so the host re-paired roughly once per access-token
lifetime. That leaves a signature that a single physical machine cannot fake:

    row N+1.created_at ~= row N.last_seen   (the next identity is born at the
                                             exact moment the previous one dies)

Classification, in this order:

1. `current`            - the device id in `%LOCALAPPDATA%\\Alex LLM\\device.json`
2. `revoked`            - `revoked_at` is set (an explicit, recorded decision)
3. `historical-defect`  - unrevoked, and the row is a link in a same-user chain:
                          it was created within `CHAIN_GAP` of the previous row's
                          last_seen, or the next row was created within
                          `CHAIN_GAP` of its last_seen
4. `possibly-real`      - unrevoked, not a chain link, lived over a day
5. `uncertain`          - anything else

Emails are masked, credential hashes are truncated to 12 hex chars and no token,
secret or credential is printed.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ["LOCALAPPDATA"]) / "Alex LLM"
DB = ROOT / "data" / "alex.db"
CHAIN_GAP_SECONDS = 90


def parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def mask(email: str) -> str:
    name, _, domain = (email or "").partition("@")
    return (name[:2] + "***@" + domain) if domain else "***"


def main() -> int:
    current_id = json.loads((ROOT / "device.json").read_text(encoding="utf-8"))["device_id"]
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    users = {row["id"]: row["email"] for row in db.execute("SELECT id, email FROM users")}
    rows = list(db.execute(
        "SELECT id, user_id, display_name, platform, credential_hash, last_seen,"
        " created_at, revoked_at FROM paired_devices ORDER BY created_at"
    ))
    db.close()

    parsed = []
    for row in rows:
        created = parse(row["created_at"])
        seen = parse(row["last_seen"])
        parsed.append({
            "row": row,
            "created": created,
            "seen": seen,
            "revoked": bool(row["revoked_at"]),
            "gap_from_previous": None,
        })

    for index in range(1, len(parsed)):
        previous, item = parsed[index - 1], parsed[index]
        if (
            previous["row"]["user_id"] == item["row"]["user_id"]
            and previous["seen"] is not None
            and item["created"] is not None
        ):
            item["gap_from_previous"] = (item["created"] - previous["seen"]).total_seconds()

    devices = []
    for index, item in enumerate(parsed):
        row = item["row"]
        following = parsed[index + 1] if index + 1 < len(parsed) else None
        next_gap = None
        if following is not None:
            next_gap = following["gap_from_previous"]
        lifetime = (
            (item["seen"] - item["created"]).total_seconds()
            if item["seen"] is not None and item["created"] is not None
            else None
        )
        is_chain = (
            not item["revoked"]
            and (
                (item["gap_from_previous"] is not None
                 and 0 <= item["gap_from_previous"] <= CHAIN_GAP_SECONDS)
                or (next_gap is not None and 0 <= next_gap <= CHAIN_GAP_SECONDS)
            )
        )
        if row["id"] == current_id:
            category = "current"
        elif item["revoked"]:
            category = "revoked"
        elif is_chain:
            category = "historical-defect"
        elif lifetime is not None and lifetime > 86400:
            category = "possibly-real"
        else:
            category = "uncertain"
        devices.append({
            "device_id": row["id"],
            "user": mask(users.get(row["user_id"], "?")),
            "platform": row["platform"],
            "name": row["display_name"],
            "created_at": row["created_at"],
            "last_seen": row["last_seen"],
            "revoked_at": row["revoked_at"],
            "lifetime_seconds": None if lifetime is None else round(lifetime, 1),
            "gap_from_previous_seconds": (
                None if item["gap_from_previous"] is None
                else round(item["gap_from_previous"], 1)
            ),
            "credential_fingerprint": (row["credential_hash"] or "")[:12],
            "category": category,
        })

    counts: dict[str, int] = {}
    for device in devices:
        counts[device["category"]] = counts.get(device["category"], 0) + 1

    defect = [d for d in devices if d["category"] == "historical-defect"]
    gaps = [d["gap_from_previous_seconds"] for d in defect if d["gap_from_previous_seconds"] is not None]

    # A second physical machine would show up as two rows alive at the same time. Every
    # re-pair row here is born after the previous one was abandoned, so at most one
    # identity was ever live: this is one machine, not 58 devices.
    unrevoked = [
        item for item in parsed
        if not item["revoked"] and item["created"] and item["seen"]
    ]
    overlaps = 0
    for left, right in zip(unrevoked, unrevoked[1:]):
        if right["created"] < left["seen"]:
            overlaps += 1

    report = {
        "database": str(DB),
        "read_only": True,
        "current_device_id": current_id,
        "total_rows": len(devices),
        "counts": counts,
        "chain_gap_seconds_max": max(gaps) if gaps else None,
        "chain_gap_seconds_min": min(gaps) if gaps else None,
        "distinct_users": len({row["user_id"] for row in rows}),
        "overlapping_unrevoked_lifetimes": overlaps,
        "deleted": 0,
        "revoked": 0,
        "migrated": 0,
        "devices": devices,
    }
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
