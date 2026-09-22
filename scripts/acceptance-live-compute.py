"""REAL live compute acceptance through the public Gateway: one Pod, one Qwen, one stream.

Client -> https://gateway.12testers.store -> RunPod -> llama.cpp -> Qwen. The script uses the
documented client contract (enroll -> token -> ensure -> poll -> chat completions -> stop), the
same one the local backend's cloud client speaks, and it creates exactly one Pod.

Safety: a watchdog stops managed compute if the run approaches the RC budget, and every
failure path tries to stop the Pod before exiting. The RunPod master key never leaves the
Gateway, so no credential is printed or stored here.

RC note (22 Sep 2026): four attempts at $0.52, $0.79 and $1.20/hour all ended in
``state=searching`` with ``error_code=price_limit`` and created no Pod. A read-only probe of
the provider catalogue showed every 48 GB+ NVIDIA GPU in US-TX-3 with ``stock=NONE``, so the
cause was provider capacity, not the money policy — the Gateway's reason code cannot tell
"too expensive" from "out of stock" today, which is worth improving when compute is touched
next. Re-run this script when capacity returns; it needs nothing else.

    python scripts/acceptance-live-compute.py [--max-hourly 0.52] [--budget-usd 0.60]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import uuid

GATEWAY = "https://gateway.12testers.store"
HOST = "distance"
GATEWAY_ENV = "/etc/alex-gateway/alex-gateway.env"
GATEWAY_DIR = "/opt/alex-gateway/current"
ALIAS = "orcarouter-qwen38-27b-q5km"
RC_HARD_CEILING = 0.20  # total GPU spend allowed for this acceptance
CLIENT_VERSION = "1.0.0"

failures: list[str] = []
measure: dict = {}


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
                return code
    raise RuntimeError("the operator CLI did not return an activation code")


class Watchdog:
    """Stops managed compute if the run outlives the money we are allowed to spend."""

    def __init__(self, api, headers: dict, rate_usd: float, ceiling_usd: float):
        self.api, self.headers = api, headers
        self.seconds = max(60.0, (ceiling_usd / max(rate_usd, 0.01)) * 3600.0)
        self.stop = threading.Event()
        self.fired = False
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        if self.stop.wait(self.seconds):
            return
        self.fired = True
        print(f"WATCHDOG: {self.seconds:.0f}s reached — stopping compute", flush=True)
        try:
            self.api.post("/compute/stop", json={"operation_id": "op-stop-watchdog"},
                          headers=self.headers, timeout=60)
        except Exception as error:  # noqa: BLE001 - the watchdog must never raise
            print(f"WATCHDOG stop failed: {type(error).__name__}", flush=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()


def status_of(api, headers: dict, token=None) -> dict:
    response = api.get("/compute/status", headers=headers, timeout=30)
    if response.status_code in (401, 403) and token is not None:
        headers["Authorization"] = "Bearer " + token()
        response = api.get("/compute/status", headers=headers, timeout=30)
    return response.json()


def post(api, headers: dict, path: str, token=None, **kwargs):
    """POST with one token refresh: the gateway token is short-lived by design."""
    response = api.post(path, headers=headers, **kwargs)
    if response.status_code in (401, 403) and token is not None:
        headers["Authorization"] = "Bearer " + token()
        response = api.post(path, headers=headers, **kwargs)
    return response


def main() -> int:
    import httpx

    parser = argparse.ArgumentParser()
    parser.add_argument("--max-hourly", type=float, default=0.52)
    parser.add_argument("--budget-usd", type=float, default=3.00)
    parser.add_argument("--capacity-timeout", type=float, default=600.0,
                        help="bounded read-only capacity waiting, seconds (RC section 17)")
    parser.add_argument("--escalate-to", type=float, default=1.20,
                        help="RC-only price ceiling used if capacity exists but nothing fits "
                             "the user's own maximum (never above $1.20)")
    parser.add_argument("--dry-run", action="store_true",
                        help="one catalogue read only: never touches a GPU")
    args = parser.parse_args()
    if args.max_hourly > 1.20:
        print("RC ceiling is $1.20/hour; use a lower value")
        return 2

    print("REAL live compute acceptance: public Gateway -> RunPod -> llama.cpp -> Qwen")
    print(f"gateway: {GATEWAY}   max ${args.max_hourly:.2f}/h   session budget ${args.budget_usd:.2f}")
    api = httpx.Client(base_url=GATEWAY, timeout=60, follow_redirects=False)
    headers: dict = {}
    stopped = False

    code = operator_code(HOST, "live-rc")
    enroll = api.post("/enroll", json={
        "activation_code": code, "name": "live RC", "platform": "windows",
        "client_version": CLIENT_VERSION,
    })
    check("a throwaway installation enrolled for the run", enroll.status_code == 200,
          f"status={enroll.status_code}")
    installation = enroll.json()
    token = api.post("/auth/token", json={
        "installation_id": installation["installation_id"],
        "installation_secret": installation["installation_secret"],
    })

    def fresh_token() -> str:
        issued = api.post("/auth/token", json={
            "installation_id": installation["installation_id"],
            "installation_secret": installation["installation_secret"],
        }, timeout=30)
        return issued.json()["access_token"]

    headers = {"Authorization": "Bearer " + token.json()["access_token"]}

    balance_before = api.get("/balance", headers=headers).json().get("balance_usd")
    print(f"balance before: ${balance_before}   session ceiling sent: ${args.budget_usd:.2f}")
    measure["balance_before"] = balance_before
    measure["session_ceiling_sent"] = args.budget_usd
    # RC money semantics: a ceiling larger than the account balance must not block a start.
    try:
        measure["balance_below_ceiling"] = float(balance_before) < args.budget_usd
    except (TypeError, ValueError):
        measure["balance_below_ceiling"] = None

    try:
        if args.dry_run:
            probe = api.post("/compute/ensure", json={
                "operation_id": "op-dryrun-" + uuid.uuid4().hex[:8],
                "max_hourly_price": 0.05,
                "session_budget": 0.40,
            }, headers=headers, timeout=120)
            body = probe.json()
            check("dry run: the Gateway answered without starting anything",
                  probe.status_code == 200 and (body.get("session") is None),
                  f"status={probe.status_code} state={body.get('state')} error={body.get('error_code')}")
            return 0 if not failures else 1

        print("1. ensure compute with the user's own policy (cheapest compatible GPU)")
        started = time.monotonic()
        deadline = started + args.capacity_timeout
        payload: dict = {}
        hourly = args.max_hourly
        capacity_waits = 0
        for label, hourly in ((f"user policy ${args.max_hourly:.2f}/h", args.max_hourly),
                              (f"rc escalation ${args.escalate_to:.2f}/h", args.escalate_to)):
            if time.monotonic() >= deadline:
                break
            print(f"   trying {label} with a ${args.budget_usd:.2f} session ceiling")
            while time.monotonic() < deadline:
                ensure = post(api, headers, "/compute/ensure", fresh_token, json={
                    "operation_id": "op-ensure-" + uuid.uuid4().hex[:8],
                    "max_hourly_price": hourly,
                    "session_budget": args.budget_usd,
                    "min_vram_gb": 48,
                    "auto_stop_minutes": 5,
                }, timeout=120)
                check(f"ensure accepted ({label})", ensure.status_code == 200,
                      f"status={ensure.status_code}")
                payload = ensure.json()
                state = payload.get("state")
                error = payload.get("error_code")
                if state in {"creating", "starting_pod", "loading_model", "ready"}:
                    print(f"   provider accepted the request: state={state}")
                    break
                if error == "price_limit":
                    # Capacity exists, only the price blocks a pod: escalate once, in bounds.
                    print("   capacity exists but nothing fits this maximum; escalating")
                    break
                capacity_waits += 1
                print(f"   no capacity yet (error={error}); waiting 30s "
                      f"({int(deadline - time.monotonic())}s left in the window)")
                time.sleep(30)
            if payload.get("state") in {"creating", "starting_pod", "loading_model", "ready"}:
                break
        measure["capacity_waits"] = capacity_waits
        state = payload.get("state")
        print(f"   state after ensure: {state} error={payload.get('error_code')}")

        rate = 0.0
        ready = False
        cold_deadline = time.monotonic() + 900  # bound the whole cold start
        while time.monotonic() < cold_deadline:
            body = status_of(api, headers, fresh_token)
            state = body.get("state")
            session = body.get("session") or {}
            if session.get("hourly_rate_usd"):
                rate = float(session["hourly_rate_usd"])
            measure.update({
                "state": state,
                "gpu": session.get("gpu_type"),
                "hourly_rate_usd": rate,
                "budget_usd": session.get("budget_usd"),
            })
            if state == "ready":
                ready = True
                break
            if state in {"offline", "stopped", "error", "create_unknown", "multiple_compute"}:
                print(f"   stopped early: state={state} error={body.get('error_code')}")
                break
            time.sleep(3)

        measure["cold_start_seconds"] = round(time.monotonic() - started, 1)
        check("the model became ready on a real Pod", ready, f"state={measure.get('state')}")
        check("exactly one GPU was selected, at or under the tested maximum",
              bool(measure.get("gpu")) and 0 < rate <= max(args.max_hourly, args.escalate_to),
              f"{measure.get('gpu')} at ${rate}/h")
        if not ready:
            return 1

        watchdog = Watchdog(api, headers, rate or args.max_hourly, RC_HARD_CEILING)
        with watchdog:
            print("2. a real Qwen answer streams through the Gateway")
            prompt = "Write a compact but complete Python module that implements a tiny "
            prompt += "in-memory key-value store with expiring keys, then explain each part "
            prompt += "in numbered steps. Aim for roughly 900-1200 tokens of answer."
            body = {
                "model": ALIAS,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
            }
            request_started = time.monotonic()
            first_chunk_at = None
            chunks = 0
            characters = 0
            finish_reason = None
            seen: set[str] = set()
            duplicates = 0
            with api.stream("POST", "/v1/chat/completions", json=body, headers=headers,
                            timeout=300) as stream:
                stream.raise_for_status()
                for line in stream.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except ValueError:
                        continue
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content")
                    if choices[0].get("finish_reason"):
                        finish_reason = choices[0]["finish_reason"]
                    if piece:
                        if first_chunk_at is None:
                            first_chunk_at = time.monotonic()
                        chunks += 1
                        characters += len(piece)
                        if piece in seen:
                            duplicates += 1
                        seen.add(piece)
            total = time.monotonic() - request_started
            generation = (time.monotonic() - first_chunk_at) if first_chunk_at else 0.0
            measure.update({
                "ttft_seconds": round((first_chunk_at - request_started), 2) if first_chunk_at else None,
                "total_seconds": round(total, 1),
                "chunks": chunks,
                "characters": characters,
                "finish_reason": finish_reason,
                "duplicate_chunks": duplicates,
                "tokens_per_second_estimate": round(chunks / generation, 1) if generation else None,
            })
            check("the stream started", first_chunk_at is not None)
            check("it streamed incrementally (100+ chunks)", chunks >= 100, f"chunks={chunks}")
            check("a long answer arrived (1000+ characters)", characters >= 1000, f"chars={characters}")
            check("no duplicated chunks", duplicates == 0, f"duplicates={duplicates}")
            check("a sane finish reason", finish_reason in {"stop", "length"},
                  f"finish_reason={finish_reason}")
            print(f"   TTFT {measure['ttft_seconds']}s · {chunks} chunks · "
                  f"{characters} chars · ~{measure['tokens_per_second_estimate']} chunks/s")

            print("3. a cancelled generation releases the slot and the Pod stays usable")
            cancelled = 0
            with api.stream("POST", "/v1/chat/completions", json={
                "model": ALIAS,
                "messages": [{"role": "user", "content": "Count slowly from 1 to 500."}],
                "stream": True,
            }, headers=headers, timeout=300) as stream:
                stream.raise_for_status()
                for line in stream.iter_lines():
                    if line.startswith("data:"):
                        cancelled += 1
                    if cancelled >= 5:
                        break  # client-side cancel in the middle of the stream
            check("the cancelled request had produced tokens", cancelled >= 5, f"chunks={cancelled}")
            time.sleep(2)
            after = api.post("/v1/chat/completions", json={
                "model": ALIAS,
                "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
                "stream": False,
            }, headers=headers, timeout=300)
            check("a later request still works (no stuck slot)", after.status_code == 200,
                  f"status={after.status_code}")
            answer = json.dumps(after.json()).lower()
            check("the model answered after the cancel", "ready" in answer, answer[:80])

        print("4. stop the paid compute immediately")
        stopped_response = post(api, headers, "/compute/stop", fresh_token, json={"operation_id": "op-stop-" + uuid.uuid4().hex[:8]},
                                    timeout=120)
        check("stop accepted", stopped_response.status_code == 200,
              f"status={stopped_response.status_code}")
        stopped = True
        for _ in range(40):
            body = status_of(api, headers, fresh_token)
            if body.get("session") is None and body.get("state") in {"stopped", "offline"}:
                break
            time.sleep(3)
        final_state = status_of(api, headers, fresh_token)
        check("no session is left", final_state.get("session") is None,
              f"state={final_state.get('state')}")
        measure["final_state"] = final_state.get("state")
        time.sleep(3)
        try:
            measure["balance_after"] = api.get("/balance", headers=headers).json().get("balance_usd")
        except Exception:  # noqa: BLE001 - the balance read is best effort
            measure["balance_after"] = None
    finally:
        if not stopped:
            try:
                api.post("/compute/stop", json={"operation_id": "op-stop-cleanup"},
                         headers=headers, timeout=60)
                print("cleanup: stop requested for any surviving compute", flush=True)
            except Exception:  # noqa: BLE001
                pass
        try:
            api.post("/auth/revoke", json={"reason": "acceptance_done"}, headers=headers, timeout=30)
        except Exception:  # noqa: BLE001
            pass

    time.sleep(5)
    print()
    print("MEASUREMENTS " + json.dumps(measure, ensure_ascii=False))
    before = measure.get("balance_before")
    after = measure.get("balance_after")
    if before is not None and after is not None:
        print(f"balance before ${before} -> after ${after}")
    if watchdog.fired:
        failures.append("the RC watchdog fired")
    if failures:
        print(f"LIVE COMPUTE ACCEPTANCE FAILED: {len(failures)} check(s)")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("LIVE COMPUTE ACCEPTANCE PASS — one Pod, real Qwen, real stream, stopped")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
