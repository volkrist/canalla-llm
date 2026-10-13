#!/usr/bin/env python3
"""Deliver the pinned llama.cpp runtime to a reserve Network Volume through one temporary CPU Pod.

The reserve datacenter's volume needs a runtime, and the cheapest safe way to put one there is a
short-lived CPU Pod that mounts the volume, downloads the pinned official archives once, verifies
them and stages them. This script owns that one transaction and nothing else:

* it **refuses to start** if any Pod is already live (never a second Pod, never an ambiguous owner),
  if the named volume is absent or in the wrong datacenter, or if the live CPU price would exceed the
  approved budget;
* the Pod runs **one** command (the text file given by ``--command-file``, produced by
  ``scripts/provision-llama-runtime.py --pod-command --pod-verify``) and reports ``CANALLA_L_*``
  markers;
* the moment the Pod has an id, an **independent** watchdog is armed on the host
  (``systemd-run --on-active=…``) that deletes exactly that Pod id until it is gone, so the Pod dies
  even if this process, the SSH session or the agent does not survive;
* the Pod is deleted in a ``finally`` block, the account is re-read until it really has no live Pod,
  and the watchdog is disarmed only after that is confirmed.

Nothing here ever touches another volume, the Gateway or the operator's price preferences. Reads are
retried; a **create is never retried**, because an ambiguous create must not become a second Pod.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import subprocess
import time
import urllib.error
import urllib.request

BASE = "https://api.runpod.io"
USER_AGENT = "canalla-llm/1.2.1 (runtime delivery)"
DEFAULT_ENV = "/etc/alex-gateway/runpod.env"
IMAGE = "ubuntu:24.04"  # glibc, the same family as the production GPU image
LIVE_STATUSES = {"RUNNING", "STARTING", "PROVISIONING"}
SETTLED_STATUSES = {"RUNNING", "EXITED", "ERROR", "TERMINATED"}
FAILURE_MARKERS = ("CANALLA_L_NO_CURL", "CANALLA_L_FETCH_FAILED", "CANALLA_L_BAD")


# ------------------------------------------------------------------------------- credential


def read_key(env_file: str) -> str:
    """The master key, from the same host store the deployed Gateway reads. Never printed."""
    try:
        with open(env_file) as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("RUNPOD_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


# ------------------------------------------------------------------------------- http


def call(path: str, key: str, *, method: str = "GET", payload: dict | None = None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT, "Accept": "application/json"}
    if body:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{BASE}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as error:
        raw, status = error.read(), error.code
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as error:
        return 0, str(error)
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw.decode("utf-8", "replace")[:400]


def live_pods(key: str) -> list[dict]:
    status, payload = call("/v2/pods", key)
    rows = payload.get("pods") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(rows, list):
        raise SystemExit(f"/v2/pods → HTTP {status}: {str(payload)[:200]}")
    return [
        {"id": row.get("id"), "name": row.get("name"), "status": row.get("status")}
        for row in rows
        if str(row.get("status") or "").upper() in LIVE_STATUSES
    ]


def cpu_hourly(key: str, flavour: str, vcpu: int) -> float:
    status, payload = call("/v2/catalog/cpus", key)
    rows = (payload.get("cpus") if isinstance(payload, dict) else payload) or []
    for row in rows:
        if str(row.get("id")) == flavour:
            per = float((row.get("price") or {}).get("securePerVcpu") or 0)
            return round(per * vcpu, 4)
    raise SystemExit(f"CPU flavour {flavour} is not in the catalogue")


def find_volume(key: str, volume_id: str) -> dict | None:
    status, payload = call("/v2/network-volumes", key)
    if status != 200:
        raise SystemExit(f"/v2/network-volumes → HTTP {status}")
    rows = (payload.get("networkVolumes") if isinstance(payload, dict) else None) or []
    for row in rows:
        if str(row.get("id")) == volume_id:
            return row
    return None


# ------------------------------------------------------------------------------- pod lifecycle


def create_pod(key: str, body: dict):
    """One create. Returns (status, pod-or-payload). The caller never retries a create."""
    return call("/v2/pods", key, method="POST", payload=body)


def pod_state(key: str, pod_id: str) -> dict:
    status, payload = call(f"/v2/pods/{pod_id}", key)
    if status != 200 or not isinstance(payload, dict):
        return {}
    pod = payload.get("pod") if isinstance(payload.get("pod"), dict) else payload
    return pod or {}


def wait_for_running(key: str, pod_id: str, seconds: int) -> dict:
    deadline, final = time.monotonic() + seconds, {}
    while time.monotonic() < deadline:
        final = pod_state(key, pod_id)
        if str(final.get("status") or "").upper() in SETTLED_STATUSES:
            return final
        time.sleep(5)
    return final


def read_logs_until(key: str, pod_id: str, seconds: int, done: str, stop: tuple[str, ...]) -> str:
    """Accumulate the log stream until the done marker, a failure marker, or the deadline."""
    collected, deadline = "", time.monotonic() + seconds
    while time.monotonic() < deadline:
        for query in ("?tail=2000", ""):
            request = urllib.request.Request(
                f"{BASE}/v2/pods/{pod_id}/logs{query}",
                headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT},
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    chunk = response.read(262144).decode("utf-8", "replace")
            except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError):
                chunk = ""
            if len(chunk) > len(collected):
                collected = chunk
        if done in log_lines(collected) or any(
            any(marker in line for line in log_lines(collected)) for marker in stop
        ):
            return collected
        time.sleep(8)
    return collected


def log_lines(text: str) -> list[str]:
    """The container's own output lines.

    The log endpoint answers either as raw text or as an SSE JSON stream
    (``data: {"source":"container","line":"…","ts":…}``); both must yield the same lines, or a
    marker that is plainly in the log would be reported as missing.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        match = re.search(r'"line":"(.*?)","ts"', raw)
        if not match:
            lines.append(raw)
            continue
        try:
            lines.append(json.loads('"' + match.group(1) + '"'))
        except ValueError:
            lines.append(match.group(1))
    return lines


def markers_from(logs: str) -> dict:
    """Every ``CANALLA_L_*`` marker line as ``{name: value}`` (the value may be empty)."""
    found: dict[str, str] = {}
    for line in log_lines(logs):
        if "CANALLA_L_" not in line:
            continue
        head, _, tail = line.partition(" ")
        found[head.strip()] = tail.strip()
    return found


def delete_pod(key: str, pod_id: str) -> dict:
    """One documented delete, then read back what the provider actually answered."""
    status, payload = call(f"/v2/pods/{pod_id}", key, method="DELETE")
    return {"status": status, "response": json.dumps(payload, ensure_ascii=False)[:200]}


def confirm_gone(key: str, pod_id: str, seconds: int) -> list[dict]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        remaining = [pod for pod in live_pods(key) if str(pod.get("id")) == pod_id]
        if not remaining:
            return []
        time.sleep(6)
    return [pod for pod in live_pods(key) if str(pod.get("id")) == pod_id]


# ------------------------------------------------------------------------------- watchdog


def arm_watchdog(script: str, pod_id: str, seconds: int, unit: str) -> dict:
    result = subprocess.run(
        ["systemd-run", "--on-active", str(seconds), f"--unit={unit}", script, pod_id],
        capture_output=True,
        text=True,
        check=False,
    )
    active = subprocess.run(
        ["systemctl", "is-active", f"{unit}.timer"], capture_output=True, text=True, check=False
    )
    return {
        "returncode": result.returncode,
        "stdout": (result.stdout or "").strip()[:200],
        "stderr": (result.stderr or "").strip()[:200],
        "timer_state": (active.stdout or "").strip(),
    }


def disarm_watchdog(unit: str) -> str:
    subprocess.run(["systemctl", "stop", f"{unit}.timer"], capture_output=True, check=False)
    subprocess.run(["systemctl", "reset-failed", f"{unit}.timer", unit], capture_output=True, check=False)
    state = subprocess.run(
        ["systemctl", "is-active", f"{unit}.timer"], capture_output=True, text=True, check=False
    )
    return (state.stdout or "").strip()


# ------------------------------------------------------------------------------- run


def run(args) -> int:
    key = read_key(args.env_file)
    if not key:
        raise SystemExit(f"no RUNPOD_API_KEY in {args.env_file}")

    command = open(args.command_file, encoding="utf-8").read()
    if "CANALLA_L_OK" not in command:
        raise SystemExit("the command file does not look like a runtime delivery command")

    print("== preflight ==")
    running = live_pods(key)
    print(f"pods: {running}")
    if running:
        raise SystemExit("a Pod is already live — refusing to create a second one")

    hourly = cpu_hourly(key, args.cpu_flavour, args.vcpu)
    expected = round(hourly * (args.max_seconds / 3600.0), 4)
    print(f"cpu {args.cpu_flavour} × {args.vcpu} vCPU = ${hourly}/hour; {args.max_seconds}s → ${expected}")
    if hourly > args.max_hourly:
        raise SystemExit(f"live price ${hourly}/hour exceeds the approved ${args.max_hourly}")
    if expected > args.budget:
        raise SystemExit(f"expected ${expected} exceeds the approved ${args.budget}")

    volume = find_volume(key, args.volume_id)
    print(f"volume {args.volume_id}: {volume}")
    if not volume:
        raise SystemExit("the named volume is not visible to the API")
    datacenter = volume.get("dataCenterId") or volume.get("dataCenter")
    if datacenter != args.datacenter:
        raise SystemExit(f"volume lives in {datacenter}, not {args.datacenter}")

    body = {
        "name": args.name,
        "cloud": "SECURE",
        "image": args.image,
        "disk": args.disk,
        "cpu": {"id": args.cpu_flavour, "vcpuCount": args.vcpu},
        "entrypoint": ["/bin/sh", "-c"],
        "cmd": [command],
        "dataCenterIds": [args.datacenter],
        "mounts": {"network": [{"volumeId": args.volume_id, "path": "/workspace"}]},
    }
    # Never log the command: it is long and not needed to explain an outcome.
    print(f"== create {args.name} in {args.datacenter} ==")
    status, payload = create_pod(key, body)
    if status not in {200, 201, 202} or not isinstance(payload, (dict, list)):
        raise SystemExit(f"create → HTTP {status}: {str(payload)[:400]}")
    pod = payload.get("pod") if isinstance(payload, dict) and isinstance(payload.get("pod"), dict) else payload
    pod_id = str((pod or {}).get("id") or "")
    if not pod_id:
        # An answered-but-id-less create is ambiguous: nothing is retried, the operator is told.
        raise SystemExit(f"create → HTTP {status} without an id: {json.dumps(payload)[:400]}")
    created_at = time.time()
    print(f"pod id: {pod_id}")

    watchdog = arm_watchdog(args.watchdog, pod_id, args.max_seconds, args.watchdog_unit)
    print(f"watchdog: {watchdog}")
    if watchdog["timer_state"] not in {"active", "waiting"}:
        # Without an independent stop the paid Pod must not run.
        delete_pod(key, pod_id)
        raise SystemExit(f"the watchdog is not armed ({watchdog}) — the Pod was deleted")

    summary = {"pod_id": pod_id, "hourly_usd": hourly, "expected_usd": expected, "watchdog": watchdog}
    try:
        final = wait_for_running(key, pod_id, args.start_seconds)
        summary["pod_status"] = final.get("status")
        print(f"pod status: {summary['pod_status']}")
        logs = read_logs_until(key, pod_id, args.log_seconds, "CANALLA_L_OK", FAILURE_MARKERS)
        lines = log_lines(logs)
        summary["markers"] = markers_from(logs)
        summary["log_tail"] = "\n".join(line for line in lines if "CANALLA_L_" in line)
        summary["ok"] = "CANALLA_L_OK" in lines
        summary["elapsed_seconds"] = round(time.time() - created_at, 1)
    finally:
        print("== delete ==")
        summary["delete"] = delete_pod(key, pod_id)
        remaining = confirm_gone(key, pod_id, args.confirm_seconds)
        summary["still_running"] = remaining
        summary["pods_after"] = live_pods(key)
        print(f"delete: {summary['delete']}; still_running={remaining}; pods_after={summary['pods_after']}")
        if not remaining:
            summary["watchdog_disarmed"] = disarm_watchdog(args.watchdog_unit)
        else:
            summary["watchdog_disarmed"] = "left armed — the Pod is still present"
        print(f"watchdog: {summary['watchdog_disarmed']}")

    print("== summary ==")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0 if summary.get("ok") and not summary.get("still_running") else 1


# ------------------------------------------------------------------------------- self-test


def self_test() -> int:
    failures = 0

    def check(name, condition):
        nonlocal failures
        if not condition:
            failures += 1
            print(f"  FAIL {name}")

    check("reads a plain key", read_key_from_text("RUNPOD_API_KEY=abc\n") == "abc")
    check("reads a quoted key", read_key_from_text('RUNPOD_API_KEY="a b"\n') == "a b")
    check("ignores other lines", read_key_from_text("OTHER=1\nRUNPOD_API_KEY=k\n") == "k")
    check("absent key is empty", read_key_from_text("OTHER=1\n") == "")

    logs = "noise\nCANALLA_L_SERVER_SHA256 deadbeef\nCANALLA_L_OK\n"
    markers = markers_from(logs)
    check("extracts a valued marker", markers.get("CANALLA_L_SERVER_SHA256") == "deadbeef")
    check("extracts a bare marker", "CANALLA_L_OK" in markers)
    check("sees success", "CANALLA_L_OK" in logs)
    check("sees no failure", not any(m in logs for m in FAILURE_MARKERS))

    sse = (
        'data: {"source":"container","line":"CANALLA_L_VERSION version: 0.6.0-dev","ts":"t"}\n'
        'id: t\ndata: {"source":"container","line":"CANALLA_L_OK","ts":"t"}\n'
    )
    check("parses the SSE log shape", "CANALLA_L_OK" in log_lines(sse))
    check(
        "parses a valued SSE marker",
        markers_from(sse).get("CANALLA_L_VERSION") == "version: 0.6.0-dev",
    )

    hourly = round(0.03 * 4, 4)
    check("cpu price is the approved $0.12/hour", hourly == 0.12)
    check("15 minutes is within $0.04", round(hourly * 0.25, 4) <= 0.04)

    if failures:
        print(f"DELIVER-POD: self-test FAILED ({failures})")
        return 1
    print("DELIVER-POD: self-test PASS")
    return 0


def read_key_from_text(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("RUNPOD_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--env-file", default=DEFAULT_ENV)
    parser.add_argument("--command-file", default="", help="the pod command to run")
    parser.add_argument("--name", default="canalla-runtime-mo1")
    parser.add_argument("--datacenter", default="US-MO-1")
    parser.add_argument("--volume-id", default="")
    parser.add_argument("--image", default=IMAGE)
    parser.add_argument("--cpu-flavour", default="cpu3c")
    parser.add_argument("--vcpu", type=int, default=4)
    parser.add_argument("--disk", type=int, default=10)
    parser.add_argument("--max-hourly", type=float, default=0.12)
    parser.add_argument("--budget", type=float, default=0.04)
    parser.add_argument("--max-seconds", type=int, default=900)
    parser.add_argument("--start-seconds", type=int, default=180)
    parser.add_argument("--log-seconds", type=int, default=420)
    parser.add_argument("--confirm-seconds", type=int, default=120)
    parser.add_argument("--watchdog", default="/home/alex/watchdog.sh")
    parser.add_argument("--watchdog-unit", default="canalla-runtime-mo1-watchdog")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.command_file or not args.volume_id:
        parser.error("--command-file and --volume-id are required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
