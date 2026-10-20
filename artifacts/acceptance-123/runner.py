"""Live runner for the Canalla 1.2.3 acceptance catalog.

`--preflight` is free and delegates to preflight.py. `--live` requires `--confirm-paid`, arms
the independent watchdog, performs exactly one product-owned ensure, runs the selected
scenarios, saves per-scenario evidence, and always stops AI. It never creates a second Pod and
never retries a capacity failure in a loop.

A scenario PASS requires a `model`-origin tool run for every expected tool. A `server_policy`
route is `BLOCKED_DETERMINISTIC`, not a Qwen PASS.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import catalog
import common

HERE = Path(__file__).resolve().parent
WATCHDOG = "/usr/local/sbin/canalla-gpu-test-watchdog.py"
WATCHDOG_PY = "/opt/alex-gateway/venv/bin/python"
ALLOWED_UNSAFE = {"M7", "T6"}


def ssh(command: str, timeout: int = 90) -> tuple[int, str]:
    result = subprocess.run(
        ["ssh", "distance", "sudo -n " + command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def watchdog(argument: str) -> tuple[int, str]:
    return ssh(WATCHDOG_PY + " " + WATCHDOG + " " + argument, timeout=90)


def pods() -> tuple[dict | None, str]:
    script = (HERE / "helpers" / "runpod-pods.sh").read_text(encoding="utf-8")
    payload = (script.replace("\r\n", "\n").strip() + "\n").encode("utf-8")
    result = subprocess.run(
        ["ssh", "distance", "sudo -n bash -s"],
        input=payload,
        capture_output=True,
        timeout=90,
    )
    text = (result.stdout or b"").decode("utf-8", "replace") + (
        result.stderr or b""
    ).decode("utf-8", "replace")
    pods_line = next((line for line in text.splitlines() if line.startswith("pods ")), "")
    balance = next(
        (line.split(" ", 1)[1] for line in text.splitlines() if line.startswith("balance_usd ")),
        None,
    )
    return ({"exit": result.returncode, "pods": pods_line, "balance_usd": balance, "raw": text[-400:]}, "")


def ensure(token: str) -> tuple[int | None, str]:
    return common.call("POST", "/cloud/compute/ensure", token=token, body={}, timeout=90)


def wait_ready(token: str, timeout: int = 420) -> tuple[bool, dict]:
    started = time.monotonic()
    last: dict = {}
    while time.monotonic() - started < timeout:
        status, text = common.call("GET", "/llm/status", token=token, timeout=20)
        try:
            value = json.loads(text) if status == 200 else {}
        except ValueError:
            value = {}
        last = value
        diagnostic = value.get("diagnostic") or {}
        if value.get("ai") == "ready" or diagnostic.get("compute_state") in {"ready", "generating"}:
            return True, value
        if diagnostic.get("compute_state") == "waiting_capacity":
            # The product opened a durable capacity wait. A live acceptance session must not
            # spend its window polling for hardware: report the typed blocker and stop.
            return False, value
        if diagnostic.get("compute_state") in {"offline", "error"} and value.get("ai") in {
            "off",
            "error",
        }:
            return False, value
        time.sleep(3)
    return False, last


def stop_ai(token: str) -> dict:
    status, text = common.call("POST", "/cloud/compute/stop", token=token, body={}, timeout=90)
    return {"http": status, "body": text[:400]}


def new_chat(token: str, title: str) -> str:
    status, text = common.call("POST", "/chats", token=token, body={"title": title})
    if status not in (200, 201):
        raise RuntimeError("chat_create_failed " + text[:200])
    return json.loads(text)["id"]


def run_scenario(scenario: catalog.Scenario, token: str) -> dict:
    root = catalog.scenario_root(scenario.id)
    root.mkdir(parents=True, exist_ok=True)
    if scenario.setup:
        catalog.setup_fixture(scenario.setup, root)
    before = common.snapshot(root)
    chat_id = new_chat(token, "acceptance " + scenario.id)
    confirmations: list[dict] = []
    switched = False
    seen: set[str] = set()

    def on_event(event: str, data: dict) -> None:
        nonlocal switched
        if event == "tool" and data.get("status") == "waiting_confirmation":
            run_id = str(data.get("id") or "")
            if not run_id or run_id in seen:
                return
            seen.add(run_id)
            allow = common.safe_run(data, root) and not scenario.deny_confirm
            status, text = common.confirm(token, run_id, allow)
            confirmations.append(
                {
                    "run_id": run_id,
                    "tool": data.get("tool_name"),
                    "origin": data.get("origin"),
                    "allow": allow,
                    "http": status,
                    "body": text[:200],
                }
            )
        if (
            scenario.id == "M6"
            and not switched
            and event == "tool"
            and data.get("tool_name") == "write_file"
            and data.get("status") == "completed"
        ):
            # The original stream stays open; a second chat is created and read, then left
            # alone. This proves the backend does not bind the running turn to the visible chat.
            other = new_chat(token, "acceptance M6 side chat")
            common.call("GET", "/chats/" + other + "/messages", token=token)
            common.call("DELETE", "/chats/" + other, token=token)
            switched = True

    stream = common.stream_chat(
        chat_id,
        scenario.prompt,
        timeout=scenario.timeout,
        token_value=token,
        on_event=on_event,
    )
    session = common.collect_session(token, chat_id)
    after = common.snapshot(root)
    changed = sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )
    evidence = {
        "scenario": scenario.id,
        "tier": scenario.tier,
        "title": scenario.title,
        "chat_id": chat_id,
        "scenario_root": str(root),
        "stream": stream,
        "session": session,
        "confirmations": confirmations,
        "files_before": before,
        "files_after": after,
        "files_changed": changed,
        "chat_switched": switched,
        "ui": None,
    }
    verdict, reasons = catalog.evaluate(scenario, evidence)
    evidence["verdict"] = verdict
    evidence["reasons"] = reasons
    return evidence


def run_live(args) -> int:
    token = common.token()
    health_status, health_text = common.call("GET", "/health", token=token, timeout=10)
    if health_status != 200:
        print("ABORT app health", health_status, health_text[:200])
        return 2
    pods_before, _ = pods()
    print("pods_before", pods_before)
    code, text = watchdog("--arm 700")
    print("watchdog_arm", code, text[:300])
    if code != 0 or '"deadline_seconds": 700' not in text:
        print("ABORT watchdog not armed")
        return 3
    try:
        status, text = ensure(token)
        print("ensure", status, text[:400])
        if status not in (200, 202):
            return 4
        ready, status_payload = wait_ready(token, args.max_ready_seconds)
        print("ready", ready, json.dumps(status_payload, ensure_ascii=False)[:500])
        if not ready:
            print("ABORT not ready; capacity/search is not retried")
            return 5
        selected = [
            scenario
            for scenario in catalog.SCENARIOS
            if (not args.tier or scenario.tier == args.tier)
            and (not args.scenario or scenario.id in args.scenario)
        ]
        run_dir = HERE / "runs" / ("live-" + common.now().replace(":", "").replace("-", "")[:15])
        run_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for scenario in selected:
            if scenario.ui_script and not args.with_ui:
                results.append(
                    {
                        "scenario": scenario.id,
                        "verdict": "BLOCKED",
                        "reasons": ["UI script not requested"],
                    }
                )
                continue
            print("scenario", scenario.id, scenario.title)
            evidence = run_scenario(scenario, token)
            (run_dir / (scenario.id + ".json")).write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(" ", evidence["verdict"], evidence["reasons"][:2])
            results.append(
                {
                    "scenario": scenario.id,
                    "verdict": evidence["verdict"],
                    "reasons": evidence["reasons"],
                    "elapsed_ms": evidence["stream"].get("elapsed_ms"),
                }
            )
        return 0
    finally:
        stopped = stop_ai(token)
        print("stop", stopped)
        time.sleep(20)
        after, _ = pods()
        summary = {
            "generated_at": common.now(),
            "results": locals().get("results", []),
            "pods": after,
            "watchdog": watchdog("--status")[1],
            "stop": stopped,
        }
        run_dir = locals().get("run_dir")
        if run_dir:
            (run_dir / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print("summary", run_dir / "summary.json")


def sessions() -> int:
    groups = {
        "simple-a": ["S1", "S2", "S3"],
        "simple-b": ["S4", "S5", "S6", "S7"],
        "medium-a": ["M1", "M2", "M3"],
        "medium-b": ["M4", "M5", "M6", "M7"],
        "hard-a": ["H1", "H2"],
        "hard-b": ["H3", "H4"],
        "hard-c": ["H5", "H6"],
        "stress": ["T1", "T2", "T5", "T6", "T7"],
    }
    for name, ids in groups.items():
        selected = [catalog.by_id(item) for item in ids]
        budget = sum(item.budget_seconds for item in selected) + 60 + 240
        print(
            f"{name:10s} {','.join(ids):22s} budget={budget:4d}s "
            f"usd={budget / 3600 * 1.09:.2f} scenarios={len(ids)}"
        )
    print(
        "Estimates include 60 s model startup and 240 s for the first planner turn. "
        "A 700 s watchdog window fits one group only when its measured first turn is below "
        "~240 s; measure before combining groups."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-paid", action="store_true")
    parser.add_argument("--tier", choices=("simple", "medium", "hard", "stress"))
    parser.add_argument("--scenario", nargs="*", default=[])
    parser.add_argument("--with-ui", action="store_true")
    parser.add_argument("--max-ready-seconds", type=int, default=420)
    parser.add_argument("--sessions", action="store_true")
    args = parser.parse_args()
    if args.sessions:
        return sessions()
    if args.preflight:
        return subprocess.call([sys.executable, str(HERE / "preflight.py")])
    if not args.live:
        parser.error("choose --preflight, --sessions or --live")
    if not args.confirm_paid:
        print("REFUSED: --live needs --confirm-paid (one approved Pod, one attempt)")
        return 1
    return run_live(args)


if __name__ == "__main__":
    sys.exit(main())
