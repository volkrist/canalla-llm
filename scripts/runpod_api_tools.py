"""Provider primitives shared by the acceptance tools: credential, calls, CPU probes, cleanup.

These are the parts every live probe needs and none of them should re-implement:

* the master key, read from the same OS store the installed product uses — never printed, never
  written to disk, only ever sent to the provider;
* one bounded HTTP call;
* the cheapest CPU flavour from the live catalogue (a probe must never need a GPU);
* a CPU probe: create one Pod with **one** mount, run one container command, read the markers out of
  the log stream, terminate the Pod and confirm the account has nothing running. One Pod at a time,
  one attempt, no create retry — the retries only ever happen on reads.

The Pod is created with ``entrypoint``/``cmd`` arrays rather than the ``args`` object: the live
validator refuses the documented object form with ``$.args: got object, want string``, while the two
array fields describe the same one command and are accepted.
"""

from __future__ import annotations

import ctypes
import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from ctypes import wintypes

BASE = "https://api.runpod.io"
PROVIDER_TARGET = "Alex LLM/provider/runpod"
USER_AGENT = "canalla-llm/1.2.0 (acceptance probe)"
TINY_IMAGE = "alpine:3.20"
LIVE_STATUSES = {"RUNNING", "STARTING", "PROVISIONING"}
SETTLED_STATUSES = {"RUNNING", "EXITED", "ERROR", "TERMINATED"}


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


def read_provider_secret(target: str = PROVIDER_TARGET) -> str:
    """The master key, read through the same OS store the installed product uses."""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    pointer = ctypes.c_void_p()
    if not advapi32.CredReadW(target, 1, 0, ctypes.byref(pointer)):
        return ""
    try:
        credential = ctypes.cast(pointer, ctypes.POINTER(CREDENTIALW)).contents
        blob = ctypes.string_at(
            credential.CredentialBlob, credential.CredentialBlobSize
        )
        return blob.decode("utf-8", "ignore").strip()
    finally:
        advapi32.CredFree(pointer)


def key_or_exit() -> str:
    key = read_provider_secret()
    if not key:
        print(f"no credential at '{PROVIDER_TARGET}'", file=sys.stderr)
        raise SystemExit(2)
    return key


def call(url: str, key: str, *, method: str = "GET", payload: dict | None = None):
    """One HTTP call. Returns (status, parsed-or-text). The key is only ever sent, never shown."""
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT}
    if body:
        headers["Content-Type"] = "application/json"
    if not url.startswith("http"):
        url = f"{BASE}{url}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as error:
        raw, status = error.read(), error.code
    except (
        urllib.error.URLError,
        http.client.HTTPException,
        TimeoutError,
        OSError,
    ) as error:
        return 0, str(error)
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw.decode("utf-8", "replace")[:400]


def cheapest_cpu(key: str) -> dict:
    """The cheapest SECURE CPU flavour the provider currently offers, with a power-of-two vCPU."""
    status, payload = call("/v2/catalog/cpus", key)
    if status != 200:
        raise SystemExit(f"catalog/cpus → HTTP {status}: {str(payload)[:200]}")
    rows = payload.get("cpus") if isinstance(payload, dict) else payload
    usable = []
    for row in rows or []:
        price = ((row.get("price") or {}).get("securePerVcpu")) or 0
        limits = row.get("vcpu") or {}
        minimum, maximum = int(limits.get("min") or 0), int(limits.get("max") or 0)
        if price > 0 and minimum >= 2 and maximum >= minimum:
            usable.append(
                (float(price), minimum, maximum, str(row.get("id")), row.get("name"))
            )
    if not usable:
        raise SystemExit("no usable CPU flavour in the catalogue")
    price, minimum, maximum, flavor, name = min(usable)
    vcpu = 2
    while vcpu < minimum:
        vcpu *= 2
    if maximum < vcpu:
        raise SystemExit(f"cheapest flavour {flavor} cannot take {vcpu} vCPUs")
    return {"flavor": flavor, "name": name, "vcpuCount": vcpu, "price_per_vcpu": price}


def flavour(key: str, wanted: str | None) -> dict:
    """A named CPU flavour from the catalogue, or the cheapest one when none is named."""
    if not wanted:
        return cheapest_cpu(key)
    _, payload = call("/v2/catalog/cpus", key)
    rows = (payload.get("cpus") if isinstance(payload, dict) else payload) or []
    for row in rows:
        if str(row.get("id")) == wanted:
            limits = row.get("vcpu") or {}
            minimum = max(2, int(limits.get("min") or 2))
            return {
                "flavor": wanted,
                "name": row.get("name"),
                "vcpuCount": minimum,
                "price_per_vcpu": float(
                    (row.get("price") or {}).get("securePerVcpu") or 0
                ),
            }
    raise SystemExit(f"CPU flavour {wanted} is not in the catalogue")


def pod_summary(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "status": row.get("status"),
        "dataCenterId": row.get("dataCenterId"),
        "cloud": row.get("cloud"),
        "mounts": row.get("mounts"),
        "cost": row.get("cost"),
        "createdAt": row.get("createdAt"),
    }


def running_pods(key: str) -> list[dict]:
    status, payload = call("/v2/pods", key)
    rows = (payload or {}).get("pods") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(rows, list):
        return [{"error": f"HTTP {status}: {str(payload)[:200]}"}]
    return [
        pod_summary(row)
        for row in rows
        if str(row.get("status") or "").upper() in LIVE_STATUSES
    ]


def wait_for_status(key: str, pod_id: str, seconds: int) -> dict:
    """Poll one Pod until it settles or the bounded wait expires. Exactly one GET per step."""
    deadline, final = time.monotonic() + seconds, {}
    while time.monotonic() < deadline:
        time.sleep(6)
        status, current = call(f"/v2/pods/{pod_id}", key)
        if status == 200 and isinstance(current, dict):
            final = (
                current.get("pod") if isinstance(current.get("pod"), dict) else current
            )
            if str(final.get("status") or "").upper() in SETTLED_STATUSES:
                break
    return final


def read_logs(
    key: str, pod_id: str, seconds: int = 40, *, stop_markers: tuple[str, ...] = ()
) -> str:
    """Read the log stream for a bounded window; three documented shapes, one attempt each.

    Only reads are retried: a create never is.
    """
    collected = ""
    for query in ("", "?follow=true", "?tail=500"):
        request = urllib.request.Request(
            f"{BASE}/v2/pods/{pod_id}/logs{query}",
            headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT},
        )
        deadline = time.monotonic() + seconds
        try:
            with urllib.request.urlopen(request, timeout=seconds) as response:
                if response.status >= 400:
                    collected += (
                        f"\n[logs {query or '(plain)'} → HTTP {response.status}]"
                    )
                    continue
                while time.monotonic() < deadline:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    collected += chunk.decode("utf-8", "replace")
                    if stop_markers and all(
                        marker in collected for marker in stop_markers
                    ):
                        return collected
        except urllib.error.HTTPError as error:
            collected += f"\n[logs {query or '(plain)'} → HTTP {error.code}: {error.read()[:120]!r}]"
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            OSError,
            ValueError,
        ) as error:
            collected += (
                f"\n[logs {query or '(plain)'} ended: {type(error).__name__}: {error}]"
            )
        if stop_markers and all(marker in collected for marker in stop_markers):
            break
    return collected


def terminate(key: str, pod_id: str) -> dict:
    """Terminate once, and report what the provider actually answered."""
    last: dict = {}
    for action in ("terminate", "stop"):
        status, payload = call(
            f"/v2/pods/{pod_id}/action", key, method="POST", payload={"action": action}
        )
        last = {
            "action": action,
            "status": status,
            "response": json.dumps(payload, ensure_ascii=False)[:200],
        }
        if status in {200, 202, 204}:
            return last
    return last


def after(logs: str, marker: str) -> str:
    """Everything on the first line carrying ``marker``, trimmed."""
    for line in logs.splitlines():
        if marker in line:
            return line.split(marker, 1)[1].strip()[:400]
    return ""


def cpu_probe(
    key: str,
    *,
    name: str,
    script: str,
    datacenter: str | None,
    volume_id: str | None,
    cpu: dict | None = None,
    disk_gb: int = 10,
    mount_path: str = "/workspace",
    stop_markers: tuple[str, ...] = (),
    poll_seconds: int = 45,
    log_seconds: int = 40,
) -> dict:
    """Create **one** CPU Pod, run one command, read its markers, terminate it, confirm nothing runs.

    ``volume_id`` is the documented ``mounts.network[0].volumeId``; there is no second storage kind
    in the contract, so a probe either mounts what it names or the provider refuses the request.
    ``cpu`` overrides the cheapest flavour — one alternate candidate, never a retry loop, because a
    flavour whose pool is empty is a different pool rather than a second attempt at the same one.
    """
    cpu = cpu or cheapest_cpu(key)
    body = {
        "name": name,
        "cloud": "SECURE",
        "image": TINY_IMAGE,
        "disk": int(disk_gb),
        "cpu": {"id": cpu["flavor"], "vcpuCount": cpu["vcpuCount"]},
        "entrypoint": ["/bin/sh", "-c"],
        "cmd": [script],
    }
    if volume_id:
        body["mounts"] = {"network": [{"volumeId": volume_id, "path": mount_path}]}
    if datacenter:
        body["dataCenterIds"] = [datacenter]

    result: dict = {
        "name": name,
        "datacenter_requested": datacenter,
        "volume_id": volume_id,
        "cpu": cpu,
    }
    status, payload = call("/v2/pods", key, method="POST", payload=body)
    result["http"] = status
    if status not in {200, 201, 202}:
        detail = payload if isinstance(payload, dict) else {"raw": str(payload)[:400]}
        result.update(
            {
                "accepted": False,
                "provider_message": json.dumps(detail, ensure_ascii=False)[:600],
                "pods_after": running_pods(key),
            }
        )
        return result

    pod = (
        payload.get("pod")
        if isinstance(payload, dict) and isinstance(payload.get("pod"), dict)
        else payload
    )
    pod_id = str((pod or {}).get("id") or "")
    result.update({"accepted": True, "pod": pod_summary(pod or {})})
    result["pod_final"] = pod_summary(wait_for_status(key, pod_id, poll_seconds))
    result["logs"] = read_logs(key, pod_id, log_seconds, stop_markers=stop_markers)
    result["terminate"] = terminate(key, pod_id)
    time.sleep(8)
    result["pods_after"] = running_pods(key)
    return result
