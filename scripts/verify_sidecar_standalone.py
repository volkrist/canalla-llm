"""Prove alex-backend.exe starts without repo Python. No GPU. No TinyFish.

The expected version is read from `apps/backend/app/product.py` rather than pinned here: this script
checking for 0.9.3 while the product moved to 1.2.0 is exactly the drift the sidecar stamp exists to
prevent, and a second hard-coded version would only hide a stale artifact again.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "apps" / "desktop" / "src-tauri" / "sidecar" / "alex-backend"
if not (SOURCE / "alex-backend.exe").is_file():
    SOURCE = REPO / "apps" / "backend" / "dist" / "alex-backend"


def expected_version() -> str:
    source = (REPO / "apps" / "backend" / "app" / "product.py").read_text(
        encoding="utf-8"
    )
    found = re.search(r'^VERSION\s*=\s*"([^"]+)"\s*$', source, re.MULTILINE)
    if not found:
        raise SystemExit("cannot read VERSION from apps/backend/app/product.py")
    return found.group(1)


def openapi(url: str) -> dict:
    with urllib.request.urlopen(url + "/openapi.json", timeout=10) as response:
        return json.loads(response.read().decode())


def request_fields(document: dict, path: str, method: str = "post") -> set[str]:
    """The property names of a route's JSON body, inline or behind a component reference."""
    operation = document.get("paths", {}).get(path, {}).get(method, {})
    media = (
        operation.get("requestBody", {}).get("content", {}).get("application/json", {})
    )
    schema = media.get("schema", {})
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        schema = document.get("components", {}).get("schemas", {}).get(name, {})
    if "allOf" in schema:
        merged: set[str] = set()
        for part in schema["allOf"]:
            if "$ref" in part:
                name = part["$ref"].rsplit("/", 1)[-1]
                part = document.get("components", {}).get("schemas", {}).get(name, {})
            merged |= set(part.get("properties", {}))
        return merged
    return set(schema.get("properties", {}))


def health(url: str) -> dict:
    with urllib.request.urlopen(url + "/health", timeout=2) as response:
        return json.loads(response.read().decode())


def main() -> int:
    if not (SOURCE / "alex-backend.exe").is_file():
        print("SIDECAR_MISSING", SOURCE)
        return 2
    work = Path(tempfile.mkdtemp(prefix="alex-sidecar-proof-"))
    data = Path(tempfile.mkdtemp(prefix="alex-sidecar-data-"))
    copied = work / "alex-backend"
    shutil.copytree(SOURCE, copied)
    exe = copied / "alex-backend.exe"
    port = 8019
    env = {
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "SYSTEMROOT": os.environ.get(
            "SYSTEMROOT", os.environ.get("SystemRoot", r"C:\Windows")
        ),
        "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
        "SystemDrive": os.environ.get("SystemDrive", "C:"),
        "TEMP": str(work / "tmp"),
        "TMP": str(work / "tmp"),
        "LOCALAPPDATA": str(data.parent),
        "ALEX_LLM_DATA_DIR": str(data),
        "ALEX_BACKEND_PORT": str(port),
        "ALEX_BACKEND_HOST": "127.0.0.1",
        "ALEX_PACKAGED": "1",
        "ALEX_RUNTIME_MODE": "packaged",
        "PATH": os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),
    }
    (work / "tmp").mkdir(exist_ok=True)
    log = (work / "stdout.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(exe)],
        cwd=str(copied),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    body = None
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            if proc.poll() is not None:
                print("SIDECAR_EXIT", proc.returncode)
                return 3
            try:
                body = health(url)
                break
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                json.JSONDecodeError,
            ):
                time.sleep(0.4)
        if not body:
            print("HEALTH_TIMEOUT")
            return 4
        print("HEALTH", json.dumps(body))
        wanted = expected_version()
        if body.get("product") != "alex-llm" or body.get("version") != wanted:
            print(
                "VERSION_MISMATCH",
                body.get("product"),
                body.get("version"),
                "expected",
                wanted,
            )
            return 5
        print("VERSION", body.get("version"))
        document = openapi(url)
        fields = request_fields(document, "/tools/execute")
        if "network_route" not in fields:
            print("TOR_ROUTING_MISSING fields=", sorted(fields))
            return 8
        print("NETWORK_ROUTE_CONTRACT PRESENT", sorted(fields))
        if not (data / "data" / "alex.db").is_file():
            print("DB_MISSING")
            return 6
        if not (data / "runtime" / "jwt.secret").is_file():
            print("JWT_MISSING")
            return 7
        token = (
            (data / "runtime" / "shutdown.token").read_text(encoding="utf-8").strip()
        )
        req = urllib.request.Request(
            url + "/runtime/shutdown",
            method="POST",
            headers={"X-Alex-Runtime-Token": token},
        )
        with urllib.request.urlopen(req, timeout=12) as response:
            print("SHUTDOWN", response.status)
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
        proc.wait(timeout=10)
        print("PROOF_PASS data=", data)
        print("PYTHON_REQUIRED NO")
        print("REPO_REQUIRED NO")
        return 0
    finally:
        if proc.poll() is None:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
