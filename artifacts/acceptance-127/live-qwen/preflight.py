"""Read-only preflight for the single LIVE Qwen run. Creates nothing and spends nothing."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "apps" / "backend"
OUT = Path(__file__).resolve().parent
GATEWAY = "https://gateway.12testers.store"
BUDGET_USD = 0.15
HOURLY_RATE = 1.09

sys.path.insert(0, str(BACKEND))
from app.compute import cost_policy  # noqa: E402


def http_json(url: str, timeout: float = 10.0) -> tuple[int, dict | None]:
    request = urllib.request.Request(url, headers={"User-Agent": "canalla-preflight/1.2.7"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, OSError):
        return 0, None


def runpod_state() -> dict:
    completed = subprocess.run(
        [sys.executable, str(REPO / "artifacts" / "readonly-runpod-state.py")],
        capture_output=True, text=True, timeout=120,
    )
    try:
        return json.loads(completed.stdout)
    except ValueError:
        return {"ok": False, "reason": completed.stderr[-300:]}


def data_state() -> dict:
    path = Path(os.environ["LOCALAPPDATA"]) / "Alex LLM" / "data" / "alex.db"
    if not path.is_file():
        return {"ok": False, "reason": "database_missing"}
    connection = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True)
    try:
        counts = {
            table: connection.execute(f"select count(*) from {table}").fetchone()[0]
            for table in ("users", "chats", "messages", "paired_devices")
        }
        integrity = connection.execute("pragma integrity_check").fetchone()[0]
        device = connection.execute(
            "select id, last_seen from paired_devices where revoked_at is null "
            "order by created_at desc limit 1"
        ).fetchone()
        device_json = Path(os.environ["LOCALAPPDATA"]) / "Alex LLM" / "device.json"
        local_id = ""
        if device_json.is_file():
            local_id = json.loads(device_json.read_text(encoding="utf-8")).get("device_id", "")
    finally:
        connection.close()
    online = False
    if device and device[1]:
        try:
            seen = datetime.fromisoformat(str(device[1])).replace(tzinfo=timezone.utc)
            online = (datetime.now(timezone.utc) - seen).total_seconds() < 120
        except ValueError:
            online = False
    return {
        "ok": integrity == "ok",
        "counts": counts,
        "integrity": integrity,
        "device_id": local_id,
        "device_row": device[0] if device else None,
        "device_online": online,
        "device_matches_local": bool(device and local_id == device[0]),
    }


def main() -> int:
    report: dict = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "budget_usd": BUDGET_USD}

    status, health = http_json(GATEWAY + "/health")
    report["gateway"] = {"status": status, "ready": bool(health and health.get("ready")),
                         "ok": status == 200 and bool(health and health.get("ready"))}

    status, _ = http_json(GATEWAY + "/updates/latest?target=windows&arch=x86_64&current_version=1.2.6")
    report["updater_untouched"] = {"status": status, "ok": status == 204}

    pods = runpod_state()
    report["runpod"] = {
        "pods": pods.get("pods"),
        "pod_count": pods.get("pod_count"),
        "balance_usd": pods.get("balance_usd"),
        "ok": pods.get("pod_count") == 0,
    }

    status, local = http_json("http://127.0.0.1:8000/health")
    report["installed_app"] = {"status": status, "version": (local or {}).get("version"),
                               "ok": status == 200 and (local or {}).get("version") == "1.2.6"}

    data = data_state()
    report["data"] = data

    free_gb = shutil.disk_usage(str(Path.home())).free / 1e9
    report["disk"] = {"free_gb": round(free_gb, 1), "ok": free_gb > 5}

    remaining = None
    balance = pods.get("balance_usd")
    if isinstance(balance, (int, float)):
        remaining = round(float(balance), 4)
    policy = cost_policy.plan(BUDGET_USD, HOURLY_RATE)
    report["budget"] = {
        "cap_usd": BUDGET_USD,
        "balance_usd": remaining,
        "policy": policy.to_dict(),
        "ok": (remaining is None or remaining > BUDGET_USD) and policy.feasible,
    }

    # A paid run cannot be executed if the free parts of the harness are broken.
    watchdog = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "live_qwen_watchdog.py"), "--self-test"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    report["watchdog"] = {"ok": watchdog.returncode == 0, "self_test": "PASS" if watchdog.returncode == 0 else "FAIL"}
    try:
        from app.agent import provenance

        report["provenance"] = {
            "ok": provenance.REAL_PROVIDERS == {"llamacpp", "canalla-cloud"}
            and bool(provenance.REAL_RUNTIMES),
            "real_providers": sorted(provenance.REAL_PROVIDERS),
        }
    except Exception as error:  # noqa: BLE001 - a broken import is a blocker, not a pass
        report["provenance"] = {"ok": False, "error": str(error)[:200]}

    with socket.socket() as sock:
        busy = sock.connect_ex(("127.0.0.1", 8000)) == 0
    report["coordinator"] = {"backend_on_8000": busy, "note": "no paid runner is started here"}

    blockers = []
    for name, item in report.items():
        if isinstance(item, dict) and item.get("ok") is False:
            blockers.append(name)
    report["verdict"] = "BLOCKED" if blockers else "READY"
    report["blockers"] = blockers

    (OUT / "preflight.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
