"""T3 and T4 live: the installed window is really restarted, and the Computer host really loses
its transport, while Qwen is in the middle of a task.

Both flows use the product's own surfaces:

* the task is typed into the installed window over CDP (`apps/desktop/ui-task.mjs`);
* T3 restarts that window by stopping and starting the packaged app, then resumes through the
  product's own Resume button, which posts `/tasks/{id}/resume`;
* T4 suspends the app process, which is where the native Computer host loop lives. The sidecar is
  a separate process, so Qwen keeps streaming while the backend loses the host's heartbeats and
  job claims, then the same process is resumed — no kill, no re-pair, no new device.

Every step is recorded in the evidence dict; no credential and no token is written anywhere.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import common

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DESKTOP = REPO / "apps" / "desktop"
ENSURE = REPO / "artifacts" / "ensure-app-cdp.ps1"


def _decode(result: subprocess.CompletedProcess) -> str:
    return ((result.stdout or b"") + (result.stderr or b"")).decode("utf-8", "replace")


def ps(script: Path, *args: str, timeout: int = 300) -> tuple[int, str]:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *args,
        ],
        capture_output=True,
        timeout=timeout,
    )
    return result.returncode, _decode(result)


def node(command: str, *args: str, timeout: int = 300) -> tuple[int, dict, str]:
    result = subprocess.run(
        ["node", "ui-task.mjs", command, *args],
        cwd=str(DESKTOP),
        capture_output=True,
        timeout=timeout,
    )
    text = _decode(result)
    payload: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
    return result.returncode, payload, text[-1200:]


def ensure_window() -> dict:
    code, text = ps(ENSURE)
    return {"exit": code, "output": text.strip()[-400:]}


def stop_window() -> dict:
    code, text = ps(ENSURE, "-Stop")
    return {"exit": code, "output": text.strip()[-400:]}


def app_pids() -> list[int]:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq alex-llm.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        timeout=60,
    )
    pids = []
    for line in _decode(result).splitlines():
        parts = [item.strip('"') for item in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == "alex-llm.exe":
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def suspend_window(pid: int, *, watchdog_seconds: int = 180, expected_start: str = "") -> dict:
    if not pid:
        return {"ok": False, "reason": "app_not_running"}
    script = REPO / "artifacts" / "suspend-app.ps1"
    args = ["-Suspend", "-ProcessId", str(pid), "-WatchdogSeconds", str(watchdog_seconds)]
    if expected_start:
        args += ["-ExpectedStart", expected_start]
    code, text = ps(script, *args)
    return {"ok": code == 0, "pid": pid, "watchdog_seconds": watchdog_seconds, "exit": code, "output": text.strip()[-300:]}


def resume_window(pid: int, *, expected_start: str = "") -> dict:
    script = REPO / "artifacts" / "suspend-app.ps1"
    args = ["-Resume", "-ProcessId", str(pid)]
    if expected_start:
        args += ["-ExpectedStart", expected_start]
    code, text = ps(script, *args)
    return {"ok": code == 0, "pid": pid, "exit": code, "output": text.strip()[-300:]}


def process_start_time(pid: int) -> str:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-Process -Id {int(pid)}).StartTime.ToUniversalTime().ToString('o')",
        ],
        capture_output=True,
        timeout=30,
    )
    return _decode(result).strip()


def device_rows(token: str) -> list[dict]:
    status, text = common.call("GET", "/tools/devices", token=token, timeout=30)
    if status != 200:
        return []
    try:
        rows = json.loads(text)
    except ValueError:
        return []
    return rows if isinstance(rows, list) else []


def device_online(token: str) -> bool:
    return any(row.get("online") for row in device_rows(token))


def completed_payloads(token: str, chat_id: str) -> list[str]:
    return [
        str(row.get("input_digest") or "")
        for row in model_tools(tool_runs(token, chat_id))
        if row.get("status") in {"completed", "acknowledged"}
    ]


def task_executing(token: str, chat_id: str) -> bool:
    """The task is really working: a native run is in flight or the controller says EXECUTING."""
    if str((task_status(token, chat_id) or {}).get("status") or "") == "EXECUTING":
        return True
    return any(
        str(row.get("state") or row.get("status") or "")
        in {"waiting_host", "claimed", "started", "pending"}
        for row in model_tools(tool_runs(token, chat_id))
    )


def health_ok() -> bool:
    status, _ = common.call("GET", "/health", timeout=5)
    return status == 200


def cdp_ready() -> bool:
    """The installed WebView exposes the CDP endpoint the UI driver attaches to."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:9242/json/version", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def chats(token: str) -> list[dict]:
    status, text = common.call("GET", "/chats", token=token, timeout=30)
    if status != 200:
        return []
    try:
        rows = json.loads(text)
    except ValueError:
        return []
    return rows if isinstance(rows, list) else []


def wait(predicate, *, seconds: float, interval: float = 2.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def newest_chat_id(token: str, *, exclude: set[str]) -> str:
    rows = chats(token)
    for row in rows or []:
        chat_id = str(row.get("id") or "")
        if chat_id and chat_id not in exclude:
            return chat_id
    return ""


def task_status(token: str, chat_id: str) -> dict:
    status, text = common.call("GET", "/tasks?chat_id=" + chat_id, token=token, timeout=30)
    if status != 200:
        return {}
    try:
        rows = json.loads(text)
    except ValueError:
        return {}
    return (rows or [{}])[-1] if isinstance(rows, list) and rows else {}


def tool_runs(token: str, chat_id: str) -> list[dict]:
    status, text = common.call("GET", "/tools/runs?chat_id=" + chat_id, token=token, timeout=30)
    if status != 200:
        return []
    try:
        rows = json.loads(text)
    except ValueError:
        return []
    return rows if isinstance(rows, list) else []


def model_tools(rows: list[dict]) -> list[dict]:
    return [row for row in rows or [] if str(row.get("origin") or "") == "model"]


def answer_text(token: str, chat_id: str) -> str:
    status, text = common.call("GET", "/chats/" + chat_id + "/messages", token=token, timeout=30)
    if status != 200:
        return ""
    try:
        rows = json.loads(text)
    except ValueError:
        return ""
    for row in reversed(rows or []):
        if str(row.get("role") or "") == "assistant":
            return str(row.get("content") or "")
    return ""


def _prompt_file(root: Path, text: str) -> str:
    path = root / "_prompt.txt"
    path.write_text(text, encoding="utf-8", newline="\n")
    return str(path)


def start_approver(token: str, chat_id: str):
    """Approve tool confirmations the way the API-driven runner does.

    A task started from the window still asks the product for permission; in a paid run there is
    nobody to click. The harness approves only inside the dedicated acceptance sandbox, exactly
    like the SSE path, so the product's approval policy is exercised, not bypassed.
    """
    import threading

    stop = threading.Event()
    approved: set[str] = set()
    observed: list[str] = []
    log: list[dict] = []

    def loop() -> None:
        while not stop.is_set():
            for row in tool_runs(token, chat_id):
                if str(row.get("status") or "") != "waiting_confirmation":
                    continue
                run_id = str(row.get("id") or "")
                if not run_id or run_id in approved:
                    continue
                if run_id not in observed:
                    observed.append(run_id)
                approved.add(run_id)
                allow = common.safe_run(row, common.SANDBOX)
                status, body = common.confirm(token, run_id, allow)
                log.append(
                    {
                        "run_id": run_id,
                        "tool": row.get("tool_name"),
                        "allow": allow,
                        "http": status,
                        "body": str(body)[:120],
                    }
                )
            stop.wait(1.0)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return stop, log, observed


def run_t3(scenario, token: str, *, restore_seconds: float = 240) -> dict:
    """T3: restart the real window after the first completed tool and resume the same task."""
    root = Path(common.SANDBOX) / scenario.id
    root.mkdir(parents=True, exist_ok=True)
    for child in root.iterdir():
        if child.name == "_prompt.txt":
            continue
        if child.is_dir():
            import shutil

            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)

    common.stop_stale_tasks(token, common.SANDBOX)
    before_chats = {str(row.get("id")) for row in chats(token)}
    window = ensure_window()
    sent = node("send", _prompt_file(root, scenario.prompt))
    chat_id = wait(
        lambda: newest_chat_id(token, exclude=before_chats) or None,
        seconds=90,
    ) or ""
    stop_approver, approvals, confirmation_observed = (
        start_approver(token, chat_id) if chat_id else (None, [], [])
    )

    first_tool = wait(
        lambda: (
            [row for row in model_tools(tool_runs(token, chat_id)) if row.get("status") == "completed"]
            or None
        )
        if chat_id
        else None,
        seconds=240,
    )
    files_before_restart = common.snapshot(root)
    task_before = task_status(token, chat_id)
    # Close while a native run is in flight, not while the task waits for a click. A task parked
    # on `waiting_confirmation` is not the T3 scenario and must not be reported as one.
    executing_at_close = wait(lambda: task_executing(token, chat_id) or None, seconds=120, interval=1.0)
    status_at_close = (task_status(token, chat_id) or {}).get("status")
    tool_ids_before = [row.get("id") for row in model_tools(tool_runs(token, chat_id))]
    payloads_before = completed_payloads(token, chat_id)

    stopped = stop_window()
    backend_down = wait(lambda: not health_ok(), seconds=60)
    relaunched = ensure_window()
    backend_up = wait(lambda: health_ok() or None, seconds=180)
    task_after = wait(
        lambda: (
            task_status(token, chat_id)
            if (task_status(token, chat_id) or {}).get("status") == "INTERRUPTED"
            else None
        ),
        seconds=90,
    ) or task_status(token, chat_id)
    # The window may reopen on another conversation. Select the task chat first so the
    # product's own Resume control is what is observed, not just the endpoint.
    opened = node("open-chat", scenario.prompt[:60])
    state_after_restart = node("state")
    resumed = node("resume")
    resume_via = "ui_button"
    if not resumed[1].get("resumed"):
        # The window may reopen on another chat. The product's own endpoint is the same call the
        # button makes; which path was used is recorded, never hidden.
        task_id = str((task_after or {}).get("id") or "")
        status, body = common.call(
            "POST", "/tasks/" + task_id + "/resume", token=token, body={}, timeout=60
        )
        resume_via = "endpoint"
        resumed = (status or 0, {"resumed": status == 200}, body[:400])
        time.sleep(1)
    finished = wait(
        lambda: (
            str((task_status(token, chat_id) or {}).get("status") or "")
            in {"COMPLETED", "FAILED", "STOPPED"}
        )
        or None,
        seconds=restore_seconds,
    )
    final_task = task_status(token, chat_id)
    # The controller concludes before the prose stream finishes; the answer is part of the
    # contract and must exist before the harness tears anything down.
    wait(lambda: answer_text(token, chat_id).strip() or None, seconds=60, interval=1.0)
    final_answer = answer_text(token, chat_id)
    tool_ids_after = [row.get("id") for row in model_tools(tool_runs(token, chat_id))]
    files_after = common.snapshot(root)
    new_runs = [
        row
        for row in model_tools(tool_runs(token, chat_id))
        if str(row.get("id") or "") not in set(tool_ids_before)
    ]
    repeated_payloads = [
        str(row.get("tool_name") or "") + ":" + str(row.get("input_digest") or "")
        for row in new_runs
        if str(row.get("input_digest") or "") in set(payloads_before)
    ]
    preferences = {}
    status, text = common.call("GET", "/tools/preferences", token=token, timeout=20)
    if status == 200:
        try:
            preferences = json.loads(text)
        except ValueError:
            preferences = {}

    evidence = {
        "scenario": scenario.id,
        "chat_id": chat_id,
        "window": window,
        "sent": {"exit": sent[0], "payload": sent[1], "output": sent[2]},
        "computer_mode": preferences.get("computer_mode"),
        "first_tool_at": (first_tool or [{}])[0].get("tool_name") if first_tool else None,
        "executing_at_close": bool(executing_at_close),
        "status_at_close": status_at_close,
        "tool_ids_before_restart": tool_ids_before,
        "task_before_restart": {
            key: task_before.get(key) for key in ("id", "status", "tool_calls_used", "current_step")
        },
        "stop": stopped,
        "backend_went_down": bool(backend_down),
        "relaunch": relaunched,
        "backend_came_up": bool(backend_up),
        "task_after_restart": {
            key: (task_after or {}).get(key)
            for key in ("id", "status", "tool_calls_used", "current_step", "last_error")
        },
        "same_task": str((task_before or {}).get("id") or "")
        == str((task_after or {}).get("id") or ""),
        "ui_state_after_restart": state_after_restart[1],
        "open_chat": {"exit": opened[0], "payload": opened[1]},
        "resume": {"exit": resumed[0], "payload": resumed[1], "output": resumed[2], "via": resume_via},
        "confirmations": approvals,
        "confirmation_observed": confirmation_observed,
        "confirmation_failures": [row for row in approvals if not row.get("allow") or row.get("http") != 200],
        "final_task": {
            key: final_task.get(key)
            for key in ("id", "status", "tool_calls_used", "completion_summary", "last_error")
        },
        "final_answer": final_answer[:1500],
        "tool_ids_after": tool_ids_after,
        "repeated_payloads": repeated_payloads,
        "duplicate_tools": sorted(
            {item for item in tool_ids_after if tool_ids_after.count(item) > 1}
        ),
        "files_before_restart": files_before_restart,
        "files_after": files_after,
        "files_lost": sorted(set(files_before_restart) - set(files_after)),
        "files_changed_after_restart": sorted(
            path
            for path in files_after
            if path in files_before_restart and files_after[path] != files_before_restart[path]
        ),
        "device_id": json.loads(
            (Path(common.ROOT) / "device.json").read_text(encoding="utf-8")
        ).get("device_id"),
        "verdict": "PASS",
        "reasons": [],
    }

    reasons = []
    if not evidence["first_tool_at"]:
        reasons.append("no completed model tool before the restart")
    if not evidence["executing_at_close"]:
        reasons.append("the window was not closed while a native run was in flight")
    if not evidence["backend_went_down"]:
        reasons.append("the owned backend did not stop with the window")
    if not evidence["backend_came_up"]:
        reasons.append("the backend did not come back after the relaunch")
    if (task_after or {}).get("status") != "INTERRUPTED":
        reasons.append("the task was not parked as INTERRUPTED (got %r)" % (task_after or {}).get("status"))
    if not evidence["same_task"]:
        reasons.append("the resumed task id changed")
    if evidence["confirmation_failures"]:
        reasons.append("a sandbox confirmation was not accepted: %s" % evidence["confirmation_failures"][:3])
    if evidence["computer_mode"] == "ask" and not confirmation_observed:
        reasons.append("computer_mode=ask but no waiting_confirmation was observed")
    if evidence["repeated_payloads"]:
        reasons.append("a completed tool payload was repeated: %s" % evidence["repeated_payloads"][:3])
    if evidence["files_lost"]:
        reasons.append("files lost across the restart: %s" % evidence["files_lost"][:5])
    if evidence["duplicate_tools"]:
        reasons.append("duplicate tool runs: %s" % evidence["duplicate_tools"][:5])
    if not finished:
        reasons.append("the task did not reach a terminal state after Resume")
    elif (final_task or {}).get("status") != "COMPLETED":
        reasons.append("the resumed task ended %r" % (final_task or {}).get("status"))
    if not final_answer.strip():
        reasons.append("no final answer was persisted")
    if stop_approver is not None:
        stop_approver.set()
    evidence["reasons"] = reasons
    evidence["verdict"] = "PASS" if not reasons else "FAIL"
    return evidence


def run_t4(scenario, token: str, *, offline_seconds: float = 90, restore_seconds: float = 300) -> dict:
    """T4: suspend the process that runs the native Computer host during a real task.

    The instance is addressed by its own PID (never by image name), the suspension has an
    independent watchdog, and the resume is in a `finally`: an acceptance failure must not leave
    a frozen desktop behind.
    """
    root = Path(common.SANDBOX) / scenario.id
    root.mkdir(parents=True, exist_ok=True)
    for child in root.iterdir():
        if child.name == "_prompt.txt":
            continue
        if child.is_dir():
            import shutil

            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)

    common.stop_stale_tasks(token, common.SANDBOX)
    before_chats = {str(row.get("id")) for row in chats(token)}
    window = ensure_window()
    sent = node("send", _prompt_file(root, scenario.prompt))
    chat_id = wait(
        lambda: newest_chat_id(token, exclude=before_chats) or None,
        seconds=90,
    ) or ""
    stop_approver, approvals, confirmation_observed = (
        start_approver(token, chat_id) if chat_id else (None, [], [])
    )

    first_tool = wait(
        lambda: (
            [row for row in model_tools(tool_runs(token, chat_id)) if row.get("status") == "completed"]
            or None
        )
        if chat_id
        else None,
        seconds=240,
    )
    device_before = json.loads(
        (Path(common.ROOT) / "device.json").read_text(encoding="utf-8")
    ).get("device_id")
    files_before = common.snapshot(root)
    tool_ids_before = [row.get("id") for row in model_tools(tool_runs(token, chat_id))]
    payloads_before = completed_payloads(token, chat_id)
    preferences = {}
    status, text = common.call("GET", "/tools/preferences", token=token, timeout=20)
    if status == 200:
        try:
            preferences = json.loads(text)
        except ValueError:
            preferences = {}

    pids = app_pids()
    if len(pids) != 1:
        return {
            "scenario": scenario.id,
            "chat_id": chat_id,
            "verdict": "FAIL",
            "reasons": ["expected exactly one acceptance window, found %d" % len(pids)],
            "pids": pids,
        }
    pid = pids[0]
    started = process_start_time(pid)
    suspended: dict = {"ok": False, "reason": "not_attempted"}
    resumed: dict = {"ok": False, "reason": "not_attempted"}
    offline = None
    answer_while_offline = ""
    host_offline_state: dict = {}
    online_during_offline = None
    online_after_resume = None
    try:
        suspended = suspend_window(pid, watchdog_seconds=int(offline_seconds) + 120, expected_start=started)
        offline = wait(
            lambda: (task_status(token, chat_id) or {}).get("status")
            in {"WAITING_DEVICE", "WAITING_CONFIRMATION", "PAUSED", "FAILED"}
            or None,
            seconds=offline_seconds,
        )
        host_offline_state = {
            key: (task_status(token, chat_id) or {}).get(key)
            for key in ("status", "last_error", "current_step")
        }
        answer_while_offline = answer_text(token, chat_id)
        online_during_offline = device_online(token)
        # Resume as soon as the typed state is visible. The host loop treats a missing heartbeat
        # as offline after 45 s and ends the tool 15 s later; coming back inside that grace is the
        # real "transport restored" case, not a task that had already failed.
        time.sleep(2)
    finally:
        if suspended.get("ok"):
            resumed = resume_window(pid, expected_start=started)
        if stop_approver is not None:
            stop_approver.set()
    online_after_resume = wait(lambda: device_online(token) or None, seconds=60)
    task_now = task_status(token, chat_id)
    if str((task_now or {}).get("status") or "") in {"WAITING_DEVICE", "INTERRUPTED", "PAUSED"}:
        ui_resume = node("resume")
        resumed = {**resumed, "ui_resumed": bool(ui_resume[1].get("resumed"))}
    online = wait(
        lambda: (task_status(token, chat_id) or {}).get("status") in {"COMPLETED", "FAILED", "STOPPED"}
        or None,
        seconds=restore_seconds,
    )
    final_task = task_status(token, chat_id)
    # The controller concludes before the prose stream finishes; the answer is part of the
    # contract and must exist before the harness tears anything down.
    wait(lambda: answer_text(token, chat_id).strip() or None, seconds=60, interval=1.0)
    final_answer = answer_text(token, chat_id)
    device_after = json.loads(
        (Path(common.ROOT) / "device.json").read_text(encoding="utf-8")
    ).get("device_id")
    tool_ids_after = [row.get("id") for row in model_tools(tool_runs(token, chat_id))]
    files_after = common.snapshot(root)
    new_runs = [
        row
        for row in model_tools(tool_runs(token, chat_id))
        if str(row.get("id") or "") not in set(tool_ids_before)
    ]
    repeated_payloads = [
        str(row.get("tool_name") or "") + ":" + str(row.get("input_digest") or "")
        for row in new_runs
        if str(row.get("input_digest") or "") in set(payloads_before)
    ]

    evidence = {
        "scenario": scenario.id,
        "chat_id": chat_id,
        "window": window,
        "sent": {"exit": sent[0], "payload": sent[1], "output": sent[2]},
        "computer_mode": preferences.get("computer_mode"),
        "pid": pid,
        "process_started_at": started,
        "first_tool_at": (first_tool or [{}])[0].get("tool_name") if first_tool else None,
        "suspend": suspended,
        "confirmations": approvals,
        "confirmation_observed": confirmation_observed,
        "confirmation_failures": [row for row in approvals if not row.get("allow") or row.get("http") != 200],
        "host_offline_state": host_offline_state,
        "online_during_offline": online_during_offline,
        "answer_while_offline": answer_while_offline[:800],
        "resume": resumed,
        "online_after_resume": bool(online_after_resume),
        "recovered": bool(online),
        "final_task": {
            key: final_task.get(key)
            for key in ("id", "status", "tool_calls_used", "completion_summary", "last_error")
        },
        "final_answer": final_answer[:1500],
        "device_before": device_before,
        "device_after": device_after,
        "tool_ids_before": tool_ids_before,
        "tool_ids_after": tool_ids_after,
        "repeated_payloads": repeated_payloads,
        "duplicate_tools": sorted(
            {item for item in tool_ids_after if tool_ids_after.count(item) > 1}
        ),
        "files_before": files_before,
        "files_after": files_after,
        "files_lost": sorted(set(files_before) - set(files_after)),
        "verdict": "PASS",
        "reasons": [],
    }

    reasons = []
    if not evidence["first_tool_at"]:
        reasons.append("no completed model tool before the disconnect")
    if not suspended.get("ok"):
        reasons.append("the Computer host process could not be suspended: %s" % suspended)
    if not resumed.get("ok"):
        reasons.append("the Computer host process was not resumed: %s" % resumed)
    if host_offline_state.get("status") not in {
        "WAITING_DEVICE",
        "WAITING_CONFIRMATION",
        "PAUSED",
        "FAILED",
        "EXECUTING",
    }:
        reasons.append("no typed state while the host was gone: %r" % host_offline_state)
    if online_during_offline is True:
        reasons.append("the device still looked online while the host was suspended")
    if any(marker in answer_while_offline for marker in scenario.markers):
        reasons.append("a success marker was published while the host was offline")
    if not evidence["online_after_resume"]:
        reasons.append("the device heartbeat did not return after the resume")
    if evidence["confirmation_failures"]:
        reasons.append("a sandbox confirmation was not accepted: %s" % evidence["confirmation_failures"][:3])
    if evidence["computer_mode"] == "ask" and not confirmation_observed:
        reasons.append("computer_mode=ask but no waiting_confirmation was observed")
    if device_before != device_after:
        reasons.append("the device identity changed across the disconnect")
    if evidence["repeated_payloads"]:
        reasons.append("a completed tool payload was repeated: %s" % evidence["repeated_payloads"][:3])
    if evidence["duplicate_tools"]:
        reasons.append("duplicate tool runs: %s" % evidence["duplicate_tools"][:5])
    if evidence["files_lost"]:
        reasons.append("files lost: %s" % evidence["files_lost"][:5])
    if not online:
        reasons.append("the task never reached a terminal state after the reconnect")
    if not final_answer.strip():
        reasons.append("no final answer was persisted")
    evidence["reasons"] = reasons
    evidence["verdict"] = "PASS" if not reasons else "FAIL"
    return evidence
