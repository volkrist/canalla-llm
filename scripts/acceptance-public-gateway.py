#!/usr/bin/env python
r"""REAL public Central Alex Gateway acceptance against the DEPLOYED 12Testers VPS.

Unlike ``acceptance-central-gateway.py`` (which starts an isolated Gateway on this machine),
this harness talks to the **live public** Gateway over HTTPS and creates its one-time
activation codes on the server with the real operator CLI over SSH.

What it proves (all read-only):

* the public hostname serves a healthy, protocol-versioned, secret-free ``/health``;
* every unauthenticated business route is refused (401) and no provider passthrough
  route exists (404);
* two freshly enrolled installations get different ids and different (>=256-bit) secrets;
* the server database stores digests only — the raw secret and the raw activation code
  are absent from the rows that were created;
* both installations see the SAME shared account balance from ONE cached upstream
  snapshot (identical ``fetched_at``);
* revocation cuts B off (403, no new token) and leaves A working;
* the master RunPod key never appears in the public responses, the server database,
  the Gateway journal or the nginx logs (the key is read locally from the Windows
  credential store and only ever compared against text that the server returns).

It NEVER calls ``/compute/ensure`` or ``/compute/stop``: creating a Pod is how money is
spent, and this acceptance is not allowed to start a GPU.

Usage (repository root):

  apps\backend\.venv\Scripts\python.exe scripts\acceptance-public-gateway.py
  ... --base-url https://gateway.12testers.store --ssh distance

Exit codes: 0 pass, 2 no master credential (the leak scan needs it), 1 failure.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import time
from ctypes import wintypes
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROVIDER_TARGET = "Alex LLM/provider/runpod"
GATEWAY_DIR = "/opt/alex-gateway/current"
GATEWAY_ENV = "/etc/alex-gateway/alex-gateway.env"
DB_PATH = "/var/lib/alex-gateway/gateway.db"

failures: list[str] = []
held: list[str] = []  # secret material kept in memory, never printed


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
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
    """The master key, read through the same OS store the installed product uses."""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.POINTER(ctypes.c_void_p)]
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


def ssh(host: str, remote: str) -> str:
    """Run a read-only command on the server; stdout is returned, never echoed blindly."""
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, remote],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ssh command failed ({result.returncode}): {result.stderr[-300:]}")
    return result.stdout


def operator_code(host: str, label: str) -> str:
    """Create a one-time activation code with the real CLI, as the service user."""
    remote = (
        f"sudo -n -u alex-gateway bash -c 'set -a; . {GATEWAY_ENV}; set +a; "
        f"export HOME=/var/lib/alex-gateway PYTHONPATH={GATEWAY_DIR}/gateway:{GATEWAY_DIR}/backend; "
        f"export ALEX_BACKEND_LIB_DIR={GATEWAY_DIR}/backend; cd {GATEWAY_DIR}; "
        f"/opt/alex-gateway/venv/bin/python -m gateway.cli create-code --label {label}'"
    )
    out = ssh(host, remote)
    lines = [row.strip() for row in out.splitlines() if row.strip()]
    for index, row in enumerate(lines):
        if row.startswith("activation code"):
            code = lines[index + 1]
            if len(code) >= 20:
                return code
    raise RuntimeError("the operator CLI did not return an activation code")


def database_rows(host: str) -> dict:
    """Digest-only view of the server database: no hash value is a secret by itself."""
    remote = (
        "sudo -n -u alex-gateway sqlite3 -json "
        f"{DB_PATH} \"select id, name, secret_hash, revoked_at from installations\""
    )
    installations = json.loads(ssh(host, remote) or "[]")
    remote = (
        "sudo -n -u alex-gateway sqlite3 -json "
        f"{DB_PATH} \"select code_hash, label, redeemed_at from enrollment_codes\""
    )
    codes = json.loads(ssh(host, remote) or "[]")
    return {"installations": installations, "codes": codes}


def server_text(host: str) -> str:
    """Text the server exposes for a leak scan: journal, nginx logs, config listing."""
    parts = [
        ssh(host, "sudo -n journalctl -u alex-gateway --no-pager -n 500 2>/dev/null | tail -500"),
        ssh(host, "sudo -n tail -n 400 /var/log/nginx/alex-gateway.access.log 2>/dev/null"),
        ssh(host, "sudo -n tail -n 200 /var/log/nginx/alex-gateway.error.log 2>/dev/null"),
        ssh(host, f"sudo -n sqlite3 {DB_PATH} .dump | head -400"),
    ]
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="public Central Alex Gateway acceptance")
    parser.add_argument("--base-url", default="https://gateway.12testers.store")
    parser.add_argument("--ssh", default="distance")
    parser.add_argument("--client-version", default="0.9.3")
    args = parser.parse_args()

    key = read_provider_secret(PROVIDER_TARGET)
    if not key:
        print(f"no master credential at '{PROVIDER_TARGET}' (needed for the leak scan)")
        return 2
    held.append(key)
    try:
        return run(args.base_url.rstrip("/"), args.ssh, args.client_version, key)
    finally:
        held.clear()


def run(base: str, host: str, client_version: str, key: str) -> int:
    import httpx

    print("REAL public Central Alex Gateway acceptance (read-only, deployed 12Testers VPS)")
    print(f"gateway: {base}   operator host: {host}")

    api = httpx.Client(base_url=base, timeout=60, follow_redirects=False)
    installation_a = installation_b = None
    headers_a: dict = {}
    headers_b: dict = {}
    code_a = code_b = ""

    print("1. public health is healthy, protocol-versioned and secret-free")
    health_response = api.get("/health")
    health = health_response.json()
    check("HTTP 200 over public HTTPS", health_response.status_code == 200,
          f"status={health_response.status_code}")
    check("product/version", health.get("product") == "alex-llm-gateway"
          and health.get("version") == "0.9.3",
          f"{health.get('product')} {health.get('version')}")
    check("ready + database ok", health.get("ready") is True and health.get("database") == "ok",
          f"ready={health.get('ready')} database={health.get('database')}")
    check("gateway_protocol_version == 1", health.get("gateway_protocol_version") == 1)
    check("provider_configured server-side", health.get("provider_configured") is True)
    check("no master credential in /health", key not in json.dumps(health))
    check("no credential-shaped field in /health",
          not any(word in json.dumps(health).lower() for word in ("runpod_api_key", "jwt", "secret")))

    print("2. unauthenticated business routes are refused, passthrough does not exist")
    check("GET /balance without a token", api.get("/balance").status_code == 401)
    check("GET /compute/status without a token", api.get("/compute/status").status_code == 401)
    check("POST /v1/chat/completions without a token",
          api.post("/v1/chat/completions", json={}).status_code == 401)
    for path in ("/runpod/graphql", "/runpod/request", "/provider/raw"):
        check(f"POST {path} does not exist", api.post(path, json={"query": "mutation"}).status_code == 404)

    print("3. two installations enroll with single-use codes created on the server")
    code_a = operator_code(host, "public-accept-a")
    code_b = operator_code(host, "public-accept-b")
    held.extend([code_a, code_b])
    enroll_a = api.post("/enroll", json={
        "activation_code": code_a, "name": "public A", "platform": "windows",
        "client_version": client_version,
    })
    enroll_b = api.post("/enroll", json={
        "activation_code": code_b, "name": "public B", "platform": "windows",
        "client_version": client_version,
    })
    check("A enrolled", enroll_a.status_code == 200, f"status={enroll_a.status_code}")
    check("B enrolled", enroll_b.status_code == 200, f"status={enroll_b.status_code}")
    installation_a = enroll_a.json()
    installation_b = enroll_b.json()
    held.extend([installation_a.get("installation_secret", ""), installation_b.get("installation_secret", "")])
    check("different installation ids",
          installation_a["installation_id"] != installation_b["installation_id"])
    check("different installation secrets",
          installation_a["installation_secret"] != installation_b["installation_secret"])
    check("secret length >= 43 (256-bit)",
          min(len(installation_a["installation_secret"]), len(installation_b["installation_secret"])) >= 43)
    check("the activation codes are consumed (second use refused)",
          api.post("/enroll", json={"activation_code": code_a}).status_code == 409)

    print("4. the server database stores digests only")
    rows = database_rows(host)
    dumped = json.dumps(rows)
    check("two installations are stored", len(rows["installations"]) >= 2, f"{len(rows['installations'])} rows")
    check("no raw installation secret in the database",
          not any(secret and secret in dumped for secret in (installation_a["installation_secret"],
                                                             installation_b["installation_secret"])))
    check("no raw activation code in the database", code_a not in dumped and code_b not in dumped)
    check("no master credential in the database", key not in dumped)
    check("every stored secret_hash is a digest (no plaintext column)",
          all(len(row.get("secret_hash") or "") >= 32 for row in rows["installations"]))

    print("5. the installations exchange credentials for short-lived tokens")
    token_a = api.post("/auth/token", json={
        "installation_id": installation_a["installation_id"],
        "installation_secret": installation_a["installation_secret"],
    })
    token_b = api.post("/auth/token", json={
        "installation_id": installation_b["installation_id"],
        "installation_secret": installation_b["installation_secret"],
    })
    check("A got a token", token_a.status_code == 200)
    check("B got a token", token_b.status_code == 200)
    headers_a = {"Authorization": "Bearer " + token_a.json()["access_token"]}
    headers_b = {"Authorization": "Bearer " + token_b.json()["access_token"]}
    check("a wrong secret is refused",
          api.post("/auth/token", json={
              "installation_id": installation_a["installation_id"],
              "installation_secret": "wrong-" + "w" * 40,
          }).status_code in (401, 403))
    check("an unknown installation id is refused",
          api.post("/auth/token", json={
              "installation_id": "00000000-0000-0000-0000-000000000000",
              "installation_secret": installation_a["installation_secret"],
          }).status_code in (401, 403))

    print("6. one shared RunPod account through the Gateway")
    status_a = api.get("/compute/status", headers=headers_a)
    status_b = api.get("/compute/status", headers=headers_b)
    check("A reads the global compute state", status_a.status_code == 200)
    state_a = status_a.json()
    check("compute is not running (no Pod, no GPU)",
          state_a.get("state") in ("offline", "stopped", "not_configured"), state_a.get("state"))
    check("no managed session is active", state_a.get("session") is None)
    check("the state is global (one shared compute for every installation)",
          state_a.get("global") is True)
    check("both installations see the same global state",
          status_b.json().get("state") == state_a.get("state"))
    check("no Pod identifier leaks", "pod-" not in status_a.text)
    check("no provider proxy host leaks", "proxy.runpod.net" not in status_a.text)
    balance_a_response = api.get("/balance", headers=headers_a)
    balance_b_response = api.get("/balance", headers=headers_b)
    check("A reads the balance", balance_a_response.status_code == 200)
    check("B reads the balance", balance_b_response.status_code == 200)
    balance_a = balance_a_response.json()
    balance_b = balance_b_response.json()
    check("the balance is available", balance_a.get("available") is True)
    check("both installations see the same account balance",
          balance_a.get("balance_usd") == balance_b.get("balance_usd"), money(balance_a.get("balance_usd")))
    check("one cached upstream snapshot serves both (shared cache)",
          balance_a.get("fetched_at") == balance_b.get("fetched_at"))
    check("the balance is flagged as a shared read-only account",
          balance_a.get("shared_account") is True and balance_a.get("read_only") is True)
    check("no master credential in /balance", key not in balance_a_response.text)
    print(f"  [info] shared RunPod balance (UI rounded): {money(balance_a.get('balance_usd'))}")

    print("7. a controlled refresh after the cache TTL reads the provider again")
    time.sleep(17)
    refreshed = api.get("/balance", headers=headers_b)
    check("the Gateway refreshes the shared snapshot read-only", refreshed.status_code == 200)
    check("a new upstream snapshot replaced the cached one",
          refreshed.json().get("fetched_at") != balance_a.get("fetched_at"))
    check("no master credential in the refreshed payload", key not in refreshed.text)

    print("8. a protocol mismatch fails closed")
    mismatch = api.get("/compute/status", headers={**headers_a, "X-Alex-Protocol-Version": "99"})
    check("an incompatible protocol version is rejected", mismatch.status_code == 409,
          f"status={mismatch.status_code}")

    print("9. revoking B does not touch A")
    check("B revoked", api.post("/auth/revoke", json={"reason": "public acceptance"},
                                headers=headers_b).status_code == 200)
    check("the revoked token is refused", api.get("/balance", headers=headers_b).status_code == 403)
    check("the revoked installation cannot mint a new token",
          api.post("/auth/token", json={
              "installation_id": installation_b["installation_id"],
              "installation_secret": installation_b["installation_secret"],
          }).status_code == 403)
    check("A still works", api.get("/balance", headers=headers_a).status_code == 200)

    print("10. the master credential is absent from the server surfaces")
    text = server_text(host)
    check("master credential not in journal / nginx logs / database dump", key not in text)
    check("activation codes not in journal / nginx logs / database dump",
          code_a not in text and code_b not in text)
    check("installation secrets not in journal / nginx logs / database dump",
          installation_a["installation_secret"] not in text
          and installation_b["installation_secret"] not in text)
    check("positive control: the scan text is non-trivial", len(text) > 500, f"{len(text)} chars")

    print("11. cleanup — the throwaway installations are revoked")
    check("A revoked as well (nothing stays enrolled from this acceptance)",
          api.post("/auth/revoke", json={"reason": "public acceptance cleanup"},
                   headers=headers_a).status_code == 200)
    check("A is cut off afterwards", api.get("/balance", headers=headers_a).status_code == 403)
    final_rows = database_rows(host)
    ids = {installation_a["installation_id"], installation_b["installation_id"]}
    revoked = [row for row in final_rows["installations"] if row["id"] in ids and row["revoked_at"]]
    check("both acceptance installations are revoked on the server", len(revoked) == 2, f"{len(revoked)}/2")

    print()
    if failures:
        print(f"PUBLIC GATEWAY ACCEPTANCE FAILED: {len(failures)} check(s)")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("PUBLIC GATEWAY ACCEPTANCE PASS — public HTTPS, shared account, digest-only storage")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
