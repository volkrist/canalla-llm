"""Alex LLM 0.9.3 local native-host: process start/stop and WRITE queue auto-resume. Mock LLM."""

from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("real_e2e_08", SCRIPTS / "real_e2e_08.py")
if spec is None or spec.loader is None:
    raise SystemExit("cannot load the real_e2e_08.py harness")
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)

FOLDERID_DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"


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


def latest_tasks(api):
    return api.client.get("/tools/tasks", headers=api.headers(), params={"limit": 20}).json()


def main():
    stamp = e2e.utcnow().strftime("%Y%m%dT%H%M%S")
    work = Path(os.environ["TEMP"]) / "alex-llm-real-e2e" / f"local-093-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    evidence = work / "evidence.json"
    report = e2e.REPORT
    report.update({"version": "0.9.3-local-native", "workspace": str(work), "mock": True})
    log = e2e.log
    backend_proc = None
    backend_log = None
    host_proc = None
    host_log = None
    api = e2e.Api()
    desktop = known_folder(FOLDERID_DESKTOP)
    test_folder = desktop / f"Alex-LLM-E2E-093-local-{stamp}"
    test_folder.mkdir(parents=True, exist_ok=True)
    report["test_folder"] = str(test_folder)
    try:
        values = e2e.env_file_map()
        os.environ["DATABASE_URL"] = values.get("DATABASE_URL") or "sqlite:///./alex.db"
        e2e.kill_port_8000()
        time.sleep(1)
        os.environ["COMPUTE_BACKGROUND_ENABLED"] = "false"
        os.environ["ALLOW_USER_COMPUTE_START"] = "false"
        os.environ["LLM_PROVIDER"] = "mock"
        backend_proc, backend_log, _ = e2e.start_backend(dummy_discovered(), 9050, skip_gpu=True, session_budget="0.10")
        health = e2e.wait_health()
        report["backend_health"] = health
        if health.get("provider") != "mock":
            raise RuntimeError("backend_not_mock")
        email = f"local-093-{stamp}@example.com"
        password = "e2e-password-" + hashlib.sha256(stamp.encode()).hexdigest()[:12]
        api.register(email, password)
        roots = [str(desktop), str(test_folder)]
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
                "device_display_name": "E2E native host 093",
            },
        )
        host_env = os.environ.copy()
        host_env.update(
            {
                "ALEX_BACKEND_URL": "http://127.0.0.1:8000",
                "ALEX_TOKEN": api.token,
                "ALEX_DEVICE_NAME": "E2E native host 093",
                "ALEX_WORKSPACE_ROOTS": ";".join(roots),
                "ALEX_DEVICE_DIR": str(work / "device"),
                "ALEX_DEVICE_CREDENTIAL_TARGET": "Alex LLM/e2e-device-credential-093",
                "ALEX_PYTHON": str(e2e.BACKEND / ".venv" / "Scripts" / "python.exe"),
            }
        )
        (work / "device").mkdir(parents=True, exist_ok=True)
        host_log = open(work / "host.log", "ab")
        host_proc = subprocess.Popen(
            [str(host_bin())],
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
                report["device"] = {"online": True, "id_prefix": (devices[0].get("device_id") or "")[:8]}
                break
            time.sleep(0.5)
        report["native_host"] = "yes" if paired else "no"
        if not paired:
            raise RuntimeError("native_host_offline")

        chat = api.client.post("/chats", headers=api.headers(), json={"title": "093-process"}).json()
        started = e2e.collect_stream(
            api,
            chat["id"],
            "Запусти безопасный процесс python sleep на 30 секунд",
            web_mode="off",
            computer_mode="trusted",
            read_timeout=90,
        )
        runs = e2e.latest_runs(api, chat["id"])
        python_runs = [row for row in runs if row.get("tool_name") == "run_python"]
        first_meta = (python_runs[0].get("result_metadata") if python_runs else {}) or {}
        host = first_meta.get("host_result") or {}
        pid = host.get("pid") or first_meta.get("pid")
        if not pid:
            match = re.search(r"pid=(\d+)", str(started.get("text") or "") + str(host.get("text") or ""))
            pid = int(match.group(1)) if match else None
        report["process_start"] = {
            "answer": (started.get("text") or "")[:400],
            "tools": [row.get("tool_name") for row in runs],
            "pid": pid,
            "wait": (first_meta.get("host_args") or host.get("host_args") or {}).get("wait"),
            "host_text": str(host.get("text") or "")[:200],
        }
        stop = e2e.collect_stream(
            api,
            chat["id"],
            "Останови процесс, который ты только что запустила.",
            web_mode="off",
            computer_mode="trusted",
            read_timeout=90,
        )
        stop_runs = e2e.latest_runs(api, chat["id"])
        report["process_stop"] = {
            "answer": (stop.get("text") or "")[:300],
            "tools": [row.get("tool_name") for row in stop_runs],
            "verified_dead": any(
                ((row.get("result_metadata") or {}).get("host_result") or {}).get("verified_dead")
                for row in stop_runs
                if row.get("tool_name") == "stop_process"
            ),
        }

        chat_a = api.client.post("/chats", headers=api.headers(), json={"title": "093-queue-a"}).json()
        chat_b = api.client.post("/chats", headers=api.headers(), json={"title": "093-queue-b"}).json()
        a_holder = {}

        def run_a():
            a_holder["stream"] = e2e.collect_stream(
                api,
                chat_a["id"],
                f"Создай файл {test_folder / 'queue-a.txt'} с текстом QUEUE_A",
                web_mode="off",
                computer_mode="trusted",
                read_timeout=90,
            )

        thread = threading.Thread(target=run_a, daemon=True)
        thread.start()
        time.sleep(0.8)
        b_stream = e2e.collect_stream(
            api,
            chat_b["id"],
            f"Создай файл {test_folder / 'queue-b.txt'} с текстом QUEUE_B",
            web_mode="off",
            computer_mode="trusted",
            read_timeout=120,
        )
        thread.join(timeout=90)
        for _ in range(40):
            if (test_folder / "queue-b.txt").is_file():
                break
            time.sleep(0.5)
        report["queue"] = {
            "a_exists": (test_folder / "queue-a.txt").is_file(),
            "a_text": (test_folder / "queue-a.txt").read_text(encoding="utf-8")
            if (test_folder / "queue-a.txt").is_file()
            else "",
            "b_exists": (test_folder / "queue-b.txt").is_file(),
            "b_text": (test_folder / "queue-b.txt").read_text(encoding="utf-8")
            if (test_folder / "queue-b.txt").is_file()
            else "",
            "b_answer": (b_stream.get("text") or "")[:300],
            "waiting": "очеред" in (b_stream.get("text") or "").casefold()
            or "WAITING" in (b_stream.get("text") or ""),
            "desktop_test_leak": (desktop / "тест" / "queue-b.txt").exists(),
        }
        report["verdict"] = {
            "native_host": report["native_host"],
            "process_started": bool(pid),
            "process_stopped": bool(report["process_stop"]["verified_dead"]),
            "queue_a": report["queue"]["a_text"] == "QUEUE_A",
            "queue_b": report["queue"]["b_text"] == "QUEUE_B",
            "desktop_leak": report["queue"]["desktop_test_leak"],
        }
        log("VERDICT %s" % report["verdict"])
        if not report["verdict"]["process_started"] or not report["verdict"]["process_stopped"]:
            raise RuntimeError("process_native_failed")
        if not report["verdict"]["queue_a"] or not report["verdict"]["queue_b"]:
            raise RuntimeError("queue_native_failed:%s" % report["queue"])
    finally:
        if host_proc:
            host_proc.terminate()
            try:
                host_proc.wait(timeout=5)
            except Exception:
                host_proc.kill()
        if host_log:
            host_log.close()
        if backend_proc:
            backend_proc.terminate()
            try:
                backend_proc.wait(timeout=8)
            except Exception:
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
        path = Path(os.environ.get("TEMP", ".")) / "alex-llm-real-e2e" / f"fatal-093-local-{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(e2e.REPORT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"FATAL {type(error).__name__}:{error} evidence={path}", flush=True)
        raise
