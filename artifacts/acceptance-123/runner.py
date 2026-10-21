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
import threading
import time
from pathlib import Path

import catalog
import common

HERE = Path(__file__).resolve().parent
WATCHDOG = "/usr/local/sbin/canalla-gpu-test-watchdog.py"
WATCHDOG_PY = "/opt/alex-gateway/venv/bin/python"
UI_SCRIPT = HERE / "helpers" / "ui-nav-during-gen.ps1"
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


def as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def session_spend(token: str) -> dict:
    """What the Gateway says this session has actually cost, read-only."""
    status, text = common.call("GET", "/llm/status", token=token, timeout=20)
    try:
        value = json.loads(text) if status == 200 else {}
    except ValueError:
        value = {}
    session = (value.get("diagnostic") or {}).get("session") or {}
    return {
        "http": status,
        "state": session.get("state"),
        "gpu": session.get("gpu"),
        "hourly_rate_usd": as_float(session.get("hourly_rate_usd")),
        "estimated_usd": as_float(session.get("estimated_usd")),
        "billable_seconds": session.get("billable_seconds"),
    }


def budget_state(elapsed_s: float, rate: float | None, budget: float | None) -> dict:
    rate = rate if rate and rate > 0 else 2.0
    spent = elapsed_s / 3600.0 * rate
    return {
        "elapsed_seconds": round(elapsed_s, 1),
        "rate_usd_per_hour_used": rate,
        "estimated_usd": round(spent, 4),
        "budget_usd": budget,
        "remaining_usd": None if budget is None else round(budget - spent, 4),
        "rate_source": "gateway_session" if rate != 2.0 else "ceiling_fallback",
    }


def fits_budget(state: dict, planned_seconds: int, budget: float | None) -> bool:
    if budget is None:
        return True
    remaining = state.get("remaining_usd")
    if remaining is None:
        return True
    projected = planned_seconds / 3600.0 * state["rate_usd_per_hour_used"]
    return projected <= remaining


def new_chat(token: str, title: str) -> str:
    status, text = common.call("POST", "/chats", token=token, body={"title": title})
    if status not in (200, 201):
        raise RuntimeError("chat_create_failed " + text[:200])
    return json.loads(text)["id"]


def ui_during_generation() -> dict:
    """The installed-window navigation smoke, run while a real answer streams."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(UI_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=240,
    )
    text = (result.stdout or "") + (result.stderr or "")
    return {
        "ok": result.returncode == 0 and "installed-window-navigation: PASS" in text,
        "exit": result.returncode,
        "output": text[-1600:],
    }


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
    ui_state: dict = {"result": None, "started_at": None}

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

    ui_thread = None
    if scenario.ui_script == "generation-navigation":
        # Two seconds after the request starts the answer is streaming; the script then drives
        # New Chat, chat switching, typing, Settings and Back in the real window. The stream is
        # untouched by the navigation, which is exactly what this scenario claims.
        def drive_ui() -> None:
            time.sleep(2)
            ui_state["started_at"] = common.now()
            ui_state["result"] = ui_during_generation()

        ui_thread = threading.Thread(target=drive_ui, daemon=True)
        ui_thread.start()

    stream = common.stream_chat(
        chat_id,
        scenario.prompt,
        timeout=scenario.timeout,
        token_value=token,
        on_event=on_event,
    )
    if ui_thread is not None:
        ui_thread.join(240)
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
        "ui": ui_state["result"],
        "ui_started_at": ui_state["started_at"],
        "ui_stream_open_ms": stream.get("elapsed_ms"),
    }
    verdict, reasons = catalog.evaluate(scenario, evidence)
    evidence["verdict"] = verdict
    evidence["reasons"] = reasons
    return evidence


def selected_scenarios(args) -> list[catalog.Scenario]:
    chosen = catalog.package_scenarios(args.package) if args.package else list(catalog.SCENARIOS)
    if args.scenario:
        chosen = [item for item in chosen if item.id in args.scenario]
    if args.tier:
        chosen = [item for item in chosen if item.tier == args.tier]
    return chosen


def run_live(args) -> int:
    selected = selected_scenarios(args)
    if not selected:
        print("ABORT no scenarios selected")
        return 2
    # Free, pre-Pod gate: a prompt the server would execute itself can never prove Qwen did it.
    risks = [
        item
        for item in catalog.route_risks(selected)
        if item.get("urgent") or not item.get("ok")
    ]
    if risks:
        print("ABORT deterministic route risk; no Pod was created")
        print(json.dumps(risks, ensure_ascii=False)[:1500])
        return 3
    token = common.token()
    health_status, health_text = common.call("GET", "/health", token=token, timeout=10)
    if health_status != 200:
        print("ABORT app health", health_status, health_text[:200])
        return 2
    pods_before, _ = pods()
    print("pods_before", pods_before)
    if "pods=[]" not in json.dumps(pods_before):
        print("NOTE pods_before is not empty; the one-Pod rule is checked again after stop")
    code, text = watchdog("--arm 700")
    print("watchdog_arm", code, text[:300])
    if code != 0 or '"deadline_seconds": 700' not in text:
        print("ABORT watchdog not armed")
        return 3
    run_dir = HERE / "runs" / ("live-" + common.now().replace(":", "").replace("-", "")[:15])
    run_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    session_start: dict = {}
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
        started = time.monotonic()
        session_start = session_spend(token)
        rate = session_start.get("hourly_rate_usd")
        print("session", json.dumps(session_start, ensure_ascii=False))
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
            elapsed = time.monotonic() - started
            state = budget_state(elapsed, rate, args.budget_usd)
            planned = scenario.budget_seconds + 20
            if elapsed + planned > args.max_session_seconds:
                reason = (
                    f"session cap {args.max_session_seconds}s: "
                    f"elapsed {elapsed:.0f}s + {planned}s would exceed it"
                )
                print(" skip", scenario.id, reason)
                results.append(
                    {"scenario": scenario.id, "verdict": "BLOCKED_SESSION_LIMIT", "reasons": [reason]}
                )
                continue
            if not fits_budget(state, planned, args.budget_usd):
                reason = (
                    f"budget ${args.budget_usd}: planned ${planned / 3600.0 * state['rate_usd_per_hour_used']:.4f} "
                    f"exceeds remaining ${state.get('remaining_usd')}"
                )
                print(" skip", scenario.id, reason)
                results.append(
                    {"scenario": scenario.id, "verdict": "BLOCKED_BUDGET", "reasons": [reason]}
                )
                continue
            print("scenario", scenario.id, scenario.title, state)
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
                    "budget": state,
                }
            )
        return 0
    finally:
        elapsed_total = round(time.monotonic() - (started if "started" in locals() else time.monotonic()), 1)
        stopped = stop_ai(token)
        print("stop", stopped)
        time.sleep(20)
        after, _ = pods()
        spend = session_spend(token)
        summary = {
            "generated_at": common.now(),
            "selected": [item.id for item in selected],
            "results": results,
            "pods": after,
            "watchdog": watchdog("--status")[1],
            "stop": stopped,
            "session_start": session_start,
            "session_end": spend,
            "elapsed_seconds": elapsed_total,
            "budget_usd": args.budget_usd,
            "max_session_seconds": args.max_session_seconds,
        }
        (run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("summary", run_dir / "summary.json")
        print("spend", json.dumps(spend, ensure_ascii=False))


def sessions() -> int:
    for name, ids in catalog.PACKAGES.items():
        selected = [catalog.by_id(item) for item in ids]
        budget = sum(item.budget_seconds for item in selected) + 60
        print(
            f"{name:10s} {','.join(ids):28s} budget={budget:4d}s "
            f"usd={budget / 3600 * 1.09:.2f} scenarios={len(ids)}"
        )
    print(
        "Estimates include 60 s model startup and no long first turn: the 240 s first-turn figure "
        "was measured before the final-stream thinking fix and must be re-measured."
    )
    return 0


def self_test() -> int:
    """Free proof of the guards that decide whether a Pod may be started at all."""
    state = budget_state(1800, 2.0, 0.32)
    assert state["estimated_usd"] == 1.0, state
    assert state["remaining_usd"] == -0.68, state
    assert not fits_budget(state, 60, 0.32)
    roomy = budget_state(0, 1.09, 0.32)
    assert fits_budget(roomy, 600, 0.32), roomy
    assert not fits_budget(roomy, 1200, 0.32), roomy
    assert budget_state(0, None, None)["rate_usd_per_hour_used"] == 2.0
    package = [item.id for item in catalog.package_scenarios(["simple-a"])]
    assert package == ["S1", "S2", "S3"], package
    both = [item.id for item in catalog.package_scenarios(["simple-a", "simple-b"])]
    assert both == ["S1", "S2", "S3", "S4", "S5", "S6", "S7"], both
    risks = catalog.route_risks(catalog.package_scenarios(["simple-a", "simple-b"]))
    assert not risks, risks
    print("SELF-TEST PASS: budget guard, package selection and the model-origin route gate")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-paid", action="store_true")
    parser.add_argument("--tier", choices=("simple", "medium", "hard", "stress"))
    parser.add_argument("--package", nargs="+", default=[], choices=sorted(catalog.PACKAGES))
    parser.add_argument("--scenario", nargs="*", default=[])
    parser.add_argument("--with-ui", action="store_true")
    parser.add_argument("--max-ready-seconds", type=int, default=420)
    parser.add_argument(
        "--max-session-seconds",
        type=int,
        default=600,
        help="local session cap that stops before the 700 s watchdog (Pod lifetime)",
    )
    parser.add_argument(
        "--budget-usd",
        type=float,
        default=None,
        help="USD budget for this invocation; a scenario that would exceed it is not started",
    )
    parser.add_argument("--sessions", action="store_true")
    args = parser.parse_args()
    if args.sessions:
        return sessions()
    if args.self_test:
        return self_test()
    if args.preflight:
        command = [sys.executable, str(HERE / "preflight.py")]
        if args.with_ui:
            command.append("--with-ui")
        return subprocess.call(command)
    if not args.live:
        parser.error("choose --preflight, --sessions or --live")
    if not args.confirm_paid:
        print("REFUSED: --live needs --confirm-paid (one approved Pod, one attempt)")
        return 1
    return run_live(args)


if __name__ == "__main__":
    sys.exit(main())
