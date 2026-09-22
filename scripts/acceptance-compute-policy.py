"""REAL public acceptance: a user's own compute policy is honoured, never clamped.

Runs against the deployed Gateway over public HTTPS and creates NO Pod: an out-of-range
policy must be refused with a typed error, and a policy above the old $1.20/$3 ceilings
must reach the provider search (proved by an impossible VRAM floor) instead of being
silently replaced. Secrets stay in memory; only check results are printed.

    python scripts/acceptance-compute-policy.py
"""

from __future__ import annotations

import json
import subprocess
import sys

GATEWAY = "https://gateway.12testers.store"
HOST = "distance"
GATEWAY_ENV = "/etc/alex-gateway/alex-gateway.env"
GATEWAY_DIR = "/opt/alex-gateway/current"
CLIENT_VERSION = "1.1.0"

failures: list[str] = []
held: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def ssh(host: str, remote: str) -> str:
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, remote],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ssh failed ({result.returncode}): {result.stderr[-300:]}")
    return result.stdout


def operator_code(host: str, label: str) -> str:
    remote = (
        f"sudo -n -u alex-gateway bash -c 'set -a; . {GATEWAY_ENV}; set +a; "
        f"export HOME=/var/lib/alex-gateway PYTHONPATH={GATEWAY_DIR}/gateway:{GATEWAY_DIR}/backend; "
        f"export ALEX_BACKEND_LIB_DIR={GATEWAY_DIR}/backend; cd {GATEWAY_DIR}; "
        f"/opt/alex-gateway/venv/bin/python -m gateway.cli create-code --label {label}'"
    )
    lines = [row.strip() for row in ssh(host, remote).splitlines() if row.strip()]
    for index, row in enumerate(lines):
        if row.startswith("activation code") and index + 1 < len(lines):
            code = lines[index + 1]
            if len(code) >= 20:
                held.append(code)
                return code
    raise RuntimeError("the operator CLI did not return an activation code")


def ensure(api, headers: dict, operation_id: str, **caps):
    body = {"operation_id": operation_id}
    body.update(caps)
    return api.post("/compute/ensure", json=body, headers=headers)


def main() -> int:
    import uuid

    import httpx

    run = uuid.uuid4().hex[:8]
    print("REAL public compute-policy acceptance (no Pod, no GPU spend)")
    print(f"gateway: {GATEWAY}   operator host: {HOST}")
    api = httpx.Client(base_url=GATEWAY, timeout=60, follow_redirects=False)

    print("1. the deployed service is the version that owns the new contract")
    health = api.get("/health")
    body = health.json()
    check("public /health is 200", health.status_code == 200)
    check("ready + database ok", body.get("ready") is True and body.get("database") == "ok")
    check("provider configured server-side", body.get("provider_configured") is True)
    check("no secret field in /health",
          not any(word in json.dumps(body).lower() for word in ("runpod_api_key", "jwt", "secret")))

    print("2. a throwaway installation enrolls with a one-time server code")
    code = operator_code(HOST, "policy-accept")
    enroll = api.post("/enroll", json={
        "activation_code": code,
        "name": "policy accept",
        "platform": "windows",
        "client_version": CLIENT_VERSION,
    })
    check("enrolled", enroll.status_code == 200, f"status={enroll.status_code}")
    installation = enroll.json()
    held.append(installation.get("installation_secret", ""))
    token = api.post("/auth/token", json={
        "installation_id": installation["installation_id"],
        "installation_secret": installation["installation_secret"],
    })
    check("token issued", token.status_code == 200, f"status={token.status_code}")
    headers = {"Authorization": "Bearer " + token.json()["access_token"]}
    check("the activation code is single use",
          api.post("/enroll", json={"activation_code": code}).status_code == 409)

    print("3. a malformed policy is refused with a typed error, never replaced")
    for index, caps in enumerate([
        {"max_hourly_price": 999, "session_budget": 3},
        {"max_hourly_price": 0.52, "session_budget": 5000},
    ]):
        response = ensure(api, headers, f"op-bad-{run}-{index:04d}", **caps)
        detail = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        check(f"policy {caps} refused", response.status_code == 422, f"status={response.status_code}")
        check("refusal carries compute_policy_invalid", detail.get("code") == "compute_policy_invalid")

    print("4. the user's own number is what counts — in both directions")
    balance = api.get("/balance", headers=headers).json()
    available = balance.get("balance_usd")
    print(f"   shared account balance (read-only): {available}")
    # (a) A policy above the old $1.20/$3 ceilings is accepted. The balance gate is a real
    # guard (a session budget larger than the account balance is refused), so the budget
    # here stays inside the balance; the impossible VRAM floor keeps the run free.
    generous = ensure(
        api,
        headers,
        "op-generous-" + run,
        max_hourly_price=2.00,
        session_budget=0.40,
        min_vram_gb=1024,
    )
    payload = generous.json()
    check("a $2.00/h policy is accepted", generous.status_code == 200, f"status={generous.status_code}")
    check("no Pod was created (a session only exists when one is)",
          payload.get("session") is None, f"state={payload.get('state')}")
    check("the search failed on compatibility, not on a money cap",
          payload.get("error_code") == "no_compatible_gpu", f"error={payload.get('error_code')}")
    # (b) A deliberately low maximum is honoured instead of being raised.
    frugal = ensure(
        api, headers, "op-frugal-" + run, max_hourly_price=0.10, session_budget=0.40
    )
    frugal_body = frugal.json()
    check("a $0.10/h policy is accepted", frugal.status_code == 200, f"status={frugal.status_code}")
    check("nothing is started above the user's maximum",
          frugal_body.get("session") is None and frugal_body.get("error_code") == "price_limit",
          f"error={frugal_body.get('error_code')}")

    print("5. status stays read-only and reports no compute")
    status = api.get("/compute/status", headers=headers).json()
    check("no session", status.get("session") is None)
    # After a search that found nothing the truthful AI state is "unavailable", never "ready".
    check("ai is not claimed ready", status.get("ai") in {"off", "unavailable"},
          f"ai={status.get('ai')} state={status.get('state')}")
    check("no RunPod key anywhere in the status",
          "runpod" not in json.dumps(status).lower() or "key" not in json.dumps(status).lower())

    print("6. the throwaway installation is revoked and leaves nothing behind")
    check("revoked", api.post("/auth/revoke", json={"reason": "acceptance_done"},
                              headers=headers).status_code == 200)
    check("the revoked token is refused",
          api.get("/compute/status", headers=headers).status_code in (401, 403))

    print()
    if failures:
        print(f"COMPUTE POLICY ACCEPTANCE FAILED: {len(failures)} check(s)")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("COMPUTE POLICY ACCEPTANCE PASS — per-user policy honoured, no clamp, no GPU spend")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        del held
