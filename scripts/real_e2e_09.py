"""Alex LLM 0.9 GPU: autonomous task agent. One L40S Pod, hard $0.45."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("real_e2e_08", SCRIPTS / "real_e2e_08.py")
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)

e2e.SESSION_BUDGET = 0.45
e2e.GPU_COST_STOP = 0.42
e2e.GPU_WALL = 30 * 60
e2e.MAX_HOURLY = 1.10
e2e.CATALOG_WAIT = 25 * 60

NATURAL_PROMPT = (
    "Полностью проверь этот тестовый проект.\n"
    "Найди причины падающих тестов.\n"
    "Если для правильного решения нужна актуальная документация —\n"
    "найди её в интернете и используй официальный источник.\n"
    "Исправь проект.\n"
    "Запускай проверки повторно и не завершай задачу,\n"
    "пока тесты не проходят.\n"
    "В конце проверь изменения и дай мне отчёт."
)

FORBIDDEN_PROMPT = (
    "read_file",
    "write_file",
    "patch_file",
    "web_search",
    "web_fetch",
    "tor_search",
    "tor_fetch",
    "tor_browser",
    "run_python",
    "pytest",
    "utcnow",
    "app.py",
    "calculator",
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


def create_autonomous_project(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "demo-repair"\n'
        'version = "0.1.0"\n'
        'description = "Disposable repair fixture"\n'
        'requires-python = ">=3.11"\n'
        "\n"
        "[tool.pytest.ini_options]\n"
        'pythonpath = ["."]\n'
        'testpaths = ["."]\n',
        encoding="utf-8",
        newline="\n",
    )
    (root / "README.md").write_text(
        "# demo-repair\n\n"
        "Small Python package used for a repair exercise.\n\n"
        "## Checks\n\n"
        "From this directory run the test runner declared in `pyproject.toml`.\n\n"
        "## Time APIs\n\n"
        "`utcstamp()` must return a timezone-aware UTC datetime. "
        "Follow the current official Python `datetime` documentation if the "
        "legacy naive UTC helper is no longer the correct contract.\n",
        encoding="utf-8",
        newline="\n",
    )
    app = root / "app.py"
    app.write_text(
        "from datetime import datetime\n"
        "\n"
        "\n"
        "def add(a, b):\n"
        "    return a - b\n"
        "\n"
        "\n"
        "def utcstamp():\n"
        "    return datetime.utcnow()\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "test_app.py").write_text(
        "from datetime import timezone\n"
        "\n"
        "from app import add, utcstamp\n"
        "\n"
        "\n"
        "def test_add():\n"
        "    assert add(21, 21) == 42\n"
        "\n"
        "\n"
        "def test_utcstamp_is_timezone_aware():\n"
        "    value = utcstamp()\n"
        "    assert value.tzinfo is not None\n"
        "    assert value.utcoffset() is not None\n"
        "    assert abs(value.utcoffset().total_seconds()) < 1\n"
        "    assert value.tzinfo is timezone.utc or str(value.tzinfo) in {\"UTC\", \"utc\"}\n",
        encoding="utf-8",
        newline="\n",
    )
    subprocess.check_call(
        ["git", "-c", "core.autocrlf=false", "init"],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(["git", "-c", "core.autocrlf=false", "add", "."], cwd=root)
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
            "initial failing tests",
        ],
        cwd=root,
        stdout=subprocess.DEVNULL,
    )
    before = {
        "app.py": hashlib.sha256(app.read_bytes()).hexdigest(),
        "test_app.py": hashlib.sha256((root / "test_app.py").read_bytes()).hexdigest(),
    }
    pytest_before = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"], cwd=root, capture_output=True, text=True
    )
    return {
        "before_sha256": before,
        "pytest_before_code": pytest_before.returncode,
        "pytest_before_tail": (pytest_before.stdout + pytest_before.stderr)[-800:],
        "failing_names": ["test_add", "test_utcstamp_is_timezone_aware"],
    }


def collect_task_stream(api, method_path, body, read_timeout=1100):
    parts = []
    events = []
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
                    row["keys"] = sorted(payload)
                    if event == "task":
                        task_snaps.append(
                            {
                                "status": payload.get("status"),
                                "plan_revision": payload.get("plan_revision"),
                                "tool_calls_used": payload.get("tool_calls_used"),
                                "files_changed": payload.get("files_changed"),
                                "current_step": payload.get("current_step"),
                                "last_error": payload.get("last_error"),
                                "steps": [
                                    {
                                        "title": step.get("title"),
                                        "status": step.get("status"),
                                        "attempts": step.get("attempts"),
                                    }
                                    for step in (payload.get("steps") or [])
                                ],
                            }
                        )
                    if event in {"error", "task_status"}:
                        row["code"] = payload.get("code") or payload.get("state")
                    if event == "tool":
                        row["tool"] = payload.get("name") or payload.get("tool_name")
                        row["status"] = payload.get("status")
                    if event == "done":
                        row["payload_keys"] = sorted(payload)
                events.append(row)
                if event == "delta" and isinstance(payload, dict):
                    parts.append(payload.get("content") or "")
                if event in {"done", "error"}:
                    break
    finally:
        client.close()
    return {
        "text": "".join(parts),
        "events": events,
        "chars": len("".join(parts)),
        "task_snaps": task_snaps,
    }


class PauseWatcher(threading.Thread):
    def __init__(self, api):
        super().__init__(daemon=True)
        self.api = api
        self.stop = threading.Event()
        self.result = {"attempted": False, "paused": False}

    def run(self):
        while not self.stop.wait(1.5):
            try:
                tasks = self.api.client.get("/tasks", headers=self.api.headers(), params={"limit": 5}).json()
            except Exception:
                continue
            if not tasks:
                continue
            task = tasks[0]
            steps = task.get("steps") or []
            completed = sum(1 for step in steps if step.get("status") == "COMPLETED")
            used = int(task.get("tool_calls_used") or 0)
            if used < 2 or completed < 1:
                continue
            if task.get("status") in {"COMPLETED", "FAILED", "STOPPED", "PAUSED"}:
                self.result["skip_reason"] = task.get("status")
                return
            self.result["attempted"] = True
            self.result["task_id"] = task.get("id")
            self.result["calls_before"] = used
            try:
                paused = self.api.client.post(
                    f"/tasks/{task['id']}/pause", headers=self.api.headers()
                )
                self.result["pause_http"] = paused.status_code
                body = paused.json() if paused.headers.get("content-type", "").startswith("application/json") else {}
                self.result["status_after_pause"] = body.get("status")
                self.result["paused"] = paused.status_code == 200
            except Exception as error:
                self.result["error"] = type(error).__name__
                return
            time.sleep(8)
            try:
                later = self.api.client.get(
                    f"/tasks/{task['id']}", headers=self.api.headers()
                ).json()
                self.result["calls_after_wait"] = later.get("tool_calls_used")
                self.result["status_after_wait"] = later.get("status")
                self.result["no_new_tools"] = int(later.get("tool_calls_used") or 0) <= used + 1
            except Exception as error:
                self.result["wait_error"] = type(error).__name__
            return


def host_bin():
    release = e2e.DESKTOP / "target" / "release" / "alex-host-loop.exe"
    debug = e2e.DESKTOP / "target" / "debug" / "alex-host-loop.exe"
    if release.exists():
        return release
    if debug.exists():
        return debug
    raise RuntimeError("host_bin_missing")


def summarize_task(task):
    if not task:
        return {}
    return {
        "id": task.get("id"),
        "status": task.get("status"),
        "plan_revision": task.get("plan_revision"),
        "tool_calls_used": task.get("tool_calls_used"),
        "files_changed": task.get("files_changed"),
        "last_error": task.get("last_error"),
        "completion_summary": (task.get("completion_summary") or "")[:800],
        "verification": task.get("verification"),
        "success_criteria": task.get("success_criteria"),
        "message": task.get("message"),
        "steps": [
            {"title": step.get("title"), "status": step.get("status"), "attempts": step.get("attempts")}
            for step in (task.get("steps") or [])
        ],
        "events": [
            {"kind": item.get("kind"), "payload": item.get("payload")}
            for item in (task.get("events") or [])[:80]
        ],
    }


def compute_running(api):
    try:
        status = api.client.get("/compute/status", headers=api.headers()).json()
    except Exception as error:
        return {"error": type(error).__name__}
    session = status.get("session") or {}
    state = status.get("state")
    running = state not in {None, "stopped", "idle", "not_configured", "error"} and bool(
        session.get("pod_id")
    )
    return {
        "state": state,
        "pod_id": session.get("pod_id"),
        "managed": session.get("managed"),
        "running_gpu": 1 if running else 0,
    }


def main():
    stamp = e2e.utcnow().strftime("%Y%m%dT%H%M%S")
    work = Path(os.environ["TEMP"]) / "alex-llm-real-e2e" / f"autonomous-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    project = work / "demo-repair"
    host_dir = work / "host-data"
    host_dir.mkdir(parents=True, exist_ok=True)
    evidence = work / "evidence.json"
    report = e2e.REPORT
    report.update(
        {
            "version": "0.9-candidate",
            "workspace": str(work),
            "project": str(project),
            "ui_automation": "no",
            "cursor_fixed_project": False,
        }
    )

    log = e2e.log
    backend_proc = None
    backend_log = None
    host_proc = None
    host_log = None
    approver = None
    watcher = None
    api = e2e.Api()
    gpu_started = None

    try:
        log("create disposable autonomous project")
        meta = create_autonomous_project(project)
        report["initial"] = {
            "pytest_before": meta["pytest_before_code"],
            "before_sha256": meta["before_sha256"],
            "pytest_before_tail": meta["pytest_before_tail"],
        }
        if meta["pytest_before_code"] == 0:
            raise RuntimeError("coding_fixture_not_failing")
        lowered = NATURAL_PROMPT.lower()
        if any(token.lower() in lowered for token in FORBIDDEN_PROMPT):
            raise RuntimeError("prompt_mentions_tools_or_bugs")
        report["prompt"] = NATURAL_PROMPT

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
            discovered = dummy_discovered()

        log("restart backend with llamacpp")
        os.environ["DATABASE_URL"] = e2e.env_file_map().get("DATABASE_URL") or "sqlite:///./alex.db"
        e2e.kill_port_8000()
        time.sleep(1)
        os.environ["COMPUTE_BACKGROUND_ENABLED"] = "true"
        os.environ["ALLOW_USER_COMPUTE_START"] = "true"
        os.environ["LLM_PROVIDER"] = "llamacpp"
        backend_proc, backend_log, _ = e2e.start_backend(discovered, 9050, session_budget="0.45")
        health = e2e.wait_health()
        report["backend_health"] = health
        if health.get("provider") != "llamacpp":
            raise RuntimeError("backend_not_llamacpp")

        email = f"autonomous-e2e-{stamp}@example.com"
        password = "e2e-password-" + hashlib.sha256(stamp.encode()).hexdigest()[:12]
        api.register(email, password)
        api.client.post("/compute/search/cancel", headers=api.headers())
        try:
            e2e.stop_compute(api)
        except Exception:
            pass
        api.client.put(
            "/tools/preferences",
            headers=api.headers(),
            json={
                "search_enabled": True,
                "fetch_enabled": True,
                "default_mode": "auto",
                "agent_enabled": False,
                "browser_enabled": False,
                "tor_enabled": False,
                "computer_mode": "trusted",
                "workspace_roots": [str(project), str(work)],
                "device_display_name": "E2E native host",
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
                "ALEX_WORKSPACE_ROOTS": f"{project};{work}",
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

        approver = e2e.Approver(api, str(work))
        approver.start()

        smoke_chat = api.client.post("/chats", headers=api.headers(), json={"title": "local-smoke"}).json()
        smoke = e2e.explicit_tool(api, smoke_chat["id"], "get_system_info", {})
        report["local_smoke"] = {"ok": smoke.get("ok")}
        if not smoke.get("ok"):
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

        log("autonomous natural task")
        task_chat = api.client.post("/chats", headers=api.headers(), json={"title": "autonomous-task"}).json()
        watcher = PauseWatcher(api)
        watcher.start()
        first = collect_task_stream(
            api,
            f"/chats/{task_chat['id']}/stream",
            {
                "content": NATURAL_PROMPT,
                "web_mode": "auto",
                "computer_mode": "trusted",
                "tor_mode": "off",
            },
            read_timeout=1100,
        )
        watcher.stop.set()
        watcher.join(timeout=2)
        report["first_stream"] = {
            "chars": first["chars"],
            "answer_prefix": (first.get("text") or "")[:800],
            "events": [item.get("event") for item in first["events"]],
            "task_snaps": first["task_snaps"][-6:],
        }
        e2e.record_gpu("task_first_stream")
        e2e.gpu_guard(gpu_started)

        tasks = api.client.get("/tasks", headers=api.headers(), params={"chat_id": task_chat["id"]}).json()
        task = tasks[0] if tasks else {}
        if task.get("id"):
            task = api.client.get(f"/tasks/{task['id']}", headers=api.headers()).json()
        report["pause"] = watcher.result if watcher else {"attempted": False}
        report["task_after_first"] = summarize_task(task)

        if task.get("status") == "PAUSED" and e2e.estimated_cost() < 0.32:
            log("resume paused autonomous task")
            resumed = collect_task_stream(
                api,
                f"/tasks/{task['id']}/resume",
                {},
                read_timeout=900,
            )
            report["resume_stream"] = {
                "chars": resumed["chars"],
                "answer_prefix": (resumed.get("text") or "")[:800],
                "events": [item.get("event") for item in resumed["events"]],
                "task_snaps": resumed["task_snaps"][-6:],
            }
            task = api.client.get(f"/tasks/{task['id']}", headers=api.headers()).json()
            report["pause"]["resumed"] = True
            e2e.record_gpu("task_resume")
        else:
            report["resume_stream"] = {
                "skipped": True,
                "reason": task.get("status") or "no_task",
            }

        runs = e2e.latest_runs(api, task_chat["id"])
        after = {
            "app.py": hashlib.sha256((project / "app.py").read_bytes()).hexdigest(),
            "test_app.py": hashlib.sha256((project / "test_app.py").read_bytes()).hexdigest(),
        }
        pytest_after = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"], cwd=project, capture_output=True, text=True
        )
        diff = subprocess.run(
            ["git", "diff"], cwd=project, capture_output=True, text=True
        ).stdout
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=project, capture_output=True, text=True
        ).stdout
        app_text = (project / "app.py").read_text(encoding="utf-8")
        files_changed = [name for name, digest in after.items() if digest != meta["before_sha256"][name]]
        web_used = any(row.get("tool_name") in {"web_search", "web_fetch"} for row in runs)
        process_runs = [row for row in runs if row.get("tool_name") in {"run_python", "run_process"}]
        failed_then = False
        passed_later = False
        for row in process_runs:
            meta_row = row.get("result_metadata") or {}
            code = meta_row.get("exit_code")
            if code not in (None, 0):
                failed_then = True
            if code == 0:
                passed_later = True
        events = task.get("events") or []
        plan_updated = any(item.get("kind") == "PLAN_UPDATED" for item in events)
        report["autonomous"] = {
            "task": summarize_task(task),
            "tools": e2e.tool_entries(runs),
            "web_used": web_used,
            "web_why": "official datetime/docs" if web_used else "not_required_or_unused",
            "files_changed": files_changed,
            "after_sha256": after,
            "pytest_after": pytest_after.returncode,
            "pytest_tail": (pytest_after.stdout + pytest_after.stderr)[-800:],
            "git_diff": diff[:2500],
            "git_status": status[:500],
            "add_fixed": "return a + b" in app_text or "return a+b" in app_text,
            "utc_aware": "timezone.utc" in app_text or "datetime.UTC" in app_text or "now(" in app_text,
            "model_origin": any(item.get("origin") == "model" for item in e2e.tool_entries(runs)),
            "model_patch_or_write": any(
                item.get("name") in {"patch_file", "write_file"} and item.get("origin") == "model"
                for item in e2e.tool_entries(runs)
            ),
            "cursor_fixed": False,
            "self_correction": bool(failed_then and passed_later) or plan_updated or int(task.get("plan_revision") or 1) >= 2,
            "saw_failing_tests": failed_then,
            "saw_passing_tests": passed_later,
            "answer": (first.get("text") or "")[:1200],
        }
        auto = report["autonomous"]
        real_pass = bool(
            not report["orcarouter_basic"]["mock"]
            and task.get("status") == "COMPLETED"
            and auto["pytest_after"] == 0
            and files_changed
            and auto["model_patch_or_write"]
            and not auto["cursor_fixed"]
            and report["native_host"] == "yes"
            and (task.get("steps") or [])
        )
        report["verdict"] = {
            "real_pass": real_pass,
            "task_status": task.get("status"),
            "mock": report["orcarouter_basic"]["mock"],
            "pytest_after": auto["pytest_after"],
            "files_changed": files_changed,
            "web_used": web_used,
            "self_correction": auto["self_correction"],
            "pause": report["pause"],
        }
        e2e.record_gpu("autonomous_done")

        all_runs = e2e.latest_runs(api)
        report["tinyfish"] = {
            "search": sum(1 for row in all_runs if row.get("tool_name") == "web_search"),
            "fetch": sum(1 for row in all_runs if row.get("tool_name") == "web_fetch"),
            "agent_calls": sum(1 for row in all_runs if "agent" in (row.get("tool_name") or "")),
            "browser_calls": sum(
                1 for row in all_runs if str(row.get("tool_name") or "").startswith("browser")
            ),
        }
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
        if approver:
            report["approvals"] = list(approver.approved)
        log("VERDICT real_pass=%s status=%s pytest=%s" % (real_pass, task.get("status"), auto["pytest_after"]))
    finally:
        log("cleanup gpu")
        if watcher:
            watcher.stop.set()
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
        path = Path(os.environ.get("TEMP", ".")) / "alex-llm-real-e2e" / f"fatal-autonomous-{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(e2e.REPORT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"FATAL {type(error).__name__}:{error} evidence={path}", flush=True)
        raise
