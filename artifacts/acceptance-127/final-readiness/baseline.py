"""Production baseline, captured read-only before and after this stage.

The database is opened with `mode=ro`; nothing is migrated, restored or written. The comparison is
structural (ids, counts, integrity), never a secret, and the report must show that the installed
application kept the same data and the same active device.

Run:  python artifacts/acceptance-127/final-readiness/baseline.py --label start
      python artifacts/acceptance-127/final-readiness/baseline.py --label end
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
ROOT = Path(os.environ["LOCALAPPDATA"]) / "Alex LLM"
DB = ROOT / "data" / "alex.db"
DEVICE = ROOT / "device.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="start")
    args = parser.parse_args()
    payload: dict = {
        "label": args.label,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_root_present": ROOT.is_dir(),
        "database_present": DB.is_file(),
    }
    payload["git"] = {
        "branch": subprocess.run(
            ["git", "branch", "--show-current"], cwd=str(REPO), capture_output=True, text=True
        ).stdout.strip(),
        "head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO), capture_output=True, text=True
        ).stdout.strip(),
    }
    if DEVICE.is_file():
        try:
            payload["local_device_id"] = json.loads(DEVICE.read_text(encoding="utf-8")).get("device_id", "")
        except ValueError:
            payload["local_device_id"] = ""
    if not DB.is_file():
        payload["verdict"] = "BLOCKED"
        payload["reason"] = "production database not found"
    else:
        import sqlite3

        connection = sqlite3.connect("file:" + DB.as_posix() + "?mode=ro", uri=True)
        try:
            payload["counts"] = {
                table: connection.execute(f"select count(*) from {table}").fetchone()[0]
                for table in ("users", "chats", "messages", "paired_devices", "local_tasks")
            }
            payload["integrity"] = connection.execute("pragma integrity_check").fetchone()[0]
            payload["foreign_keys"] = connection.execute("pragma foreign_key_check").fetchall()[:5]
            payload["tasks_by_status"] = dict(
                connection.execute(
                    "select status, count(*) from local_tasks group by status order by status"
                ).fetchall()
            )
            rows = connection.execute(
                "select id, revoked_at, created_at from paired_devices order by created_at"
            ).fetchall()
            payload["devices"] = [
                {"id": row[0], "revoked": bool(row[1]), "created_at": str(row[2])} for row in rows
            ]
            active = connection.execute(
                "select id from paired_devices where revoked_at is null order by created_at desc limit 1"
            ).fetchone()
            payload["active_device_id"] = active[0] if active else ""
        finally:
            connection.close()
        payload["verdict"] = "OK" if payload["integrity"] == "ok" and not payload["foreign_keys"] else "FAIL"

    health = None
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=10) as response:
            health = json.loads(response.read() or b"{}")
    except Exception:
        health = None
    payload["installed_app"] = {
        "health": "up" if health else "down",
        "version": (health or {}).get("version"),
    }
    (OUT / f"baseline-{args.label}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=1)[:2000])
    return 0 if payload["verdict"] == "OK" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
