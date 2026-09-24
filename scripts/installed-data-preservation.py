"""Installed Canalla acceptance: a non-secret data inventory, a product backup, and a comparison.

The 1.2.0 release replaces an already-installed 1.2.0, so the question this answers is narrow and
factual: *what exactly was in the operator's data root before the install, and is every bit of it
still there afterwards?* "The app opened" proves nothing about preservation, and a table-by-table
count is the cheapest honest answer.

It never prints a secret. Counts, ids, table and setting *names*, file names and sizes only — no
values from a settings row, no credential, no token, no API key. Settings are reported as the set of
keys, because a key appearing or disappearing is the signal; its value is the operator's business.

Subcommands:

    inventory --data-root <dir>            # non-secret facts, as JSON on stdout
    backup --data-root <dir> --label <s>   # the product's own BackupService, created then verified
    compare --before <a.json> --after <b.json>

The backup runs the *product's* code path (`app.backup.archive.BackupService`), not a copy of it, so
"there is a verified backup" means the same thing here as it does in Settings.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Tables whose rows are secrets or identity material: their existence is reported, their contents
# never are. Counting them is still meaningful — a restore that lost every credential would show it.
SENSITIVE_HINTS = ("secret", "token", "credential", "password", "key")

COUNTED_TABLES = (
    "users",
    "chats",
    "messages",
    "projects",
    "memories",
    "documents",
    "paired_devices",
    "compute_preferences",
    "tool_runs",
    "backup_events",
    "tasks",
)


def open_db(data_root: Path) -> sqlite3.Connection:
    """The product database, read-only. Never creates a file, never takes a write lock."""
    db = data_root / "data" / "alex.db"
    if not db.is_file():
        raise SystemExit(f"no product database at {db}")
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def count(connection: sqlite3.Connection, table: str) -> int | None:
    try:
        return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except sqlite3.Error:
        return None


def inventory(data_root: Path) -> dict:
    connection = open_db(data_root)
    try:
        names = tables(connection)
        counts = {name: count(connection, name) for name in names}
        facts: dict = {
            "data_root": str(data_root),
            "tables": len(names),
            "counts": counts,
        }

        # The identity the device keeps. Not a secret: it is the id the operator's own device shows,
        # and it is exactly what must *not* change across an install. The Desktop writes it at the
        # root of its data dir (`host.rs`: `data_dir().join("device.json")`), not under `runtime/`.
        device = data_root / "device.json"
        facts["device_json"] = device.is_file()
        if device.is_file():
            try:
                payload = json.loads(device.read_text(encoding="utf-8"))
                facts["device_id"] = payload.get("device_id") or payload.get("id")
                facts["device_name"] = payload.get("display_name")
            except (OSError, ValueError):
                facts["device_id"] = "unreadable"

        # Enrollment and installation identity: presence only.
        for name in ("install.id", "session.id", "jwt.secret"):
            facts[f"runtime_{name}"] = (data_root / "runtime" / name).is_file()

        settings = data_root / "data" / "settings.json"
        if settings.is_file():
            try:
                payload = json.loads(settings.read_text(encoding="utf-8"))
                facts["settings_keys"] = (
                    sorted(payload) if isinstance(payload, dict) else []
                )
            except (OSError, ValueError):
                facts["settings_keys"] = "unreadable"

        baseline = data_root / "runtime" / "tor.json"
        facts["tor_proof"] = baseline.is_file()

        backups = data_root / "backups"
        if backups.is_dir():
            ids = sorted(entry.name for entry in backups.iterdir() if entry.is_dir())
            facts["backup_ids"] = ids
            facts["backup_count"] = len(ids)

        for hint in SENSITIVE_HINTS:
            hits = [name for name in names if hint in name.lower()]
            if hits:
                facts.setdefault("sensitive_tables", []).extend(
                    f"{name}={counts.get(name)}" for name in hits
                )
        return facts
    finally:
        connection.close()


def create_backup(data_root: Path, label: str) -> dict:
    """Create and verify a backup through the product's own service."""
    os.environ["ALEX_LLM_DATA_DIR"] = str(data_root)
    sys.path.insert(0, str(REPO / "apps" / "backend"))
    from app.backup.archive import BackupService, verify_backup

    service = BackupService(data_root)
    # The synchronous entry point is the one a caller who owns the process uses (CLI, startup,
    # restore). Calling it here means this script exercises the shipped path, not a parallel copy.
    created = service.create_now(label=label)
    verified = verify_backup(service.resolve(created["id"]))
    return {"created": created, "verified": verified}


def compare(before: dict, after: dict) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True

    for key in sorted(set(before.get("counts", {})) | set(after.get("counts", {}))):
        was = before.get("counts", {}).get(key)
        now = after.get("counts", {}).get(key)
        if was != now:
            # Growth is a real possibility (a launch records presence, a backup records an event);
            # loss is not. Report both, only fail on loss.
            lost = now is None or (was is not None and now < was)
            lines.append(f"{'FAIL' if lost else 'GROW'} {key}: {was} -> {now}")
            ok = ok and not lost
        else:
            lines.append(f"PASS {key}: {now}")

    if before.get("device_id") != after.get("device_id"):
        ok = False
        lines.append(
            f"FAIL device_id: {before.get('device_id')} -> {after.get('device_id')}"
        )
    else:
        lines.append(f"PASS device_id: {after.get('device_id')}")

    was_keys = set(before.get("settings_keys") or [])
    now_keys = set(after.get("settings_keys") or [])
    if was_keys - now_keys:
        ok = False
        lines.append(f"FAIL settings keys lost: {sorted(was_keys - now_keys)}")
    else:
        added = sorted(now_keys - was_keys)
        lines.append(f"PASS settings keys kept{f' (+{added})' if added else ''}")

    was_backups = set(before.get("backup_ids") or [])
    now_backups = set(after.get("backup_ids") or [])
    if was_backups - now_backups:
        ok = False
        lines.append(f"FAIL backups lost: {sorted(was_backups - now_backups)}")
    else:
        lines.append(f"PASS backups kept ({len(now_backups)} present)")

    return ok, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)

    inv = sub.add_parser("inventory")
    inv.add_argument("--data-root", required=True, type=Path)
    inv.add_argument("--out", type=Path, default=None)

    bak = sub.add_parser("backup")
    bak.add_argument("--data-root", required=True, type=Path)
    bak.add_argument("--label", required=True)

    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("--before", required=True, type=Path)
    cmp_.add_argument("--after", required=True, type=Path)

    args = parser.parse_args(argv)

    if args.action == "inventory":
        facts = inventory(args.data_root)
        text = json.dumps(facts, indent=2, ensure_ascii=False, sort_keys=True)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
            print(f"wrote {args.out}")
        print(text)
        return 0

    if args.action == "backup":
        result = create_backup(args.data_root, args.label)
        created = result["created"]
        # The size and the id are safe; a backup never carries a secret (see AGENTS.md).
        print(f"backup_id={created.get('id')}")
        print(f"backup_bytes={created.get('bytes')}")
        print(f"backup_files={created.get('files')}")
        print(f"backup_label={created.get('label')}")
        print(f"verified={result['verified'].get('verified')}")
        if not result["verified"].get("verified"):
            print(f"verify_detail={result['verified']}")
            return 4
        return 0

    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    ok, lines = compare(before, after)
    for line in lines:
        print(line)
    print("PRESERVATION", "PASS" if ok else "FAIL")
    return 0 if ok else 5


if __name__ == "__main__":
    raise SystemExit(main())
