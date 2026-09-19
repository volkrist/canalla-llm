"""Alex LLM 0.9.3 GPU: TinyFish Browser routing + grounded local answers. One L40S, hard $0.25."""

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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("real_e2e_08", SCRIPTS / "real_e2e_08.py")
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)

e2e.SESSION_BUDGET = 0.25
e2e.GPU_COST_STOP = 0.22
e2e.GPU_WALL = 12 * 60
e2e.MAX_HOURLY = 1.10
e2e.CATALOG_WAIT = 25 * 60

FOLDERID_DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"
MARKER = "ALEX_SEARCH_MARKER_49127"
PAID = {"web_agent", "web_browser", "browser_start", "browser_read", "browser_write", "web_agent_read"}
PROMPT_BROWSER = (
    "Открой в браузере официальный сайт Python, прочитай заголовок, "
    "перейди по ссылке на документацию и скажи заголовок следующей страницы."
)
PROMPT_MATH = "Сколько будет 2+2?"


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
            }
            for item in options
            if item.get("id") in {"NVIDIA L40S", "NVIDIA A40", "NVIDIA RTX 6000 Ada"}
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
    raise RuntimeError("no_l40s_after_wait")


e2e.wait_for_selectable_gpu = wait_l40s_only
_orig_compatible = e2e.compatible_gpus


def compatible_gpus_l40s(options):
    return [item for item in _orig_compatible(options) if item.get("id") == "NVIDIA L40S"]


e2e.compatible_gpus = compatible_gpus_l40s


def compute_running(api):
    try:
        status = api.client.get("/compute/status", headers=api.headers()).json()
    except Exception as error:
        return {"error": type(error).__name__}
    session = status.get("session") or {}
    state = status.get("state")
    running = state not in {None, "stopped", "idle", "not_configured", "error"} and bool(session.get("pod_id"))
    return {"state": state, "pod_id": session.get("pod_id"), "running_gpu": 1 if running else 0}


def sources_for(api, chat_id):
    messages = api.client.get(f"/chats/{chat_id}/messages", headers=api.headers()).json()
    last = [row for row in messages if row.get("role") == "assistant"]
    if not last:
        return [], ""
    snaps = api.client.get(f"/messages/{last[-1]['id']}/web-sources", headers=api.headers()).json()
    return snaps, last[-1].get("content") or ""


def summarize_runs(runs):
    rows = []
    for run in runs:
        meta = run.get("result_metadata") or {}
        host = meta.get("host_result") or {}
        rows.append(
            {
                "name": run.get("tool_name"),
                "origin": run.get("origin"),
                "status": run.get("status"),
                "error": run.get("error_code"),
                "estimated_cost": run.get("estimated_cost") or meta.get("estimated_provider_cost"),
                "retrieval": meta.get("retrieval") or host.get("retrieval"),
                "pid": host.get("pid") or meta.get("pid"),
            }
        )
    return rows


def wait_model(api, started):
    for _ in range(120):
        e2e.gpu_guard(started)
        status = api.client.get("/compute/status", headers=api.headers()).json()
        llm = api.client.get("/llm/status", headers=api.headers()).json()
        if status.get("state") in {"ready", "generating"} and llm.get("state") == "ready":
            return {"status": status, "llm": llm}
        time.sleep(2)
    raise RuntimeError("model_not_ready_after_pod")


def wallet_snapshot():
    sys.path.insert(0, str(e2e.BACKEND))
    cwd = os.getcwd()
    os.chdir(e2e.BACKEND)
    try:
        from app.tools.tinyfish.client import WALLET, TinyFishClient

        client = TinyFishClient()

        async def _read():
            value = await client.request("GET", WALLET, retry=False, timeout=20)
            return {
                "available_balance": value.get("available_balance"),
                "currency": value.get("currency"),
                "auto_reload_state": ((value.get("auto_reload") or {}) if isinstance(value.get("auto_reload"), dict) else value.get("auto_reload")),
            }

        import asyncio

        return asyncio.run(_read())
    finally:
        os.chdir(cwd)


def run_prompt(api, title, prompt, web_mode="off", computer_mode="trusted", timeout=180):
    chat = api.client.post("/chats", headers=api.headers(), json={"title": title}).json()
    stream = e2e.collect_stream(
        api,
        chat["id"],
        prompt,
        web_mode=web_mode,
        computer_mode=computer_mode,
        tor_mode="off",
        read_timeout=timeout,
    )
    runs = e2e.latest_runs(api, chat["id"])
    snaps, answer = sources_for(api, chat["id"])
    return {
        "chat_id": chat["id"],
        "prompt": prompt,
        "chars": stream["chars"],
        "answer": answer or stream.get("text") or "",
        "tools": summarize_runs(runs),
        "tool_names": [row.get("tool_name") for row in runs],
        "paid_tools": [row.get("tool_name") for row in runs if row.get("tool_name") in PAID],
        "origins": sorted({(row.get("tool_name"), row.get("origin")) for row in runs}),
        "sources": [
            {
                "label": row.get("label"),
                "kind": row.get("kind"),
                "url": row.get("final_url") or row.get("url"),
                "title": (row.get("title") or "")[:160],
                "retrieval": (row.get("details") or {}).get("retrieval"),
            }
            for row in snaps
        ],
    }


def estimated_cost():
    row = e2e.session_row(active_only=True) or e2e.session_row()
    return float((row or {}).get("estimated_cost") or 0)


def main():
    stamp = e2e.utcnow().strftime("%Y%m%dT%H%M%S")
    work = Path(os.environ["TEMP"]) / "alex-llm-real-e2e" / f"gpu-093-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    evidence = work / "evidence.json"
    report = e2e.REPORT
    report.update({"version": "0.9.3", "workspace": str(work), "mock": False, "cursor_executed_task": False})
    log = e2e.log
    backend_proc = None
    backend_log = None
    host_proc = None
    host_log = None
    api = e2e.Api()
    gpu_started = None
    desktop = known_folder(FOLDERID_DESKTOP)
    test_folder = desktop / f"Alex-LLM-E2E-093-{stamp}"
    test_folder.mkdir(parents=True, exist_ok=True)
    (test_folder / "alpha.txt").write_text("nope", encoding="utf-8")
    (test_folder / "beta.md").write_text("nope", encoding="utf-8")
    (test_folder / "data.json").write_text('{"k":"%s"}' % MARKER, encoding="utf-8")
    report["test_folder"] = str(test_folder)
    try:
        values = e2e.env_file_map()
        report["config"] = {
            "runpod_key": e2e.secret_present(values, "RUNPOD_API_KEY"),
            "llm_key": e2e.secret_present(values, "LLM_API_KEY"),
            "tinyfish_key": e2e.secret_present(values, "TINYFISH_API_KEY")
            or bool(os.environ.get("TINYFISH_API_KEY")),
            "jwt": e2e.secret_present(values, "JWT_SECRET"),
        }
        if not report["config"]["runpod_key"] or not report["config"]["llm_key"]:
            raise SystemExit("missing compute credentials")
        os.environ["DATABASE_URL"] = values.get("DATABASE_URL") or "sqlite:///./alex.db"
        e2e.kill_port_8000()
        time.sleep(1)
        os.environ["COMPUTE_BACKGROUND_ENABLED"] = "true"
        os.environ["ALLOW_USER_COMPUTE_START"] = "true"
        os.environ["LLM_PROVIDER"] = "llamacpp"
        backend_proc, backend_log, _ = e2e.start_backend(dummy_discovered(), 9050, session_budget="0.25")
        health = e2e.wait_health()
        report["backend_health"] = health
        if health.get("provider") != "llamacpp":
            raise RuntimeError("backend_not_llamacpp")
        email = f"gpu-093-{stamp}@example.com"
        password = "e2e-password-" + hashlib.sha256(stamp.encode()).hexdigest()[:12]
        api.register(email, password)
        api.client.post("/compute/search/cancel", headers=api.headers())
        live = compute_running(api)
        report["compute_before"] = live
        if live.get("running_gpu"):
            raise RuntimeError("existing_gpu_must_not_start_second_pod:%s" % live)
        try:
            e2e.stop_compute(api)
        except Exception:
            pass
        roots = [str(desktop), str(test_folder)]
        api.client.put(
            "/tools/preferences",
            headers=api.headers(),
            json={
                "search_enabled": True,
                "fetch_enabled": True,
                "default_mode": "on",
                "agent_mode": "auto",
                "browser_mode": "auto",
                "agent_enabled": True,
                "browser_enabled": True,
                "browser_max_sessions": 1,
                "browser_max_minutes": 4,
                "tinyfish_paid_task_budget_usd": 0.20,
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
                "ALEX_DEVICE_CREDENTIAL_TARGET": "Alex LLM/e2e-device-credential-093-gpu",
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
                break
            time.sleep(0.5)
        report["native_host"] = "yes" if paired else "no"
        if not paired:
            raise RuntimeError("native_host_offline")
        try:
            report["wallet_before"] = wallet_snapshot()
        except Exception as error:
            report["wallet_before"] = {"error": type(error).__name__}

        gpu_started = e2e.launch_managed_pod(api)
        e2e.record_gpu("pod_ready")
        wait_model(api, gpu_started)
        e2e.record_gpu("model_ready")

        sanity = run_prompt(api, "093-sanity", PROMPT_MATH, web_mode="off", computer_mode="off", timeout=90)
        usage = (e2e.usage_rows(1) or [{}])[0]
        sanity["provider"] = usage.get("provider")
        sanity["mock"] = usage.get("provider") == "mock"
        report["orcarouter_basic"] = {"chars": sanity["chars"], "provider": sanity["provider"], "mock": sanity["mock"]}
        if sanity["mock"] or sanity["chars"] < 1:
            raise RuntimeError("orcarouter_sanity_failed")
        e2e.gpu_guard(gpu_started)

        log("GPU TEST A browser")
        report["test_browser"] = run_prompt(
            api, "093-browser", PROMPT_BROWSER, web_mode="on", computer_mode="off", timeout=150
        )
        e2e.record_gpu("browser")
        e2e.gpu_guard(gpu_started)

        log("GPU TEST B grounded file")
        prompt_file = (
            f"Создай файл {test_folder / 'grounded.txt'} с текстом GROUNDING_REAL_PASS, "
            "прочитай его и скажи, что именно записано в файле."
        )
        report["test_file"] = run_prompt(
            api, "093-file", prompt_file, web_mode="off", computer_mode="trusted", timeout=120
        )
        grounded = test_folder / "grounded.txt"
        report["test_file"]["file_exists"] = grounded.is_file()
        report["test_file"]["file_text"] = grounded.read_text(encoding="utf-8") if grounded.is_file() else ""
        report["test_file"]["answer_has_marker"] = "GROUNDING_REAL_PASS" in (report["test_file"]["answer"] or "")
        e2e.record_gpu("file")
        e2e.gpu_guard(gpu_started)

        log("GPU TEST C hash")
        independent = sha256_file(grounded) if grounded.is_file() else ""
        report["test_hash"] = run_prompt(
            api,
            "093-hash",
            f"Посчитай SHA256 {grounded} и скажи только фактический hash.",
            web_mode="off",
            computer_mode="trusted",
            timeout=90,
        )
        report["test_hash"]["independent"] = independent
        report["test_hash"]["answer_has_digest"] = independent.lower() in (report["test_hash"]["answer"] or "").lower()
        e2e.record_gpu("hash")
        e2e.gpu_guard(gpu_started)

        log("GPU TEST D search")
        report["test_search"] = run_prompt(
            api,
            "093-search",
            f"Найди в этой папке файл, в котором есть {MARKER}, и скажи имя файла. Папка: {test_folder}",
            web_mode="off",
            computer_mode="trusted",
            timeout=120,
        )
        report["test_search"]["answer_has_file"] = "data.json" in (report["test_search"]["answer"] or "")
        report["test_search"]["tool_count"] = len(report["test_search"]["tool_names"])
        report["test_search"]["helper_files"] = [
            item.name
            for item in desktop.glob("alex_*")
            if item.is_file() and stamp in item.name
        ]
        e2e.record_gpu("search")

        cost = estimated_cost()
        report["cost_after_d"] = cost
        if cost < 0.18:
            log("GPU TEST E queue")
            queue_a = api.client.post("/chats", headers=api.headers(), json={"title": "093-queue-a"}).json()
            queue_b = api.client.post("/chats", headers=api.headers(), json={"title": "093-queue-b"}).json()
            a_holder = {}

            def run_a():
                a_holder["stream"] = e2e.collect_stream(
                    api,
                    queue_a["id"],
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
                queue_b["id"],
                f"Создай файл {test_folder / 'queue-b.txt'} с текстом QUEUE_B",
                web_mode="off",
                computer_mode="trusted",
                read_timeout=120,
            )
            thread.join(timeout=90)
            for _ in range(30):
                if (test_folder / "queue-b.txt").is_file():
                    break
                time.sleep(0.5)
            report["test_queue"] = {
                "a": (test_folder / "queue-a.txt").read_text(encoding="utf-8")
                if (test_folder / "queue-a.txt").is_file()
                else "",
                "b": (test_folder / "queue-b.txt").read_text(encoding="utf-8")
                if (test_folder / "queue-b.txt").is_file()
                else "",
                "b_answer": (b_stream.get("text") or "")[:240],
            }
            e2e.record_gpu("queue")
        else:
            report["test_queue"] = {"skipped": True, "reason": "cost_near_cap", "cost": cost}

        try:
            report["wallet_after"] = wallet_snapshot()
        except Exception as error:
            report["wallet_after"] = {"error": type(error).__name__}

        browser = report["test_browser"]
        report["verdict"] = {
            "mock": False,
            "browser_selected": "web_browser" in browser["paid_tools"],
            "browser_search_instead": bool({"web_search", "web_fetch"} & set(browser["tool_names"]))
            and "web_browser" not in browser["paid_tools"],
            "file_pass": report["test_file"].get("answer_has_marker") and report["test_file"].get("file_text")
            == "GROUNDING_REAL_PASS",
            "hash_pass": report["test_hash"].get("answer_has_digest"),
            "search_pass": report["test_search"].get("answer_has_file")
            and report["test_search"].get("tool_count", 99) <= 5,
            "queue": report.get("test_queue"),
            "cursor_executed_task": False,
        }
        log("VERDICT %s" % report["verdict"])
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
            }
        else:
            report["runpod_final"] = {"skipped": True}
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
        path = Path(os.environ.get("TEMP", ".")) / "alex-llm-real-e2e" / f"fatal-093-{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(e2e.REPORT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"FATAL {type(error).__name__}:{error} evidence={path}", flush=True)
        raise
