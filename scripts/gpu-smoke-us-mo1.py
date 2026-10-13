#!/usr/bin/env python3
"""One short, real GPU acceptance for the reserve runtime, in US-MO-1.

This is the paid test the owner approves explicitly — it creates **one** GPU Pod, mounts the reserve
Network Volume, runs ``scripts/gpu-smoke-in-pod.sh`` inside the production image, and deletes the Pod
no matter how it ends. The discipline is the same one the CPU delivery uses:

* it refuses to start if any Pod is already live, if the live GPU price exceeds the approved ceiling,
  if the expected cost exceeds the approved budget, or if the volume is not in the named datacenter;
* the live per-datacenter availability is read first and reported, so ``LOW`` is a fact in the log and
  not an assumption;
* an independent ``systemd-run --on-active`` watchdog is armed the moment the Pod has an id and is
  disarmed only after the account is confirmed empty;
* the create is never retried (an ambiguous create must not become a second Pod), and the Pod is
  deleted in a ``finally`` block.

Nothing here changes the Gateway, the volumes or the operator's preferences.
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
USER_AGENT = "canalla-llm/1.2.1 (gpu smoke)"
DEFAULT_ENV = "/etc/alex-gateway/runpod.env"
LIVE_STATUSES = {"RUNNING", "STARTING", "PROVISIONING"}
SETTLED_STATUSES = {"RUNNING", "EXITED", "ERROR", "TERMINATED"}
FAILURE_MARKERS = ("CANALLA_G_NOT_READY", "CANALLA_G_GEN_FAILED", "CANALLA_G_MISSING")
DONE = "CANALLA_G_DONE"


# ------------------------------------------------------------------------------- credential


def read_key(env_file: str) -> str:
    try:
        with open(env_file) as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("RUNPOD_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def read_key_from_text(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("RUNPOD_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
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


def gpu_offer(key: str, gpu_id: str) -> dict:
    status, payload = call("/v2/catalog/gpus?include=AVAILABILITY&product=POD", key)
    rows = (payload.get("gpus") if isinstance(payload, dict) else payload) or []
    for row in rows:
        if str(row.get("id")) == gpu_id:
            price = row.get("price") or {}
            return {
                "id": gpu_id,
                "name": row.get("name"),
                "memory_gb": row.get("memory"),
                "secure_per_hour": float(price.get("secure") or 0),
                "availability": row.get("availability"),
            }
    raise SystemExit(f"GPU {gpu_id} is not in the catalogue")


def datacenter_gpus(key: str, datacenter: str) -> dict:
    status, payload = call("/v2/catalog/datacenters?include=GPU_AVAILABILITY", key)
    centers = (payload.get("dataCenters") if isinstance(payload, dict) else payload) or []
    for center in centers:
        if str(center.get("id")) == datacenter:
            return {
                "volume_types": center.get("networkVolumeTypes") or [],
                "gpus": {
                    str(item.get("id")): item.get("availability")
                    for item in (center.get("gpuAvailability") or [])
                    if isinstance(item, dict)
                },
            }
    return {}


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
        if str(final.get("status") or "").upper() == "ERROR":
            return final
        time.sleep(6)
    return final


def log_lines(text: str) -> list[str]:
    """The container's own lines, whether the endpoint answered with text or an SSE JSON stream."""
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
    found: dict[str, str] = {}
    for line in log_lines(logs):
        if "CANALLA_G_" not in line:
            continue
        head, _, tail = line.partition(" ")
        found[head.strip()] = tail.strip()
    return found


def read_logs_until(key: str, pod_id: str, seconds: int, stop_markers: tuple[str, ...]) -> str:
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
        lines = log_lines(collected)
        if DONE in lines or any(any(marker in line for line in lines) for marker in stop_markers):
            return collected
        time.sleep(8)
    return collected


def delete_pod(key: str, pod_id: str) -> dict:
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


# ------------------------------------------------------------------------------- preflight


def preflight(args, key: str) -> dict:
    running = live_pods(key)
    print(f"pods: {running}")
    if running:
        raise SystemExit("a Pod is already live — refusing to create a second one")

    offer = gpu_offer(key, args.gpu_id)
    expected = round(offer["secure_per_hour"] * (args.max_seconds / 3600.0), 4)
    print(f"gpu: {offer}")
    print(f"expected cost for {args.max_seconds}s: ${expected}")
    if offer["secure_per_hour"] <= 0:
        raise SystemExit("the GPU has no live secure price")
    if offer["secure_per_hour"] > args.max_hourly:
        raise SystemExit(
            f"live price ${offer['secure_per_hour']}/hour exceeds the approved ${args.max_hourly}"
        )
    if expected > args.budget:
        raise SystemExit(f"expected ${expected} exceeds the approved ${args.budget}")

    center = datacenter_gpus(key, args.datacenter)
    print(f"{args.datacenter}: volume_types={center.get('volume_types')} gpus={center.get('gpus')}")
    if args.gpu_id not in (center.get("gpus") or {}):
        raise SystemExit(f"{args.gpu_id} is not offered in {args.datacenter} right now")

    volume = find_volume(key, args.volume_id)
    print(f"volume {args.volume_id}: {volume}")
    if not volume:
        raise SystemExit("the named volume is not visible to the API")
    datacenter = volume.get("dataCenterId") or volume.get("dataCenter")
    if datacenter != args.datacenter:
        raise SystemExit(f"volume lives in {datacenter}, not {args.datacenter}")
    return offer


# ------------------------------------------------------------------------------- run


def run(args) -> int:
    key = read_key(args.env_file)
    if not key:
        raise SystemExit(f"no RUNPOD_API_KEY in {args.env_file}")
    command = open(args.command_file, encoding="utf-8").read()
    if DONE not in command:
        raise SystemExit("the command file is not a GPU smoke script")

    print("== preflight ==")
    offer = preflight(args, key)

    body = {
        "name": args.name,
        "cloud": "SECURE",
        "image": args.image,
        "disk": args.disk,
        "gpu": {"id": args.gpu_id, "count": 1, "minCudaVersion": args.min_cuda},
        "entrypoint": ["/bin/bash", "-lc"],
        "cmd": [command],
        "dataCenterIds": [args.datacenter],
        "mounts": {"network": [{"volumeId": args.volume_id, "path": "/workspace"}]},
    }
    print(f"== create {args.name} in {args.datacenter} ==")
    status, payload = call("/v2/pods", key, method="POST", payload=body)
    if status not in {200, 201, 202} or not isinstance(payload, (dict, list)):
        raise SystemExit(f"create → HTTP {status}: {str(payload)[:400]}")
    pod = payload.get("pod") if isinstance(payload, dict) and isinstance(payload.get("pod"), dict) else payload
    pod_id = str((pod or {}).get("id") or "")
    if not pod_id:
        raise SystemExit(f"create → HTTP {status} without an id: {json.dumps(payload)[:400]}")
    created_at = time.time()
    print(f"pod id: {pod_id}")

    watchdog = arm_watchdog(args.watchdog, pod_id, args.max_seconds, args.watchdog_unit)
    print(f"watchdog: {watchdog}")
    if watchdog["timer_state"] not in {"active", "waiting"}:
        delete_pod(key, pod_id)
        raise SystemExit(f"the watchdog is not armed ({watchdog}) — the Pod was deleted")

    summary = {
        "pod_id": pod_id,
        "gpu": offer,
        "expected_usd": round(offer["secure_per_hour"] * (args.max_seconds / 3600.0), 4),
        "watchdog": watchdog,
    }
    try:
        final = wait_for_running(key, pod_id, args.start_seconds)
        summary["pod_status"] = final.get("status")
        summary["pod_error"] = final.get("error") if isinstance(final, dict) else None
        print(f"pod status: {summary['pod_status']} {summary['pod_error'] or ''}")
        logs = read_logs_until(key, pod_id, args.log_seconds, FAILURE_MARKERS)
        summary["markers"] = markers_from(logs)
        summary["log"] = "\n".join(
            line for line in log_lines(logs) if "CANALLA_G" in line or "CANALLA_G_ERROR" in line
        )
        summary["ok"] = "CANALLA_G_ANSWER" in summary["markers"] and DONE in log_lines(logs)
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
    check("absent key is empty", read_key_from_text("OTHER=1\n") == "")

    logs = (
        'data: {"source":"container","line":"CANALLA_G_READY_SECONDS 95","ts":"t"}\n'
        'id: t\ndata: {"source":"container","line":"CANALLA_G_ANSWER Привет","ts":"t"}\n'
        'id: t\ndata: {"source":"container","line":"CANALLA_G_DONE","ts":"t"}\n'
    )
    check("parses the SSE shape", "CANALLA_G_DONE" in log_lines(logs))
    check("reads a valued marker", markers_from(logs).get("CANALLA_G_ANSWER") == "Привет")
    check("sees success", "CANALLA_G_ANSWER" in markers_from(logs))
    check("does not see a failure", not any(m in logs for m in FAILURE_MARKERS))

    # The approved ceiling: $1.79/hour for at most 12 minutes is inside the approved $0.36.
    hourly = 1.79
    check("12 minutes at $1.79 is within the approved budget", round(hourly * 720 / 3600, 4) <= 0.36)

    if failures:
        print(f"GPU-SMOKE: self-test FAILED ({failures})")
        return 1
    print("GPU-SMOKE: self-test PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--env-file", default=DEFAULT_ENV)
    parser.add_argument("--command-file", default="")
    parser.add_argument("--name", default="canalla-gpu-smoke-mo1")
    parser.add_argument("--datacenter", default="US-MO-1")
    parser.add_argument("--volume-id", default="")
    parser.add_argument("--gpu-id", default="NVIDIA A100-SXM4-80GB")
    parser.add_argument("--image", default="runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404")
    parser.add_argument("--min-cuda", default="12.8")
    parser.add_argument("--disk", type=int, default=10)
    parser.add_argument("--max-hourly", type=float, default=1.79)
    parser.add_argument("--budget", type=float, default=0.36)
    parser.add_argument("--max-seconds", type=int, default=720)
    parser.add_argument("--start-seconds", type=int, default=240)
    parser.add_argument("--log-seconds", type=int, default=420)
    parser.add_argument("--confirm-seconds", type=int, default=120)
    parser.add_argument("--watchdog", default="/home/alex/watchdog.sh")
    parser.add_argument("--watchdog-unit", default="canalla-gpu-smoke-watchdog")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    key = read_key(args.env_file)
    if not key:
        raise SystemExit(f"no RUNPOD_API_KEY in {args.env_file}")
    if args.preflight_only:
        preflight(args, key)
        return 0
    if not args.command_file or not args.volume_id:
        parser.error("--command-file and --volume-id are required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
