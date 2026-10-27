"""Final state snapshot: versions, digests, device identity, counts, readiness. Read-only."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(os.environ["LOCALAPPDATA"]) / "Alex LLM"
INSTALL = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Canalla LLM"
DB = ROOT / "data" / "alex.db"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def tree_digest(root: Path) -> str:
    value = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            value.update(path.relative_to(root).as_posix().encode())
            value.update(digest(path).encode())
    return value.hexdigest()


def main() -> int:
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    counts = {
        table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("users", "chats", "messages", "paired_devices")
    }
    counts["revoked_devices"] = db.execute(
        "SELECT count(*) FROM paired_devices WHERE revoked_at IS NOT NULL"
    ).fetchone()[0]
    counts["integrity"] = db.execute("PRAGMA integrity_check").fetchone()[0]
    db.close()

    device = json.loads((ROOT / "device.json").read_text(encoding="utf-8"))
    stamp = json.loads(
        (INSTALL / "sidecar" / "alex-backend" / "build-stamp.json").read_text(encoding="utf-8")
    )
    dist = Path(__file__).resolve().parents[1] / "apps" / "desktop" / "dist"
    payload = {
        "device_id": device["device_id"],
        "counts": counts,
        "installed": {
            "desktop_sha256": digest(INSTALL / "alex-llm.exe").lower(),
            "desktop_size": (INSTALL / "alex-llm.exe").stat().st_size,
            "host_sha256": digest(INSTALL / "alex-host-loop.exe").lower(),
            "host_size": (INSTALL / "alex-host-loop.exe").stat().st_size,
            "backend_sha256": digest(
                INSTALL / "sidecar" / "alex-backend" / "alex-backend.exe"
            ).lower(),
            "backend_size": (INSTALL / "sidecar" / "alex-backend" / "alex-backend.exe").stat().st_size,
            "tor_sha256": digest(INSTALL / "runtime" / "tor" / "tor.exe").lower(),
            "tor_size": (INSTALL / "runtime" / "tor" / "tor.exe").stat().st_size,
            "backend_stamp": stamp,
            "frontend_dist_sha256_local": tree_digest(dist) if dist.is_dir() else None,
        },
        "product_version": subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-Item '" + str(INSTALL / "alex-llm.exe") + "').VersionInfo.ProductVersion",
            ],
            capture_output=True,
            text=True,
        ).stdout.strip(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
