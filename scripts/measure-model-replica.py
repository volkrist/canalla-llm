"""Measure the exact replica footprint of the model: what is on the primary volume, byte for byte.

This runs the cheapest CPU Pod that can mount the primary Network Volume — no GPU, no model load —
and asks the volume itself for the numbers a replica has to reproduce:

* the GGUF's exact path, byte count and SHA256;
* the startup/health scripts the runtime refuses to start without;
* every file under ``/workspace`` with its size, so "required replica bytes" is a measurement rather
  than an estimate.

The Pod is terminated immediately afterwards and the tool confirms nothing is running. Its container
command repeats its summary so the log stream cannot miss it, and it is the *same* harness that
proved the Global Volume is unattachable — so a success here is also the control that separates a
payload problem from a volume-identity problem.

    python scripts/measure-model-replica.py --datacenter US-TX-3 --volume-id uwgeaie5b0
    python scripts/measure-model-replica.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from runpod_api_tools import (
    cpu_probe,
    flavour,
    key_or_exit,
    running_pods,
)

MODEL_DIR = "/workspace/models/orcarouter-qwen38"
MARKER_DONE = "CANALLA_MEASURE_DONE"

# One command, then the same summary again so a stream that starts late still sees it.
MEASURE = f"""set -x
echo CANALLA_MEASURE_START
df -h /workspace
ls -la /workspace
ls -la {MODEL_DIR} 2>/dev/null || echo CANALLA_MEASURE_NO_MODEL_DIR
for f in /workspace/start-llm.sh /workspace/check-llm.sh; do
  echo "SCRIPT $f"; ls -l "$f"; sha256sum "$f" 2>/dev/null; wc -c < "$f" 2>/dev/null
done
for g in {MODEL_DIR}/*.gguf; do
  echo "GGUF_PATH=$g"
  echo "GGUF_BYTES=$(wc -c < "$g")"
  echo "GGUF_SHA256=$(sha256sum "$g" | cut -d' ' -f1)"
done
echo "DISK_USED_BYTES=$(du -sb /workspace | cut -f1)"
echo "FILE_COUNT=$(find /workspace -type f | wc -l)"
find /workspace -type f -printf '%s %p\\n' | sort -rn | head -40
echo "{MARKER_DONE}"
i=0; while [ $i -lt 30 ]; do sleep 5; i=$((i+1)); echo "{MARKER_DONE} alive=$i"; done"""


def fields(logs: str) -> dict:
    """The measured values, read straight out of the markers the container printed."""
    out: dict = {"scripts": {}, "gguf": {}}
    for line in logs.splitlines():
        if line.startswith("GGUF_PATH="):
            out["gguf"]["path"] = line.split("=", 1)[1].strip()
        elif line.startswith("GGUF_BYTES="):
            out["gguf"]["bytes"] = int(line.split("=", 1)[1].strip() or 0)
        elif line.startswith("GGUF_SHA256="):
            out["gguf"]["sha256"] = line.split("=", 1)[1].strip()
        elif line.startswith("DISK_USED_BYTES="):
            out["required_bytes"] = int(line.split("=", 1)[1].strip() or 0)
        elif line.startswith("FILE_COUNT="):
            out["file_count"] = int(line.split("=", 1)[1].strip() or 0)
        elif line.startswith("SCRIPT "):
            out["scripts"].setdefault(line.split(" ", 1)[1].strip(), {})
    return out


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datacenter", default="US-TX-3")
    parser.add_argument("--volume-id", default="uwgeaie5b0")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan, create nothing"
    )
    parser.add_argument(
        "--flavor",
        default="",
        help="CPU flavour to use when the cheapest has no capacity (one alternate, not a loop)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    key = key_or_exit()
    cpu = flavour(key, args.flavor or None)
    print(f"cheapest CPU flavour: {json.dumps(cpu, ensure_ascii=False)}")
    print(
        f"probe: one CPU Pod in {args.datacenter}, mount {args.volume_id} at /workspace, then terminate"
    )
    print(f"running pods before: {running_pods(key) or 'none'}")
    if args.dry_run:
        print("DRY RUN — nothing created.")
        return 0

    started = time.monotonic()
    result = cpu_probe(
        key,
        name=f"canalla-measure-{int(time.time())}",
        script=MEASURE,
        datacenter=args.datacenter,
        volume_id=args.volume_id,
        cpu=cpu,
        stop_markers=(MARKER_DONE,),
        poll_seconds=90,
        log_seconds=180,
    )
    elapsed = round(time.monotonic() - started, 1)
    measured = fields(result.get("logs") or "")
    result["measured"] = measured
    result["elapsed_seconds"] = elapsed

    if not result.get("accepted"):
        print(f"rejected: HTTP {result.get('http')} {result.get('provider_message')}")
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 4

    print(
        f"pod {result['pod'].get('id')} status {result['pod_final'].get('status')} in {elapsed}s"
    )
    print(f"terminate: {json.dumps(result['terminate'], ensure_ascii=False)}")
    print(f"running pods after: {result['pods_after'] or 'none'}")
    print("--- measured")
    print(json.dumps(measured, ensure_ascii=False, indent=1))
    print("--- log tail")
    for line in (result.get("logs") or "").splitlines()[-25:]:
        print(f"  | {line[:170]}")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
