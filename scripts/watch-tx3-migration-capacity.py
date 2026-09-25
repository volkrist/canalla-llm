"""Watch US-TX-3 for mount-capable compute, then migrate the model to a verified secondary replica.

US-TX-3 has had zero placeable compute for hours at a time, and capacity returns without warning, so
this is a watcher and not a script that gets run by hand: it polls the **free** catalogue every two
minutes, and the moment a mount-capable option appears inside the operator's budget it runs the whole
migration once, on its own:

1. create exactly ONE temporary migration Pod in US-TX-3 with the primary volume mounted — a CPU Pod
   if the platform has one, otherwise the cheapest Secure GPU, never above ``$1.00/h``;
2. measure the production model and **save it**: exact GGUF path/bytes/SHA256, both runtime scripts
   (base64 in the log, decoded into the artifacts), the file list, the llama.cpp binary and version;
3. re-scan **all** datacenters at that moment and pick the best secondary — real availability first,
   then how many eligible GPU types it carries, then price, always requiring STANDARD volumes;
4. refuse to buy if the replica does not fit in 50 GB, otherwise create exactly ONE 50 GB STANDARD
   volume there;
5. copy the assets over an authenticated, short-lived HTTP port (the account has no SSH keys, and the
   S3 API does not cover US-TX-3), resumable per file;
6. verify SHA256, byte counts and file counts on the destination — the replica is only "verified"
   when every one of them matches;
7. terminate both migration Pods and prove nothing is left running.

State is on disk (``state.json``), the log is append-only (``watcher.log``) and the pid is pinned
(``watcher.pid``), so the process can be detached from any chat session and inspected later. A real
byte-range lock (``watcher.lock``) is held for the lifetime of the process, and every create first
looks for a live migration Pod by name, so a restart can neither double-spend nor double-create.
Budgets are hard limits, not intentions: exceeding one stops the run instead of spending.

    python scripts/watch-tx3-migration-capacity.py --once     # one free scan, prints the verdict
    python scripts/watch-tx3-migration-capacity.py --run      # scan now, migrate if possible
    python scripts/watch-tx3-migration-capacity.py            # the watcher loop (detached)
    python scripts/watch-tx3-migration-capacity.py --status   # state + last log lines
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runpod_api_tools import (
    LIVE_STATUSES,
    call,
    cheapest_cpu,
    key_or_exit,
    read_logs,
    running_pods,
    terminate,
)

PRIMARY_VOLUME = "uwgeaie5b0"
PRIMARY_DC = "US-TX-3"
MODEL_DIR = "/workspace/models/orcarouter-qwen38"

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts" / "runpod-tx3-watcher"
STATE = ARTIFACTS / "state.json"
LOG = ARTIFACTS / "watcher.log"
PIDFILE = ARTIFACTS / "watcher.pid"
LOCKFILE = ARTIFACTS / "watcher.lock"
MIGRATION_POD_PREFIX = "canalla-migrate-"

POLL_SECONDS = 120
CPU_PROBE_COOLDOWN = 900
# Hard budgets. A run that would exceed any of them stops instead of spending.
MIGRATION_MAX_HOURLY = 1.00
MIGRATION_MAX_SECONDS = 15 * 60
SECONDARY_SIZE_GB = 50
SECONDARY_MAX_MONTHLY = 3.50
LIVE_TEST_MAX_HOURLY = 2.00
LIVE_TEST_MAX_SECONDS = 20 * 60
SETTLED = {"RUNNING", "EXITED", "ERROR", "TERMINATED"}
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


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"phase": "watching", "poll_count": 0, "started_at": now(), "events": []}


def save_state(state: dict) -> None:
    state["updated_at"] = now()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )


def event(state: dict, message: str, **fields) -> None:
    log(message)
    state.setdefault("events", []).append({"at": now(), "message": message, **fields})
    state["events"] = state["events"][-200:]
    save_state(state)


_LOCK_HANDLE = None


def take_lock() -> bool:
    """Take the OS byte-range lock only — no pid-file write, so probes stay non-destructive."""
    global _LOCK_HANDLE
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    handle = LOCKFILE.open("a+b")
    handle.seek(0)
    handle.write(b"0")
    handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _LOCK_HANDLE = handle
    return True


def exclusive_pid() -> bool:
    """True when this process may own the watcher: the OS lock is taken and no live pid is pinned.

    The pid file on its own is a check-then-write race — two watchers started seconds apart both
    "won" it once, and two live pollers mean two create attempts for the same capacity. The lock is
    a real byte-range lock held open for the lifetime of the process, so the second one loses.
    """
    if not take_lock():
        log("another watcher holds the lock — refusing to start a second one")
        return False
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    if PIDFILE.exists():
        try:
            other = int(PIDFILE.read_text(encoding="utf-8").strip() or 0)
        except ValueError:
            other = 0
        if other and other != os.getpid() and _alive(other):
            log(
                f"another watcher is alive (pid {other}) — refusing to start a second one"
            )
            return False
    PIDFILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# --------------------------------------------------------------------- catalogue (read-only)


def gpu_rows(key: str) -> list[dict]:
    status, payload = call(
        "/v2/catalog/gpus?include=AVAILABILITY&product=POD&count=1", key
    )
    rows = (payload or {}).get("gpus") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(rows, list):
        raise RuntimeError(f"catalog/gpus → HTTP {status}: {str(payload)[:200]}")
    return rows


def tx3_candidates(key: str, *, max_hourly: float) -> list[dict]:
    """Every bookable Secure GPU in US-TX-3 at or below the budget — any VRAM is enough to migrate."""
    found = []
    for row in gpu_rows(key):
        if not row.get("secure"):
            continue
        price = (row.get("price") or {}).get("secure") or 0
        if not price or float(price) > max_hourly:
            continue
        for center in row.get("dataCenters") or []:
            if str(center.get("id")) != PRIMARY_DC:
                continue
            stock = str(center.get("availability") or "NONE").upper()
            if stock in BOOKABLE:
                found.append(
                    {
                        "gpu": row.get("id"),
                        "vram_gb": int(row.get("memory") or 0),
                        "price_per_hour": float(price),
                        "availability": stock,
                        "cuda": row.get("cudaVersions"),
                    }
                )
    found.sort(
        key=lambda item: (
            item["price_per_hour"],
            -STOCK_RANK.get(item["availability"], 0),
        )
    )
    return found


def volume_support(key: str) -> dict:
    status, payload = call("/v2/catalog/datacenters", key)
    rows = (payload or {}).get("dataCenters") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(rows, list):
        return {}
    return {str(row.get("id")): row for row in rows if isinstance(row, dict)}


def secondary_ranking(key: str, *, min_vram: int, max_hourly: float) -> list[dict]:
    """Datacenters that can hold the replica, ordered by how reliable their capacity looks.

    Availability first (a bookable card beats a cheaper absent one), then how many *different*
    eligible GPU types the datacenter carries, then the best stock signal, then price: a datacenter
    with several ways to place the same model is the one worth trusting.
    """
    centers = volume_support(key)
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
        volumes = (centers.get(datacenter) or {}).get("networkVolumeTypes") or []
        cards.sort(
            key=lambda card: (
                card["price_per_hour"],
                -STOCK_RANK.get(card["availability"], 0),
            )
        )
        ranking.append(
            {
                "datacenter": datacenter,
                "network_volume_types": volumes,
                "supports_standard_volume": "STANDARD" in volumes,
                "gpu_types": len(cards),
                "best_stock": max(
                    STOCK_RANK.get(card["availability"], 0) for card in cards
                ),
                "cheapest": cards[0]["price_per_hour"],
                "cards": cards,
            }
        )
    ranking.sort(
        key=lambda item: (
            not item["supports_standard_volume"],
            -item["gpu_types"],
            -item["best_stock"],
            item["cheapest"],
            item["datacenter"],
        )
    )
    return ranking


# --------------------------------------------------------------------- migration pod


def migration_script(password: str) -> str:
    """Measure the model, save the scripts, then serve the volume over an authenticated port."""
    return f"""set -x
echo CANALLA_M_START
df -h /workspace; ls -la /workspace
for f in /workspace/start-llm.sh /workspace/check-llm.sh; do
  [ -f "$f" ] || {{ echo "CANALLA_M_MISSING $f"; continue; }}
  echo "CANALLA_M_FILE $f $(wc -c < "$f") $(sha256sum "$f" | cut -d' ' -f1)"
  echo "CANALLA_M_B64_$(basename "$f")=$(base64 -w0 < "$f")"
done
GGUF=$(ls {MODEL_DIR}/*.gguf 2>/dev/null | head -1)
if [ -n "$GGUF" ]; then
  echo "CANALLA_M_GGUF_PATH=$GGUF"
  echo "CANALLA_M_GGUF_BYTES=$(wc -c < "$GGUF")"
  echo "CANALLA_M_GGUF_SHA256=$(sha256sum "$GGUF" | cut -d' ' -f1)"
else
  echo CANALLA_M_GGUF_MISSING
fi
echo "CANALLA_M_USED_BYTES=$(du -sb /workspace | cut -f1)"
echo "CANALLA_M_FILE_COUNT=$(find /workspace -type f | wc -l)"
find /workspace -type f -printf '%s %p\\n' | sort -rn | head -25
for b in llama-server llama-cli llama-bench; do
  p=$(command -v $b 2>/dev/null || ls /workspace/$b /workspace/*/$b 2>/dev/null | head -1)
  [ -n "$p" ] && {{ echo "CANALLA_M_BIN $b $p"; $p --version 2>&1 | head -3; }}
done
printf '/workspace:canalla:{password}\\n' > /tmp/httpd.conf
busybox httpd -f -p 8000 -c /tmp/httpd.conf &
sleep 2
wget -q -O /dev/null --user=canalla --password='{password}' http://127.0.0.1:8000/start-llm.sh \\
  && echo CANALLA_M_SERVE_SELFTEST_OK || echo CANALLA_M_SERVE_SELFTEST_FAIL
echo CANALLA_M_READY_FOR_COPY
i=0; while [ $i -lt 100 ]; do sleep 10; i=$((i+1)); echo "CANALLA_M_ALIVE $i"; done"""


def _age_seconds(stamp: object) -> float | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def live_migration_pods(key: str, *, mine: str = "") -> list[dict]:
    """Live pods this watcher family created (matched by name), excluding the one we already own."""
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
        if str(row.get("name") or "").startswith(MIGRATION_POD_PREFIX)
        and str(row.get("status") or "").upper() in LIVE_STATUSES
        and str(row.get("id") or "") != mine
    ]


def create_migration_pod(
    key: str, state: dict, candidates: list[dict], *, allow_cpu: bool = True
) -> dict:
    """At most ONE live migration Pod: adopt nothing, create nothing while another one is alive.

    ``allow_cpu=False`` is the rule for a real catalogue signal: the eligible card the catalogue just
    showed *is* the attempt for this poll, and the blind CPU-probe cooldown must never delay it.
    """
    mine = str(state.get("source_pod") or "")
    for orphan in live_migration_pods(key, mine=mine):
        age = _age_seconds(orphan.get("createdAt"))
        if age is None or age <= MIGRATION_MAX_SECONDS:
            event(
                state,
                "another migration pod is already live — not creating a second one",
                pod=orphan["id"],
                age_seconds=None if age is None else int(age),
            )
            return {}
        event(
            state,
            f"orphan migration pod {orphan['id']} is past its "
            f"{MIGRATION_MAX_SECONDS // 60} min budget — terminating it",
            age_seconds=int(age),
        )
        terminate(key, orphan["id"])
    return _create_migration_pod(key, state, candidates, allow_cpu=allow_cpu)


def _create_migration_pod(
    key: str, state: dict, candidates: list[dict], *, allow_cpu: bool = True
) -> dict:
    """One attempt with exactly one spec: the catalogue's card, or a CPU flavour on a blind probe.

    A failed attempt never retries inside the same poll — a capacity race puts the watcher back to
    ``watching`` and the next try is the next poll, 120 seconds later.
    """
    password = base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip("=")
    cpu = None
    if allow_cpu:
        try:
            cpu = cheapest_cpu(key)
        except SystemExit:
            cpu = None
    bodies = []
    if cpu and cpu["price_per_vcpu"] * cpu["vcpuCount"] <= MIGRATION_MAX_HOURLY:
        bodies.append(
            {"kind": "cpu", "cpu": {"id": cpu["flavor"], "vcpuCount": cpu["vcpuCount"]}}
        )
    if candidates:
        cheapest = candidates[0]
        bodies.append(
            {
                "kind": "gpu",
                "gpu": {"id": cheapest["gpu"], "count": 1},
                "price": cheapest,
            }
        )

    for candidate in bodies:
        body = {
            "name": f"{MIGRATION_POD_PREFIX}{int(time.time())}",
            "cloud": "SECURE",
            "image": "alpine:3.20",
            "disk": 20,
            "dataCenterIds": [PRIMARY_DC],
            "mounts": {"network": [{"volumeId": PRIMARY_VOLUME, "path": "/workspace"}]},
            "ports": ["8000/http"],
            "entrypoint": ["/bin/sh", "-c"],
            "cmd": [migration_script(password)],
        }
        if candidate["kind"] == "cpu":
            body["cpu"] = candidate["cpu"]
        else:
            body["gpu"] = candidate["gpu"]
        status, payload = call("/v2/pods", key, method="POST", payload=body)
        state["migration_attempts"] = state.get("migration_attempts", []) + [
            {"at": now(), "kind": candidate["kind"], "http": status}
        ]
        if status in {200, 201, 202}:
            pod = (
                payload.get("pod") if isinstance(payload.get("pod"), dict) else payload
            )
            event(
                state,
                f"migration pod created ({candidate['kind']})",
                pod=(pod or {}).get("id"),
                spec=candidate,
            )
            return {
                "pod": pod or {},
                "kind": candidate["kind"],
                "password": password,
                "spec": candidate,
            }
        detail = payload if isinstance(payload, dict) else {"raw": str(payload)[:200]}
        event(
            state,
            f"migration {candidate['kind']} refused: HTTP {status}",
            detail=json.dumps(detail, ensure_ascii=False)[:300],
        )
    return {}


def wait_running(key: str, pod_id: str, seconds: int = 120) -> dict:
    deadline, final = time.monotonic() + seconds, {}
    while time.monotonic() < deadline:
        time.sleep(6)
        status, current = call(f"/v2/pods/{pod_id}", key)
        if status == 200 and isinstance(current, dict):
            final = (
                current.get("pod") if isinstance(current.get("pod"), dict) else current
            )
            if str(final.get("status") or "").upper() in SETTLED:
                break
    return final


def published_address(key: str, pod_id: str, private_port: int = 8000) -> dict:
    _, current = call(f"/v2/pods/{pod_id}", key)
    pod = (
        current.get("pod")
        if isinstance(current, dict) and isinstance(current.get("pod"), dict)
        else current
    )
    runtime = (pod or {}).get("runtime") or {}
    for entry in runtime.get("ports") or []:
        if int(entry.get("private") or 0) == private_port:
            return {
                "ip": entry.get("ip"),
                "public": entry.get("public"),
                "type": entry.get("type"),
            }
    return {}


def measure_from_logs(logs_text: str) -> dict:
    """The primary's canonical manifest, read out of the markers the pod printed."""
    read = lambda marker: next(
        (
            line.split(marker, 1)[1].strip()
            for line in logs_text.splitlines()
            if marker in line
        ),
        "",
    )
    manifest = {
        "dc": PRIMARY_DC,
        "volume_id": PRIMARY_VOLUME,
        "gguf_path": read("CANALLA_M_GGUF_PATH="),
        "gguf_bytes": int(read("CANALLA_M_GGUF_BYTES=") or 0),
        "gguf_sha256": read("CANALLA_M_GGUF_SHA256="),
        "used_bytes": int(read("CANALLA_M_USED_BYTES=") or 0),
        "file_count": int(read("CANALLA_M_FILE_COUNT=") or 0),
        "serve_selftest": read("CANALLA_M_SERVE_SELFTEST_"),
        "measured_at": now(),
        "scripts": {},
        "binaries": [],
    }
    for name in ("start-llm.sh", "check-llm.sh"):
        encoded = read(f"CANALLA_M_B64_{name}=")
        meta = read(f"CANALLA_M_FILE /workspace/{name} ")
        parts = meta.split()
        manifest["scripts"][name] = {
            "bytes": int(parts[0]) if parts else 0,
            "sha256": parts[1] if len(parts) > 1 else "",
            "content_b64": encoded,
        }
    for line in logs_text.splitlines():
        if line.startswith("CANALLA_M_BIN "):
            manifest["binaries"].append(line[len("CANALLA_M_BIN ") :][:200])
    return manifest


def copy_script(host: str, port: int, password: str, gguf_path: str) -> str:
    """Pull the replica: resumable per file, then hash everything on the destination."""
    remote = gguf_path.lstrip("/")
    return f"""set -x
mkdir -p {MODEL_DIR} /workspace
fetch() {{
  wget -c -T 60 --user=canalla --password='{password}' -O "$2" "$1" || echo "CANALLA_C_FETCH_FAILED $1"
}}
fetch http://{host}:{port}/{remote} {gguf_path}
fetch http://{host}:{port}/start-llm.sh /workspace/start-llm.sh
fetch http://{host}:{port}/check-llm.sh /workspace/check-llm.sh
echo CANALLA_C_GGUF_BYTES=$(wc -c < {gguf_path} 2>/dev/null || echo 0)
echo CANALLA_C_GGUF_SHA256=$(sha256sum {gguf_path} 2>/dev/null | cut -d' ' -f1)
for f in start-llm.sh check-llm.sh; do
  echo "CANALLA_C_FILE $f $(wc -c < /workspace/$f 2>/dev/null || echo 0) $(sha256sum /workspace/$f 2>/dev/null | cut -d' ' -f1)"
done
echo CANALLA_C_FILE_COUNT=$(find /workspace -type f | wc -l)
echo CANALLA_C_DONE
i=0; while [ $i -lt 60 ]; do sleep 10; i=$((i+1)); echo "CANALLA_C_ALIVE $i"; done"""


def live_test_script(alias: str) -> str:
    """One GPU Pod, from a verified replica: start llama.cpp, wait for the alias, one generation."""
    return f"""set -x
ls -la /workspace; df -h /workspace
echo CANALLA_L_SHA=$(sha256sum {MODEL_DIR}/*.gguf 2>/dev/null | cut -d' ' -f1)
nohup bash /workspace/start-llm.sh > /tmp/start.log 2>&1 &
i=0
until curl -sf --max-time 3 http://127.0.0.1:8080/health >/dev/null; do
  i=$((i+1)); [ $i -gt 120 ] && {{ echo CANALLA_L_TIMEOUT; tail -20 /tmp/start.log; exit 0; }}
  sleep 5
done
echo CANALLA_L_HEALTH_OK
echo CANALLA_L_MODELS=$(curl -s --max-time 10 http://127.0.0.1:8080/v1/models | head -c 400)
curl -s --max-time 120 http://127.0.0.1:8080/v1/chat/completions \\
  -H 'Content-Type: application/json' \\
  -d '{{"model":"{alias}","messages":[{{"role":"user","content":"Ответь одним словом: готово"}}],"max_tokens":16,"temperature":0}}' \\
  > /tmp/gen.json; echo CANALLA_L_GEN=$(head -c 600 /tmp/gen.json)
echo CANALLA_L_DONE
sleep 120"""


def live_secondary_test(
    key: str, state: dict, winner: dict, volume_id: str, alias: str
) -> dict:
    """Prove the replica end to end on ONE GPU Pod, only when the copy is already verified."""
    eligible = [
        card
        for card in winner.get("cards", [])
        if card["price_per_hour"] <= LIVE_TEST_MAX_HOURLY
    ]
    if not eligible:
        return {"ran": False, "reason": "no eligible >=48 GB GPU <= $2.00/h right now"}
    card = eligible[0]
    body = {
        "name": f"canalla-live-{int(time.time())}",
        "cloud": "SECURE",
        "image": "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
        "disk": 20,
        "dataCenterIds": [winner["datacenter"]],
        "gpu": {"id": card["gpu"], "count": 1},
        "mounts": {"network": [{"volumeId": volume_id, "path": "/workspace"}]},
        "entrypoint": ["/bin/bash", "-c"],
        "cmd": [live_test_script(alias)],
    }
    status, payload = call("/v2/pods", key, method="POST", payload=body)
    pod = (
        payload.get("pod")
        if isinstance(payload, dict) and isinstance(payload.get("pod"), dict)
        else payload
    )
    pod_id = str((pod or {}).get("id") or "")
    event(state, f"live secondary test create → HTTP {status}", pod=pod_id, card=card)
    if status not in {200, 201, 202} or not pod_id:
        return {
            "ran": False,
            "reason": f"HTTP {status}: {str(payload)[:200]}",
            "card": card,
        }
    started = time.monotonic()
    wait_running(key, pod_id, 180)
    logs_text = read_logs(
        key, pod_id, min(600, LIVE_TEST_MAX_SECONDS), stop_markers=("CANALLA_L_DONE",)
    )
    read = lambda marker: next(
        (
            line.split(marker, 1)[1].strip()
            for line in logs_text.splitlines()
            if marker in line
        ),
        "",
    )
    result = {
        "ran": True,
        "card": card,
        "health": "CANALLA_L_HEALTH_OK" in logs_text,
        "models": read("CANALLA_L_MODELS=")[:300],
        "generation": read("CANALLA_L_GEN=")[:400],
        "sha_on_volume": read("CANALLA_L_SHA="),
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "max_hourly": LIVE_TEST_MAX_HOURLY,
    }
    result["answer_non_empty"] = (
        bool(result["generation"]) and '"content":' in result["generation"]
    )
    terminate(key, pod_id)
    event(
        state,
        f"live secondary test: health={result['health']} answer={result['answer_non_empty']}",
        elapsed=result["elapsed_seconds"],
    )
    return result


# --------------------------------------------------------------------- the pipeline


def run_pipeline(key: str, state: dict, *, allow_cpu: bool = True) -> None:
    candidates = tx3_candidates(key, max_hourly=MIGRATION_MAX_HOURLY)
    event(
        state,
        f"US-TX-3 candidates within ${MIGRATION_MAX_HOURLY:.2f}/h: {len(candidates)}",
        found=candidates[:4],
    )
    started = time.monotonic()
    session = create_migration_pod(key, state, candidates, allow_cpu=allow_cpu)
    if not session:
        state["phase"] = "watching"
        event(
            state,
            "no migration compute obtainable right now — back to watching (not an error)",
        )
        return

    pod_id = str((session["pod"] or {}).get("id") or "")
    state.update(
        {"phase": "measuring", "source_pod": pod_id, "source_spec": session["spec"]}
    )
    save_state(state)
    final = wait_running(key, pod_id)
    logs_text = read_logs(key, pod_id, 120, stop_markers=("CANALLA_M_READY_FOR_COPY",))
    manifest = measure_from_logs(logs_text)
    address = published_address(key, pod_id)
    state["primary_manifest"] = manifest
    state["source_address"] = address
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "primary-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    for name, data in manifest["scripts"].items():
        if data.get("content_b64"):
            (ARTIFACTS / name).write_bytes(base64.b64decode(data["content_b64"]))
    event(
        state,
        "primary measured",
        gguf=manifest["gguf_path"],
        bytes=manifest["gguf_bytes"],
        sha256=manifest["gguf_sha256"],
        used=manifest["used_bytes"],
        files=manifest["file_count"],
    )
    if not manifest["gguf_sha256"]:
        event(
            state,
            "measurement incomplete — stopping before any purchase",
            status=final.get("status"),
        )
        terminate(key, pod_id)
        state["phase"] = "error"
        save_state(state)
        return

    required = manifest["gguf_bytes"] + sum(
        s["bytes"] for s in manifest["scripts"].values()
    )
    fits = required <= SECONDARY_SIZE_GB * 1000**3
    event(
        state,
        f"replica size check: {required} bytes fits in {SECONDARY_SIZE_GB} GB = {fits}",
    )
    if not fits:
        event(state, "replica does not fit in 50 GB — stopping before purchase")
        terminate(key, pod_id)
        state["phase"] = "error"
        save_state(state)
        return

    ranking = secondary_ranking(key, min_vram=48, max_hourly=2.00)
    state["secondary_ranking"] = ranking[:5]
    winner = next((item for item in ranking if item["supports_standard_volume"]), None)
    event(
        state,
        f"secondary candidates: {[item['datacenter'] for item in ranking[:5]]}",
        winner=winner,
    )
    if winner is None:
        event(
            state,
            "no datacenter with STANDARD volumes has bookable >=48 GB capacity — releasing the migration pod",
        )
        terminate(key, pod_id)
        state["phase"] = "watching"
        save_state(state)
        return

    state.update({"phase": "volume", "secondary": winner})
    save_state(state)
    status, payload = call(
        "/v2/network-volumes",
        key,
        method="POST",
        payload={
            "name": f"canalla-secondary-{winner['datacenter'].lower()}",
            "dataCenter": winner["datacenter"],
            "size": SECONDARY_SIZE_GB,
            "type": "STANDARD",
        },
    )
    volume_id = (
        str((payload or {}).get("id") or "") if isinstance(payload, dict) else ""
    )
    event(
        state,
        f"secondary volume create → HTTP {status}",
        volume_id=volume_id,
        payload=str(payload)[:200],
    )
    if status not in {200, 201} or not volume_id:
        terminate(key, pod_id)
        state["phase"] = "error"
        save_state(state)
        return
    state["secondary_volume"] = {
        "id": volume_id,
        "datacenter": winner["datacenter"],
        "size_gb": SECONDARY_SIZE_GB,
        "type": "STANDARD",
        "monthly_usd": round(SECONDARY_SIZE_GB * 0.07, 2),
    }
    save_state(state)

    if not address.get("ip") or not address.get("public"):
        event(
            state, "source pod has no published address — cannot copy", address=address
        )
        terminate(key, pod_id)
        state["phase"] = "error"
        save_state(state)
        return

    dest_cpu = None
    try:
        dest_cpu = cheapest_cpu(key)
    except SystemExit:
        dest_cpu = None
    dest_body = {
        "name": f"canalla-copy-{int(time.time())}",
        "cloud": "SECURE",
        "image": "alpine:3.20",
        "disk": 20,
        "dataCenterIds": [winner["datacenter"]],
        "mounts": {"network": [{"volumeId": volume_id, "path": "/workspace"}]},
        "entrypoint": ["/bin/sh", "-c"],
        "cmd": [
            copy_script(
                address["ip"],
                int(address["public"]),
                session["password"],
                manifest["gguf_path"],
            )
        ],
    }
    if dest_cpu:
        dest_body["cpu"] = {
            "id": dest_cpu["flavor"],
            "vcpuCount": dest_cpu["vcpuCount"],
        }
    state["phase"] = "copying"
    save_state(state)
    status, payload = call("/v2/pods", key, method="POST", payload=dest_body)
    dest_pod = (
        (payload or {}).get("pod")
        if isinstance(payload, dict) and isinstance(payload.get("pod"), dict)
        else payload
    )
    dest_id = str((dest_pod or {}).get("id") or "")
    event(
        state,
        f"destination pod create → HTTP {status}",
        pod=dest_id,
        payload=str(payload)[:200],
    )
    if status not in {200, 201, 202} or not dest_id:
        terminate(key, pod_id)
        state["phase"] = "error"
        save_state(state)
        return
    state["dest_pod"] = dest_id
    save_state(state)

    wait_running(key, dest_id, 120)
    logs_text = read_logs(key, dest_id, 240, stop_markers=("CANALLA_C_DONE",))
    read = lambda marker: next(
        (
            line.split(marker, 1)[1].strip()
            for line in logs_text.splitlines()
            if marker in line
        ),
        "",
    )
    copy_result = {
        "gguf_bytes": int(read("CANALLA_C_GGUF_BYTES=") or 0),
        "gguf_sha256": read("CANALLA_C_GGUF_SHA256="),
        "file_count": int(read("CANALLA_C_FILE_COUNT=") or 0),
        "scripts": {},
    }
    for name in ("start-llm.sh", "check-llm.sh"):
        meta = read(f"CANALLA_C_FILE {name} ").split()
        copy_result["scripts"][name] = {
            "bytes": int(meta[0]) if meta else 0,
            "sha256": meta[1] if len(meta) > 1 else "",
        }
    verified = (
        copy_result["gguf_sha256"] == manifest["gguf_sha256"]
        and copy_result["gguf_bytes"] == manifest["gguf_bytes"]
        and all(
            copy_result["scripts"][name]["sha256"]
            == manifest["scripts"][name]["sha256"]
            for name in ("start-llm.sh", "check-llm.sh")
        )
    )
    state["copy"] = {**copy_result, "verified": verified}
    event(
        state,
        f"copy verified={verified}",
        bytes=copy_result["gguf_bytes"],
        sha256=copy_result["gguf_sha256"],
        files=copy_result["file_count"],
    )
    if verified:
        replica = {
            "datacenter": winner["datacenter"],
            "volume_id": volume_id,
            "model_id": "orcarouter/Qwen3.8-27B-Uncensored",
            "model_path": manifest["gguf_path"],
            "model_sha256": manifest["gguf_sha256"],
            "model_bytes": manifest["gguf_bytes"],
            "runtime_version": "; ".join(manifest["binaries"])[:200],
            "bootstrap_version": (Path(__file__).resolve().parents[1] / "VERSION")
            .read_text()
            .strip()
            if (Path(__file__).resolve().parents[1] / "VERSION").exists()
            else "1.2.0",
            "last_verified": now(),
            "ready": True,
        }
        (ARTIFACTS / "secondary-replica.json").write_text(
            json.dumps({"replicas": [replica]}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        (ARTIFACTS / "placements.env").write_text(
            f"RUNPOD_DATACENTERS={PRIMARY_DC}:{PRIMARY_VOLUME},{winner['datacenter']}:{volume_id}\n"
            f"RUNPOD_REPLICAS={json.dumps({'replicas': [replica]}, ensure_ascii=False)}\n",
            encoding="utf-8",
        )
        event(state, "secondary replica VERIFIED and recorded", replica=replica)

    # The copy pod is done once verification has run; the live test then uses a GPU pod on its own.
    terminate(key, dest_id)
    state["live_test"] = {}
    if verified:
        state["live_test"] = live_secondary_test(
            key, state, winner, volume_id, "orcarouter-qwen38-27b-q5km"
        )
        save_state(state)

    terminate(key, pod_id)
    time.sleep(10)
    state["phase"] = "done" if verified else "error"
    state["pods_after"] = running_pods(key)
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    event(
        state,
        f"migration finished in {state['elapsed_seconds']}s",
        pods_after=state["pods_after"],
    )
    save_state(state)


def watch(key: str, *, run_when_ready: bool) -> int:
    if not exclusive_pid():
        return 3
    state = load_state()
    state["pid"] = os.getpid()
    state.setdefault("started_at", now())
    save_state(state)
    event(
        state,
        f"watcher started (poll every {POLL_SECONDS}s, budget ${MIGRATION_MAX_HOURLY:.2f}/h)",
    )
    while True:
        try:
            state["poll_count"] = int(state.get("poll_count", 0)) + 1
            candidates = tx3_candidates(key, max_hourly=MIGRATION_MAX_HOURLY)
            cpu_allowed = (
                time.time() - float(state.get("last_cpu_probe", 0)) > CPU_PROBE_COOLDOWN
            )
            state["last_scan"] = {
                "at": now(),
                "candidates": len(candidates),
                "cheapest": candidates[0] if candidates else None,
            }
            log(
                f"poll {state['poll_count']}: US-TX-3 candidates={len(candidates)} "
                f"gpu_signal={bool(candidates)} cpu_probe_allowed={cpu_allowed}"
            )
            save_state(state)
            if run_when_ready:
                if candidates:
                    # A real catalogue signal is acted on in this very poll. No CPU cooldown, no
                    # waiting for the next cycle: the eligible card is the attempt.
                    state["last_signal"] = {
                        "at": now(),
                        "kind": "gpu_catalogue_signal",
                        "candidate": candidates[0],
                    }
                    save_state(state)
                    event(
                        state,
                        "US-TX-3 GPU signal — attempting the migration Pod now (CPU cooldown bypassed)",
                        card=candidates[0],
                    )
                    run_pipeline(key, state, allow_cpu=False)
                elif cpu_allowed:
                    # The blind probe: only its own cooldown applies, and nothing was seen yet.
                    state["last_cpu_probe"] = time.time()
                    state["last_signal"] = {"at": now(), "kind": "cpu_blind_probe"}
                    save_state(state)
                    run_pipeline(key, state)
            if state.get("phase") in {"done"}:
                event(state, "watcher exiting: migration complete")
                PIDFILE.unlink(missing_ok=True)
                return 0
        except (
            RuntimeError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
            ValueError,
        ) as error:  # a watcher must survive a transient provider failure
            event(state, f"scan error: {type(error).__name__}: {error}")
        time.sleep(POLL_SECONDS)


def print_status() -> int:
    state = load_state()
    print(json.dumps(state, ensure_ascii=False, indent=1, default=str)[:4000])
    if LOG.exists():
        print("--- last log lines")
        for line in LOG.read_text(encoding="utf-8").splitlines()[-12:]:
            print(line)
    return 0


def self_test() -> int:
    """Offline (zero provider calls, zero spend) proof of the guards around the paid step.

    The paid pipeline is only ever reached through these guards, so they are the part that has to be
    provable without a Pod: one live migration Pod is never doubled, an orphan past its budget is
    reaped, only in-budget US-TX-3 capacity is a candidate, and the secondary ranking needs STANDARD
    volumes and real availability.
    """
    module = sys.modules[__name__]
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    gcatalogue = [
        {
            "id": "NVIDIA GeForce RTX 4090",
            "secure": True,
            "memory": 24,
            "price": {"secure": 0.74},
            "dataCenters": [{"id": PRIMARY_DC, "availability": "LOW"}],
        },
        {
            "id": "NVIDIA L40S",
            "secure": True,
            "memory": 48,
            "price": {"secure": 1.60},
            "dataCenters": [{"id": PRIMARY_DC, "availability": "LOW"}],
        },
        {
            "id": "NVIDIA L40S",
            "secure": True,
            "memory": 48,
            "price": {"secure": 1.09},
            "dataCenters": [{"id": "US-NE-1", "availability": "LOW"}],
        },
        {
            "id": "NVIDIA A100 80GB PCIe",
            "secure": True,
            "memory": 80,
            "price": {"secure": 1.59},
            "dataCenters": [{"id": "US-NE-1", "availability": "MEDIUM"}],
        },
        {
            "id": "NVIDIA H100 80GB HBM3",
            "secure": True,
            "memory": 80,
            "price": {"secure": 3.19},
            "dataCenters": [{"id": "US-NE-1", "availability": "HIGH"}],
        },
        {
            "id": "NVIDIA RTX 6000 Ada",
            "secure": True,
            "memory": 48,
            "price": {"secure": 1.14},
            "dataCenters": [{"id": "EU-RO-1", "availability": "LOW"}],
        },
        {
            "id": "NVIDIA L40S",
            "secure": True,
            "memory": 48,
            "price": {"secure": 1.19},
            "dataCenters": [{"id": "US-KS-2", "availability": "NONE"}],
        },
        {
            "id": "NVIDIA L40S",
            "secure": False,
            "memory": 48,
            "price": {"secure": 0.40},
            "dataCenters": [{"id": PRIMARY_DC, "availability": "HIGH"}],
        },
    ]
    gvolumes = {
        "US-NE-1": {"networkVolumeTypes": ["STANDARD"]},
        "US-KS-2": {"networkVolumeTypes": ["STANDARD"]},
        "EU-RO-1": {"networkVolumeTypes": ["HIGH_PERFORMANCE"]},
    }
    pods: list[dict] = []
    posts: list[dict] = []
    terminated: list[str] = []
    cpu_calls: list[str] = []

    def fake_call(url: str, key: str, *, method: str = "GET", payload=None):
        if method == "POST" and str(url).startswith("/v2/pods"):
            posts.append(payload or {})
            return 500, {"detail": "the self-test must never create a Pod"}
        if str(url) == "/v2/pods":
            return 200, {"pods": pods}
        return 404, {"detail": f"unexpected {method} {url}"}

    def fake_cheapest_cpu(key: str) -> dict:
        cpu_calls.append(key)
        return {"flavor": "cpu5c", "vcpuCount": 1, "price_per_vcpu": 0.20}

    patch = (module.call, module.terminate, module.cheapest_cpu, module.gpu_rows, module.volume_support)
    settings = (module.STATE, module.LOG, module.PIDFILE, module.ARTIFACTS, module.PRIMARY_DC)
    real_pidfile = module.PIDFILE
    sandbox = Path(tempfile.mkdtemp(prefix="canalla-watcher-selftest-"))
    module.call = fake_call
    module.terminate = lambda key, pod_id: terminated.append(pod_id) or {}
    module.cheapest_cpu = fake_cheapest_cpu
    module.gpu_rows = lambda key: gcatalogue
    module.volume_support = lambda key: gvolumes
    module.STATE = sandbox / "state.json"
    module.LOG = sandbox / "watcher.log"
    module.PIDFILE = sandbox / "watcher.pid"
    module.ARTIFACTS = sandbox
    try:
        check(
            "age parses an ISO stamp",
            (_age_seconds("2026-09-25T12:00:00Z") or 0) > 0,
            "Z suffix must be accepted",
        )
        check("age rejects junk", _age_seconds("soon") is None)

        candidates = tx3_candidates("k", max_hourly=MIGRATION_MAX_HOURLY)
        check(
            "only in-budget bookable US-TX-3 capacity is a candidate",
            [item["gpu"] for item in candidates] == ["NVIDIA GeForce RTX 4090"],
            str(candidates),
        )

        ranking = secondary_ranking("k", min_vram=48, max_hourly=2.00)
        order = [item["datacenter"] for item in ranking]
        check(
            "secondary ranking: eligibility, primary exclusion, STANDARD last",
            order == ["US-NE-1", "EU-RO-1"],
            str(order),
        )
        check(
            "secondary ranking needs real availability",
            all(item["cards"] for item in ranking) and "US-KS-2" not in order,
            str(order),
        )
        check(
            "secondary ranking prefers more eligible GPU types",
            next(item for item in ranking if item["datacenter"] == "US-NE-1")["gpu_types"] == 2,
        )
        check(
            "price above the operator maximum is never a candidate",
            all(card["price_per_hour"] <= 2.00 for item in ranking for card in item["cards"]),
        )
        check(
            "a datacenter without STANDARD volumes is not chosen",
            not next(item for item in ranking if item["datacenter"] == "EU-RO-1")[
                "supports_standard_volume"
            ],
        )

        state = {"phase": "watching", "events": []}
        pods[:] = [
            {
                "id": "pod-live",
                "name": f"{MIGRATION_POD_PREFIX}1",
                "status": "RUNNING",
                "createdAt": now(),
            }
        ]
        session = create_migration_pod("k", state, candidates)
        check(
            "a live migration Pod is never doubled",
            session == {} and not posts and not terminated,
            f"posts={posts} terminated={terminated}",
        )

        pods[:] = [
            {
                "id": "pod-orphan",
                "name": f"{MIGRATION_POD_PREFIX}0",
                "status": "RUNNING",
                "createdAt": (
                    datetime.now(timezone.utc) - timedelta(seconds=MIGRATION_MAX_SECONDS + 120)
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        ]
        session = create_migration_pod("k", state, [], allow_cpu=False)
        check(
            "an orphan past its budget is reaped once, not re-created",
            terminated == ["pod-orphan"] and session == {} and not posts,
            f"terminated={terminated} posts={len(posts)}",
        )

        # The rule this pass adds: a real catalogue card is attempted in its own poll, and the blind
        # CPU-probe cooldown has nothing to do with it.
        posts.clear()
        cpu_calls.clear()
        pods[:] = []
        create_migration_pod("k", {"phase": "watching", "events": []}, candidates, allow_cpu=False)
        check(
            "a GPU signal attempts exactly the card, never the CPU",
            len(posts) == 1 and "gpu" in posts[0] and "cpu" not in posts[0] and not cpu_calls,
            f"posts={len(posts)} specs={[sorted(post) for post in posts]} cpu_calls={cpu_calls}",
        )
        check(
            "the attempt mounts the primary volume in its own datacenter",
            bool(posts)
            and posts[0].get("dataCenterIds") == [PRIMARY_DC]
            and posts[0].get("mounts")
            == {"network": [{"volumeId": PRIMARY_VOLUME, "path": "/workspace"}]},
            f"dc={posts[0].get('dataCenterIds') if posts else None}",
        )
        check(
            "a refused attempt never retries inside the same poll",
            len(posts) == 1,
            f"posts={len(posts)}",
        )

        posts.clear()
        cpu_calls.clear()
        create_migration_pod("k", {"phase": "watching", "events": []}, [], allow_cpu=True)
        check(
            "a blind probe still tries one CPU flavour",
            len(posts) == 1 and "cpu" in posts[0] and len(cpu_calls) == 1,
            f"posts={len(posts)} specs={[sorted(post) for post in posts]} cpu_calls={cpu_calls}",
        )

        if _watcher_pid_alive(real_pidfile):
            probe = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--lock-probe"],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            check(
                "a second watcher is refused while one is live",
                probe.returncode == 3,
                f"rc={probe.returncode} out={probe.stdout.strip()}",
            )
    finally:
        (module.call, module.terminate, module.cheapest_cpu, module.gpu_rows, module.volume_support) = patch
        (module.STATE, module.LOG, module.PIDFILE, module.ARTIFACTS, module.PRIMARY_DC) = settings
        shutil.rmtree(sandbox, ignore_errors=True)

    failures = [name for name, ok, _ in results if not ok]
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail and not ok else ''}")
    print(f"self-test: {len(results) - len(failures)}/{len(results)} passed")
    return 1 if failures else 0


def _watcher_pid_alive(path: Path | None = None) -> bool:
    pidfile = path or PIDFILE
    if not pidfile.exists():
        return False
    try:
        return _alive(int(pidfile.read_text(encoding="utf-8").strip() or 0))
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="one free scan and the verdict"
    )
    parser.add_argument(
        "--run", action="store_true", help="scan once and migrate if capacity is here"
    )
    parser.add_argument(
        "--status", action="store_true", help="print the persisted state and log tail"
    )
    parser.add_argument(
        "--foreground", action="store_true", help="watch in this process (no detach)"
    )
    parser.add_argument(
        "--self-test", action="store_true", help="offline proof of the watcher guards"
    )
    parser.add_argument("--lock-probe", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.lock_probe:
        held = not take_lock()
        print("lock held" if held else "lock free")
        return 3 if held else 0
    if args.self_test:
        return self_test()
    if args.status:
        return print_status()
    key = key_or_exit()
    if args.once or args.run:
        candidates = tx3_candidates(key, max_hourly=MIGRATION_MAX_HOURLY)
        print(
            json.dumps(
                {"us_tx3_candidates": candidates, "running_pods": running_pods(key)},
                ensure_ascii=False,
                indent=1,
            )
        )
        if args.run:
            state = load_state()
            state["pid"] = os.getpid()
            save_state(state)
            run_pipeline(key, state, allow_cpu=not candidates)
            print_status()
        return 0
    return watch(key, run_when_ready=True)


if __name__ == "__main__":
    raise SystemExit(main())
