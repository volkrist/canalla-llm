"""Finish the secondary replica: one volume, one copy transaction, byte-for-byte verification.

This is the paid half of the migration, and it only ever runs on the canonical manifest produced by
the controlled measurement (``primary-manifest.json``) — a replica is never built from an assumed
model. Everything it creates is short-lived and explicit:

* **one** STANDARD Network Volume in the freshly scanned secondary datacenter (``--create-volume``);
* **one** copy transaction: ``canalla-copy-source-<tx>`` in the primary datacenter serving the
  volume over an authenticated port, and ``canalla-copy-destination-<tx>`` in the secondary
  datacenter fetching with ``wget -c`` into the new volume (``--copy``).

Both ends live on one clock: a window is at most 15 minutes (the reaper enforces the same 900 s from
outside), the transfer is resumable across windows, and the pair is always released together — the
partial destination data stays on the volume and the next window continues it with ``wget -c``.

Verification is byte-for-byte against the manifest, never "it looks right": GGUF SHA256 and bytes,
both startup scripts, and every runtime binary the manifest recorded. Only then is
``secondary-replica.json`` written.

    python scripts/finish-secondary-replica.py --self-test
    python scripts/finish-secondary-replica.py --scan
    python scripts/finish-secondary-replica.py --create-volume --dc US-NE-1
    python scripts/finish-secondary-replica.py --copy --dc US-NE-1 --volume-id <id> --window 1
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from runpod_api_tools import (
    LIVE_STATUSES,
    call,
    cheapest_cpu,
    key_or_exit,
    read_logs,
    terminate,
)

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO / "artifacts" / "runpod-tx3-watcher"
MANIFEST = ARTIFACTS / "primary-manifest.json"
COPY_STATE = ARTIFACTS / "copy-state.json"
REPLICA_FILE = ARTIFACTS / "secondary-replica.json"
PLACEMENTS = ARTIFACTS / "placements.env"
LOG = ARTIFACTS / "finish-replica.log"

PRIMARY_DC = "US-TX-3"
PRIMARY_VOLUME = "uwgeaie5b0"
SOURCE_PREFIX = "canalla-copy-source-"
DESTINATION_PREFIX = "canalla-copy-destination-"
TTL_SECONDS = 15 * 60
MAX_WINDOWS = 2
COPY_MAX_HOURLY = 1.09
VOLUME_SIZE_GB = 50
VOLUME_MONTHLY_USD = round(VOLUME_SIZE_GB * 0.07, 2)
PRODUCTION_IMAGE = "alpine:3.20"  # the copy only moves bytes; the model is never executed here
BOOKABLE = {"LOW", "MEDIUM", "HIGH"}
STOCK_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message: str) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    line = f"[{now()}] {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )


# --------------------------------------------------------------------- canonical manifest


def canonical_manifest() -> dict:
    """The measurement's manifest, or a refusal: no replica is built from an assumption."""
    if not MANIFEST.exists():
        raise SystemExit(f"no canonical manifest at {MANIFEST} — measure the primary first")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for field in ("gguf_path", "gguf_bytes", "gguf_sha256"):
        if not manifest.get(field):
            raise SystemExit(f"manifest field {field!r} is empty — refusing to copy")
    if manifest["gguf_bytes"] <= 0:
        raise SystemExit("manifest says the model is zero bytes — refusing to copy")
    return manifest


def required_assets(manifest: dict) -> list[dict]:
    """Exactly the production assets: the model, both startup scripts, the runtime binaries."""
    assets = [
        {
            "remote": manifest["gguf_path"],
            "dest": manifest["gguf_path"],
            "bytes": int(manifest["gguf_bytes"]),
            "sha256": manifest["gguf_sha256"],
            "kind": "model",
        }
    ]
    for name, meta in (manifest.get("scripts") or {}).items():
        assets.append(
            {
                "remote": f"/workspace/{name}",
                "dest": f"/workspace/{name}",
                "bytes": int(meta.get("bytes") or 0),
                "sha256": meta.get("sha256") or "",
                "kind": "script",
            }
        )
    for line in manifest.get("binaries") or []:
        parts = str(line).split()
        if len(parts) >= 3:
            assets.append(
                {
                    "remote": parts[0],
                    "dest": parts[0],
                    "bytes": int(parts[1] or 0),
                    "sha256": parts[2],
                    "kind": "runtime",
                }
            )
    return assets


# --------------------------------------------------------------------- datacenter scan (§3)


def gpu_rows(key: str) -> list[dict]:
    status, payload = call(
        "/v2/catalog/gpus?include=AVAILABILITY&product=POD&count=1", key
    )
    rows = (payload or {}).get("gpus") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(rows, list):
        raise SystemExit(f"catalog/gpus → HTTP {status}: {str(payload)[:200]}")
    return rows


def volume_types(key: str) -> dict:
    status, payload = call("/v2/catalog/datacenters", key)
    rows = (payload or {}).get("dataCenters") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(rows, list):
        return {}
    return {str(row.get("id")): row for row in rows if isinstance(row, dict)}


def scan(key: str, *, min_vram: int = 48, max_hourly: float = 2.00) -> list[dict]:
    """Every datacenter that could hold the replica, ranked the way the task specifies."""
    centers = volume_types(key)
    grouped: dict[str, list[dict]] = {}
    for row in gpu_rows(key):
        if not row.get("secure") or int(row.get("memory") or 0) < min_vram:
            continue
        price = (row.get("price") or {}).get("secure") or 0
        if not price or float(price) > max_hourly:
            continue
        for center in row.get("dataCenters") or []:
            stock = str(center.get("availability") or "NONE").upper()
            if stock not in BOOKABLE:
                continue
            grouped.setdefault(str(center.get("id")), []).append(
                {
                    "gpu": row.get("id"),
                    "vram_gb": int(row.get("memory") or 0),
                    "price_per_hour": float(price),
                    "availability": stock,
                }
            )
    ranking = []
    for datacenter, cards in grouped.items():
        if datacenter == PRIMARY_DC:
            continue
        supported = (centers.get(datacenter) or {}).get("networkVolumeTypes") or []
        cards.sort(key=lambda c: (c["price_per_hour"], -STOCK_RANK.get(c["availability"], 0)))
        ranking.append(
            {
                "datacenter": datacenter,
                "network_volume_types": supported,
                "supports_standard_volume": "STANDARD" in supported,
                "gpu_types": len(cards),
                "best_stock": max(STOCK_RANK.get(c["availability"], 0) for c in cards),
                "cheapest": cards[0]["price_per_hour"],
                "cards": cards,
            }
        )
    # 1 availability, 2 number of eligible SKUs, 3 stock, 4 price, 5 the caller breaks the tie.
    ranking.sort(
        key=lambda item: (
            not item["supports_standard_volume"],
            not item["cards"],
            -item["gpu_types"],
            -item["best_stock"],
            item["cheapest"],
            item["datacenter"],
        )
    )
    return ranking


# --------------------------------------------------------------------- the two Pod scripts


def serve_script(password: str) -> str:
    """Serve /workspace over an authenticated port for one window, then idle until released."""
    return f"""set +x
apk add --no-cache busybox-extras >/dev/null 2>&1 || true
printf '/workspace:canalla:{password}\\n' > /tmp/httpd.conf
busybox httpd -f -p 8000 -c /tmp/httpd.conf >/dev/null 2>&1 &
sleep 2
AUTH=$(printf 'canalla:{password}' | base64 | tr -d '\\n')
if wget -q -O /dev/null --header "Authorization: Basic $AUTH" http://127.0.0.1:8000/start-llm.sh; then
  echo CANALLA_S_SERVE_OK header
elif wget -q -O /dev/null "http://canalla:{password}@127.0.0.1:8000/start-llm.sh"; then
  echo CANALLA_S_SERVE_OK url
else
  echo CANALLA_S_SERVE_FAIL
fi
echo CANALLA_S_READY
i=0; while [ $i -lt 200 ]; do sleep 10; i=$((i+1)); echo "CANALLA_S_ALIVE $i"; done"""


def fetch_script(password: str, host: str, port: int, assets: list[dict]) -> str:
    """Fetch the required assets with resume, then hash everything that landed."""
    lines = [
        "set +x",
        f"AUTH=$(printf 'canalla:{password}' | base64 | tr -d '\\n')",
        "fetch() {",
        f'  if wget -c -T 60 --header "Authorization: Basic $AUTH" -O "$2" "http://{host}:{port}$1";',
        "  then return 0; fi",
        f'  if wget -c -T 60 -O "$2" "http://canalla:{password}@{host}:{port}$1";',
        "  then return 0; fi",
        '  echo "CANALLA_C_FETCH_FAILED $1"; return 1',
        "}",
    ]
    for asset in assets:
        remote, dest = asset["remote"], asset["dest"]
        lines.append(f'mkdir -p "$(dirname "{dest}")"')
        lines.append(f'fetch "{remote}" "{dest}"')
    for asset in assets:
        dest = asset["dest"]
        lines.append(
            f'if [ -f "{dest}" ]; then echo "CANALLA_C_FILE {dest} '
            f'$(wc -c < "{dest}" | tr -d \' \') $(sha256sum "{dest}" | cut -d\' \' -f1)"; '
            f'else echo "CANALLA_C_MISSING {dest}"; fi'
        )
    lines.append(
        'echo "CANALLA_C_FILE_COUNT $(find /workspace/models /workspace/*.sh '
        '/workspace/llama.cpp -type f 2>/dev/null | wc -l)"'
    )
    lines.append("echo CANALLA_C_DONE")
    lines.append('i=0; while [ $i -lt 60 ]; do sleep 10; i=$((i+1)); echo "CANALLA_C_ALIVE $i"; done')
    return "\n".join(lines)


# --------------------------------------------------------------------- provider helpers


def live_pods(key: str) -> list[dict]:
    status, payload = call("/v2/pods", key)
    rows = (payload or {}).get("pods") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(rows, list):
        return []
    return [
        {
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or ""),
            "status": str(row.get("status") or "").upper(),
            "createdAt": row.get("createdAt"),
        }
        for row in rows
        if str(row.get("name") or "").startswith((SOURCE_PREFIX, DESTINATION_PREFIX))
        and str(row.get("status") or "").upper() in LIVE_STATUSES
    ]


def create_pod(key: str, body: dict) -> tuple[int, dict]:
    status, payload = call("/v2/pods", key, method="POST", payload=body)
    pod = (
        payload.get("pod")
        if isinstance(payload, dict) and isinstance(payload.get("pod"), dict)
        else payload
    )
    return status, pod if isinstance(pod, dict) else {}


def wait_for_status(key: str, pod_id: str, seconds: int = 120) -> dict:
    deadline, final = time.monotonic() + seconds, {}
    while time.monotonic() < deadline:
        time.sleep(6)
        _, payload = call(f"/v2/pods/{pod_id}", key)
        pod = (
            payload.get("pod")
            if isinstance(payload, dict) and isinstance(payload.get("pod"), dict)
            else payload
        )
        if isinstance(pod, dict) and pod:
            final = pod
            if str(pod.get("status") or "").upper() in {"RUNNING", "EXITED", "ERROR"}:
                break
    return final


def published_port(key: str, pod_id: str, private_port: int = 8000) -> dict:
    _, payload = call(f"/v2/pods/{pod_id}", key)
    pod = (
        payload.get("pod")
        if isinstance(payload, dict) and isinstance(payload.get("pod"), dict)
        else payload
    )
    for entry in ((pod or {}).get("runtime") or {}).get("ports") or []:
        if int(entry.get("private") or 0) == private_port:
            return {"ip": entry.get("ip"), "public": entry.get("public")}
    return {}


def pod_billing(key: str, pod_id: str) -> float | None:
    status, payload = call("/v2/billing/pods", key)
    rows = (payload or {}).get("records") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(rows, list):
        return None
    total, seen = 0.0, False
    for row in rows:
        if str(row.get("podId")) != pod_id:
            continue
        seen = True
        total += sum(
            float(row.get(field) or 0) for field in ("gpuAmount", "diskAmount", "cpuAmount")
        )
    return round(total, 4) if seen else None


def cheapest_gpu_in(key: str, datacenter: str, *, max_hourly: float) -> dict | None:
    best = None
    for row in gpu_rows(key):
        if not row.get("secure"):
            continue
        price = (row.get("price") or {}).get("secure") or 0
        if not price or float(price) > max_hourly:
            continue
        for center in row.get("dataCenters") or []:
            if str(center.get("id")) != datacenter:
                continue
            if str(center.get("availability") or "NONE").upper() not in BOOKABLE:
                continue
            card = {
                "gpu": row.get("id"),
                "vram_gb": int(row.get("memory") or 0),
                "price_per_hour": float(price),
            }
            if best is None or card["price_per_hour"] < best["price_per_hour"]:
                best = card
    return best


# --------------------------------------------------------------------- actions


def create_volume(key: str, datacenter: str) -> dict:
    name = f"canalla-secondary-{datacenter.lower()}"
    status, payload = call(
        "/v2/network-volumes",
        key,
        method="POST",
        payload={
            "name": name,
            "dataCenter": datacenter,
            "size": VOLUME_SIZE_GB,
            "type": "STANDARD",
        },
    )
    if status not in {200, 201} or not isinstance(payload, dict) or not payload.get("id"):
        raise SystemExit(f"volume create → HTTP {status}: {str(payload)[:200]}")
    record = {
        "volume_id": str(payload["id"]),
        "datacenter": datacenter,
        "size_gb": VOLUME_SIZE_GB,
        "type": "STANDARD",
        "name": name,
        "created_at": now(),
        "monthly_usd": VOLUME_MONTHLY_USD,
    }
    write_json(ARTIFACTS / "secondary-volume.json", record)
    log(f"secondary volume created: {record['volume_id']} in {datacenter}")
    return record


def copy_window(
    key: str, *, datacenter: str, volume_id: str, window: int, budget_seconds: int = 780
) -> dict:
    """One bounded copy window: source serves, destination fetches with resume, both released."""
    manifest = canonical_manifest()
    assets = required_assets(manifest)
    password = base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip("=")
    tx = f"w{window}-{int(time.time())}"
    started = time.monotonic()
    state = {
        "window": window,
        "transaction": tx,
        "started_at": now(),
        "datacenter": datacenter,
        "volume_id": volume_id,
        "assets": [
            {"path": a["dest"], "bytes": a["bytes"], "kind": a["kind"]} for a in assets
        ],
    }

    expected = live_pods(key)
    if expected:
        raise SystemExit(
            f"a copy pair is already live ({[p['id'] for p in expected]}) — not starting a second one"
        )

    source_body = {
        "name": f"{SOURCE_PREFIX}{tx}",
        "cloud": "SECURE",
        "image": PRODUCTION_IMAGE,
        "disk": 20,
        "dataCenterIds": [PRIMARY_DC],
        "mounts": {"network": [{"volumeId": PRIMARY_VOLUME, "path": "/workspace"}]},
        "ports": ["8000/http"],
        "entrypoint": ["/bin/sh", "-c"],
        "cmd": [serve_script(password)],
    }
    source_cpu = _try_cpu(key)
    source_gpu = cheapest_gpu_in(key, PRIMARY_DC, max_hourly=COPY_MAX_HOURLY)
    if source_cpu:
        source_body["cpu"] = source_cpu
        source_cost = "cpu"
    elif source_gpu:
        source_body["gpu"] = {"id": source_gpu["gpu"], "count": 1}
        source_cost = source_gpu
    else:
        raise SystemExit(
            f"no mount-capable compute in {PRIMARY_DC} within ${COPY_MAX_HOURLY:.2f}/h — stopping"
        )

    dest_body = {
        "name": f"{DESTINATION_PREFIX}{tx}",
        "cloud": "SECURE",
        "image": PRODUCTION_IMAGE,
        "disk": 20,
        "dataCenterIds": [datacenter],
        "mounts": {"network": [{"volumeId": volume_id, "path": "/workspace"}]},
        "entrypoint": ["/bin/sh", "-c"],
        "cmd": [fetch_script(password, "<source>", 8000, assets)],
    }
    dest_cpu = _try_cpu(key)
    dest_gpu = cheapest_gpu_in(key, datacenter, max_hourly=COPY_MAX_HOURLY)
    if dest_cpu:
        dest_body["cpu"] = dest_cpu
    elif dest_gpu:
        dest_body["gpu"] = {"id": dest_gpu["gpu"], "count": 1}
    else:
        raise SystemExit(
            f"no mount-capable compute in {datacenter} within ${COPY_MAX_HOURLY:.2f}/h — stopping"
        )

    source_id = dest_id = ""
    try:
        status, source_pod = create_pod(key, source_body)
        source_id = str(source_pod.get("id") or "")
        log(f"source create → HTTP {status} pod={source_id} ({source_cost})")
        if status not in {200, 201, 202} or not source_id:
            raise SystemExit(f"source create failed → HTTP {status}")

        wait_for_status(key, source_id, 180)
        read_logs(key, source_id, 120, stop_markers=("CANALLA_S_READY",))
        address = published_port(key, source_id)
        if not address.get("public") or not address.get("ip"):
            raise SystemExit("source pod published no address — cannot copy")
        log(f"source serving at {address['ip']}:{address['public']}")

        dest_body["cmd"] = [
            fetch_script(password, str(address["ip"]), int(address["public"]), assets)
        ]
        status, dest_pod = create_pod(key, dest_body)
        dest_id = str(dest_pod.get("id") or "")
        log(f"destination create → HTTP {status} pod={dest_id}")
        if status not in {200, 201, 202} or not dest_id:
            raise SystemExit(f"destination create failed → HTTP {status}")

        wait_for_status(key, dest_id, 180)
        remaining = max(120, budget_seconds - int(time.monotonic() - started))
        logs = read_logs(key, dest_id, min(remaining, 600), stop_markers=("CANALLA_C_DONE",))
        state["destination_log_tail"] = logs[-4000:]
        state["elapsed_seconds"] = round(time.monotonic() - started, 1)
        state["measurement"] = parse_copy_log(logs, assets)
        log(
            "copy window "
            f"{window}: done={state['measurement']['done']} "
            f"bytes={state['measurement']['bytes_on_destination']}/{state['measurement']['bytes_expected']}"
        )
    finally:
        for pod_id in (dest_id, source_id):
            if pod_id:
                released = terminate(key, pod_id)
                log(f"released {pod_id} → {str(released)[:80]}")

    state["pods"] = {
        "source": {"id": source_id, "spec": source_cost, "billing_usd": source_id and pod_billing(key, source_id)},
        "destination": {
            "id": dest_id,
            "spec": dest_cpu or dest_gpu,
            "billing_usd": dest_id and pod_billing(key, dest_id),
        },
    }
    state["finished_at"] = now()
    state["pods_after"] = [p["id"] for p in live_pods(key)]
    write_json(COPY_STATE, state)
    return state


def _try_cpu(key: str) -> dict | None:
    try:
        cpu = cheapest_cpu(key)
    except SystemExit:
        return None
    if cpu and cpu["price_per_vcpu"] * cpu["vcpuCount"] <= COPY_MAX_HOURLY:
        return {"id": cpu["flavor"], "vcpuCount": cpu["vcpuCount"]}
    return None


def parse_copy_log(logs: str, assets: list[dict]) -> dict:
    """What the destination actually reported: per-file bytes/SHA, and whether it finished."""
    from_log = {}
    for line in logs.splitlines():
        for marker in ("CANALLA_C_FILE ", "CANALLA_C_MISSING "):
            if marker in line:
                tail = line.split(marker, 1)[1].strip().rstrip("'\",}")
                parts = tail.split()
                if parts:
                    path = parts[0]
                    if marker.endswith("MISSING "):
                        from_log[path] = {"bytes": 0, "sha256": "", "missing": True}
                    elif len(parts) >= 3:
                        from_log[path] = {
                            "bytes": int(parts[1]),
                            "sha256": parts[2],
                            "missing": False,
                        }
    per_asset, matched = [], 0
    for asset in assets:
        got = from_log.get(asset["dest"]) or from_log.get(asset["remote"]) or {}
        ok = (
            not got.get("missing", True)
            and int(got.get("bytes") or 0) == asset["bytes"]
            and (got.get("sha256") or "").lower() == (asset["sha256"] or "").lower()
        )
        matched += 1 if ok else 0
        per_asset.append(
            {
                "path": asset["dest"],
                "kind": asset["kind"],
                "expected_bytes": asset["bytes"],
                "expected_sha256": asset["sha256"],
                "got_bytes": int(got.get("bytes") or 0),
                "got_sha256": got.get("sha256") or "",
                "verified": ok,
            }
        )
    return {
        "done": "CANALLA_C_DONE" in logs,
        "bytes_expected": sum(a["bytes"] for a in assets),
        "bytes_on_destination": sum(item["got_bytes"] for item in per_asset),
        "assets_verified": matched,
        "assets_total": len(assets),
        "verified": matched == len(assets) and bool(assets),
        "files": per_asset,
    }


def write_replica(datacenter: str, volume_id: str, manifest: dict, verification: dict) -> dict:
    replica = {
        "model": "orcarouter/Qwen3.8-27B-Uncensored (Q5_K_M)",
        "gguf_path": manifest["gguf_path"],
        "model_bytes": int(manifest["gguf_bytes"]),
        "model_sha256": manifest["gguf_sha256"],
        "runtime": manifest.get("binaries") or [],
        "scripts": {
            name: meta.get("sha256") for name, meta in (manifest.get("scripts") or {}).items()
        },
        "volume_id": volume_id,
        "datacenter": datacenter,
        "storage_type": "regional_network_volume",
        "size_gb": VOLUME_SIZE_GB,
        "verified_at": now(),
        "ready": True,
        "verification": verification,
    }
    write_json(REPLICA_FILE, {"replicas": [replica]})
    write_json(
        ARTIFACTS / "placements.json",
        {
            "primary": {
                "datacenter": PRIMARY_DC,
                "volume_id": PRIMARY_VOLUME,
                "storage_type": "regional_network_volume",
            },
            "secondary": {
                "datacenter": datacenter,
                "volume_id": volume_id,
                "storage_type": "regional_network_volume",
            },
        },
    )
    PLACEMENTS.write_text(
        f"RUNPOD_DATACENTERS={PRIMARY_DC}:{PRIMARY_VOLUME},{datacenter}:{volume_id}\n"
        f"RUNPOD_REPLICAS={json.dumps({'replicas': [replica]}, ensure_ascii=False)}\n",
        encoding="utf-8",
    )
    log(f"secondary replica VERIFIED and recorded ({datacenter}:{volume_id})")
    return replica


# --------------------------------------------------------------------- offline proof


def self_test() -> int:
    """Offline proof of the rules that spend money: assets, verification, one pair, windows."""
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    manifest = {
        "gguf_path": "/workspace/models/m/model.gguf",
        "gguf_bytes": 1000,
        "gguf_sha256": "a" * 64,
        "scripts": {
            "start-llm.sh": {"bytes": 10, "sha256": "b" * 64},
            "check-llm.sh": {"bytes": 5, "sha256": "c" * 64},
        },
        "binaries": [f"/workspace/llama.cpp/build/bin/llama-server 20 {'d' * 64}"],
    }
    assets = required_assets(manifest)
    check(
        "only the required production assets are copied",
        [a["dest"] for a in assets]
        == [
            "/workspace/models/m/model.gguf",
            "/workspace/start-llm.sh",
            "/workspace/check-llm.sh",
            "/workspace/llama.cpp/build/bin/llama-server",
        ],
        str([a["dest"] for a in assets]),
    )
    check(
        "a copy never brings logs, pid files or caches",
        all(
            not a["dest"].endswith((".log", ".pid", ".lock"))
            for a in assets
        ),
    )

    good = "\n".join(
        [
            'CANALLA_C_FILE /workspace/models/m/model.gguf 1000 ' + "a" * 64,
            'CANALLA_C_FILE /workspace/start-llm.sh 10 ' + "b" * 64,
            'CANALLA_C_FILE /workspace/check-llm.sh 5 ' + "c" * 64,
            'CANALLA_C_FILE /workspace/llama.cpp/build/bin/llama-server 20 ' + "d" * 64,
            "CANALLA_C_DONE",
        ]
    )
    parsed = parse_copy_log(good, assets)
    check(
        "a complete destination is verified byte-for-byte",
        parsed["verified"] and parsed["assets_verified"] == 4 and parsed["done"],
        str(parsed)[:200],
    )

    wrong = good.replace("a" * 64, "e" * 64)
    check(
        "a wrong GGUF hash rejects the replica",
        not parse_copy_log(wrong, assets)["verified"],
    )

    short = good.replace(" 1000 ", " 999 ")
    check(
        "a short GGUF rejects the replica",
        not parse_copy_log(short, assets)["verified"],
    )

    partial = "\n".join(
        [
            'CANALLA_C_FILE /workspace/models/m/model.gguf 400 ' + "a" * 64,
            "CANALLA_C_MISSING /workspace/start-llm.sh",
        ]
    )
    partial_parsed = parse_copy_log(partial, assets)
    check(
        "a partial window is reported as unfinished and resumable",
        not partial_parsed["verified"]
        and partial_parsed["bytes_on_destination"] == 400
        and not partial_parsed["done"],
        str(partial_parsed)[:200],
    )

    source_body = {"name": f"{SOURCE_PREFIX}w1-1"}
    dest_body = {"name": f"{DESTINATION_PREFIX}w1-1"}
    check(
        "the pair shares one transaction id and is named as the reaper expects",
        source_body["name"].startswith(SOURCE_PREFIX)
        and dest_body["name"].startswith(DESTINATION_PREFIX)
        and source_body["name"][len(SOURCE_PREFIX) :] == dest_body["name"][len(DESTINATION_PREFIX) :],
    )
    check(
        "the copy ceiling is the approved $1.09/h",
        COPY_MAX_HOURLY == 1.09 and MAX_WINDOWS == 2 and TTL_SECONDS == 900,
        f"{COPY_MAX_HOURLY=} {MAX_WINDOWS=} {TTL_SECONDS=}",
    )
    check(
        "the secondary volume is one 50 GB STANDARD",
        VOLUME_SIZE_GB == 50 and VOLUME_MONTHLY_USD == 3.50,
    )

    failures = [name for name, ok, _ in results if not ok]
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail and not ok else ''}")
    print(f"finish-replica self-test: {len(results) - len(failures)}/{len(results)} passed")
    return 1 if failures else 0


# --------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--scan", action="store_true", help="rank secondary datacenters (read-only)")
    parser.add_argument("--create-volume", action="store_true", help="create the one secondary volume")
    parser.add_argument("--copy", action="store_true", help="one bounded copy window")
    parser.add_argument("--dc", help="secondary datacenter")
    parser.add_argument("--volume-id", help="secondary volume id")
    parser.add_argument("--window", type=int, default=1, help="1 or 2")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    key = key_or_exit()
    if args.scan:
        ranking = scan(key)
        for item in ranking:
            mark = "CANDIDATE" if item["supports_standard_volume"] else "-"
            print(
                f"[{mark}] {item['datacenter']:10} volumes={item['network_volume_types']} "
                f"skus={item['gpu_types']} stock={item['best_stock']} "
                f"cheapest=${item['cheapest']}"
            )
            for card in item["cards"]:
                print(f"        {card['gpu']} {card['vram_gb']}GB ${card['price_per_hour']} {card['availability']}")
        winner = next((i for i in ranking if i["supports_standard_volume"]), None)
        print(f"chosen: {winner['datacenter'] if winner else 'NONE'}")
        return 0 if winner else 1

    if args.create_volume:
        if not args.dc:
            raise SystemExit("--create-volume needs --dc")
        record = create_volume(key, args.dc)
        print(json.dumps(record, ensure_ascii=False, indent=1))
        return 0

    if args.copy:
        if not args.dc or not args.volume_id:
            raise SystemExit("--copy needs --dc and --volume-id")
        if args.window not in (1, 2):
            raise SystemExit("--window is 1 or 2; a third paid window is not authorized")
        state = copy_window(
            key, datacenter=args.dc, volume_id=args.volume_id, window=args.window
        )
        verification = state["measurement"]
        print(json.dumps(verification, ensure_ascii=False, indent=1))
        if verification["verified"]:
            manifest = canonical_manifest()
            write_replica(args.dc, args.volume_id, manifest, verification)
            print("SECONDARY REPLICA = VERIFIED")
            return 0
        print(
            f"window {args.window} incomplete: "
            f"{verification['bytes_on_destination']}/{verification['bytes_expected']} bytes"
        )
        return 2
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
