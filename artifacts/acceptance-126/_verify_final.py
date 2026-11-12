"""Read-only post-release verification: database integrity and the stored device identity."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(os.environ["LOCALAPPDATA"]) / "Alex LLM"
DB = ROOT / "data" / "alex.db"

conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
print("db", DB, "exists", DB.is_file())
print("integrity", conn.execute("pragma integrity_check").fetchone())
print("users", conn.execute("select count(*) from users").fetchone())
print("chats", conn.execute("select count(*) from chats").fetchone())
print("messages", conn.execute("select count(*) from messages").fetchone())
print("paired_devices", conn.execute("select count(*) from paired_devices").fetchone())
conn.close()

device = json.loads((ROOT / "device.json").read_text(encoding="utf-8"))
print("device.json id", device.get("device_id") or device.get("id"))

checkpoints = json.loads(
    Path("artifacts/acceptance-126/checkpoints.json").read_text(encoding="utf-8")
)
entry = checkpoints.get("DEVICE_STABLE") if isinstance(checkpoints, dict) else None
print("checkpoint DEVICE_STABLE", json.dumps(entry, ensure_ascii=False)[:500])
