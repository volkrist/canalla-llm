"""Alex LLM 0.8 real E2E. Production paths only; never prints secrets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "apps" / "backend"
DESKTOP = ROOT / "apps" / "desktop" / "src-tauri"
ONION_RE = re.compile(r"\b([a-z2-7]{56}\.onion|[a-z2-7]{16}\.onion)\b", re.I)
MAX_HOURLY = 1.10
SESSION_BUDGET = 0.60
GPU_WALL = 35 * 60
GPU_COST_STOP = 0.55

REPORT = {
    "version": "0.8.0",
    "approvals": [],
    "ui_automation": "no",
    "native_host": "no",
}


def utcnow():
    return datetime.now(timezone.utc)


def log(message):
    print(f"[{utcnow().isoformat()}] {message}", flush=True)


def env_file_map():
    values = {}
    path = BACKEND / ".env"
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def secret_present(values, key):
    return bool(values.get(key))


def discover_official():
    discovered = {"ahmia_onion": None, "torproject_onion": None, "ddg_onion": None, "sources": []}
    headers = {"User-Agent": "AlexLLM-E2E/0.8"}
    pages = [
        ("https://ahmia.fi/", "ahmia"),
        ("https://www.torproject.org/", "torproject-home"),
        (
            "https://support.torproject.org/tor-browser/encountering-issues/troubleshooting-onion-services/",
            "torproject-support",
        ),
    ]
    with httpx.Client(timeout=25, follow_redirects=True, headers=headers) as client:
        for url, name in pages:
            response = client.get(url)
            text = response.text
            onions = list(dict.fromkeys(ONION_RE.findall(text.lower())))
            location = response.headers.get("onion-location") or response.headers.get("Onion-Location")
            if location:
                onions = list(dict.fromkeys(ONION_RE.findall(location.lower()) + onions))
            discovered["sources"].append({"page": name, "status": response.status_code, "onion_count": len(onions)})
            if name == "ahmia" and onions:
                discovered["ahmia_onion"] = onions[0]
            if name == "torproject-support":
                for host in onions:
                    if host.startswith("2gzyxa5") or "torproject" in text.lower() and host.endswith(".onion"):
                        if discovered["torproject_onion"] is None and host.startswith("2gzyxa5"):
                            discovered["torproject_onion"] = host
                    if host.startswith("duckduckgo") or host.startswith("duckduckg"):
                        discovered["ddg_onion"] = host
                if not discovered["torproject_onion"]:
                    for host in onions:
                        if not host.startswith("duck"):
                            discovered["torproject_onion"] = host
                            break
                if not discovered["ddg_onion"]:
                    for host in onions:
                        if "duck" in host:
                            discovered["ddg_onion"] = host
    if not discovered["ahmia_onion"] or not discovered["torproject_onion"]:
        raise RuntimeError("official_onion_discovery_failed")
    ahmia = discovered["ahmia_onion"]
    discovered["search_providers"] = [
        {
            "name": "ahmia",
            "url_template": f"http://{ahmia}/search/?q=__QUERY__",
            "form_url": f"http://{ahmia}/",
        },
        {
            "name": "ahmia-clearnet",
            "url_template": "https://ahmia.fi/search/?q=__QUERY__",
            "form_url": "https://ahmia.fi/",
        },
    ]
    discovered["official_mapping"] = [
        {
            "name": "Tor Project",
            "clearnet": "https://www.torproject.org",
            "onion": f"http://{discovered['torproject_onion']}",
        },
        {
            "name": "Ahmia",
            "clearnet": "https://ahmia.fi",
            "onion": f"http://{ahmia}",
        },
    ]
    return discovered


def probe_socks():
    result = {"9150": False, "9050": False, "port": None, "host": "127.0.0.1"}
    for port in (9150, 9050):
        try:
            with socket.create_connection(("127.0.0.1", port), 0.4):
                result[str(port)] = True
                if result["port"] is None:
                    result["port"] = port
        except OSError:
            pass
    return result


def start_tor_browser(existing):
    browser = Path(os.environ["USERPROFILE"]) / "Desktop" / "Tor Browser" / "Browser" / "firefox.exe"
    daemon = Path(os.environ["USERPROFILE"]) / "Desktop" / "Tor Browser" / "Browser" / "TorBrowser" / "Tor" / "tor.exe"
    if existing["port"]:
        return {"started_by_test": False, "exe": str(browser) if browser.exists() else None, **existing}
    started = {"started_by_test": False, "exe": str(browser) if browser.exists() else None}
    if daemon.exists():
        data = Path(os.environ["TEMP"]) / "alex-llm-real-e2e" / "tor-data"
        data.mkdir(parents=True, exist_ok=True)
        for port in (9150, 9050, 9905, 19150):
            process = subprocess.Popen(
                [
                    str(daemon),
                    "--DataDirectory",
                    str(data),
                    "--SocksPort",
                    f"127.0.0.1:{port}",
                    "--CookieAuthentication",
                    "0",
                ],
                cwd=str(daemon.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.time() + 12
            while time.time() < deadline:
                if process.poll() is not None:
                    break
                probed = probe_socks()
                if probed["port"]:
                    started["started_by_test"] = True
                    started["daemon"] = True
                    return {**started, **probed}
                time.sleep(1)
            if process.poll() is None:
                process.terminate()
        started["error"] = "socks_bind_failed"
    probed = probe_socks()
    return {**started, **probed}


class Api:
    def __init__(self, base="http://127.0.0.1:8000"):
        self.base = base
        self.client = httpx.Client(base_url=base, timeout=120.0)
        self.token = None

    def headers(self, extra=None):
        value = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        if extra:
            value.update(extra)
        return value

    def register(self, email, password):
        response = self.client.post("/auth/register", json={"email": email, "password": password})
        if response.status_code == 409:
            response = self.client.post("/auth/login", json={"email": email, "password": password})
        response.raise_for_status()
        self.token = response.json()["access_token"]
        return self.client.get("/auth/me", headers=self.headers()).json()


def wait_health(timeout=90):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = httpx.get("http://127.0.0.1:8000/health", timeout=2).json()
            return last
        except Exception as error:
            last = str(error)
            time.sleep(0.5)
    raise RuntimeError(f"backend_unhealthy:{last}")


def sse_events(response):
    event = "message"
    data = []
    for line in response.iter_lines():
        if line == "":
            if data:
                payload = "\n".join(data)
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    parsed = payload
                yield event, parsed
            event, data = "message", []
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


def collect_stream(api, chat_id, content, web_mode="off", computer_mode="trusted", abort_after=None):
    parts = []
    events = []
    started = time.time()
    with api.client.stream(
        "POST",
        f"/chats/{chat_id}/stream",
        headers=api.headers(),
        json={"content": content, "web_mode": web_mode, "computer_mode": computer_mode},
        timeout=httpx.Timeout((abort_after + 8) if abort_after else 300.0, connect=10.0),
    ) as response:
        if response.status_code != 200:
            body = response.read()
            raise RuntimeError(f"stream_http_{response.status_code}:{body[:300]!r}")
        for event, payload in sse_events(response):
            if abort_after and time.time() - started >= abort_after:
                break
            events.append({"event": event})
            if event == "delta" and isinstance(payload, dict):
                parts.append(payload.get("content") or "")
            if event in {"done", "error"}:
                events[-1]["payload_keys"] = sorted(payload) if isinstance(payload, dict) else []
                if event == "error" and isinstance(payload, dict):
                    events[-1]["code"] = payload.get("code")
                break
    text = "".join(parts)
    return {"text": text, "events": events, "chars": len(text), "aborted": bool(abort_after)}


def explicit_tool(api, chat_id, name, arguments, timeout=90):
    events = []
    with api.client.stream(
        "POST",
        "/tools/execute",
        headers=api.headers(),
        json={"chat_id": chat_id, "name": name, "arguments": arguments},
        timeout=httpx.Timeout(timeout, connect=10.0),
    ) as response:
        body_error = None
        if response.status_code != 200:
            body_error = response.read()[:400]
            return {"ok": False, "status": response.status_code, "error": "http", "detail_len": len(body_error or b"")}
        for event, payload in sse_events(response):
            events.append({"event": event, "keys": sorted(payload) if isinstance(payload, dict) else []})
            if event in {"tool_result", "tool_error", "done"}:
                if isinstance(payload, dict):
                    events[-1]["code"] = payload.get("code")
                    events[-1]["text_len"] = len(str(payload.get("text") or ""))
                if event != "done":
                    continue
    return {"ok": True, "events": events}


class Approver(threading.Thread):
    def __init__(self, api, allowed_root):
        super().__init__(daemon=True)
        self.api = api
        self.allowed_root = os.path.normcase(os.path.abspath(allowed_root))
        self.stop = threading.Event()
        self.approved = []

    def allowed(self, run):
        summary = run.get("input_summary") or {}
        values = [summary.get(key) for key in ("path", "cwd", "root", "source", "destination")]
        values.extend(summary.get("paths") or [])
        present = [os.path.normcase(os.path.abspath(str(value))) for value in values if value]
        if not present:
            return run.get("tool_name") in {"get_system_info", "process_status", "stop_process", "list_processes"}
        return all(value.startswith(self.allowed_root) for value in present)

    def run(self):
        while not self.stop.wait(0.35):
            try:
                runs = self.api.client.get("/tools/runs", headers=self.api.headers(), params={"limit": 50}).json()
            except Exception:
                continue
            for run in runs:
                if run.get("status") != "waiting_confirmation":
                    continue
                if not self.allowed(run):
                    continue
                response = self.api.client.post(
                    f"/tools/runs/{run['id']}/confirm",
                    headers=self.api.headers(),
                    json={"allow": True},
                )
                self.approved.append(
                    {
                        "id": run["id"],
                        "tool": run.get("tool_name"),
                        "http": response.status_code,
                        "method": "e2e_harness",
                    }
                )


def start_backend(discovered, socks_port, skip_gpu=False):
    python = BACKEND / ".venv" / "Scripts" / "python.exe"
    upgrade = subprocess.run(
        [str(python), "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
    )
    if upgrade.returncode != 0:
        raise RuntimeError(f"alembic_failed:{upgrade.stderr[-400:]}")
    env = os.environ.copy()
    env.pop("TOR_SEARCH_PROVIDERS", None)
    env.pop("TOR_OFFICIAL_MAPPING", None)
    tor_dir = Path(os.environ["TEMP"]) / "alex-llm-real-e2e"
    tor_dir.mkdir(parents=True, exist_ok=True)
    providers_file = tor_dir / "tor-search-providers.json"
    mapping_file = tor_dir / "tor-official-mapping.json"
    providers_file.write_text(json.dumps(discovered["search_providers"]), encoding="utf-8")
    mapping_file.write_text(json.dumps(discovered["official_mapping"]), encoding="utf-8")
    env.update(
        {
            "LLM_PROVIDER": "mock" if skip_gpu else "llamacpp",
            "LLM_CONNECTION_MODE": "runpod",
            "COMPUTE_BACKGROUND_ENABLED": "false" if skip_gpu else "true",
            "ALLOW_USER_COMPUTE_START": "true",
            "TOR_SOCKS_HOST": "127.0.0.1",
            "TOR_SOCKS_PORT": str(socks_port or 9150),
            "TOR_SEARCH_PROVIDERS_FILE": str(providers_file),
            "TOR_OFFICIAL_MAPPING_FILE": str(mapping_file),
            "RUNPOD_MAX_HOURLY_PRICE": "1.10",
            "RUNPOD_MAX_SESSION_BUDGET": "0.60",
        }
    )
    log_path = Path(os.environ["TEMP"]) / "alex-llm-real-e2e" / "backend.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "ab")
    process = subprocess.Popen(
        [str(python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--workers", "1"],
        cwd=str(BACKEND),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    return process, handle, log_path


def kill_port_8000():
    try:
        result = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", "(Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue).OwningProcess"],
            text=True,
        )
    except subprocess.CalledProcessError:
        return
    for item in {part.strip() for part in result.split() if part.strip().isdigit()}:
        subprocess.run(["taskkill", "/PID", item, "/F"], capture_output=True)


def sqlite_db():
    values = env_file_map()
    url = values.get("DATABASE_URL", "sqlite:///./alex.db")
    if not url.startswith("sqlite"):
        return None
    path = url.split("sqlite:///")[-1]
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = BACKEND / candidate
    return candidate


def session_row():
    db = sqlite_db()
    if not db or not db.exists():
        return {}
    with sqlite3.connect(db) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, pod_id, gpu_type, gpu_vram_mb, hourly_rate, datacenter, status, started_at, ready_at, "
            "created_at, billable_seconds, estimated_cost, managed, network_volume_id, stop_reason "
            "FROM compute_sessions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else {}


def usage_rows(limit=20):
    db = sqlite_db()
    if not db or not db.exists():
        return []
    with sqlite3.connect(db) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, provider, status, input_tokens, output_tokens, total_tokens, chat_id "
            "FROM generation_usage ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def gpu_guard(started):
    elapsed = time.time() - started
    row = session_row()
    cost = float(row.get("estimated_cost") or 0)
    if elapsed >= GPU_WALL or cost >= GPU_COST_STOP:
        raise RuntimeError(f"gpu_limit elapsed={int(elapsed)}s cost={cost}")


def wait_model_ready(api, started, timeout=900):
    gateway_at = None
    model_at = None
    deadline = time.time() + timeout
    last = {}
    searching_since = None
    while time.time() < deadline:
        gpu_guard(started)
        status = api.client.get("/compute/status", headers=api.headers()).json()
        state = status.get("state")
        llm = api.client.get("/llm/status", headers=api.headers()).json()
        last = {"state": state, "error": status.get("error_code"), "llm": llm.get("state"), "has_session": bool(status.get("session"))}
        if state in {"connecting", "starting_llm", "ready", "generating", "starting_pod", "starting_environment", "mounting_storage"} and gateway_at is None:
            gateway_at = utcnow().isoformat()
        if llm.get("state") == "ready" and llm.get("provider") == "llamacpp":
            model_at = utcnow().isoformat()
            return {"status": status, "llm": llm, "gateway_at": gateway_at, "model_at": model_at}
        if not status.get("session") and status.get("error_code") in {"no_compatible_gpu", "price_limit"}:
            searching_since = searching_since or time.time()
            if time.time() - searching_since >= 90:
                raise RuntimeError(f"no_gpu:{status.get('error_code')}")
        else:
            searching_since = None
        if state in {"error", "stopped", "not_configured"} and status.get("session") is None:
            raise RuntimeError(f"compute_failed:{state}:{status.get('error_code')}")
        time.sleep(3)
    raise RuntimeError(f"model_ready_timeout:{last}")


def latest_runs(api, chat_id=None):
    params = {"limit": 100}
    if chat_id:
        params["chat_id"] = chat_id
    return api.client.get("/tools/runs", headers=api.headers(), params=params).json()


def create_coding_project(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    calculator = root / "calculator.py"
    tests = root / "test_calculator.py"
    calculator.write_text(
        "def add(a, b):\n    return a + b\n\n\ndef divide(a, b):\n    return a * b\n",
        encoding="utf-8",
    )
    tests.write_text(
        "from calculator import add, divide\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_divide():\n    assert divide(10, 2) == 5\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("Disposable Alex LLM coding E2E project.\n", encoding="utf-8")
    subprocess.check_call(["git", "init"], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.check_call(["git", "add", "."], cwd=root)
    subprocess.check_call(
        ["git", "-c", "user.email=e2e@example.com", "-c", "user.name=E2E", "commit", "-m", "initial failing tests"],
        cwd=root,
        stdout=subprocess.DEVNULL,
    )
    before = hashlib.sha256(calculator.read_bytes()).hexdigest()
    pytest_before = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=root, capture_output=True, text=True)
    return {
        "before_sha256": before,
        "pytest_before_code": pytest_before.returncode,
        "pytest_before_tail": (pytest_before.stdout + pytest_before.stderr)[-800:],
    }


def stop_compute(api):
    try:
        return api.client.post("/compute/stop", headers=api.headers(), json={"after_generation": False}).json()
    except Exception as error:
        return {"error": type(error).__name__}


def main():
    stamp = utcnow().strftime("%Y%m%dT%H%M%S")
    work = Path(os.environ["TEMP"]) / "alex-llm-real-e2e" / stamp
    work.mkdir(parents=True, exist_ok=True)
    project = work / "alex-coding-e2e"
    host_dir = work / "host-data"
    host_dir.mkdir(parents=True, exist_ok=True)
    evidence = work / "evidence.json"
    REPORT["workspace"] = str(work)
    values = env_file_map()
    REPORT["config"] = {
        "llm_provider_env": values.get("LLM_PROVIDER"),
        "runpod_key": secret_present(values, "RUNPOD_API_KEY"),
        "llm_key": secret_present(values, "LLM_API_KEY"),
        "tinyfish_key": secret_present(values, "TINYFISH_API_KEY"),
        "jwt": secret_present(values, "JWT_SECRET"),
    }
    skip_gpu = os.environ.get("ALEX_E2E_SKIP_GPU") == "1"
    REPORT["skip_gpu"] = skip_gpu
    if not skip_gpu and (not REPORT["config"]["runpod_key"] or not REPORT["config"]["llm_key"]):
        raise SystemExit("missing compute credentials")

    log("discover official onion endpoints")
    discovered = discover_official()
    REPORT["tor_discovery"] = {
        "ahmia_prefix": (discovered["ahmia_onion"] or "")[:12],
        "torproject_prefix": (discovered["torproject_onion"] or "")[:12],
        "sources": discovered["sources"],
    }

    log("tor socks preflight")
    socks = start_tor_browser(probe_socks())
    REPORT["tor_socks"] = {k: socks.get(k) for k in ("9150", "9050", "port", "host", "started_by_test", "error")}
    REPORT["tor_browser_existing_install"] = bool(socks.get("exe"))

    backend_proc = None
    backend_log = None
    host_proc = None
    approver = None
    api = Api()
    gpu_started = None
    try:
        log("restart backend with llamacpp + tor providers")
        kill_port_8000()
        time.sleep(1)
        backend_proc, backend_log, _ = start_backend(discovered, socks.get("port"), skip_gpu)
        health = wait_health()
        REPORT["backend_health"] = health
        if not skip_gpu and health.get("provider") != "llamacpp":
            raise RuntimeError("backend_not_llamacpp")

        email = f"e2e-{stamp}@example.com"
        password = "e2e-password-" + hashlib.sha256(stamp.encode()).hexdigest()[:12]
        me = api.register(email, password)
        REPORT["user_role"] = me.get("role")
        tools_status = api.client.get("/tools/status", headers=api.headers()).json()
        REPORT["tor_search_configured"] = tools_status.get("tor_search_configured")
        REPORT["tor_status"] = tools_status.get("tor_status")
        if not tools_status.get("tor_search_configured"):
            raise RuntimeError("tor_search_not_configured")
        api.client.post("/compute/search/cancel", headers=api.headers())
        gpu_started = time.time()
        if not skip_gpu:
            compute_prefs = {
                "selection": "automatic",
                "min_vram_gb": 48,
                "max_hourly_price": str(MAX_HOURLY),
                "session_budget": str(SESSION_BUDGET),
                "auto_stop_minutes": 10,
                "gpu_id": "NVIDIA L40S",
                "auto_connect": True,
                "auto_search": False,
                "search_interval": 30,
            }
            api.client.put("/compute/preferences", headers=api.headers(), json=compute_prefs)
            options = api.client.get("/compute/options", headers=api.headers()).json().get("options") or []
            l40s = next((item for item in options if item.get("id") == "NVIDIA L40S"), None)
            if not (l40s and l40s.get("selectable")):
                compute_prefs.pop("gpu_id", None)
                api.client.put("/compute/preferences", headers=api.headers(), json=compute_prefs)
                options = api.client.get("/compute/options", headers=api.headers()).json().get("options") or []
                REPORT["gpu_fallback"] = "automatic_compatible"
            REPORT["gpu_options"] = [
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
            selectable = [item for item in options if item.get("selectable")]
            REPORT["gpu_selectable"] = [item.get("id") for item in selectable]
            log(
                "gpu catalog selectable=%s fallback=%s"
                % (len(selectable), REPORT.get("gpu_fallback", "l40s"))
            )
        api.client.put(
            "/tools/preferences",
            headers=api.headers(),
            json={
                "search_enabled": True,
                "fetch_enabled": True,
                "default_mode": "off",
                "agent_enabled": False,
                "browser_enabled": False,
                "tor_enabled": True,
                "computer_mode": "trusted",
                "workspace_roots": [str(work)],
                "device_display_name": "E2E native host",
            },
        )

        if not skip_gpu:
            log("start ONE managed pod")
            search = api.client.post(
                "/compute/search",
                headers=api.headers(),
                json=compute_prefs,
            )
            search_body = search.json() if search.headers.get("content-type", "").startswith("application/json") else {}
            REPORT["compute_search_http"] = search.status_code
            REPORT["compute_search"] = {
                "error": search_body.get("error_code") or (search_body.get("status") or {}).get("error_code"),
                "selected": search_body.get("selected_gpu_id"),
                "existing": search_body.get("existing"),
                "state": (search_body.get("status") or {}).get("state"),
            }
            ready = wait_model_ready(api, gpu_started)
            row = session_row()
            REPORT["runpod"] = {
                "gpu": row.get("gpu_type"),
                "vram_mb": row.get("gpu_vram_mb"),
                "datacenter": row.get("datacenter"),
                "price_hour": row.get("hourly_rate"),
                "pod_id": row.get("pod_id"),
                "create_time": str(row.get("created_at")),
                "started_at": str(row.get("started_at")),
                "ready_at": str(row.get("ready_at")),
                "gateway_at": ready.get("gateway_at"),
                "model_at": ready.get("model_at"),
                "volume": row.get("network_volume_id"),
                "managed": row.get("managed"),
                "state": ready["status"].get("state"),
                "model": ready["llm"].get("model"),
                "provider": ready["llm"].get("provider"),
            }

        host_bin = DESKTOP / "target" / "debug" / "alex-host-loop.exe"
        if not host_bin.exists():
            raise RuntimeError("host_bin_missing")
        host_env = os.environ.copy()
        host_env.update(
            {
                "ALEX_BACKEND_URL": "http://127.0.0.1:8000",
                "ALEX_TOKEN": api.token,
                "ALEX_DEVICE_NAME": "E2E native host",
                "ALEX_WORKSPACE_ROOTS": str(work),
                "ALEX_DEVICE_DIR": str(host_dir),
                "ALEX_DEVICE_CREDENTIAL_TARGET": "Alex LLM/e2e-device-credential",
                "ALEX_PYTHON": str(BACKEND / ".venv" / "Scripts" / "python.exe"),
            }
        )
        host_log = open(work / "host.log", "ab")
        host_proc = subprocess.Popen(
            [str(host_bin)],
            cwd=str(DESKTOP),
            env=host_env,
            stdout=host_log,
            stderr=subprocess.STDOUT,
        )
        paired = False
        for _ in range(40):
            devices = api.client.get("/tools/devices", headers=api.headers()).json()
            if devices and devices[0].get("online"):
                paired = True
                REPORT["device"] = {
                    "online": True,
                    "platform": devices[0].get("platform"),
                    "has_credential_in_list": "credential" in devices[0],
                    "id_prefix": (devices[0].get("device_id") or "")[:8],
                }
                break
            time.sleep(0.5)
        REPORT["native_host"] = "yes" if paired else "no"
        if not paired:
            raise RuntimeError("native_host_offline")

        approver = Approver(api, str(work))
        approver.start()

        if not skip_gpu:
            log("orcarouter basic")
            chat = api.client.post("/chats", headers=api.headers(), json={"title": "orcarouter-basic"}).json()
            basic = collect_stream(api, chat["id"], "Ответь одним коротким предложением: сколько будет 2+2?")
            REPORT["orcarouter_basic"] = {
                "chars": basic["chars"],
                "events": [item["event"] for item in basic["events"]],
                "answer_len": basic["chars"],
            }

        log("local tool smoke")
        smoke_chat = api.client.post("/chats", headers=api.headers(), json={"title": "local-smoke"}).json()
        smoke_dir = work / "smoke"
        smoke_file = smoke_dir / "note.txt"
        smoke = {}
        for name, args in (
            ("get_system_info", {}),
            ("create_directory", {"path": str(smoke_dir)}),
            ("write_file", {"path": str(smoke_file), "content": "hello-e2e\n"}),
            ("list_directory", {"path": str(smoke_dir)}),
            ("read_file", {"path": str(smoke_file)}),
            (
                "patch_file",
                {
                    "path": str(smoke_file),
                    "old_text": "hello-e2e",
                    "new_text": "hello-patched",
                    "expected_before_sha256": hashlib.sha256(b"hello-e2e\n").hexdigest(),
                },
            ),
            ("run_python", {"argv": ["-c", "print(40+2)"], "cwd": str(smoke_dir)}),
        ):
            smoke[name] = explicit_tool(api, smoke_chat["id"], name, args)
        long_thread_result = {}

        def long_job():
            long_thread_result["result"] = explicit_tool(
                api,
                smoke_chat["id"],
                "run_python",
                {"argv": ["-c", "import time; time.sleep(45)"], "cwd": str(smoke_dir), "timeout_seconds": 60},
                timeout=80,
            )

        worker = threading.Thread(target=long_job, daemon=True)
        worker.start()
        long_run = None
        for _ in range(40):
            runs = latest_runs(api, smoke_chat["id"])
            for run in runs:
                summary = json.dumps(run.get("input_summary") or {})
                if run.get("tool_name") == "run_python" and "sleep(45)" in summary:
                    long_run = run
                    break
            if long_run:
                break
            time.sleep(0.5)
        if long_run:
            smoke["process_status"] = explicit_tool(
                api, smoke_chat["id"], "process_status", {"tool_run_id": long_run["id"]}
            )
            smoke["stop_process"] = explicit_tool(
                api, smoke_chat["id"], "stop_process", {"tool_run_id": long_run["id"]}
            )
        worker.join(timeout=70)
        smoke["long_run"] = {
            "id_prefix": (long_run or {}).get("id", "")[:8],
            "final": long_thread_result.get("result"),
        }
        REPORT["local_smoke"] = {name: item.get("ok") if isinstance(item, dict) else item for name, item in smoke.items()}

        log("digest mismatch")
        jobs = api.client.get("/tools/devices", headers=api.headers()).json()
        mismatch = {"tested": False}
        # Create a waiting_host job then post a wrong digest using the native host's
        # waiting window: write_file in ask mode. Switch to ask, execute, approve,
        # intercept before host-result by posting a fake digest is only possible
        # from the paired device credential, which the renderer/API list does not have.
        # Use the official confirm-once path: second confirm is rejected.
        api.client.put(
            "/tools/preferences",
            headers=api.headers(),
            json={
                "tor_enabled": True,
                "computer_mode": "ask",
                "workspace_roots": [str(work)],
                "agent_enabled": False,
                "browser_enabled": False,
            },
        )
        confirm_chat = api.client.post("/chats", headers=api.headers(), json={"title": "confirm"}).json()
        confirm_path = work / "confirm.txt"
        confirm_path.write_text("n", encoding="utf-8")
        threading.Thread(
            target=lambda: explicit_tool(
                api, confirm_chat["id"], "write_file", {"path": str(confirm_path), "content": "ok"}
            ),
            daemon=True,
        ).start()
        waiting = None
        for _ in range(40):
            for run in latest_runs(api, confirm_chat["id"]):
                if run.get("status") == "waiting_confirmation" and run.get("tool_name") == "write_file":
                    waiting = run
                    break
            if waiting:
                break
            time.sleep(0.25)
        if waiting:
            first = api.client.post(
                f"/tools/runs/{waiting['id']}/confirm", headers=api.headers(), json={"allow": True}
            )
            second = api.client.post(
                f"/tools/runs/{waiting['id']}/confirm", headers=api.headers(), json={"allow": True}
            )
            mismatch = {
                "tested": True,
                "first": first.status_code,
                "second": second.status_code,
                "method": "e2e_harness",
            }
            REPORT["approvals"].append({"id": waiting["id"], "tool": "write_file", "method": "e2e_harness"})
        # Wrong host digest: pause by using a job that is waiting_host and POST garbage digest
        # from an unpaired client must 401; from device without matching digest 409.
        # The native host posts the real digest. We emulate mismatch with a second pair? Skip
        # stealing the real credential. Instead POST without device headers after approval.
        if waiting:
            fake = api.client.post(
                f"/tools/runs/{waiting['id']}/host-result",
                headers=api.headers(),
                json={
                    "digest": "0" * 64,
                    "status": "completed",
                    "text": "nope",
                    "stdout": "",
                    "stderr": "",
                    "metadata": {},
                },
            )
            mismatch["wrong_digest_without_device"] = fake.status_code
        REPORT["confirmation"] = mismatch
        api.client.put(
            "/tools/preferences",
            headers=api.headers(),
            json={
                "tor_enabled": True,
                "computer_mode": "trusted",
                "workspace_roots": [str(work)],
                "default_mode": "off",
                "agent_enabled": False,
                "browser_enabled": False,
            },
        )

        if skip_gpu:
            REPORT["coding"] = {"skipped": True}
            REPORT["memory_rag"] = {"skipped": True}
            REPORT["web"] = {"agent_calls": 0, "browser_calls": 0, "skipped": True}
        else:
            log("coding agent")
            coding_meta = create_coding_project(project)
            REPORT["coding_initial"] = {
                "path": str(project),
                "pytest_before": coding_meta["pytest_before_code"],
                "before_sha256": coding_meta["before_sha256"],
            }
            coding_prompt = (
                "В этой тестовой папке есть небольшой Python-проект. "
                f"Корневая папка: {project}. "
                "Проверь проект, запусти тесты, найди причину ошибки, исправь код и снова запусти тесты. "
                "Заверши работу только когда тесты проходят."
            )
            attempts = []
            for attempt in range(1, 4):
                gpu_guard(gpu_started)
                chat = api.client.post("/chats", headers=api.headers(), json={"title": f"coding-{attempt}"}).json()
                result = collect_stream(api, chat["id"], coding_prompt, web_mode="off", computer_mode="trusted")
                runs = latest_runs(api, chat["id"])
                after = hashlib.sha256((project / "calculator.py").read_bytes()).hexdigest()
                pytest_after = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q"], cwd=project, capture_output=True, text=True
                )
                attempts.append(
                    {
                        "attempt": attempt,
                        "tools": [{"name": r.get("tool_name"), "origin": r.get("origin"), "status": r.get("status")} for r in runs],
                        "after_sha256": after,
                        "pytest_after": pytest_after.returncode,
                        "answer_len": result["chars"],
                    }
                )
                if pytest_after.returncode == 0 and after != coding_meta["before_sha256"]:
                    break
            diff = subprocess.run(["git", "diff"], cwd=project, capture_output=True, text=True).stdout
            REPORT["coding"] = {
                "attempts": attempts,
                "final_pytest": attempts[-1]["pytest_after"] if attempts else None,
                "before_sha256": coding_meta["before_sha256"],
                "after_sha256": attempts[-1]["after_sha256"] if attempts else None,
                "diff": diff[:2000],
                "cursor_fixed": False,
                "alex_fixed": bool(attempts and attempts[-1]["pytest_after"] == 0 and attempts[-1]["after_sha256"] != coding_meta["before_sha256"]),
            }

            log("memory/rag")
            general_marker = f"E2E_GENERAL_MARKER_{stamp}"
            project_marker = f"E2E_PROJECT_MARKER_{stamp}"
            rag_marker = f"E2E_RAG_MARKER_{stamp}"
            project_row = api.client.post(
                "/projects", headers=api.headers(), json={"name": f"e2e-{stamp}", "description": project_marker}
            ).json()
            general_mem = api.client.post(
                "/memory",
                headers=api.headers(),
                json={"content": f"Harmless marker {general_marker}", "category": "fact", "importance": 5, "is_pinned": True},
            ).json()
            project_mem = api.client.post(
                "/memory",
                headers=api.headers(),
                json={
                    "content": f"Harmless project marker {project_marker}",
                    "category": "project",
                    "importance": 5,
                    "is_pinned": True,
                    "project_id": project_row["id"],
                },
            ).json()
            rag_status = api.client.get("/rag/model", headers=api.headers()).json()
            rag = {"model_ready": rag_status.get("ready"), "asked": False}
            if rag_status.get("ready"):
                upload = api.client.post(
                    "/documents",
                    headers=api.headers(),
                    files={"file": ("e2e-rag.txt", f"Harmless document {rag_marker}\n", "text/plain")},
                    data={"project_id": project_row["id"]},
                )
                rag["upload"] = upload.status_code
            mem_chat = api.client.post("/chats", headers=api.headers(), json={"title": "memory"}).json()
            api.client.patch(f"/chats/{mem_chat['id']}", headers=api.headers(), json={"project_id": project_row["id"]})
            mem_answer = collect_stream(
                api,
                mem_chat["id"],
                "Повтори все harmless marker строки, которые есть в твоём контексте, без домыслов.",
                web_mode="off",
                computer_mode="off",
            )
            text = mem_answer["text"]
            rag["asked"] = True
            rag["general"] = general_marker in text
            rag["project"] = project_marker in text
            rag["rag"] = rag_marker in text
            REPORT["memory_rag"] = rag
            api.client.delete(f"/memory/{general_mem['id']}", headers=api.headers())
            api.client.delete(f"/memory/{project_mem['id']}", headers=api.headers())

            log("web short")
            tools_status = api.client.get("/tools/status", headers=api.headers()).json()
            REPORT["web"] = {
                "tinyfish_configured": tools_status.get("configured"),
                "search_fetch_free": tools_status.get("search_fetch_free"),
                "agent_calls": 0,
                "browser_calls": 0,
            }
            if tools_status.get("configured"):
                web_chat = api.client.post("/chats", headers=api.headers(), json={"title": "web"}).json()
                web = collect_stream(
                    api,
                    web_chat["id"],
                    "Какая сейчас актуальная стабильная версия Python на python.org?",
                    web_mode="auto",
                    computer_mode="off",
                )
                REPORT["web"]["answer_len"] = web["chars"]
                REPORT["web"]["tools"] = [
                    r.get("tool_name") for r in latest_runs(api, web_chat["id"])
                ]
            else:
                REPORT["web"]["search_fetch"] = "NOT TESTED"

        log("tor search/fetch")
        tor_chat = api.client.post("/chats", headers=api.headers(), json={"title": "tor-direct"}).json()
        tor_search = explicit_tool(
            api,
            tor_chat["id"],
            "tor_search",
            {"query": "Tor Project official onion service"},
            timeout=120,
        )
        REPORT["tor_search_direct"] = tor_search
        sources = []
        for run in latest_runs(api, tor_chat["id"]):
            if run.get("tool_name") == "tor_search":
                REPORT["tor_search_run"] = {
                    "status": run.get("status"),
                    "provider": run.get("provider"),
                    "origin": run.get("origin"),
                    "error": run.get("error_code"),
                    "socks": (run.get("result_metadata") or {}).get("socks"),
                    "transport": (run.get("result_metadata") or {}).get("transport"),
                }
        messages = api.client.get(f"/chats/{tor_chat['id']}/messages", headers=api.headers()).json()
        # explicit execute has no generation snapshots; fetch official onion next
        official = f"http://{discovered['torproject_onion']}/"
        tor_fetch = explicit_tool(api, tor_chat["id"], "tor_fetch", {"urls": [official]}, timeout=120)
        REPORT["tor_fetch_direct"] = tor_fetch
        for run in latest_runs(api, tor_chat["id"]):
            if run.get("tool_name") == "tor_fetch":
                REPORT["tor_fetch_run"] = {
                    "status": run.get("status"),
                    "error": run.get("error_code"),
                    "socks": (run.get("result_metadata") or {}).get("socks"),
                    "authority_meta": True,
                }

        if not skip_gpu:
            log("model-driven tor")
            gpu_guard(gpu_started)
            tor_model_chat = api.client.post("/chats", headers=api.headers(), json={"title": "tor-model"}).json()
            tor_model = collect_stream(
                api,
                tor_model_chat["id"],
                "Через Tor найди официальный onion-ресурс Tor Project, проверь, что адрес подтверждается официальным источником, и открой его через Tor.",
                web_mode="off",
                computer_mode="off",
            )
            tor_model_runs = latest_runs(api, tor_model_chat["id"])
            REPORT["tor_model"] = {
                "answer_len": tor_model["chars"],
                "tools": [
                    {"name": r.get("tool_name"), "origin": r.get("origin"), "status": r.get("status")}
                    for r in tor_model_runs
                ],
            }
            if tor_model_runs:
                last = [m for m in api.client.get(f"/chats/{tor_model_chat['id']}/messages", headers=api.headers()).json() if m.get("role") == "assistant"]
                if last:
                    snaps = api.client.get(f"/messages/{last[-1]['id']}/web-sources", headers=api.headers()).json()
                    REPORT["tor_model"]["sources"] = [
                        {
                            "label": s.get("label"),
                            "channel": s.get("channel"),
                            "authority": s.get("authority"),
                            "kind": s.get("kind"),
                        }
                        for s in snaps
                    ]

            combined_ok = REPORT["coding"].get("alex_fixed") and any(
                r.get("name") in {"tor_search", "tor_fetch"} for r in REPORT["tor_model"].get("tools", [])
            )
            if combined_ok:
                log("combined short")
                gpu_guard(gpu_started)
                (project / "calculator.py").write_text(
                    "def add(a, b):\n    return a + b\n\n\ndef divide(a, b):\n    return a * b\n",
                    encoding="utf-8",
                )
                comb = api.client.post("/chats", headers=api.headers(), json={"title": "combined"}).json()
                api.client.patch(f"/chats/{comb['id']}", headers=api.headers(), json={"project_id": project_row["id"]})
                combined = collect_stream(
                    api,
                    comb["id"],
                    "Найди через обычный web актуальную официальную документацию по небольшой Python-функции, затем проверь мой disposable test project и исправь его.",
                    web_mode="auto",
                    computer_mode="trusted",
                )
                REPORT["combined"] = {
                    "answer_len": combined["chars"],
                    "tools": [
                        {"name": r.get("tool_name"), "origin": r.get("origin"), "status": r.get("status")}
                        for r in latest_runs(api, comb["id"])
                    ],
                }

            log("stop generation")
            gpu_guard(gpu_started)
            stop_chat = api.client.post("/chats", headers=api.headers(), json={"title": "stop"}).json()
            stopped = collect_stream(
                api,
                stop_chat["id"],
                "Напиши очень длинный подробный рассказ о числах от 1 до 200, не останавливайся.",
                abort_after=6,
            )
            messages = api.client.get(f"/chats/{stop_chat['id']}/messages", headers=api.headers()).json()
            assistant = next((m for m in reversed(messages) if m.get("role") == "assistant"), {})
            REPORT["stop"] = {
                "aborted": stopped["aborted"],
                "partial_chars": stopped["chars"],
                "message_status": assistant.get("status"),
                "has_partial": bool(assistant.get("content")),
                "cancellation": bool(assistant.get("cancellation")),
            }

        usages = usage_rows()
        REPORT["usage"] = [
            {
                "provider": row["provider"],
                "status": row["status"],
                "input": row["input_tokens"],
                "output": row["output_tokens"],
                "total": row["total_tokens"],
            }
            for row in usages
        ]
        REPORT["approvals"] = REPORT["approvals"] + approver.approved
    finally:
        log("cleanup gpu")
        try:
            REPORT["compute_stop"] = stop_compute(api)
        except Exception as error:
            REPORT["compute_stop"] = {"error": type(error).__name__}
        time.sleep(3)
        row = session_row()
        REPORT["runpod_final"] = {
            "status": row.get("status"),
            "pod_id": row.get("pod_id"),
            "billable_seconds": row.get("billable_seconds"),
            "estimated_cost": row.get("estimated_cost"),
            "stop_reason": row.get("stop_reason"),
            "volume": row.get("network_volume_id"),
        }
        if approver:
            approver.stop.set()
        if host_proc:
            host_proc.terminate()
            try:
                host_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                host_proc.kill()
        if backend_proc:
            backend_proc.terminate()
            try:
                backend_proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                backend_proc.kill()
        if backend_log:
            backend_log.close()
        evidence.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        log(f"evidence={evidence}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        REPORT["fatal"] = f"{type(error).__name__}"
        stamp = utcnow().strftime("%Y%m%dT%H%M%S")
        path = Path(os.environ.get("TEMP", ".")) / "alex-llm-real-e2e" / f"fatal-{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"FATAL {type(error).__name__} evidence={path}", flush=True)
        raise
