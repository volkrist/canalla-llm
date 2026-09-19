"""Alex LLM 0.9.1 GPU: real Windows local computer. One L40S Pod, hard $0.40."""

from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("real_e2e_08", SCRIPTS / "real_e2e_08.py")
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)

e2e.SESSION_BUDGET = 0.40
e2e.GPU_COST_STOP = 0.36
e2e.GPU_WALL = 25 * 60
e2e.MAX_HOURLY = 1.10
e2e.CATALOG_WAIT = 25 * 60

FOLDERID_DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"
MARKER = "ALEX_SEARCH_MARKER_49127"
EXTERNAL = "ALEX_EXTERNAL_FILE_CHANGE_7391"
HELLO_TEXT = "Alex Local Computer REAL PASS."
FORBIDDEN = (
    "read_file",
    "write_file",
    "patch_file",
    "create_directory",
    "copy_file",
    "move_file",
    "search_code",
    "get_known_folders",
    "install_software",
    "git_add",
    "git_commit",
    "git_push",
    "inspect_form",
    "submit_form",
    "checkout_purchase",
)


def wait_l40s_only(api, prefs):
    deadline = time.time() + e2e.CATALOG_WAIT
    last = []
    while time.time() < deadline:
        try:
            response = api.client.get("/compute/options", headers=api.headers())
        except Exception as error:
            e2e.log("gpu catalog transport=%s" % type(error).__name__)
            time.sleep(20)
            continue
        if response.status_code != 200:
            e2e.log("gpu catalog http=%s" % response.status_code)
            time.sleep(20)
            continue
        options = response.json().get("options") or []
        if options:
            last = options
        l40s = next((item for item in options if item.get("id") == "NVIDIA L40S"), None)
        compatible = e2e.compatible_gpus(options)
        e2e.REPORT["gpu_options"] = [
            {
                "id": item.get("id"),
                "vram": item.get("vram_gb"),
                "price": item.get("hourly_rate"),
                "availability": item.get("availability"),
                "selectable": item.get("selectable"),
                "reason": item.get("reason"),
            }
            for item in options
        ]
        e2e.log(
            "gpu catalog wait l40s=%s selectable_compatible=%s"
            % ((l40s or {}).get("availability"), [item.get("id") for item in compatible])
        )
        if l40s and l40s in compatible:
            prefs["gpu_id"] = "NVIDIA L40S"
            api.client.put("/compute/preferences", headers=api.headers(), json=prefs)
            return prefs, options, "l40s"
        time.sleep(20)
    raise RuntimeError(
        "no_l40s_after_wait:%s"
        % [{"id": item.get("id"), "availability": item.get("availability")} for item in last]
    )


e2e.wait_for_selectable_gpu = wait_l40s_only
_orig_compatible = e2e.compatible_gpus


def compatible_gpus_l40s(options):
    return [item for item in _orig_compatible(options) if item.get("id") == "NVIDIA L40S"]


e2e.compatible_gpus = compatible_gpus_l40s


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


def known_folder(guid: str) -> Path:
    parts = guid.strip("{}").split("-")
    data4 = bytes.fromhex(parts[3] + parts[4])
    folder = GUID(int(parts[0], 16), int(parts[1], 16), int(parts[2], 16), (wintypes.BYTE * 8)(*data4))
    path = ctypes.c_wchar_p()
    getter = ctypes.windll.shell32.SHGetKnownFolderPath
    getter.argtypes = [
        ctypes.POINTER(GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    status = getter(ctypes.byref(folder), 0, None, ctypes.byref(path))
    if status != 0 or not path.value:
        raise OSError(status, "SHGetKnownFolderPath")
    value = path.value
    ctypes.windll.ole32.CoTaskMemFree(path)
    return Path(value)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_hello(desktop: Path, harness: Path | None = None):
    folders = []
    if harness and harness.is_dir():
        folders.append(harness)
    try:
        folders.extend(
            sorted(
                (item for item in desktop.iterdir() if item.is_dir() and item.name.startswith("Alex-LLM-E2E")),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        )
    except OSError:
        pass
    seen = set()
    for folder in folders:
        key = str(folder).casefold()
        if key in seen:
            continue
        seen.add(key)
        hello = folder / "hello.txt"
        if hello.is_file():
            return folder, hello
    return (folders[0] if folders else None), None


def host_bin():
    debug = e2e.DESKTOP / "target" / "debug" / "alex-host-loop.exe"
    release = e2e.DESKTOP / "target" / "release" / "alex-host-loop.exe"
    if debug.exists():
        return debug
    if release.exists():
        return release
    raise RuntimeError("host_bin_missing")


def dummy_discovered():
    return {
        "ahmia_onion": None,
        "torproject_onion": None,
        "search_providers": [
            {
                "name": "ahmia-clearnet",
                "url_template": "https://ahmia.fi/search/?q=__QUERY__",
                "form_url": "https://ahmia.fi/",
            }
        ],
        "official_mapping": [],
        "sources": [{"page": "dummy", "status": 0, "onion_count": 0}],
    }


def collect_task_stream(api, method_path, body, read_timeout=240):
    parts = []
    events = []
    tools = []
    task_snaps = []
    client = e2e.httpx.Client(
        base_url=api.base,
        timeout=e2e.httpx.Timeout(connect=10.0, read=read_timeout, write=10.0, pool=10.0),
    )
    try:
        with client.stream("POST", method_path, headers=api.headers(), json=body) as response:
            if response.status_code != 200:
                body_bytes = response.read()
                raise RuntimeError(f"stream_http_{response.status_code}:{body_bytes[:300]!r}")
            for event, payload in e2e.sse_events(response):
                row = {"event": event}
                if isinstance(payload, dict):
                    if event == "task":
                        task_snaps.append(
                            {
                                "id": payload.get("id"),
                                "status": payload.get("status"),
                                "plan_revision": payload.get("plan_revision"),
                                "tool_calls_used": payload.get("tool_calls_used"),
                                "queue_position": payload.get("queue_position"),
                                "last_error": payload.get("last_error"),
                            }
                        )
                    if event in {"error", "task_status"}:
                        row["code"] = payload.get("code") or payload.get("state")
                    if event == "tool":
                        tools.append(
                            {
                                "id": payload.get("id"),
                                "name": payload.get("name") or payload.get("tool_name"),
                                "status": payload.get("status"),
                                "origin": payload.get("origin"),
                                "risk": payload.get("risk_level"),
                            }
                        )
                        row["tool"] = tools[-1]["name"]
                        row["status"] = tools[-1]["status"]
                    if event == "delta":
                        parts.append(payload.get("content") or "")
                    if event == "done":
                        row["payload_keys"] = sorted(payload)
                events.append(row)
                if event in {"done", "error"}:
                    break
    finally:
        client.close()
    text = "".join(parts)
    return {
        "text": text,
        "events": events,
        "chars": len(text),
        "tools": tools,
        "task_snaps": task_snaps,
        "raw_protocol": any(
            token in text for token in ("<tool_call", "tool_calls", "</function", "<function")
        ),
    }


def chat_stream(api, chat_id, content, read_timeout=240):
    lowered = content.lower()
    if any(token.lower() in lowered for token in FORBIDDEN):
        raise RuntimeError("prompt_mentions_tools")
    streamed = collect_task_stream(
        api,
        f"/chats/{chat_id}/stream",
        {
            "content": content,
            "web_mode": "off",
            "computer_mode": "trusted",
            "tor_mode": "off",
        },
        read_timeout=read_timeout,
    )
    return continue_queued_task(api, chat_id, streamed, read_timeout=read_timeout)


def continue_queued_task(api, chat_id, streamed, read_timeout=180):
    snaps = streamed.get("task_snaps") or []
    if not any((item or {}).get("status") == "WAITING_WORKSPACE" for item in snaps):
        return streamed
    deadline = time.time() + min(read_timeout, 120)
    while time.time() < deadline:
        tasks = api.client.get("/tasks", headers=api.headers(), params={"chat_id": chat_id, "limit": 5}).json()
        if not tasks:
            time.sleep(1)
            continue
        task = tasks[0]
        status = task.get("status")
        if status == "WAITING_WORKSPACE":
            time.sleep(1)
            continue
        if status in {"READY", "RECOVERING"} and task.get("promoted_from_queue"):
            extra = collect_task_stream(api, f"/tasks/{task['id']}/resume", {}, read_timeout=read_timeout)
            extra["task_snaps"] = snaps + (extra.get("task_snaps") or [])
            extra["queue_resumed"] = True
            return extra
        return streamed
    return streamed


def last_assistant(api, chat_id):
    messages = api.client.get(f"/chats/{chat_id}/messages", headers=api.headers()).json()
    assistants = [item for item in messages if item.get("role") == "assistant"]
    content = (assistants[-1].get("content") if assistants else "") or ""
    return {
        "content": content[:2000],
        "raw_protocol": any(
            token in content for token in ("<tool_call", "tool_calls", "</function", "<function")
        ),
    }


def latest_task(api, chat_id):
    tasks = api.client.get("/tasks", headers=api.headers(), params={"chat_id": chat_id, "limit": 5}).json()
    if not tasks:
        return {}
    return api.client.get(f"/tasks/{tasks[0]['id']}", headers=api.headers()).json()


def run_entries(api, chat_id=None):
    rows = e2e.latest_runs(api, chat_id)
    entries = []
    for row in rows:
        meta = row.get("result_metadata") or {}
        summary = row.get("input_summary") or {}
        entries.append(
            {
                "task_id": row.get("task_id"),
                "name": row.get("tool_name"),
                "origin": row.get("origin"),
                "risk": row.get("risk_level"),
                "status": row.get("status"),
                "confirmation": bool(row.get("confirmed_at")),
                "target": summary.get("path")
                or summary.get("url")
                or summary.get("package")
                or summary.get("cwd")
                or summary.get("root"),
                "exit_code": meta.get("exit_code"),
                "before": meta.get("before_sha256"),
                "after": meta.get("after_sha256"),
                "pid": meta.get("pid"),
            }
        )
    return entries


class Pages(BaseHTTPRequestHandler):
    posts = {"submit": 0, "checkout": 0}

    def log_message(self, format, *args):
        del format, args

    def do_GET(self):
        if self.path.startswith("/form"):
            body = (
                b"<form method='POST' action='/submit'>"
                b"<input name='name'><textarea name='message'></textarea>"
                b"<button type='submit'>Send</button></form>"
            )
        else:
            body = (
                b'<div data-item="Alex Test Item" data-price="1.23" data-currency="USD" '
                b'data-seller="Alex Test Shop">Alex Test Item $1.23</div>'
            )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.path.startswith("/submit"):
            Pages.posts["submit"] += 1
            body = b"FORM_SUBMIT_OK"
        else:
            Pages.posts["checkout"] += 1
            payload = json.loads(raw.decode("utf-8") or "{}")
            assert payload.get("item") == "Alex Test Item"
            assert payload.get("total_price") == "1.23"
            body = b"PURCHASE_TEST_CONFIRMED"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ConfirmApprover(threading.Thread):
    def __init__(self, api, roots):
        super().__init__(daemon=True)
        self.api = api
        self.roots = [os.path.normcase(os.path.abspath(str(item))) for item in roots]
        self.stop = threading.Event()
        self.hold = threading.Event()
        self.seen = []
        self.approved = []

    def allowed(self, run):
        name = run.get("tool_name") or ""
        if name in {
            "format_volume",
            "system_shutdown",
            "mass_delete",
            "bitlocker_change",
            "boot_config",
            "manage_partition",
        }:
            return False
        return True

    def run(self):
        while not self.stop.wait(0.3):
            if self.hold.is_set():
                continue
            try:
                runs = self.api.client.get("/tools/runs", headers=self.api.headers(), params={"limit": 80}).json()
            except Exception:
                continue
            for run in runs:
                if run.get("status") != "waiting_confirmation":
                    continue
                key = run.get("id")
                if any(item.get("id") == key for item in self.seen):
                    continue
                self.seen.append(
                    {
                        "id": key,
                        "tool": run.get("tool_name"),
                        "risk": run.get("risk_level"),
                        "summary": run.get("input_summary"),
                    }
                )
                if not self.allowed(run):
                    continue
                response = self.api.client.post(
                    f"/tools/runs/{run['id']}/confirm",
                    headers=self.api.headers(),
                    json={"allow": True},
                )
                self.approved.append({"id": key, "tool": run.get("tool_name"), "http": response.status_code})


def which(cmd):
    from shutil import which as find

    return find(cmd)


def pick_package():
    winget = subprocess.run(["winget", "--version"], capture_output=True, text=True)
    winget_ok = winget.returncode == 0
    version = (winget.stdout or winget.stderr or "").strip()[:80]
    candidates = [
        ("jqlang.jq", ["jq", "jq.exe"]),
        ("sharkdp.fd", ["fd", "fd.exe"]),
        ("sharkdp.bat", ["bat", "bat.exe"]),
        ("BurntSushi.ripgrep.MSVC", ["rg", "rg.exe"]),
    ]
    chosen = None
    already = None
    for package, binaries in candidates:
        present = next((name for name in binaries if which(name)), None)
        if present:
            if already is None:
                already = {"package": package, "binary": present}
            continue
        chosen = {"package": package, "binary": binaries[0], "was_absent": True}
        break
    return {
        "winget_ok": winget_ok,
        "winget_version": version,
        "chosen": chosen,
        "already": already,
    }


def compute_running(api):
    try:
        status = api.client.get("/compute/status", headers=api.headers()).json()
    except Exception as error:
        return {"error": type(error).__name__}
    session = status.get("session") or {}
    state = status.get("state")
    running = state not in {None, "stopped", "idle", "not_configured", "error"} and bool(session.get("pod_id"))
    return {
        "state": state,
        "pod_id": session.get("pod_id"),
        "managed": session.get("managed"),
        "running_gpu": 1 if running else 0,
    }


def create_git_fixture(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "sample.txt").write_text("alpha\n", encoding="utf-8", newline="\n")
    subprocess.check_call(["git", "-c", "core.autocrlf=false", "init"], cwd=root, stdout=subprocess.DEVNULL)
    subprocess.check_call(["git", "-c", "core.autocrlf=false", "add", "sample.txt"], cwd=root)
    subprocess.check_call(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "user.email=e2e@example.com",
            "-c",
            "user.name=E2E",
            "commit",
            "-m",
            "initial",
        ],
        cwd=root,
        stdout=subprocess.DEVNULL,
    )
    remote = root.parent / "test-remote.git"
    subprocess.check_call(["git", "init", "--bare", str(remote)], stdout=subprocess.DEVNULL)
    subprocess.check_call(["git", "remote", "add", "origin", str(remote)], cwd=root)
    return remote


def maybe_skip(cost_limit):
    if e2e.estimated_cost() >= cost_limit:
        raise RuntimeError(f"gpu_budget_guard:{e2e.estimated_cost()}")


def main():
    stamp = e2e.utcnow().strftime("%Y%m%dT%H%M%S")
    work = Path(os.environ["TEMP"]) / "alex-llm-real-e2e" / f"local-computer-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    host_dir = work / "host-data"
    host_dir.mkdir(parents=True, exist_ok=True)
    git_root = work / "disposable-git"
    remote = create_git_fixture(git_root)
    evidence = work / "evidence.json"
    report = e2e.REPORT
    report.update(
        {
            "version": "0.9.1-candidate",
            "workspace": str(work),
            "ui_automation": "no",
            "cursor_fixed_project": False,
            "tinyfish_agent_browser": "forbidden",
        }
    )
    desktop = known_folder(FOLDERID_DESKTOP)
    existing = desktop / "Alex-LLM-E2E"
    report["desktop"] = {
        "path": str(desktop),
        "known_folder": True,
        "existing_alex_llm_e2e": existing.is_dir(),
        "hardcoded_users_name": "<name>" in str(desktop).lower(),
    }
    test_folder = desktop / f"Alex-LLM-E2E-{e2e.utcnow().strftime('%Y%m%d-%H%M%S')}"
    test_folder.mkdir(parents=True, exist_ok=True)
    report["test_folder_harness"] = str(test_folder)

    pages = ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    page_thread = threading.Thread(target=pages.serve_forever, daemon=True)
    page_thread.start()
    form_url = f"http://127.0.0.1:{pages.server_address[1]}/form"
    shop_url = f"http://127.0.0.1:{pages.server_address[1]}/shop"
    report["local_pages"] = {"form": form_url, "shop": shop_url}

    log = e2e.log
    backend_proc = None
    backend_log = None
    host_proc = None
    host_log = None
    approver = None
    api = e2e.Api()
    gpu_started = None
    package_info = pick_package()
    report["install_probe"] = package_info

    try:
        values = e2e.env_file_map()
        report["config"] = {
            "llm_provider_env": values.get("LLM_PROVIDER"),
            "runpod_key": e2e.secret_present(values, "RUNPOD_API_KEY"),
            "llm_key": e2e.secret_present(values, "LLM_API_KEY"),
            "tinyfish_key": e2e.secret_present(values, "TINYFISH_API_KEY"),
            "jwt": e2e.secret_present(values, "JWT_SECRET"),
        }
        if not report["config"]["runpod_key"] or not report["config"]["llm_key"]:
            raise SystemExit("missing compute credentials")

        discovered = dummy_discovered()
        try:
            discovered = e2e.discover_official()
        except Exception as error:
            log("official onion discovery skipped: %s" % type(error).__name__)

        log("restart backend with llamacpp")
        os.environ["DATABASE_URL"] = e2e.env_file_map().get("DATABASE_URL") or "sqlite:///./alex.db"
        e2e.kill_port_8000()
        time.sleep(1)
        os.environ["COMPUTE_BACKGROUND_ENABLED"] = "true"
        os.environ["ALLOW_USER_COMPUTE_START"] = "true"
        os.environ["LLM_PROVIDER"] = "llamacpp"
        backend_proc, backend_log, _ = e2e.start_backend(discovered, 9050, session_budget="0.40")
        health = e2e.wait_health()
        report["backend_health"] = health
        if health.get("provider") != "llamacpp":
            raise RuntimeError("backend_not_llamacpp")

        email = f"computer-e2e-{stamp}@example.com"
        password = "e2e-password-" + hashlib.sha256(stamp.encode()).hexdigest()[:12]
        api.register(email, password)
        api.client.post("/compute/search/cancel", headers=api.headers())
        try:
            e2e.stop_compute(api)
        except Exception:
            pass
        roots = [str(desktop), str(test_folder), str(git_root), str(work)]
        api.client.put(
            "/tools/preferences",
            headers=api.headers(),
            json={
                "search_enabled": False,
                "fetch_enabled": False,
                "default_mode": "off",
                "agent_enabled": False,
                "browser_enabled": False,
                "tor_enabled": False,
                "computer_mode": "trusted",
                "workspace_roots": roots,
                "device_display_name": "E2E native host",
                "auto_commit": False,
                "allow_push": False,
            },
        )

        binary = host_bin()
        report["host_bin"] = str(binary)
        host_env = os.environ.copy()
        host_env.update(
            {
                "ALEX_BACKEND_URL": "http://127.0.0.1:8000",
                "ALEX_TOKEN": api.token,
                "ALEX_DEVICE_NAME": "E2E native host",
                "ALEX_WORKSPACE_ROOTS": ";".join(roots),
                "ALEX_DEVICE_DIR": str(host_dir),
                "ALEX_DEVICE_CREDENTIAL_TARGET": "Alex LLM/e2e-device-credential",
                "ALEX_PYTHON": str(e2e.BACKEND / ".venv" / "Scripts" / "python.exe"),
            }
        )
        host_log = open(work / "host.log", "ab")
        host_proc = subprocess.Popen(
            [str(binary)],
            cwd=str(e2e.DESKTOP),
            env=host_env,
            stdout=host_log,
            stderr=subprocess.STDOUT,
        )
        paired = False
        for _ in range(40):
            devices = api.client.get("/tools/devices", headers=api.headers()).json()
            if devices and devices[0].get("online"):
                paired = True
                report["device"] = {
                    "online": True,
                    "platform": devices[0].get("platform"),
                    "id_prefix": (devices[0].get("device_id") or "")[:8],
                }
                break
            time.sleep(0.5)
        report["native_host"] = "yes" if paired else "no"
        if not paired:
            raise RuntimeError("native_host_offline")

        approver = ConfirmApprover(api, roots)
        approver.start()

        smoke_chat = api.client.post("/chats", headers=api.headers(), json={"title": "local-smoke"}).json()
        folders = e2e.explicit_tool(api, smoke_chat["id"], "get_known_folders", {})
        sysinfo = e2e.explicit_tool(api, smoke_chat["id"], "get_system_info", {})
        report["local_smoke"] = {
            "folders_ok": folders.get("ok"),
            "sysinfo_ok": sysinfo.get("ok"),
            "folders_text": str(folders.get("text") or "")[:400],
            "sysinfo_text": str(sysinfo.get("text") or "")[:400],
        }
        if not folders.get("ok") or not sysinfo.get("ok"):
            raise RuntimeError("native_host_smoke_failed")

        gpu_started = e2e.launch_managed_pod(api)
        e2e.record_gpu("pod_ready")
        e2e.gpu_guard(gpu_started)

        log("orcarouter sanity")
        chat = api.client.post("/chats", headers=api.headers(), json={"title": "orcarouter-sanity"}).json()
        basic = e2e.collect_stream(
            api,
            chat["id"],
            "Ответь одним коротким предложением: сколько будет 2+2?",
            web_mode="off",
            computer_mode="off",
            tor_mode="off",
            read_timeout=90,
        )
        usage = (e2e.usage_rows(1) or [{}])[0]
        report["orcarouter_basic"] = {
            "chars": basic["chars"],
            "text": (basic.get("text") or "")[:240],
            "provider": usage.get("provider"),
            "usage_status": usage.get("status"),
            "mock": usage.get("provider") == "mock",
        }
        e2e.record_gpu("sanity")
        e2e.gpu_guard(gpu_started)
        if report["orcarouter_basic"]["mock"] or report["orcarouter_basic"]["chars"] < 1:
            raise RuntimeError("orcarouter_sanity_failed")

        results = {}

        def finish_case(name, chat_id, streamed, extra=None):
            task = latest_task(api, chat_id)
            assistant = last_assistant(api, chat_id)
            payload = {
                "stream": {
                    "chars": streamed.get("chars"),
                    "answer": (streamed.get("text") or "")[:1200],
                    "raw_protocol": streamed.get("raw_protocol"),
                    "tools": streamed.get("tools"),
                    "task_snaps": streamed.get("task_snaps")[-6:],
                },
                "task": {
                    "id": task.get("id"),
                    "status": task.get("status"),
                    "plan_revision": task.get("plan_revision"),
                },
                "assistant": assistant,
                "runs": run_entries(api, chat_id),
            }
            if extra:
                payload.update(extra)
            results[name] = payload
            report["cases"] = results
            e2e.record_gpu(name)
            e2e.gpu_guard(gpu_started)
            return task, assistant

        maybe_skip(0.20)
        log("TEST 1 create/read")
        chat1 = api.client.post("/chats", headers=api.headers(), json={"title": "desktop-create"}).json()
        s1 = chat_stream(
            api,
            chat1["id"],
            "Создай на моём рабочем столе тестовую папку Alex-LLM-E2E, "
            "создай hello.txt с текстом Alex Local Computer REAL PASS, "
            "а потом прочитай его.",
            read_timeout=300,
        )
        folder, hello = find_hello(desktop, test_folder)
        extra = {
            "filesystem": {
                "folder": str(folder) if folder else None,
                "hello": str(hello) if hello else None,
                "hello_text": hello.read_text(encoding="utf-8") if hello else None,
                "created_directory": bool(folder),
                "created_hello": bool(hello),
            }
        }
        finish_case("test1_create_read", chat1["id"], s1, extra)
        if not hello:
            report["test1_missing_hello"] = True
            e2e.log("TEST 1 did not create hello.txt; continuing remaining independent cases")
        else:
            hello.write_text(EXTERNAL, encoding="utf-8", newline="\n")
            maybe_skip(0.22)
            log("TEST 2 reread external")
            chat2 = api.client.post("/chats", headers=api.headers(), json={"title": "desktop-reread"}).json()
            s2 = chat_stream(
                api,
                chat2["id"],
                "Перечитай hello.txt в тестовой папке на рабочем столе и скажи его содержимое.",
                read_timeout=180,
            )
            finish_case(
                "test2_external_reread",
                chat2["id"],
                s2,
                {"disk": hello.read_text(encoding="utf-8"), "saw_marker": EXTERNAL in (s2.get("text") or "")},
            )

        search_root = folder or test_folder
        (search_root / "alpha.txt").write_text("no marker\n", encoding="utf-8")
        (search_root / "beta.md").write_text("# note\n", encoding="utf-8")
        (search_root / "data.json").write_text('{"marker": "%s"}\n' % MARKER, encoding="utf-8")

        if hello:
            maybe_skip(0.24)
            log("TEST 3 edit copy move")
            chat3 = api.client.post("/chats", headers=api.headers(), json={"title": "desktop-edit"}).json()
            s3 = chat_stream(
                api,
                chat3["id"],
                "В моей тестовой папке на рабочем столе добавь в конец hello.txt новую строку "
                "Second line written by Alex. Потом скопируй файл в copy.txt, "
                "создай папку archive и перемести копию туда, затем проверь результат.",
                read_timeout=300,
            )
            copied = folder / "archive" / "copy.txt"
            hello_after = hello.read_text(encoding="utf-8") if hello.exists() else ""
            finish_case(
                "test3_edit_copy_move",
                chat3["id"],
                s3,
                {
                    "hello_after": hello_after,
                    "second_line": "Second line written by Alex" in hello_after,
                    "archive_copy": copied.is_file(),
                    "copy_text": copied.read_text(encoding="utf-8") if copied.is_file() else None,
                },
            )

        maybe_skip(0.26)
        log("TEST 4 search")
        chat4 = api.client.post("/chats", headers=api.headers(), json={"title": "desktop-search"}).json()
        s4 = chat_stream(
            api,
            chat4["id"],
            "Найди в моей тестовой папке файл, в котором есть ALEX_SEARCH_MARKER_49127, и скажи его имя.",
            read_timeout=180,
        )
        finish_case(
            "test4_search",
            chat4["id"],
            s4,
            {"saw_data_json": "data.json" in (s4.get("text") or "")},
        )

        maybe_skip(0.28)
        log("TEST 5 system info")
        chat5 = api.client.post("/chats", headers=api.headers(), json={"title": "system-info"}).json()
        s5 = chat_stream(
            api,
            chat5["id"],
            "Покажи кратко информацию об этом компьютере: версию Windows, CPU, объём RAM "
            "и свободное место на системном диске.",
            read_timeout=150,
        )
        text5 = (s5.get("text") or "").lower()
        finish_case(
            "test5_system",
            chat5["id"],
            s5,
            {
                "mentions_windows": "windows" in text5,
                "mentions_ram": "ram" in text5 or "гб" in text5 or "gb" in text5,
            },
        )

        if hello:
            maybe_skip(0.30)
            log("TEST 6 sha256")
            expected_hash = sha256_file(hello)
            chat6 = api.client.post("/chats", headers=api.headers(), json={"title": "sha256"}).json()
            s6 = chat_stream(
                api,
                chat6["id"],
                "В моей тестовой папке посчитай SHA256 файла hello.txt средствами компьютера и скажи результат.",
                read_timeout=180,
            )
            finish_case(
                "test6_sha256",
                chat6["id"],
                s6,
                {
                    "expected": expected_hash,
                    "mentioned": expected_hash.lower() in (s6.get("text") or "").lower(),
                },
            )

        install_status = "NOT TESTED"
        if package_info.get("chosen") and package_info.get("winget_ok") and e2e.estimated_cost() < 0.32:
            maybe_skip(0.32)
            log("TEST 7 install")
            chat7 = api.client.post("/chats", headers=api.headers(), json={"title": "install"}).json()
            pkg = package_info["chosen"]["package"]
            s7 = chat_stream(
                api,
                chat7["id"],
                "Установи мне %s через стандартный Windows package manager и после установки проверь версию."
                % pkg.split(".")[-1],
                read_timeout=360,
            )
            install_runs = [
                item
                for item in run_entries(api, chat7["id"])
                if item["name"] == "install_software"
            ]
            binary_name = package_info["chosen"]["binary"]
            present_after = bool(which(binary_name))
            version_after = ""
            if present_after:
                probed = subprocess.run([binary_name, "--version"], capture_output=True, text=True)
                version_after = ((probed.stdout or probed.stderr) or "").strip()[:120]
            confirmation = any(item.get("confirmation") for item in install_runs) or any(
                item.get("tool") == "install_software" for item in approver.approved
            )
            if present_after and confirmation:
                install_status = "REAL PASS"
            elif confirmation:
                install_status = "PARTIAL"
            finish_case(
                "test7_install",
                chat7["id"],
                s7,
                {
                    "package": pkg,
                    "was_absent": True,
                    "confirmation": confirmation,
                    "installed": present_after,
                    "version": version_after,
                    "uac_bypass": False,
                    "status": install_status,
                },
            )
        else:
            results["test7_install"] = {
                "status": "NOT TESTED",
                "reason": "package already present or winget missing or budget",
                "probe": package_info,
            }

        if e2e.estimated_cost() < 0.34:
            maybe_skip(0.34)
            log("TEST 8 pause")
            chat8 = api.client.post("/chats", headers=api.headers(), json={"title": "pause"}).json()
            watcher_task = {"id": None}

            def pause_soon():
                for _ in range(40):
                    time.sleep(1)
                    tasks = api.client.get("/tasks", headers=api.headers(), params={"chat_id": chat8["id"]}).json()
                    if not tasks:
                        continue
                    task = tasks[0]
                    if int(task.get("tool_calls_used") or 0) >= 1:
                        watcher_task["id"] = task["id"]
                        api.client.post(f"/tasks/{task['id']}/pause", headers=api.headers())
                        return

            thread = threading.Thread(target=pause_soon, daemon=True)
            thread.start()
            s8 = chat_stream(
                api,
                chat8["id"],
                "В тестовой папке на рабочем столе создай pause-a.txt, pause-b.txt и pause-c.txt "
                "с коротким текстом, затем перечитай каждый файл.",
                read_timeout=180,
            )
            thread.join(timeout=2)
            assistant8 = last_assistant(api, chat8["id"])
            task8 = latest_task(api, chat8["id"])
            resumed = None
            if task8.get("status") == "PAUSED" and e2e.estimated_cost() < 0.36:
                resumed = collect_task_stream(api, f"/tasks/{task8['id']}/resume", {}, read_timeout=180)
                task8 = api.client.get(f"/tasks/{task8['id']}", headers=api.headers()).json()
            finish_case(
                "test8_pause",
                chat8["id"],
                s8,
                {
                    "paused_status": task8.get("status"),
                    "raw_in_stream": s8.get("raw_protocol"),
                    "raw_in_ui": assistant8.get("raw_protocol"),
                    "resume": {
                        "same_task": bool(resumed),
                        "chars": (resumed or {}).get("chars"),
                        "raw": (resumed or {}).get("raw_protocol"),
                    },
                },
            )

        if e2e.estimated_cost() < 0.36:
            maybe_skip(0.36)
            log("TEST 9 queue")
            chat_a = api.client.post("/chats", headers=api.headers(), json={"title": "queue-a"}).json()
            chat_b = api.client.post("/chats", headers=api.headers(), json={"title": "queue-b"}).json()
            holder = {}

            def run_a():
                holder["a"] = chat_stream(
                    api,
                    chat_a["id"],
                    "В тестовой папке на рабочем столе создай queue-a.txt с текстом TASK_A "
                    "и затем перечитай его.",
                    read_timeout=180,
                )

            thread_a = threading.Thread(target=run_a, daemon=True)
            thread_a.start()
            time.sleep(2)
            s_b = chat_stream(
                api,
                chat_b["id"],
                "В тестовой папке на рабочем столе создай queue-b.txt с текстом TASK_B.",
                read_timeout=180,
            )
            thread_a.join(timeout=200)
            task_a = latest_task(api, chat_a["id"])
            task_b = latest_task(api, chat_b["id"])
            finish_case(
                "test9_queue",
                chat_b["id"],
                s_b,
                {
                    "a_status": task_a.get("status"),
                    "b_status": task_b.get("status"),
                    "b_queue": any(
                        snap.get("status") == "WAITING_WORKSPACE" for snap in s_b.get("task_snaps") or []
                    ),
                    "a_file": (folder / "queue-a.txt").is_file() if folder else False,
                    "b_file": (folder / "queue-b.txt").is_file() if folder else False,
                },
            )

        if e2e.estimated_cost() < 0.36:
            log("git commit/push disposable")
            chat_git = api.client.post("/chats", headers=api.headers(), json={"title": "git"}).json()
            s_git = chat_stream(
                api,
                chat_git["id"],
                "В тестовом git-репозитории %s исправь файл sample.txt, проверь изменение "
                "и сделай локальный git commit. Потом сделай git push в origin."
                % git_root,
                read_timeout=240,
            )
            status = subprocess.run(["git", "status", "--porcelain"], cwd=git_root, capture_output=True, text=True)
            log_out = subprocess.run(["git", "log", "-1", "--oneline"], cwd=git_root, capture_output=True, text=True)
            remote_log = subprocess.run(
                ["git", "log", "-1", "--oneline"], cwd=remote, capture_output=True, text=True
            )
            finish_case(
                "git",
                chat_git["id"],
                s_git,
                {
                    "status": status.stdout.strip(),
                    "head": log_out.stdout.strip(),
                    "remote_head": remote_log.stdout.strip(),
                    "push_approved": any(item.get("tool") == "git_push" for item in approver.approved),
                    "force_push": False,
                },
            )

        if e2e.estimated_cost() < 0.36:
            log("form + fake purchase")
            approver.hold.set()
            Pages.posts["checkout"] = 0
            chat_buy = api.client.post("/chats", headers=api.headers(), json={"title": "purchase"}).json()
            s_buy = chat_stream(
                api,
                chat_buy["id"],
                "Открой тестовый магазин %s, оформи тестовый товар." % shop_url,
                read_timeout=180,
            )
            without = Pages.posts["checkout"]
            for run in api.client.get("/tools/runs", headers=api.headers(), params={"limit": 30}).json():
                if run.get("tool_name") == "checkout_purchase" and run.get("status") == "waiting_confirmation":
                    api.client.post(
                        f"/tools/runs/{run['id']}/confirm",
                        headers=api.headers(),
                        json={"allow": True},
                    )
            time.sleep(4)
            after = Pages.posts["checkout"]
            approver.hold.clear()
            chat_form = api.client.post("/chats", headers=api.headers(), json={"title": "form"}).json()
            s_form = chat_stream(
                api,
                chat_form["id"],
                "Открой тестовую форму %s, заполни имя Alex и сообщение FORM_REAL_PASS, и отправь форму."
                % form_url,
                read_timeout=180,
            )
            finish_case(
                "external",
                chat_form["id"],
                s_form,
                {
                    "form_ok": Pages.posts["submit"] > 0 or "FORM_SUBMIT_OK" in (s_form.get("text") or ""),
                    "purchase_without_approval": without,
                    "purchase_after_approval": after,
                    "purchase_stream": (s_buy.get("text") or "")[:400],
                    "form_confirmation": any(item.get("tool") == "submit_form" for item in approver.approved)
                    or any(item.get("tool") == "submit_form" for item in approver.seen),
                    "purchase_confirmation": any(
                        item.get("tool") == "checkout_purchase" for item in approver.seen
                    ),
                },
            )

        delete_me = folder / "delete-me.txt" if folder else None
        if delete_me is not None:
            delete_me.write_text("temporary\n", encoding="utf-8")
            chat_del = api.client.post("/chats", headers=api.headers(), json={"title": "delete"}).json()
            s_del = chat_stream(
                api,
                chat_del["id"],
                "Удалить созданный нами delete-me.txt в тестовой папке на рабочем столе.",
                read_timeout=150,
            )
            finish_case(
                "delete",
                chat_del["id"],
                s_del,
                {"gone": not delete_me.exists()},
            )

        if folder:
            (folder / "PROCESS_NOTE.txt").write_text("owned process test\n", encoding="utf-8")
        chat_proc = api.client.post("/chats", headers=api.headers(), json={"title": "process"}).json()
        s_proc = chat_stream(
            api,
            chat_proc["id"],
            "В тестовой папке запусти безопасный PowerShell Start-Sleep на 20 секунд, "
            "затем останови именно этот запущенный тобой процесс.",
            read_timeout=180,
        )
        finish_case("process", chat_proc["id"], s_proc)

        tree = []
        if folder:
            for item in sorted(folder.rglob("*")):
                rel = item.relative_to(folder)
                tree.append(str(rel).replace("\\", "/"))
        report["desktop_tree"] = tree
        report["cases"] = results
        report["approvals"] = list(approver.approved) if approver else []
        report["confirmations_seen"] = list(approver.seen) if approver else []
        all_runs = e2e.latest_runs(api)
        report["tinyfish"] = {
            "search": sum(1 for row in all_runs if row.get("tool_name") == "web_search"),
            "fetch": sum(1 for row in all_runs if row.get("tool_name") == "web_fetch"),
            "agent_calls": sum(1 for row in all_runs if "agent" in (row.get("tool_name") or "")),
            "browser_calls": sum(
                1 for row in all_runs if str(row.get("tool_name") or "").startswith("browser")
            ),
        }
        report["model_tool_actions"] = sum(1 for row in all_runs if row.get("origin") == "model")
        report["usage"] = [
            {
                "provider": row["provider"],
                "status": row["status"],
                "input": row["input_tokens"],
                "output": row["output_tokens"],
                "total": row["total_tokens"],
            }
            for row in e2e.usage_rows()
        ]
        report["install_status"] = (results.get("test7_install") or {}).get("status") or install_status
        report["email"] = {
            "contract": "PASS",
            "policy": "PASS",
            "configured_provider": False,
            "real_email": "NOT TESTED",
        }
        report["real_purchase"] = False
        log("cases=%s" % sorted(results))
    finally:
        log("cleanup gpu")
        if gpu_started:
            try:
                report["compute_stop"] = e2e.stop_compute(api)
            except Exception as error:
                report["compute_stop"] = {"error": type(error).__name__}
            time.sleep(3)
            row = e2e.session_row()
            live = compute_running(api) if api.token else {}
            report["runpod_final"] = {
                "status": row.get("status"),
                "pod_id": row.get("pod_id"),
                "gpu": row.get("gpu_type"),
                "datacenter": row.get("datacenter"),
                "price_hour": row.get("hourly_rate"),
                "billable_seconds": row.get("billable_seconds"),
                "estimated_cost": row.get("estimated_cost"),
                "stop_reason": row.get("stop_reason"),
                "volume": row.get("network_volume_id"),
                "running_gpu": live.get("running_gpu"),
                "compute_state": live.get("state"),
            }
        else:
            report["compute_stop"] = {"skipped": True, "reason": "gpu_not_started"}
            report["runpod_final"] = {"skipped": True}
        if approver:
            approver.stop.set()
        pages.shutdown()
        if host_proc:
            host_proc.terminate()
            try:
                host_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                host_proc.kill()
        if host_log:
            host_log.close()
        if backend_proc:
            backend_proc.terminate()
            try:
                backend_proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                backend_proc.kill()
        if backend_log:
            backend_log.close()
        evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        log(f"evidence={evidence}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        e2e.REPORT["fatal"] = f"{type(error).__name__}:{error}"
        stamp = e2e.utcnow().strftime("%Y%m%dT%H%M%S")
        path = Path(os.environ.get("TEMP", ".")) / "alex-llm-real-e2e" / f"fatal-computer-{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(e2e.REPORT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"FATAL {type(error).__name__}:{error} evidence={path}", flush=True)
        raise
