"""Create and verify a real product backup before installing the 1.2.6 candidate.

Two modes:

* default — start the installed desktop app (the product path);
* ``--sidecar`` — start the installed sidecar directly. The desktop supervises the backend and
  restarts it when a cold start (Defender scanning a fresh 24 MB sidecar) exceeds its deadline,
  which makes an automated backup impossible while the app is still warming up; the backup service
  itself lives in the backend, so this is the same code path without the supervisor.

The data root is never overridden: this is the owner's real installation, and the whole point is a
verified backup of it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "artifacts" / "acceptance-123"))
import common  # noqa: E402

OUT = Path(__file__).resolve().parent
INSTALL = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Canalla LLM"
DESKTOP = INSTALL / "alex-llm.exe"
SIDECAR = INSTALL / "sidecar" / "alex-backend" / "alex-backend.exe"


def health(timeout=5):
    with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_health(deadline):
    started = time.monotonic()
    seen = 0
    last = None
    while time.monotonic() - started < deadline:
        try:
            payload = health()
            seen += 1
            if seen >= 3:
                return payload
        except Exception as error:  # noqa: BLE001
            last = error
            seen = 0
        time.sleep(1.0)
    raise RuntimeError(f"the backend did not answer /health ({last})")


def call(method, path, body=None, token=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:8000" + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    if token:
        request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sidecar", action="store_true")
    args = parser.parse_args()

    target = SIDECAR if args.sidecar else DESKTOP
    result = {"target": str(target), "mode": "sidecar" if args.sidecar else "desktop"}
    process = subprocess.Popen([str(target)], cwd=str(target.parent))
    try:
        result["health"] = wait_health(300)
        access = common.token()
        created = call("POST", "/backup", {"label": "pre-1.2.6-install"}, access)
        result["backup"] = created
        backup_id = created.get("id") or created.get("backup_id")
        if not backup_id:
            raise RuntimeError(f"the product returned no backup id: {created}")
        result["verify"] = call("POST", "/backup/verify", {"backup_id": backup_id}, access)
        result["status"] = "PASS"
        return 0
    except Exception as error:  # noqa: BLE001
        result["status"] = "FAIL"
        result["error"] = f"{type(error).__name__}: {error}"
        return 1
    finally:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False
        )
        time.sleep(1)
        (OUT / "pre-install-backup.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
