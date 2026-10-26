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
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# The acceptance console is cp1251 on this machine; a scenario title containing `→` crashed the
# runner before the scenario ran (and burned the last paid Pod). Never let a display character
# decide whether a test executes.
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except (AttributeError, OSError):  # pragma: no cover - non-reconfigurable stream
    pass

import catalog
import common

HERE = Path(__file__).resolve().parent
WATCHDOG = "/usr/local/sbin/canalla-gpu-test-watchdog.py"
WATCHDOG_PY = "/opt/alex-gateway/venv/bin/python"
UI_SCRIPT = HERE / "helpers" / "ui-nav-during-gen.ps1"
ALLOWED_UNSAFE = {"M7", "T6"}


def _text(result: subprocess.CompletedProcess) -> str:
    """Both streams as text, never raising on a byte that is not UTF-8.

    PowerShell on this host can emit a cp1251 byte (for example in a localised error message).
    `text=True` then raised UnicodeDecodeError inside the reader thread and the harness lost the
    very output that explained the failure.
    """
    raw = (result.stdout or b"") + (result.stderr or b"")
    return raw.decode("utf-8", "replace")


def ssh(command: str, timeout: int = 90) -> tuple[int, str]:
    result = subprocess.run(
        ["ssh", "distance", "sudo -n " + command],
        capture_output=True,
        timeout=timeout,
    )
    return result.returncode, _text(result)


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


def pods_empty(state: dict | None) -> bool:
    """The helper prints `pods []`; comparing the serialized dict to `pods=[]` never matched."""
    value = str((state or {}).get("pods") or "").strip()
    return value in {"pods []", "pods=[]", "[]", ""}


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


def remaining_timeout(scenario_timeout: float, remaining: float) -> int:
    """A scenario may never run past the Pod's remaining window (15 s stop margin)."""
    return int(min(scenario_timeout, remaining - 15))


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
        timeout=240,
    )
    text = _text(result)
    return {
        "ok": result.returncode == 0 and "installed-window-navigation: PASS" in text,
        "exit": result.returncode,
        "output": text[-1600:],
    }


def fault_control(path: str, timeout: float = 8.0) -> dict:
    """Arm/clear the local fault proxy. It never touches the Gateway or the product."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request("http://127.0.0.1:8766" + path, method="POST", data=b"")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"http": response.status, "body": response.read().decode("utf-8", "replace")[:400]}
    except (urllib.error.URLError, OSError) as error:
        return {"http": 0, "error": str(error)[:200]}


def run_stop_ai(scenario, token, root, chat_id, before, confirmations, on_event) -> dict:
    """T2: stop the paid AI in the middle of a real model stream.

    The generation runs in a thread. Once the first delta arrives and the answer is genuinely
    streaming, the product's own Stop AI is called. The scenario passes only when the Pod is
    gone, the watchdog confirms the stop, the AI stays off and a later read shows no reconnect.
    """
    first_delta = threading.Event()
    first_delta_at: dict[str, str | None] = {"at": None}

    def watch(event: str, data: dict) -> None:
        on_event(event, data)
        if event == "delta" and not first_delta.is_set():
            first_delta_at["at"] = common.now()
            first_delta.set()

    box: dict = {}
    stop_signal = threading.Event()

    def generation() -> None:
        box["stream"] = common.stream_chat(
            chat_id,
            scenario.prompt,
            timeout=300,
            token_value=token,
            on_event=watch,
            stop_event=stop_signal,
        )

    thread = threading.Thread(target=generation, daemon=True)
    thread.start()
    # The first turn can take far longer than a short poll (140 s was measured on 25 Oct).
    # Wait for a real delta; stopping during prefill is not the mid-stream case this tests.
    streaming = first_delta.wait(300)
    # Mid-answer, not before the first token: the product must stop an active generation.
    time.sleep(5)
    stop = stop_ai(token)
    # The product's Stop AI aborts the answer as well; that disconnect is what releases the
    # generation the Gateway is waiting for before it terminates the Pod.
    stop_signal.set()
    thread.join(30)

    pods_before, _ = pods()
    # Provider termination is not instantaneous: the Pod can stay `RUNNING` for a minute while
    # the in-flight generation drains. Poll until the provider and the independent watchdog both
    # confirm, instead of sampling once and calling a correct stop a failure.
    deadline = time.monotonic() + 120
    samples: list[dict] = []
    watchdog_state: dict = {}
    pods_after = pods_before
    confirmed = False
    while time.monotonic() < deadline:
        pods_after, _ = pods()
        code, text = watchdog("--status")
        try:
            watchdog_state = json.loads(text)
        except ValueError:
            watchdog_state = {}
        samples.append(
            {
                "at": common.now(),
                "pods": pods_after.get("pods"),
                "watchdog": watchdog_state.get("status"),
            }
        )
        if pods_empty(pods_after) and watchdog_state.get("status") in {
            "confirmed_stopped",
            "closed",
        }:
            confirmed = True
            break
        time.sleep(5)
    status, text = common.call("GET", "/llm/status", token=token, timeout=20)
    ai = json.loads(text) if status == 200 else {}
    status, text = common.call("GET", "/compute/status", token=token, timeout=20)
    compute = json.loads(text) if status == 200 else {}
    # A manual Stop must not be undone by the demand path a few seconds later.
    time.sleep(8)
    pods_later, _ = pods()
    status, text = common.call("GET", "/llm/status", token=token, timeout=20)
    ai_later = json.loads(text) if status == 200 else {}
    no_reconnect = pods_empty(pods_later) and str(
        ai_later.get("ai") or ""
    ).lower() in {"off", "stopped", ""}
    ok = bool(
        streaming
        and stop.get("http") == 200
        and confirmed
        and no_reconnect
    )
    ui = {
        "ok": ok,
        "streaming_started": bool(streaming),
        "first_delta_at": first_delta_at["at"],
        "stop": stop,
        "pods_before": pods_before,
        "pods_after": pods_after,
        "pods_later": pods_later,
        "stop_confirmed": confirmed,
        "samples": samples,
        "watchdog": watchdog_state,
        "ai": ai,
        "ai_later": ai_later,
        "compute": compute,
        "no_reconnect": no_reconnect,
        "output": "pods=%s watchdog=%s confirmed=%s no_reconnect=%s"
        % (
            (pods_after or {}).get("pods"),
            watchdog_state.get("status"),
            confirmed,
            no_reconnect,
        ),
    }
    stream = box.get("stream") or {}
    evidence = {
        "scenario": scenario.id,
        "tier": scenario.tier,
        "title": scenario.title,
        "chat_id": chat_id,
        "scenario_root": str(root),
        "stream": stream,
        "session": common.collect_session(token, chat_id),
        "confirmations": confirmations,
        "files_before": before,
        "files_after": common.snapshot(root),
        "files_changed": [],
        "chat_switched": False,
        "ui": ui,
        "ui_started_at": first_delta_at["at"],
        "ui_stream_open_ms": stream.get("elapsed_ms"),
        "late_answer": "",
        "fault": {"armed": False},
    }
    verdict, reasons = catalog.evaluate(scenario, evidence)
    evidence["verdict"] = verdict
    evidence["reasons"] = reasons
    return evidence


def run_scenario(scenario: catalog.Scenario, token: str, timeout: float | None = None) -> dict:
    root = catalog.scenario_root(scenario.id)
    root.mkdir(parents=True, exist_ok=True)
    # Every scenario starts from its own clean directory, seeded or not. A leftover from a failed
    # attempt must never be read as this attempt's work (H1 was failed by exactly that).
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    if scenario.setup:
        catalog.setup_fixture(scenario.setup, root)
    # A task parked by an earlier session owns the single computer workspace; without this the
    # new scenario would answer `WAITING_WORKSPACE` and never reach the model.
    stale = common.stop_stale_tasks(token, common.SANDBOX)
    before = common.snapshot(root)
    chat_id = new_chat(token, "acceptance " + scenario.id)
    confirmations: list[dict] = []
    switched = False
    seen: set[str] = set()
    fault: dict = {"armed": False}
    ui_state: dict = {"result": None, "started_at": None}

    def on_event(event: str, data: dict) -> None:
        nonlocal switched
        if event == "tool" and data.get("status") == "waiting_confirmation":
            run_id = str(data.get("id") or "")
            if not run_id or run_id in seen:
                return
            seen.add(run_id)
            # The dedicated acceptance sandbox as a whole is the trust boundary: a scenario may
            # work in its own folder or in the shared sandbox root, but never outside it.
            allow = common.safe_run(data, common.SANDBOX) and not scenario.deny_confirm
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
            scenario.id == "H6"
            and not fault["armed"]
            and event == "tool"
            and data.get("tool_name") == "write_file"
            and data.get("status") == "completed"
        ):
            # The write is on disk. Break backend -> Gateway for 6 s: the next planner call and
            # the immediate answer both fail typed, the task parks, then the queue monitor's
            # backoff retry reaches the Gateway again. The write must not be replayed.
            fault["armed"] = True
            fault["armed_at"] = common.now()

            def disturb() -> None:
                fault["block"] = fault_control("/block?seconds=6")
                time.sleep(6)
                fault["clear"] = fault_control("/clear")

            threading.Thread(target=disturb, daemon=True).start()
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

    if scenario.id == "T2":
        # The stop must happen inside a live stream, so it has its own flow instead of the
        # synchronous request used by the other scenarios.
        return run_stop_ai(scenario, token, root, chat_id, before, confirmations, on_event)

    stream = common.stream_chat(
        chat_id,
        scenario.prompt,
        timeout=timeout or scenario.timeout,
        token_value=token,
        on_event=on_event,
    )
    if ui_thread is not None:
        ui_thread.join(240)
    session = common.collect_session(token, chat_id)
    late_answer = ""
    if scenario.id == "H6" and fault.get("armed"):
        # The stream ends while the task is parked. The answer arrives after the network is back;
        # that persisted answer is the evidence for H6, not the interim halt text.
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            status, text = common.call(
                "GET", "/chats/" + chat_id + "/messages", token=token, timeout=20
            )
            if status == 200:
                try:
                    rows = json.loads(text)
                except ValueError:
                    rows = []
                for message in reversed(rows):
                    content = str(message.get("content") or "")
                    if message.get("role") == "assistant" and any(
                        marker in content for marker in scenario.markers
                    ):
                        late_answer = content
                        break
            if late_answer:
                break
            time.sleep(2)
        fault["late_answer_seconds"] = int(time.monotonic() - (deadline - 90))
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
        "stale_tasks_stopped": stale,
        "files_before": before,
        "files_after": after,
        "files_changed": changed,
        "chat_switched": switched,
        "ui": ui_state["result"],
        "ui_started_at": ui_state["started_at"],
        "ui_stream_open_ms": stream.get("elapsed_ms"),
        "late_answer": late_answer,
        "fault": fault,
    }
    verdict, reasons = catalog.evaluate(scenario, evidence)
    evidence["verdict"] = verdict
    evidence["reasons"] = reasons
    return evidence


def selected_scenarios(args) -> list[catalog.Scenario]:
    chosen = catalog.package_scenarios(args.package) if args.package else list(catalog.SCENARIOS)
    if args.scenario:
        # An explicit list is an execution order, not just a filter. H6 is short and H5 is long:
        # catalog order would run the long one first and could leave the short one unrun.
        known = {item.id: item for item in chosen}
        chosen = [known[item] for item in args.scenario if item in known]
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
    if not pods_empty(pods_before):
        print("NOTE pods_before is not empty; the one-Pod rule is checked again after stop")
    if args.cooldown_seconds > 0:
        # A regional Network Volume has to detach from the Pod that just stopped before the next
        # Pod can mount it. Creating immediately is the plausible cause of a 300 s
        # `startup_timeout` seen after a back-to-back stop on the same volume.
        print("cooldown", args.cooldown_seconds, "s before the next Pod")
        time.sleep(args.cooldown_seconds)
    code, text = watchdog("--arm " + str(args.watchdog_seconds))
    print("watchdog_arm", code, text[:300])
    if code != 0 or f'"deadline_seconds": {args.watchdog_seconds}' not in text:
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
        session_start = session_spend(token)
        rate = session_start.get("hourly_rate_usd")
        # The Pod's clock started when it was created, not when the model became ready. Counting
        # the startup seconds is what keeps `max_session_seconds` a real cap on Pod lifetime.
        startup_seconds = float(session_start.get("billable_seconds") or 0)
        started = time.monotonic() - startup_seconds
        print("session", json.dumps(session_start, ensure_ascii=False), "startup_s", startup_seconds)
        for scenario in selected:
            # T2 drives the product's own Stop AI through the API and records the result as
            # its `ui` evidence; it does not need the installed-window script.
            if scenario.ui_script and not args.with_ui and scenario.id != "T2":
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
            remaining = args.max_session_seconds - elapsed
            effective_timeout = remaining_timeout(scenario.timeout, remaining)
            # The money budget is a second bound, measured at the rate the Gateway actually
            # charged. Without it a cheaper cap could still overspend if the Pod ran the full
            # session window at a higher rate than planned.
            if args.budget_usd is not None and state.get("remaining_usd") is not None:
                affordable = (
                    int(max(0.0, state["remaining_usd"]) / state["rate_usd_per_hour_used"] * 3600)
                    - 15
                )
                effective_timeout = min(effective_timeout, affordable)
            if effective_timeout < 60:
                reason = (
                    f"effective window {effective_timeout}s is less than the 60 s minimum "
                    f"(session cap {args.max_session_seconds}s leaves {remaining:.0f}s; "
                    f"budget ${args.budget_usd} at ${state['rate_usd_per_hour_used']}/h)"
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
            if scenario.id == "H6":
                probe = fault_control("/status")
                if probe.get("http") != 200:
                    reason = (
                        "fault proxy is not running; H6 would not test a real interruption: "
                        + str(probe)[:200]
                    )
                    print(" skip", scenario.id, reason)
                    results.append(
                        {"scenario": scenario.id, "verdict": "BLOCKED", "reasons": [reason]}
                    )
                    continue
            print("scenario", scenario.id, scenario.title, state, "timeout_s", effective_timeout)
            evidence = run_scenario(scenario, token, timeout=effective_timeout)
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
    # A scenario can never outlive the Pod: it gets the smaller of its own timeout and what is
    # left, minus the stop margin.
    assert remaining_timeout(480, 300) == 285
    assert remaining_timeout(480, 1000) == 480
    assert remaining_timeout(300, 1000) == 300
    assert remaining_timeout(480, 60) == 45
    package = [item.id for item in catalog.package_scenarios(["simple-a"])]
    assert package == ["S1", "S2", "S3"], package
    both = [item.id for item in catalog.package_scenarios(["simple-a", "simple-b"])]
    assert both == ["S1", "S2", "S3", "S4", "S5", "S6", "S7"], both
    risks = catalog.route_risks(catalog.package_scenarios(["simple-a", "simple-b"]))
    assert not risks, risks

    # The harness policy that blocked M3: a package directory and a patch inside the sandbox are
    # approvable; a command outside it, or a non-Python executable, is not.
    sandbox = common.SANDBOX / "SELFTEST"
    assert common.safe_run(
        {"tool_name": "create_directory", "input_summary": {"path": str(sandbox / "pkg")}},
        sandbox,
    )
    assert common.safe_run(
        {"tool_name": "patch_file", "input_summary": {"path": str(sandbox / "pkg" / "a.py")}},
        sandbox,
    )
    assert not common.safe_run(
        {"tool_name": "create_directory", "input_summary": {"path": "C:\\Windows\\Temp\\x"}},
        sandbox,
    )
    assert common.safe_run(
        {"tool_name": "run_process", "input_summary": {"executable": "python", "cwd": str(sandbox)}},
        sandbox,
    )
    assert not common.safe_run(
        {"tool_name": "run_process", "input_summary": {"executable": "cmd", "cwd": str(sandbox)}},
        sandbox,
    )
    assert not common.safe_run(
        {"tool_name": "run_process", "input_summary": {"executable": "python", "cwd": "C:\\Windows"}},
        sandbox,
    )
    print("SELF-TEST PASS: budget guard, package selection and the model-origin route gate")
    print("SELF-TEST PASS: the harness approves package dirs and patches inside the sandbox only")
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
    parser.add_argument(
        "--cooldown-seconds",
        type=int,
        default=0,
        help="wait this long before creating a Pod (volume detach after a previous session)",
    )
    parser.add_argument(
        "--watchdog-seconds",
        type=int,
        default=700,
        help="independent watchdog deadline from Pod creation (hard cap for this run)",
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
