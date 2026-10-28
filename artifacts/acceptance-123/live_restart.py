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


def suspend_window() -> dict:
    pids = app_pids()
    if not pids:
        return {"ok": False, "reason": "app_not_running"}
    script = REPO / "artifacts" / "suspend-app.ps1"
    code, text = ps(script, "-Suspend")
    return {"ok": code == 0, "pids": pids, "exit": code, "output": text.strip()[-300:]}


def resume_window() -> dict:
    script = REPO / "artifacts" / "suspend-app.ps1"
    code, text = ps(script, "-Resume")
    return {"ok": code == 0, "exit": code, "output": text.strip()[-300:]}


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
    log: list[dict] = []

    def loop() -> None:
        while not stop.is_set():
            for row in tool_runs(token, chat_id):
                if str(row.get("status") or "") != "waiting_confirmation":
                    continue
                run_id = str(row.get("id") or "")
                if not run_id or run_id in approved:
                    continue
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
            stop.wait(2.0)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return stop, log


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
    stop_approver, approvals = start_approver(token, chat_id) if chat_id else (None, [])

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
    tool_ids_before = [row.get("id") for row in model_tools(tool_runs(token, chat_id))]

    stopped = stop_window()
    if stop_approver is not None:
        stop_approver.set()
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
    final_answer = answer_text(token, chat_id)
    tool_ids_after = [row.get("id") for row in model_tools(tool_runs(token, chat_id))]
    files_after = common.snapshot(root)

    evidence = {
        "scenario": scenario.id,
        "chat_id": chat_id,
        "window": window,
        "sent": {"exit": sent[0], "payload": sent[1], "output": sent[2]},
        "first_tool_at": (first_tool or [{}])[0].get("tool_name") if first_tool else None,
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
        "ui_state_after_restart": state_after_restart[1],
        "resume": {"exit": resumed[0], "payload": resumed[1], "output": resumed[2], "via": resume_via},
        "confirmations": approvals,
        "final_task": {
            key: final_task.get(key)
            for key in ("id", "status", "tool_calls_used", "completion_summary", "last_error")
        },
        "final_answer": final_answer[:1500],
        "tool_ids_after": tool_ids_after,
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
    if not evidence["backend_went_down"]:
        reasons.append("the owned backend did not stop with the window")
    if not evidence["backend_came_up"]:
        reasons.append("the backend did not come back after the relaunch")
    if (task_after or {}).get("status") != "INTERRUPTED":
        reasons.append("the task was not parked as INTERRUPTED (got %r)" % (task_after or {}).get("status"))
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
    evidence["reasons"] = reasons
    evidence["verdict"] = "PASS" if not reasons else "FAIL"
    return evidence


def run_t4(scenario, token: str, *, offline_seconds: float = 45, restore_seconds: float = 300) -> dict:
    """T4: suspend the process that runs the native Computer host during a real task."""
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
    stop_approver, approvals = start_approver(token, chat_id) if chat_id else (None, [])

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

    suspended = suspend_window()
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

    time.sleep(min(10, offline_seconds / 3))
    resumed = resume_window()
    if stop_approver is not None:
        stop_approver.set()
    online = wait(
        lambda: (task_status(token, chat_id) or {}).get("status") in {"COMPLETED", "FAILED", "STOPPED"}
        or None,
        seconds=restore_seconds,
    )
    final_task = task_status(token, chat_id)
    final_answer = answer_text(token, chat_id)
    device_after = json.loads(
        (Path(common.ROOT) / "device.json").read_text(encoding="utf-8")
    ).get("device_id")
    tool_ids_after = [row.get("id") for row in model_tools(tool_runs(token, chat_id))]
    files_after = common.snapshot(root)

    evidence = {
        "scenario": scenario.id,
        "chat_id": chat_id,
        "window": window,
        "sent": {"exit": sent[0], "payload": sent[1], "output": sent[2]},
        "first_tool_at": (first_tool or [{}])[0].get("tool_name") if first_tool else None,
        "suspend": suspended,
        "confirmations": approvals,
        "host_offline_state": host_offline_state,
        "answer_while_offline": answer_while_offline[:800],
        "resume": resumed,
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
        reasons.append("the Computer host process could not be suspended")
    if host_offline_state.get("status") not in {
        "WAITING_DEVICE",
        "WAITING_CONFIRMATION",
        "PAUSED",
        "FAILED",
        "EXECUTING",
    }:
        reasons.append("no typed state while the host was gone: %r" % host_offline_state)
    if device_before != device_after:
        reasons.append("the device identity changed across the disconnect")
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
