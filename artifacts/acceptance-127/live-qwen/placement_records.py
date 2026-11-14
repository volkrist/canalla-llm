"""The two paid placements and what each volume's own record actually proves.

Both regional volumes are equal candidates. They are not equally *proven*, and the difference must
be a fact rather than a habit:

* ``y92bt25ru8`` (US-MO-1) carries a replica record written after a real GPU answer - VERIFIED;
* ``uwgeaie5b0`` (US-TX-3) carries a measured manifest whose serving self-test is recorded as FAIL.
  The free review of that run shows ``curl`` printing its usage text immediately before the FAIL
  marker, so the *check command* was malformed: the record proves a measurement defect, not a
  broken model. Its assets (GGUF path, size, SHA-256, scripts, llama.cpp tree) are internally
  consistent. So it is UNVERIFIED_NEEDS_LIVE_CHECK - usable as a candidate only if the one paid Pod
  proves the model before doing any work.

Nothing here performs I/O beyond reading those two local records, and nothing calls a provider.

    from placement_records import PLACEMENTS, model_status, rank
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

GPU_ID = "NVIDIA L40S"
MODEL_ID = "orcarouter-qwen38-27b-q5km"
MODEL_BYTES = 20_752_787_712
MODEL_SHA256 = "4ad98832b3d376dd274df5bba59dd34834f0f401593e1463f38fd5fa219293b1"
MODEL_BASENAME = "orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf"

VERIFIED = "VERIFIED"
UNVERIFIED = "UNVERIFIED_NEEDS_LIVE_CHECK"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"

# Order matters only as a tie-break when both datacenters can book the same card at the same price:
# the proven copy first. Availability is never widened by this list.
PLACEMENTS = (
    {
        "volume_id": "uwgeaie5b0",
        "datacenter": "US-TX-3",
        "name": "orcarouter-storage",
        "record": REPO / "artifacts" / "runpod-tx3-watcher" / "primary-manifest.json",
        "raw_log": REPO / "artifacts" / "runpod-tx3-watcher" / "primary-raw-log.txt",
        "kind": "manifest",
    },
    {
        "volume_id": "y92bt25ru8",
        "datacenter": "US-MO-1",
        "name": "orcarouter-storage-mo1",
        "record": REPO / "docs" / "replica-us-mo-1.json",
        "raw_log": None,
        "kind": "replica",
    },
)

VOLUME_IDS = tuple(item["volume_id"] for item in PLACEMENTS)


def placement_for(volume_id: str) -> dict | None:
    return next((item for item in PLACEMENTS if item["volume_id"] == volume_id), None)


def _read(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _serving_check_was_malformed(path: Path | None) -> bool:
    """The recorded FAIL was produced by a malformed check, not by the model.

    The evidence is in the run's own raw log: the marker appears next to ``curl``'s option help,
    which is what curl prints when it is invoked with arguments it does not accept. A window is
    used rather than adjacency because the log is an interleaved stream.
    """
    if path is None or not path.is_file():
        return False
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    marker = next(
        (index for index, line in enumerate(lines) if "CANALLA_M_SERVE_SELFTEST_FAIL" in line),
        None,
    )
    if marker is None:
        return False
    window = "\n".join(lines[max(0, marker - 200) : marker])
    return "usage: curl" in window.lower() or "--spider" in window


def model_status(volume_id: str) -> tuple[str, str]:
    """Status plus the reason code, from the volume's own record. Never calls a provider."""
    spec = placement_for(volume_id)
    if spec is None:
        return UNKNOWN, "unknown_volume"
    record = _read(spec["record"])
    if not record:
        return UNKNOWN, "no_record"

    if spec["kind"] == "replica":
        replicas = record.get("replicas")
        replica = replicas[0] if isinstance(replicas, list) and replicas else None
        if not isinstance(replica, dict):
            return UNKNOWN, "no_replica_record"
        if replica.get("model_sha256", "").lower() != MODEL_SHA256:
            return FAILED, "replica_digest_mismatch"
        if replica.get("ready") is True and replica.get("last_verified"):
            return VERIFIED, "replica_ready_with_real_model_answer"
        return UNKNOWN, "replica_not_ready"

    # Manifest: assets first, then the recorded check itself.
    problems = []
    if str(record.get("gguf_sha256", "")).lower() != MODEL_SHA256:
        problems.append("gguf_digest_mismatch")
    if int(record.get("gguf_bytes") or 0) != MODEL_BYTES:
        problems.append("gguf_size_mismatch")
    if Path(str(record.get("gguf_path") or "")).name != MODEL_BASENAME:
        problems.append("gguf_name_mismatch")
    scripts = record.get("scripts") if isinstance(record.get("scripts"), dict) else {}
    if not {"start-llm.sh", "check-llm.sh"} <= set(scripts):
        problems.append("scripts_missing")
    if not record.get("binaries"):
        problems.append("runtime_binaries_missing")
    if "llama.cpp" not in str(record.get("runtime_listing") or ""):
        problems.append("runtime_tree_missing")
    if problems:
        return FAILED, ",".join(problems)

    value = str(record.get("serve_selftest") or "").strip().upper()
    if value in {"PASS", "OK", "READY", "TRUE"}:
        return VERIFIED, "recorded_selftest_passed"
    if value == "FAIL" and _serving_check_was_malformed(spec["raw_log"]):
        return UNVERIFIED, "recorded_selftest_was_malformed_assets_intact"
    if value == "FAIL":
        return FAILED, "recorded_selftest_failed"
    return UNKNOWN, "no_selftest_record"


def rank(status: str) -> int:
    """Lower is preferred when price and availability are equal."""
    return {VERIFIED: 0, UNVERIFIED: 1}.get(status, 9)


def is_usable(status: str) -> bool:
    """A copy may be attempted only if its record is at least honest about its state."""
    return status in {VERIFIED, UNVERIFIED}
