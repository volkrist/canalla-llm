"""Free T3/T4 mechanics self-test against the REAL installed window, a REAL backend and the
REAL Computer host, with the deterministic mock model.

No GPU, no Canalla Cloud, no user data: every run gets its own data root, device directory and
device-credential target, and the mock provider is the only model. What is proven here is the
harness and the product mechanics (confirmation, tool execution, window close, backend down/up,
Resume, transport loss, heartbeat recovery, no duplicate side effects). A live PASS still needs
Qwen; this script exists so a harness defect cannot burn a Pod again.

Run with the backend venv:
    python artifacts/acceptance-126/free_restart_selftest.py t3
    python artifacts/acceptance-126/free_restart_selftest.py t4
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ACC = REPO / "artifacts" / "acceptance-123"
sys.path.insert(0, str(ACC))
import common  # noqa: E402

SANDBOX = Path(os.environ.get("CANALLA_ACCEPTANCE_SANDBOX", os.path.join(os.path.expanduser("~"), "Canalla Acceptance")))
RUN_ROOT = SANDBOX / "free-126"
EXE = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Canalla LLM" / "alex-llm.exe"
BACKEND_PY = REPO / "apps" / "backend" / ".venv" / "Scripts" / "python.exe"
DESKTOP = REPO / "apps" / "desktop"
CDP_PORT = int(os.environ.get("CANALLA_CDP_PORT", "9244"))
BASE = "http://127.0.0.1:8000"


# --------------------------------------------------------------------------- credentials


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
_advapi32.CredReadW.restype = wintypes.BOOL
_advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
_advapi32.CredWriteW.restype = wintypes.BOOL
_advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
_advapi32.CredDeleteW.restype = wintypes.BOOL
_advapi32.CredFree.argtypes = [ctypes.c_void_p]


def read_secret(target: str) -> str:
    pointer = ctypes.POINTER(CREDENTIALW)()
    if not _advapi32.CredReadW(target, 1, 0, ctypes.byref(pointer)):
        raise RuntimeError("cred_read_failed " + target)
    try:
        credential = pointer.contents
        return ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize).decode("utf-8")
    finally:
        _advapi32.CredFree(pointer)


def write_secret(target: str, value: str) -> None:
    blob = value.encode("utf-8")
    buffer = ctypes.create_string_buffer(blob, len(blob))
    credential = CREDENTIALW()
    credential.Type = 1
    credential.TargetName = target
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    credential.Persist = 2
    if not _advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise RuntimeError("cred_write_failed " + target)


def delete_secret(target: str) -> None:
    _advapi32.CredDeleteW(target, 1, 0)


# --------------------------------------------------------------------------- http / process


def call(
    method: str,
    path: str,
    token: str | None = None,
    body: dict | None = None,
    timeout: float = 30,
    headers: dict | None = None,
):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
    )
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, str(error)


def get_json(path: str, token: str | None = None):
    status, text = call("GET", path, token=token)
    if status != 200:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def health() -> bool:
    return get_json("/health") is not None


def wait(predicate, seconds: float, interval: float = 1.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def app_pids() -> list[int]:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq alex-llm.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        timeout=60,
    )
    pids = []
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        parts = [item.strip('"') for item in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == "alex-llm.exe":
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def node(*args: str, timeout: int = 300) -> tuple[int, dict, str]:
    result = subprocess.run(
        ["node", "ui-task.mjs", *args],
        cwd=str(DESKTOP),
        capture_output=True,
        timeout=timeout,
        env={**os.environ, "CANALLA_CDP_PORT": str(CDP_PORT)},
    )
    text = (result.stdout or b"").decode("utf-8", "replace") + (result.stderr or b"").decode("utf-8", "replace")
    payload: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
    return result.returncode, payload, text[-1200:]


# --------------------------------------------------------------------------- lifecycle


class Instance:
    def __init__(self, name: str) -> None:
        self.name = name
        self.root = RUN_ROOT / name / "root"
        self.device = RUN_ROOT / name / "device"
        self.workspace = RUN_ROOT / name / "workspace"
        self.credential_target = "Alex LLM/device-credential-free126-" + name
        self.gateway_name = "free126-" + name
        self.proc: subprocess.Popen | None = None
        self.backend: subprocess.Popen | None = None
        self.log = RUN_ROOT / name / "backend.log"

    def prepare(self) -> None:
        for path in (self.root, self.device, self.workspace):
            if path.exists():
                shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)
        (self.workspace / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (self.workspace / "test_app.py").write_text(
            "import time\n\nfrom app import add\n\n\ndef test_add():\n    time.sleep(60)\n    assert add(2, 2) == 4\n",
            encoding="utf-8",
        )
        (self.workspace / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
        delete_secret(self.credential_target)

    def backend_env(self) -> dict:
        return {
            **os.environ,
            "ALEX_LLM_DATA_DIR": str(self.root),
            "ALEX_DEVICE_DIR": str(self.device),
            "ALEX_DEVICE_CREDENTIAL_TARGET": self.credential_target,
            "ALEX_BACKEND_HOST": "127.0.0.1",
            "ALEX_BACKEND_PORT": "8000",
            "APP_ENV": "development",
            "LLM_PROVIDER": "mock",
            "ALEX_AI_MODE": "direct",
            "ALEX_RUNTIME_MODE": "dev_owned",
            "TOR_MANAGED_ENABLED": "false",
            "ALEX_GATEWAY_CREDENTIAL_NAME": self.gateway_name,
        }

    def app_env(self) -> dict:
        return {
            **os.environ,
            "ALEX_LLM_DATA_DIR": str(self.root),
            "ALEX_DEVICE_DIR": str(self.device),
            "ALEX_DEVICE_CREDENTIAL_TARGET": self.credential_target,
            "ALEX_AI_MODE": "direct",
            "ALEX_GATEWAY_CREDENTIAL_NAME": self.gateway_name,
            "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={CDP_PORT}",
        }

    def start_backend(self) -> None:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.log, "ab")
        self.backend = subprocess.Popen(
            [str(BACKEND_PY), "-m", "app.runtime_entry"],
            cwd=str(REPO / "apps" / "backend"),
            env=self.backend_env(),
            stdout=handle,
            stderr=handle,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if not wait(health, 120, 0.5):
            raise RuntimeError("backend did not become healthy; see " + str(self.log))

    def stop_backend(self) -> None:
        if self.backend and self.backend.poll() is None:
            token = (self.root / "runtime" / "shutdown.token").read_text(encoding="utf-8").strip()
            request = urllib.request.Request(BASE + "/runtime/shutdown", data=b"{}", method="POST")
            request.add_header("X-Alex-Runtime-Token", token)
            try:
                urllib.request.urlopen(request, timeout=5).read()
            except Exception:
                pass
            self.backend.terminate()
            try:
                self.backend.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.backend.kill()
        self.backend = None
        wait(lambda: not health(), 30, 0.5)

    def start_app(self) -> None:
        self.proc = subprocess.Popen(
            [str(EXE)],
            env=self.app_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not wait(lambda: cdp_ready(), 150, 0.5):
            raise RuntimeError("CDP did not become ready")

    def close_app(self, *, force: bool = False) -> None:
        if not self.proc:
            return
        if not force:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-Process -Id {self.proc.pid}).CloseMainWindow() | Out-Null",
                ],
                capture_output=True,
                timeout=30,
            )
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                force = True
        if force:
            subprocess.run(
                ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=30,
            )
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pass
        self.proc = None
        wait(lambda: not app_pids(), 30, 0.5)

    def token(self) -> str:
        session_id = (self.root / "runtime" / "session.id").read_text(encoding="utf-8").strip()
        install_id = (self.root / "runtime" / "install.id").read_text(encoding="utf-8").strip()
        target = "Alex LLM/session/" + session_id
        status, text = call(
            "POST",
            "/auth/refresh",
            body={"session_id": session_id, "refresh_secret": read_secret(target)},
            headers={"X-Alex-Device-Id": install_id},
        )
        if status != 200:
            raise RuntimeError("session refresh failed: %s %s" % (status, text[:200]))
        payload = json.loads(text)
        write_secret(target, payload["refresh_secret"])
        return payload["access_token"]

    def cleanup(self) -> None:
        self.close_app()
        self.stop_backend()
        delete_secret(self.credential_target)
        try:
            session_id = (self.root / "runtime" / "session.id").read_text(encoding="utf-8").strip()
            delete_secret("Alex LLM/session/" + session_id)
        except OSError:
            pass


def cdp_ready() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def ensure_device(instance: Instance, token: str) -> dict:
    """Wait for the app's own pairing; pair through the API only if the UI did not."""
    rows = get_json("/tools/devices", token) or []
    if not wait(lambda: [row for row in (get_json("/tools/devices", token) or []) if row.get("online")], 45, 1.0):
        user = get_json("/auth/me", token) or {}
        status, text = call("POST", "/tools/devices/pair", token, {"platform": "windows"})
        if status != 200:
            raise RuntimeError("device pair failed: %s %s" % (status, text[:200]))
        paired = json.loads(text)
        write_secret(instance.credential_target, paired["credential"])
        (instance.device / "device.json").write_text(
            json.dumps(
                {
                    "device_id": paired["device_id"],
                    "display_name": "Free acceptance device",
                    "user_id": user.get("id") or "",
                    "backend": BASE,
                }
            ),
            encoding="utf-8",
        )
        if not wait(lambda: [row for row in (get_json("/tools/devices", token) or []) if row.get("online")], 60, 1.0):
            raise RuntimeError("device never came online")
    rows = get_json("/tools/devices", token) or []
    return next((row for row in rows if row.get("online")), rows[0] if rows else {})


def configure(instance: Instance, token: str) -> None:
    status, text = call(
        "PUT",
        "/tools/preferences",
        token,
        {"computer_mode": "ask", "workspace_roots": [str(instance.workspace)]},
    )
    if status != 200:
        raise RuntimeError("preferences failed: %s %s" % (status, text[:200]))
    ensure_device(instance, token)


class Approver:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id
        self.stop = threading.Event()
        self.seen: list[str] = []
        self.log: list[dict] = []
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def start(self) -> "Approver":
        self.thread.start()
        return self

    def loop(self) -> None:
        while not self.stop.wait(1.0):
            runs = get_json("/tools/runs?chat_id=" + self.chat_id, self.token) or []
            for row in runs:
                if row.get("status") != "waiting_confirmation":
                    continue
                run_id = str(row.get("id") or "")
                if not run_id or run_id in self.seen:
                    continue
                self.seen.append(run_id)
                allow = common.safe_run(row, SANDBOX)
                status, body = common.confirm(self.token, run_id, allow)
                self.log.append(
                    {
                        "run_id": run_id,
                        "tool": row.get("tool_name"),
                        "allow": allow,
                        "http": status,
                        "body": str(body)[:100],
                        "at": time.time(),
                    }
                )

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=5)


def task_status(token: str, chat_id: str) -> dict:
    rows = get_json("/tasks?chat_id=" + chat_id, token) or []
    return rows[-1] if isinstance(rows, list) and rows else {}


def task_executing(token: str, chat_id: str) -> bool:
    """True while the task is really working, not parked for a click."""
    status = str(task_status(token, chat_id).get("status") or "")
    if status == "EXECUTING":
        return True
    return any(
        str(row.get("state") or row.get("status") or "")
        in {"waiting_host", "claimed", "started", "pending"}
        for row in runs(token, chat_id)
    )


def runs(token: str, chat_id: str) -> list[dict]:
    rows = get_json("/tools/runs?chat_id=" + chat_id, token) or []
    return [row for row in rows if row.get("origin") == "model"]


def answer(token: str, chat_id: str) -> str:
    rows = get_json("/chats/" + chat_id + "/messages", token) or []
    for row in reversed(rows):
        if row.get("role") == "assistant":
            return str(row.get("content") or "")
    return ""


def newest_chat(token: str, exclude: set[str]) -> str:
    for row in get_json("/chats", token) or []:
        chat_id = str(row.get("id") or "")
        if chat_id and chat_id not in exclude:
            return chat_id
    return ""


def send_prompt(instance: Instance, text: str) -> dict:
    prompt_file = RUN_ROOT / instance.name / "prompt.txt"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(text, encoding="utf-8")
    code, payload, output = node("send", str(prompt_file))
    return {"exit": code, "payload": payload, "output": output}


def run_t3() -> dict:
    instance = Instance("t3")
    instance.prepare()
    evidence: dict = {"verdict": "FAIL", "reasons": [], "checks": {}}
    token = ""
    approver = None
    try:
        instance.start_backend()
        instance.start_app()
        code, payload, output = node("register", "free-t3@example.com", "free-password-12345", "T3 Owner")
        if code != 0:
            raise RuntimeError("registration failed: " + output)
        token = instance.token()
        configure(instance, token)
        before = {str(row.get("id")) for row in (get_json("/chats", token) or [])}
        sent = send_prompt(
            instance,
            "Полностью проверь проект в папке "
            + str(instance.workspace)
            + ", запусти pytest и исправь падающие тесты. Работай до конца.",
        )
        chat_id = wait(lambda: newest_chat(token, before) or None, 90, 1.0) or ""
        if not chat_id:
            raise RuntimeError("no chat was created")
        approver = Approver(token, chat_id).start()
        first_tool = wait(
            lambda: [row for row in runs(token, chat_id) if row.get("status") == "completed"] or None,
            180,
            1.0,
        )
        evidence["checks"]["first_tool"] = bool(first_tool)
        evidence["first_tool"] = (first_tool or [{}])[0].get("tool_name")
        # Close while the task is really working. A task parked on a confirmation is not the
        # scenario: the native run has to be in flight so the product can really lose it.
        executing = wait(lambda: task_executing(token, chat_id) or None, 60, 0.5)
        evidence["checks"]["executing_at_close"] = bool(executing)
        evidence["status_at_close"] = task_status(token, chat_id).get("status")
        evidence["confirmation_observed"] = list(approver.seen)
        evidence["confirmations"] = list(approver.log)
        evidence["computer_mode"] = (get_json("/tools/preferences", token) or {}).get("computer_mode")
        evidence["device_before"] = (get_json("/tools/devices", token) or [{}])[0].get("device_id")
        evidence["task_before"] = {
            key: task_status(token, chat_id).get(key) for key in ("id", "status", "tool_calls_used")
        }
        evidence["files_before"] = common.snapshot(instance.workspace)
        evidence["payloads_before"] = [
            str(row.get("input_digest") or "") for row in runs(token, chat_id) if row.get("status") == "completed"
        ]
        evidence["tool_ids_before"] = [row.get("id") for row in runs(token, chat_id)]

        # Stop approving before the window goes away, so the restarted product really parks the
        # task instead of being silently continued by this harness. Either a dead in-flight run
        # (`INTERRUPTED`) or a step that needs a fresh approval (`WAITING_CONFIRMATION`) proves
        # the product did not pretend the work succeeded.
        approver.close()
        instance.close_app(force=True)
        instance.stop_backend()
        evidence["checks"]["backend_went_down"] = not health()
        instance.start_backend()
        instance.start_app()
        evidence["checks"]["backend_came_up"] = health()
        parked = wait(
            lambda: (
                task_status(token, chat_id).get("status")
                in {"INTERRUPTED", "WAITING_CONFIRMATION"}
            )
            or None,
            90,
            1.0,
        )
        evidence["status_after_restart"] = task_status(token, chat_id).get("status")
        evidence["checks"]["parked_after_restart"] = bool(parked)
        # The window may reopen on another conversation. Select the task chat first so the
        # product's own Resume control is what is observed, not just the endpoint.
        opened = node("open-chat", "Полностью проверь проект")
        evidence["open_chat"] = {"exit": opened[0], "payload": opened[1]}
        state = node("state")
        evidence["ui_state"] = state[1]
        resumed = node("resume")
        evidence["resume"] = {"exit": resumed[0], "payload": resumed[1], "output": resumed[2]}
        if not resumed[1].get("resumed"):
            task_id = str(task_status(token, chat_id).get("id") or "")
            status, body = call("POST", "/tasks/" + task_id + "/resume", token, {})
            evidence["resume"]["endpoint"] = {"http": status, "body": body[:200]}
        # Approval resumes only after the product's own Resume was asked for.
        approver = Approver(token, chat_id).start()
        terminal = wait(
            lambda: task_status(token, chat_id).get("status") in {"COMPLETED", "FAILED", "STOPPED"} or None,
            180,
            1.0,
        )
        final = task_status(token, chat_id)
        evidence["final_task"] = {
            key: final.get(key) for key in ("id", "status", "tool_calls_used", "completion_summary", "last_error")
        }
        # The controller concludes before the prose stream finishes. The answer is part of the
        # contract: wait for it, never close the window under an answer that is still writing.
        saved_answer = wait(lambda: answer(token, chat_id).strip() or None, 60, 1.0) or ""
        evidence["final_answer"] = saved_answer[:800]
        evidence["files_after"] = common.snapshot(instance.workspace)
        evidence["files_lost"] = sorted(set(evidence["files_before"]) - set(evidence["files_after"]))
        after_runs = runs(token, chat_id)
        new_runs = [row for row in after_runs if str(row.get("id") or "") not in set(evidence["tool_ids_before"])]
        evidence["repeated_payloads"] = [
            row.get("tool_name")
            for row in new_runs
            if str(row.get("input_digest") or "") in set(evidence["payloads_before"])
        ]
        evidence["device_after"] = (get_json("/tools/devices", token) or [{}])[0].get("device_id")
        evidence["checks"]["same_task"] = str(evidence["task_before"].get("id")) == str(final.get("id"))
        evidence["checks"]["terminal"] = bool(terminal)
        evidence["checks"]["answer_saved"] = bool(evidence["final_answer"].strip())
        evidence["checks"]["no_duplicate_payloads"] = not evidence["repeated_payloads"]
        evidence["checks"]["device_stable"] = evidence["device_before"] == evidence["device_after"]
        evidence["checks"]["confirmation_observed"] = bool(approver.seen) if approver else bool(evidence["confirmation_observed"])
        evidence["relative_connect_used"] = True
    except Exception as error:  # noqa: BLE001 - evidence must survive a harness bug
        evidence["reasons"].append(f"{type(error).__name__}: {error}")
    finally:
        if approver is not None:
            approver.close()
        instance.cleanup()
    return finish(evidence, required=(
        "first_tool",
        "executing_at_close",
        "backend_went_down",
        "backend_came_up",
        "parked_after_restart",
        "same_task",
        "terminal",
        "answer_saved",
        "no_duplicate_payloads",
        "device_stable",
        "confirmation_observed",
    ))


def suspend_pid(pid: int, watchdog: int = 180) -> dict:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO / "artifacts" / "suspend-app.ps1"),
            "-Suspend",
            "-ProcessId",
            str(pid),
            "-WatchdogSeconds",
            str(watchdog),
        ],
        capture_output=True,
        timeout=120,
    )
    return {"exit": result.returncode, "output": (result.stdout + result.stderr).decode("utf-8", "replace")[-300:]}


def resume_pid(pid: int) -> dict:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO / "artifacts" / "suspend-app.ps1"),
            "-Resume",
            "-ProcessId",
            str(pid),
        ],
        capture_output=True,
        timeout=120,
    )
    return {"exit": result.returncode, "output": (result.stdout + result.stderr).decode("utf-8", "replace")[-300:]}


def run_t4() -> dict:
    instance = Instance("t4")
    instance.prepare()
    evidence: dict = {"verdict": "FAIL", "reasons": [], "checks": {}}
    approver = None
    pid = 0
    suspended = False
    try:
        instance.start_backend()
        instance.start_app()
        code, payload, output = node("register", "free-t4@example.com", "free-password-12345", "T4 Owner")
        if code != 0:
            raise RuntimeError("registration failed: " + output)
        token = instance.token()
        configure(instance, token)
        before = {str(row.get("id")) for row in (get_json("/chats", token) or [])}
        sent = send_prompt(
            instance,
            "Полностью проверь проект в папке "
            + str(instance.workspace)
            + ", запусти pytest и исправь падающие тесты. Работай до конца.",
        )
        chat_id = wait(lambda: newest_chat(token, before) or None, 90, 1.0) or ""
        if not chat_id:
            raise RuntimeError("no chat was created")
        approver = Approver(token, chat_id).start()
        first_tool = wait(
            lambda: [row for row in runs(token, chat_id) if row.get("status") == "completed"] or None,
            180,
            1.0,
        )
        evidence["checks"]["first_tool"] = bool(first_tool)
        evidence["first_tool"] = (first_tool or [{}])[0].get("tool_name")
        executing = wait(lambda: task_executing(token, chat_id) or None, 60, 0.5)
        evidence["checks"]["executing_at_disconnect"] = bool(executing)
        evidence["status_at_disconnect"] = task_status(token, chat_id).get("status")
        evidence["confirmation_observed"] = list(approver.seen)
        evidence["confirmations"] = list(approver.log)
        evidence["computer_mode"] = (get_json("/tools/preferences", token) or {}).get("computer_mode")
        evidence["device_before"] = (get_json("/tools/devices", token) or [{}])[0].get("device_id")
        evidence["task_before"] = {
            key: task_status(token, chat_id).get(key) for key in ("id", "status", "tool_calls_used")
        }
        evidence["files_before"] = common.snapshot(instance.workspace)
        evidence["payloads_before"] = [
            str(row.get("input_digest") or "") for row in runs(token, chat_id) if row.get("status") == "completed"
        ]
        evidence["tool_ids_before"] = [row.get("id") for row in runs(token, chat_id)]

        pids = app_pids()
        if len(pids) != 1:
            raise RuntimeError("expected one acceptance window, found %d" % len(pids))
        pid = pids[0]
        suspended_result = suspend_pid(pid, watchdog=150)
        suspended = suspended_result.get("exit") == 0
        evidence["suspend"] = suspended_result
        typed = wait(
            lambda: task_status(token, chat_id).get("status")
            in {"WAITING_DEVICE", "WAITING_CONFIRMATION", "PAUSED", "FAILED"}
            or None,
            90,
            1.0,
        )
        evidence["offline_status"] = task_status(token, chat_id).get("status")
        evidence["online_during_offline"] = any(
            row.get("online") for row in (get_json("/tools/devices", token) or [])
        )
        evidence["answer_while_offline"] = answer(token, chat_id)[:800]
        time.sleep(2)
        resumed = resume_pid(pid)
        suspended = False
        evidence["resume"] = resumed
        resumed_at = time.time()
        if not (resumed.get("exit") == 0):
            raise RuntimeError("resume failed: " + str(resumed))
        heartbeat = wait(
            lambda: any(row.get("online") for row in (get_json("/tools/devices", token) or [])) or None,
            90,
            1.0,
        )
        evidence["checks"]["heartbeat_returned"] = bool(heartbeat)
        current = task_status(token, chat_id)
        if current.get("status") in {"WAITING_DEVICE", "INTERRUPTED", "PAUSED"}:
            evidence["resume_ui"] = node("resume")[1]
        terminal = wait(
            lambda: task_status(token, chat_id).get("status") in {"COMPLETED", "FAILED", "STOPPED"} or None,
            240,
            1.0,
        )
        final = task_status(token, chat_id)
        evidence["final_task"] = {
            key: final.get(key) for key in ("id", "status", "tool_calls_used", "completion_summary", "last_error")
        }
        # The controller concludes before the prose stream finishes. The answer is part of the
        # contract: wait for it, never close the window under an answer that is still writing.
        saved_answer = wait(lambda: answer(token, chat_id).strip() or None, 60, 1.0) or ""
        evidence["final_answer"] = saved_answer[:800]
        evidence["files_after"] = common.snapshot(instance.workspace)
        evidence["files_lost"] = sorted(set(evidence["files_before"]) - set(evidence["files_after"]))
        after_runs = runs(token, chat_id)
        new_runs = [row for row in after_runs if str(row.get("id") or "") not in set(evidence["tool_ids_before"])]
        evidence["repeated_payloads"] = [
            row.get("tool_name")
            for row in new_runs
            if str(row.get("input_digest") or "") in set(evidence["payloads_before"])
        ]
        evidence["device_after"] = (get_json("/tools/devices", token) or [{}])[0].get("device_id")
        evidence["checks"]["typed_offline"] = typed is not None
        evidence["checks"]["offline_not_success"] = (
            "Задача завершена" not in evidence["answer_while_offline"]
            and "COMPLETED" not in str(evidence["offline_status"])
        )
        evidence["checks"]["same_task"] = str(evidence["task_before"].get("id")) == str(final.get("id"))
        evidence["checks"]["terminal"] = bool(terminal)
        evidence["checks"]["answer_saved"] = bool(evidence["final_answer"].strip())
        evidence["checks"]["no_duplicate_payloads"] = not evidence["repeated_payloads"]
        evidence["checks"]["device_stable"] = evidence["device_before"] == evidence["device_after"]
        evidence["checks"]["confirmation_observed"] = bool(evidence["confirmation_observed"])
        # The live T4 of 2026-11-11 died exactly here: the approval loop had already stopped, so
        # the first tool after the reconnect was never confirmed. A confirmation *and* a
        # completed tool after the resume are the proof that this cannot happen again.
        after_resume = [item for item in (evidence.get("confirmations") or [])
                        if float(item.get("at") or 0) > resumed_at]
        evidence["confirmations_after_resume"] = after_resume
        evidence["checks"]["confirmation_after_resume"] = bool(after_resume)
    except Exception as error:  # noqa: BLE001
        evidence["reasons"].append(f"{type(error).__name__}: {error}")
    finally:
        if suspended and pid:
            resume_pid(pid)
        if approver is not None:
            approver.close()
        instance.cleanup()
    return finish(evidence, required=(
        "first_tool",
        "executing_at_disconnect",
        "typed_offline",
        "heartbeat_returned",
        "offline_not_success",
        "same_task",
        "terminal",
        "answer_saved",
        "no_duplicate_payloads",
        "device_stable",
        "confirmation_observed",
        "confirmation_after_resume",
    ))


def finish(evidence: dict, required: tuple[str, ...]) -> dict:
    for name in required:
        if not evidence["checks"].get(name):
            evidence["reasons"].append("check failed: " + name)
    evidence["verdict"] = "PASS" if not evidence["reasons"] else "FAIL"
    return evidence


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    if app_pids():
        print("ABORT: an alex-llm window is already running; close it first (this harness never shares one)")
        return 2
    results: dict[str, dict] = {}
    if mode in {"t3", "all"}:
        results["t3"] = run_t3()
        print(json.dumps({"t3": results["t3"]}, ensure_ascii=False, indent=1)[:4000])
    if mode in {"t4", "all"}:
        results["t4"] = run_t4()
        print(json.dumps({"t4": results["t4"]}, ensure_ascii=False, indent=1)[:4000])
    out = RUN_ROOT / "last-result.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(item.get("verdict") == "PASS" for item in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
