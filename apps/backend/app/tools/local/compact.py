"""Bounded tool-result compaction. Failure lines are preserved."""

import re

from ..security import sanitized

FAIL = re.compile(
    r"(?im)^(FAILED|ERROR|E\s+|assert |AssertionError|Error:|Exception:|conflict|"
    r"expected_before_sha256|exit_code=[1-9]).*"
)
PYTEST_NODE = re.compile(r"(?m)^(FAILED|ERROR) .+")


def compact_text(text: str, limit: int = 4000) -> dict:
    value = text or ""
    truncated = len(value) > limit
    failures = [line[:400] for line in value.splitlines() if FAIL.match(line)][:20]
    if not failures:
        failures = [line[:400] for line in PYTEST_NODE.findall(value)][:20]
    if truncated:
        head = value[: max(400, limit // 4)]
        tail = value[-max(800, limit // 2) :]
        shown = head + "\n...\n" + tail
        if failures:
            shown = "Failures:\n" + "\n".join(failures) + "\n...\n" + tail
        shown = shown[:limit]
    else:
        shown = value
    return {
        "text": shown,
        "truncated": truncated,
        "failures": failures,
        "chars": len(value),
    }


def compact_tool_output(name: str, output: dict, secrets=(), limit: int = 4000) -> dict:
    payload = dict(output or {})
    raw = str(payload.get("text") or "")
    packed = compact_text(sanitized(raw, secrets, max(limit * 4, 20000)), limit)
    payload["text"] = packed["text"]
    payload["truncated"] = packed["truncated"] or bool(payload.get("truncated"))
    if packed["failures"]:
        payload["failures"] = packed["failures"]
    meta = dict(payload.get("metadata") or {})
    keep = {
        key: meta[key]
        for key in (
            "exit_code",
            "before_sha256",
            "after_sha256",
            "sha256",
            "digest",
            "path",
            "pid",
            "os_version",
            "cpu_logical_processors",
            "ram_total_mb",
            "ram_avail_mb",
            "system_disk_free_gb",
            "files_changed",
            "conflict",
            "error",
            "cwd",
            "branch",
            "dirty",
            "tool_run_id",
            "desktop",
            "documents",
            "downloads",
        )
        if key in meta
    }
    if keep:
        payload["metadata"] = keep
    if payload.get("error"):
        payload["error"] = sanitized(str(payload["error"]), secrets, 120)
    payload["tool"] = name
    return payload
