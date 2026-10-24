"""p50/p95 of the planner and final-answer phases for one local time window.

Reads the installed backend's own log files; no network, no secrets.
"""

import glob
import os
import re
import statistics

LOGS = os.path.join(os.environ["LOCALAPPDATA"], "Alex LLM", "logs")
SINCE = "2026-10-24 21:30:00"
PLAN = re.compile(r"gateway_plan_done id=(\w+) elapsed_ms=(\d+) first_token_ms=(\w+) tool_calls=(\d+)")
STREAM = re.compile(r"gateway_stream_done id=(\w+) elapsed_ms=(\d+) total_tokens=(\d+)")
STALL = re.compile(r"llm_stream_stalled what=(\S+) lines=(\d+) elapsed_ms=(\d+) silent_ms=(\d+)")


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


plans: list[dict] = []
streams: list[dict] = []
stalls: list[dict] = []
for path in sorted(glob.glob(os.path.join(LOGS, "backend.log*"))):
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line[:19] < SINCE:
                continue
            stamp = line[:23]
            match = PLAN.search(line)
            if match:
                plans.append(
                    {
                        "at": stamp,
                        "id": match.group(1),
                        "elapsed": int(match.group(2)),
                        "first": None if match.group(3) == "none" else int(match.group(3)),
                        "calls": int(match.group(4)),
                    }
                )
            match = STREAM.search(line)
            if match:
                streams.append(
                    {"at": stamp, "id": match.group(1), "elapsed": int(match.group(2)), "tokens": int(match.group(3))}
                )
            match = STALL.search(line)
            if match:
                stalls.append(
                    {
                        "at": stamp,
                        "what": match.group(1),
                        "lines": int(match.group(2)),
                        "elapsed": int(match.group(3)),
                        "silent": int(match.group(4)),
                    }
                )

print("planner turns:", len(plans), "streams:", len(streams), "stalls:", len(stalls))
for name, values in (
    ("plan elapsed ms", [item["elapsed"] for item in plans]),
    ("plan first token ms", [item["first"] for item in plans if item["first"]]),
    ("stream elapsed ms", [item["elapsed"] for item in streams]),
):
    if values:
        print(
            name,
            "n=",
            len(values),
            "min=",
            min(values),
            "p50=",
            percentile(values, 0.50),
            "p95=",
            percentile(values, 0.95),
            "max=",
            max(values),
            "mean=",
            round(statistics.mean(values)),
        )
print("tool calls per completed plan:", [item["calls"] for item in plans[-20:]])
for item in stalls:
    print("STALL", item)
