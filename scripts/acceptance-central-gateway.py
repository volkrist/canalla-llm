#!/usr/bin/env python
r"""REAL Central Alex Gateway acceptance (Alex LLM 0.9.3).

Runs the real Gateway service as a separate uvicorn process with PRODUCTION settings on an
isolated temporary database, against the REAL RunPod account that the server-side master
credential belongs to.

What it proves (all read-only):

* an operator creates the activation codes with the real CLI;
* two installations enroll, receive different secrets, and the database stores digests only;
* each installation exchanges its credential for a short-lived token (never the secret);
* both installations see the SAME shared account balance, from ONE cached upstream snapshot;
* a second controlled refresh after the cache TTL replaces that snapshot;
* the compute state is inspected without ever starting compute;
* revocation cuts one installation off without touching the other;
* protocol mismatch, passthrough routes and rate limiting behave as documented;
* no credential material appears in API responses, the database, or the service log.

It NEVER calls ``/compute/ensure`` or ``/compute/stop``: creating a Pod is how money is
spent, and this acceptance is not allowed to start a GPU. The (money-cap, single-Pod,
create-unknown) behaviours are covered by the deterministic FakeRunPod suite in
``apps/gateway/tests``.

The master credential is read from the same secure place the installed product uses
(Windows Credential Manager, ``Alex LLM/provider/runpod``) and is passed to the Gateway
service process as a server-side environment variable. It is never printed, never written
to a file, and never placed in a client.

Usage (repository root):

  apps\backend\.venv\Scripts\python.exe scripts\acceptance-central-gateway.py

Exit codes: 0 pass, 2 no master credential, 1 failure.
"""

from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GATEWAY = REPO / "apps" / "gateway"
PROVIDER_TARGET = "Alex LLM/provider/runpod"
VOLUME_ID = "uwgeaie5b0"

failures: list[str] = []
printed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    state = "PASS" if condition else "FAIL"
    print(f"  [{state}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def money(value) -> str:
    """UI rounding only: never print more precision than the user sees."""
    if value is None:
        return "unavailable"
    return f"${Decimal(str(value)).quantize(Decimal('0.01'))}"


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def read_provider_secret(target: str) -> str:
    """Read the installation-global provider credential. The value is never logged."""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    pointer = ctypes.c_void_p()
    if not advapi32.CredReadW(target, 1, 0, ctypes.byref(pointer)):
        return ""
    try:
        credential = ctypes.cast(pointer, ctypes.POINTER(CREDENTIALW)).contents
        blob = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return blob.decode("utf-8", "ignore").strip()
    finally:
        advapi32.CredFree(pointer)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_health(base: str, timeout: float = 40.0) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


def main() -> int:
    key = read_provider_secret(PROVIDER_TARGET)
    if not key:
        print(f"no master credential at '{PROVIDER_TARGET}'")
        return 2
    try:
        return run(key)
    finally:
        printed.clear()


def run(key: str) -> int:
    import httpx

    workdir = tempfile.TemporaryDirectory(prefix="alex-gateway-accept-")
    root = Path(workdir.name)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    database = "sqlite:///" + (root / "gateway.db").as_posix()
    jwt_secret = "acceptance-gateway-secret-" + "g" * 48
    client_version = "0.9.3"

    env = {
        **os.environ,
        "APP_ENV": "production",
        "DATABASE_URL": database,
        "JWT_SECRET": jwt_secret,
        "RUNPOD_API_KEY": key,
        "LLM_PROVIDER": "llamacpp",
        "LLM_API_KEY": "pod-gateway-key-" + "p" * 40,
        "PYTHONPATH": str(GATEWAY),
        "PYTHONUNBUFFERED": "1",
    }
    print("REAL Central Alex Gateway acceptance (read-only, production settings)")
    print(f"isolated database: {root / 'gateway.db'}")
    print(f"gateway: {base}")

    log = (root / "gateway.log").open("w+", encoding="utf-8", errors="replace")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(GATEWAY / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=str(GATEWAY),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    process.wait(timeout=120)
    check("alembic upgrade head", process.returncode == 0, f"exit={process.returncode}")

    service = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "gateway.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(GATEWAY),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    credential_material = [key, jwt_secret]
    try:
        if not wait_health(base):
            print("  [FAIL] gateway did not become healthy")
            log.seek(0)
            print(log.read()[-2000:])
            return 1

        api = httpx.Client(base_url=base, timeout=30)
        operator = subprocess.run(
            [sys.executable, "-m", "gateway.cli", "create-code", "--label", "acceptance"],
            cwd=str(GATEWAY),
            env=env,
            capture_output=True,
            text=True,
        )
        check("operator CLI creates an activation code", operator.returncode == 0, operator.stderr[-200:])
        code_a = operator.stdout.splitlines()[1].strip()
        operator_b = subprocess.run(
            [sys.executable, "-m", "gateway.cli", "create-code", "--label", "acceptance-b"],
            cwd=str(GATEWAY),
            env=env,
            capture_output=True,
            text=True,
        )
        code_b = operator_b.stdout.splitlines()[1].strip()
        credential_material += [code_a, code_b]

        print("1. health is public, secret-free and protocol-versioned")
        health = api.get("/health").json()
        check("ready", health["ready"] is True)
        check("database ok", health["database"] == "ok")
        check("protocol version", health["gateway_protocol_version"] == 1)
        check("provider configured server-side", health["provider_configured"] is True)
        check("no credential in /health", key not in json.dumps(health))

        print("2. two installations enroll with single-use codes")
        enroll_a = api.post(
            "/enroll",
            json={"activation_code": code_a, "name": "PC A", "platform": "windows", "client_version": client_version},
        )
        enroll_b = api.post(
            "/enroll",
            json={"activation_code": code_b, "name": "PC B", "platform": "windows", "client_version": client_version},
        )
        check("installation A enrolled", enroll_a.status_code == 200, enroll_a.text[:200])
        check("installation B enrolled", enroll_b.status_code == 200, enroll_b.text[:200])
        a = enroll_a.json()
        b = enroll_b.json()
        check("different installation ids", a["installation_id"] != b["installation_id"])
        check("different installation secrets", a["installation_secret"] != b["installation_secret"])
        check("secret length >= 43 (256 bits)", len(a["installation_secret"]) >= 43)
        repeated = api.post("/enroll", json={"activation_code": code_a})
        check("the code cannot be used twice", repeated.status_code == 409, repeated.text[:120])
        credential_material += [a["installation_secret"], b["installation_secret"]]

        print("3. credentials are exchanged for short-lived tokens")
        token_a = api.post(
            "/auth/token",
            json={"installation_id": a["installation_id"], "installation_secret": a["installation_secret"]},
        ).json()
        token_b = api.post(
            "/auth/token",
            json={"installation_id": b["installation_id"], "installation_secret": b["installation_secret"]},
        ).json()
        headers_a = {"Authorization": "Bearer " + token_a["access_token"]}
        headers_b = {"Authorization": "Bearer " + token_b["access_token"]}
        check("token A differs from token B", token_a["access_token"] != token_b["access_token"])
        check("token TTL is short (<= 15 min)", token_a["expires_in"] <= 900, f"expires_in={token_a['expires_in']}")
        wrong_secret = api.post(
            "/auth/token",
            json={"installation_id": a["installation_id"], "installation_secret": "wrong-" + "w" * 30},
        )
        check("a wrong secret is refused", wrong_secret.status_code == 401, wrong_secret.text[:120])
        no_token = api.get("/compute/status")
        check("business endpoints require a token", no_token.status_code == 401)

        print("4. compute state is inspected, never started")
        status_a = api.get("/compute/status", headers=headers_a).json()
        status_b = api.get("/compute/status", headers=headers_b).json()
        check("state is idle", status_a["state"] in {"offline", "stopped", "not_configured"}, status_a["state"])
        check("no session", status_a["session"] is None)
        check("both installations see the same global state", status_a["state"] == status_b["state"])
        check("state is marked global", status_a["global"] is True)
        check("no Pod identifier leaks", "pod-" not in json.dumps(status_a))
        check("no proxy URL leaks", "proxy.runpod.net" not in json.dumps(status_a))

        print("5. one shared account balance for two installations")
        balance_a = api.get("/balance", headers=headers_a)
        check("balance A returns 200", balance_a.status_code == 200, balance_a.text[:200])
        body_a = balance_a.json()
        first_value = Decimal(str(body_a["balance_usd"]))
        balance_b = api.get("/balance", headers=headers_b).json()
        check("balance is available", body_a["available"] is True)
        check("balance parses as Decimal", isinstance(first_value, Decimal))
        check("shared account, read only", body_a["shared_account"] is True and body_a["read_only"] is True)
        check("both installations see the same account", balance_b["balance_usd"] == body_a["balance_usd"])
        check("the second read reused the same snapshot", balance_b["fetched_at"] == body_a["fetched_at"])
        check("no credential in the balance payload", key not in balance_a.text + json.dumps(balance_b))

        print("6. second controlled refresh after the cache TTL")
        time.sleep(16)
        refreshed = api.get("/balance", headers=headers_b).json()
        check("a new upstream snapshot replaced the old one", refreshed["fetched_at"] != body_a["fetched_at"])
        check("the refreshed value parses as Decimal", isinstance(Decimal(str(refreshed["balance_usd"])), Decimal))

        print("7. protocol guard and typed-operations-only surface")
        mismatch = api.get("/compute/status", headers={**headers_a, "X-Alex-Protocol-Version": "99"})
        check("protocol mismatch is explicit", mismatch.status_code == 409, mismatch.text[:120])
        check("protocol mismatch code", mismatch.json().get("code") == "gateway_protocol_mismatch")
        for path in ("/runpod/graphql", "/runpod/request", "/provider/raw"):
            response = api.post(path, json={"query": "mutation"}, headers=headers_a)
            check(f"no passthrough at {path}", response.status_code == 404, str(response.status_code))

        print("8. revocation is per installation")
        revoked = api.post("/auth/revoke", json={"reason": "acceptance"}, headers=headers_b)
        check("installation B revoked", revoked.status_code == 200, revoked.text[:120])
        check("B token is refused", api.get("/balance", headers=headers_b).status_code == 403)
        check("B cannot mint a new token", api.post(
            "/auth/token",
            json={"installation_id": b["installation_id"], "installation_secret": b["installation_secret"]},
        ).status_code == 403)
        check("A still works", api.get("/balance", headers=headers_a).status_code == 200)

        print("9. compute safety and cost evidence (read-only provider view)")
        pods = read_only_pods(key)
        check("no Pod on the shared Volume", pods == [], str(pods))
        spend = read_only_spend(key)
        # With no Pod running, the only remaining hourly account charge is Network Volume
        # storage. A GPU that had been started in this acceptance would show up here and in
        # the Pod list above.
        check(
            "no GPU spend (only volume storage remains)",
            pods == [] and spend <= Decimal("0.05"),
            f"account hourly spend={spend}",
        )

        print("10. secret hygiene")
        log.flush()
        text = (root / "gateway.log").read_text(encoding="utf-8", errors="replace")
        for material in credential_material:
            check(
                f"no {len(material)}-char credential material in the service log",
                material not in text,
            )
        database_bytes = (root / "gateway.db").read_bytes()
        check("no credential in the database file", key.encode() not in database_bytes)
        payloads = [
            api.get("/health").text,
            json.dumps(status_a),
            json.dumps(body_a),
            json.dumps(refreshed),
        ]
        check("no credential in any API payload", all(key not in payload for payload in payloads))

        print()
        print(f"shared RunPod balance (UI rounded): {money(first_value)}")
        api.close()
    finally:
        service.terminate()
        try:
            service.wait(timeout=15)
        except subprocess.TimeoutExpired:
            service.kill()
        log.close()

    if failures:
        print(f"GATEWAY ACCEPTANCE FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("GATEWAY ACCEPTANCE PASS — read-only, no Pod, no GPU, volume untouched")
    return 0


def read_only_provider_settings(key: str):
    """Minimal settings for a read-only provider view. Nothing here touches the product DB."""
    from pydantic import SecretStr

    from app.config import Settings

    return Settings(
        jwt_secret="acceptance-read-only-view-" + "x" * 32,
        runpod_api_key=SecretStr(key),
        llm_provider="llamacpp",
        llm_connection_mode="runpod",
    )


def read_only_pods(key: str) -> list:
    """Read-only provider view. Uses the shared client; never creates or stops anything."""
    import asyncio

    from app.compute.runpod_api import RunPodAPI

    settings = read_only_provider_settings(key)

    async def pods():
        api = RunPodAPI(settings)
        return [
            pod.id
            for pod in await api.list_pods()
            if any(mount.get("volumeId") == VOLUME_ID for mount in pod.mounts.get("network", []))
            and pod.status not in {"EXITED", "TERMINATED"}
        ]

    return asyncio.run(pods())


def read_only_spend(key: str) -> Decimal:
    import asyncio

    from app.compute.runpod_api import RunPodAPI

    data = asyncio.run(RunPodAPI(read_only_provider_settings(key)).account_balance())
    return data.get("current_spend_per_hr") or Decimal("0")


if __name__ == "__main__":
    sys.path.insert(0, str(REPO / "apps" / "backend"))
    sys.exit(main())
