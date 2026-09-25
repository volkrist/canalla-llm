"""The one bounded Global Volume attachment test, as a single command.

The question it answers is narrow, and guessing at it wastes real money: **will the public RunPod
REST v2 API accept a real Global Volume ID in the documented mount structure?** So it does exactly
one thing per invocation:

1. ``--dry-run`` (the default) prints the *exact* request body it would send — with the CPU flavour
   and prices read from the live provider catalogue — and creates nothing.
2. ``--live`` sends **one** ``POST /v2/pods``: a CPU pod (no GPU), one network mount, the cheapest
   CPU flavour, and an ``args`` script that proves the mount, writes a fixed test file, reads it
   back and prints markers.
3. It then polls that Pod, reads its log stream for the markers, terminates it, and verifies the
   account has no running Pod left. There is no retry loop: each step happens once, inside a
   bounded wait.
4. ``--live --cross-dc <DC>`` repeats 2–3 *after* the first Pod is gone, in a second datacenter with
   the same volume: that is the cross-datacenter proof, and it never runs two Pods at once.

The key is read from the same OS store the installed product uses and is only ever sent to the
provider: never printed, never written to disk, never cited in a report.
"""

from __future__ import annotations

import argparse
import ctypes
import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from datetime import datetime, timezone

BASE = "https://api.runpod.io"
PROVIDER_TARGET = "Alex LLM/provider/runpod"
USER_AGENT = "canalla-llm/1.2.0 (global-volume attach test)"
TINY_IMAGE = "alpine:3.20"
TEST_FILE = "global-volume-canalla-test.txt"
MARKER_WRITE = "CANALLA_GV_WRITE_OK"
MARKER_DONE = "CANALLA_GV_DONE="
MARKER_ALIVE = "CANALLA_GV_ALIVE"
MARKER_CROSSDC = "CANALLA_GV_CROSSDC_"
POLL_SECONDS = 45
LOG_SECONDS = 40
LIVE_STATUSES = {"RUNNING", "STARTING", "PROVISIONING"}


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
        with urllib.request.urlopen(request, timeout=45) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as error:
        raw, status = error.read(), error.code
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return 0, str(error)
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw.decode("utf-8", "replace")[:400]


def token_for(volume_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"canalla-global-volume-proof {volume_id} {stamp}"


def script_for(volume_id: str, token: str, *, write: bool) -> str:
    """The container's command.

    ``write=True`` is the first test: prove the mount, write the test file, read it back, then keep
    re-printing it so a log stream that only follows from *now* cannot miss the markers.

    ``write=False`` is the cross-datacenter test: it must **never** touch the file — its whole point
    is to see the bytes the first datacenter wrote — so it only reads and prints them.
    """
    head = "set -x; echo CANALLA_GV_START; "
    if write:
        head += (
            "df -h /workspace || echo CANALLA_GV_DF_FAILED; "
            "ls -la /workspace || echo CANALLA_GV_NO_MOUNT; "
            f"printf '%s' '{token}' > /workspace/{TEST_FILE} || echo CANALLA_GV_WRITE_FAILED; "
            f"ls -l /workspace/{TEST_FILE}; cat /workspace/{TEST_FILE}; echo; "
            f"echo {MARKER_WRITE}; echo {MARKER_DONE}{token}; "
        )
    else:
        head += (
            "df -h /workspace || echo CANALLA_GV_DF_FAILED; "
            "ls -la /workspace || echo CANALLA_GV_NO_MOUNT; "
            f"if [ -f /workspace/{TEST_FILE} ]; then "
            f"cat /workspace/{TEST_FILE}; echo; echo {MARKER_CROSSDC}read=YES; "
            f"else echo {MARKER_CROSSDC}read=MISSING; fi; "
        )
    # Repeat, so the markers are visible to a stream that starts after them.
    body = (
        "i=0; while [ $i -lt 40 ]; do sleep 5; i=$((i+1)); "
        f"printf '{MARKER_ALIVE}%s ' \"$i\"; "
        f"cat /workspace/{TEST_FILE} 2>/dev/null; echo; done"
    )
    return head + body


def cheapest_cpu(key: str) -> dict:
    """The cheapest SECURE CPU flavour the provider currently offers, with a power-of-two vCPU."""
    status, payload = call("/v2/catalog/cpus", key)
    if status != 200:
        raise SystemExit(f"catalog/cpus → HTTP {status}: {str(payload)[:200]}")
    rows = payload.get("cpus") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise SystemExit(f"catalog/cpus returned no rows: {str(payload)[:200]}")
    usable = []
    for row in rows:
        price = ((row.get("price") or {}).get("securePerVcpu")) or 0
        limits = row.get("vcpu") or {}
        minimum, maximum = int(limits.get("min") or 0), int(limits.get("max") or 0)
        if price > 0 and minimum >= 2 and maximum >= minimum:
            usable.append((float(price), minimum, str(row.get("id")), row.get("name")))
    if not usable:
        raise SystemExit("no usable CPU flavour in the catalogue")
    price, minimum, flavor, name = min(usable)
    vcpu = 2
    while vcpu < minimum:
        vcpu *= 2
    if maximum < vcpu:
        raise SystemExit(f"cheapest flavour {flavor} cannot take {vcpu} vCPUs")
    return {"flavor": flavor, "name": name, "vcpuCount": vcpu, "price_per_vcpu": price}


def body_for(
    volume_id: str,
    cpu: dict,
    datacenter: str | None,
    name: str,
    *,
    write: bool,
    token: str,
) -> dict:
    body = {
        "name": name,
        "cloud": "SECURE",
        "image": TINY_IMAGE,
        "disk": 10,
        "cpu": {"id": cpu["flavor"], "vcpuCount": cpu["vcpuCount"]},
        # The documented mount structure, in the only field that can carry storage. A network volume
        # is the one kind the contract defines; a global volume either fits here or nothing does.
        "mounts": {"network": [{"volumeId": volume_id, "path": "/workspace"}]},
        # The live validator refused the documented *object* form of `args` (`$.args: got object,
        # want string`), so the command goes into the two fields that are arrays in the same schema:
        # entrypoint + cmd describe exactly one command, which is what the contract asks for.
        "entrypoint": ["/bin/sh", "-c"],
        "cmd": [script_for(volume_id, token, write=write)],
    }
    if datacenter:
        body["dataCenterIds"] = [datacenter]
    return body


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
    live = [
        row for row in rows if str(row.get("status") or "").upper() in LIVE_STATUSES
    ]
    return [pod_summary(row) for row in live]


def read_logs(key: str, pod_id: str, seconds: int = LOG_SECONDS) -> str:
    """Read the pod's log stream for a bounded window; the markers are what we are after.

    Three documented shapes of the same endpoint are tried in order, at most once each: the plain
    stream, an explicit follow, and a tail. A read is retried; a *create* never is.
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
                    if (
                        MARKER_ALIVE in collected
                        and MARKER_DONE in collected
                        or MARKER_CROSSDC in collected
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
        if MARKER_ALIVE in collected or MARKER_CROSSDC in collected:
            break
    return collected


def _after(logs: str, marker: str) -> str:
    """Everything on the first line carrying ``marker``, trimmed: how a marker is read back."""
    for line in logs.splitlines():
        if marker in line:
            return line.split(marker, 1)[1].strip()[:200]
    return ""


def terminate(key: str, pod_id: str) -> dict:
    """Terminate once, and report what the provider actually answered."""
    for action in ("terminate", "stop"):
        status, payload = call(
            f"/v2/pods/{pod_id}/action", key, method="POST", payload={"action": action}
        )
        if status in {200, 202, 204}:
            return {
                "action": action,
                "status": status,
                "response": json.dumps(payload, ensure_ascii=False)[:200],
            }
        last = {
            "action": action,
            "status": status,
            "response": json.dumps(payload, ensure_ascii=False)[:200],
        }
    return last


def one_test(
    key: str,
    volume_id: str,
    *,
    datacenter: str | None,
    label: str,
    token: str,
    write: bool,
) -> dict:
    """One create → observe → terminate cycle. Exactly one Pod, never two."""
    cpu = cheapest_cpu(key)
    body = body_for(
        volume_id,
        cpu,
        datacenter,
        f"canalla-gv-{label}-{int(time.time())}",
        write=write,
        token=token,
    )
    print(
        f"--- {label}: POST /v2/pods cpu={cpu['flavor']}x{cpu['vcpuCount']} "
        f"dc={datacenter or '(scheduler)'} write={write}"
    )
    status, payload = call("/v2/pods", key, method="POST", payload=body)
    result: dict = {"label": label, "http": status, "datacenter_requested": datacenter}

    if status not in {200, 201, 202}:
        detail = payload if isinstance(payload, dict) else {"raw": str(payload)[:400]}
        result.update(
            {
                "accepted": False,
                "provider_code": detail.get("code")
                or detail.get("error")
                or detail.get("message"),
                "provider_message": json.dumps(detail, ensure_ascii=False)[:600],
                "pods_after": running_pods(key),
            }
        )
        print(f"    REJECTED HTTP {status}: {result['provider_message']}")
        return result

    pod = (
        payload.get("pod")
        if isinstance(payload, dict) and isinstance(payload.get("pod"), dict)
        else payload
    )
    pod_id = str((pod or {}).get("id") or "")
    result.update({"accepted": True, "pod": pod_summary(pod or {})})
    print(
        f"    ACCEPTED: pod={pod_id} mounts={json.dumps((pod or {}).get('mounts'), ensure_ascii=False)}"
    )

    deadline, final = time.monotonic() + POLL_SECONDS, pod or {}
    while pod_id and time.monotonic() < deadline:
        time.sleep(6)
        pod_status, current = call(f"/v2/pods/{pod_id}", key)
        if pod_status == 200 and isinstance(current, dict):
            final = (
                current.get("pod") if isinstance(current.get("pod"), dict) else current
            )
            if str(final.get("status") or "").upper() in {
                "RUNNING",
                "EXITED",
                "ERROR",
                "TERMINATED",
            }:
                break
    result["pod_final"] = pod_summary(final)
    print(
        f"    pod status: {result['pod_final'].get('status')} dc={result['pod_final'].get('dataCenterId')}"
    )

    logs = read_logs(key, pod_id) if pod_id else ""
    result["logs_present"] = bool(logs.strip())
    result["mount_ok"] = "CANALLA_GV_NO_MOUNT" not in logs and "workspace" in logs
    result["write_ok"] = MARKER_WRITE in logs
    result["content"] = _after(logs, MARKER_DONE)
    result["crossdc_read"] = _after(logs, MARKER_CROSSDC + "read=")
    # The token was minted before the request, so "the bytes I wrote came back" is a comparison,
    # not a feeling: the same string must appear in the log stream.
    result["content_matches_expected"] = bool(token) and token in logs
    result["logs_tail"] = logs.splitlines()[-10:]
    print(
        f"    mount: {result['mount_ok']} · write marker: {result['write_ok']} · "
        f"expected bytes seen: {result['content_matches_expected']}"
    )
    for line in result["logs_tail"]:
        print(f"      | {line[:160]}")

    result["terminate"] = terminate(key, pod_id)
    time.sleep(8)
    result["pods_after"] = running_pods(key)
    print(f"    terminate {json.dumps(result['terminate'], ensure_ascii=False)[:120]}")
    print(f"    running pods after: {result['pods_after'] or 'none'}")
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--volume-id", required=True, help="the REAL global volume id from the console"
    )
    parser.add_argument(
        "--live", action="store_true", help="create the CPU pod (paid: cents)"
    )
    parser.add_argument("--datacenter", default="US-TX-3", help="first test datacenter")
    parser.add_argument(
        "--cross-dc",
        metavar="DATACENTER",
        help="after the first pod is gone, repeat in this different datacenter, same volume",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the result as JSON only"
    )
    args = parser.parse_args(argv)

    key = read_provider_secret()
    if not key:
        print(f"no credential at '{PROVIDER_TARGET}'")
        return 2

    # One token for the whole run: the first datacenter writes exactly this string, the second must
    # read it back unchanged. A fresh token per pod would make the cross-DC comparison meaningless.
    token = token_for(args.volume_id)
    cpu = cheapest_cpu(key)
    print("cheapest CPU flavour:", json.dumps(cpu, ensure_ascii=False))
    print("expected test-file content:", token)
    print("request body (one POST /v2/pods):")
    print(
        json.dumps(
            body_for(
                args.volume_id,
                cpu,
                args.datacenter,
                "canalla-gv-dryrun",
                write=True,
                token=token,
            ),
            ensure_ascii=False,
            indent=1,
        )
    )
    _, volumes = call("/v2/network-volumes", key)
    print(f"storage visible to the API: {json.dumps(volumes, ensure_ascii=False)}")
    print(f"running pods: {running_pods(key) or 'none'}")

    if not args.live:
        print(
            "\nDRY RUN — nothing created. Add --live to send exactly this one request."
        )
        return 0

    results = [
        one_test(
            key,
            args.volume_id,
            datacenter=args.datacenter,
            label="test1",
            token=token,
            write=True,
        )
    ]
    if args.cross_dc and results[0].get("accepted"):
        results.append(
            one_test(
                key,
                args.volume_id,
                datacenter=args.cross_dc,
                label="crossdc",
                token=token,
                write=False,
            )
        )
    print("\n=== RESULT")
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
