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
    "version": "0.8.1",
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
    aborted = False
    client = httpx.Client(
        base_url=api.base,
        timeout=httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=10.0),
    )
    if abort_after:
        threading.Thread(target=lambda: (time.sleep(abort_after), client.close()), daemon=True).start()
    try:
        with client.stream(
            "POST",
            f"/chats/{chat_id}/stream",
            headers=api.headers(),
            json={"content": content, "web_mode": web_mode, "computer_mode": computer_mode},
        ) as response:
            if response.status_code != 200:
                body = response.read()
                raise RuntimeError(f"stream_http_{response.status_code}:{body[:300]!r}")
            for event, payload in sse_events(response):
                events.append({"event": event})
                if event == "delta" and isinstance(payload, dict):
                    parts.append(payload.get("content") or "")
                if event in {"done", "error"}:
                    events[-1]["payload_keys"] = sorted(payload) if isinstance(payload, dict) else []
                    if event == "error" and isinstance(payload, dict):
                        events[-1]["code"] = payload.get("code")
                    break
    except httpx.HTTPError:
        if abort_after:
            aborted = True
        else:
            raise
    finally:
        client.close()
    text = "".join(parts)
    return {"text": text, "events": events, "chars": len(text), "aborted": aborted}


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
        self.paused = threading.Event()
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
            if self.paused.is_set():
                continue
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
            "COMPUTE_BACKGROUND_ENABLED": "false",
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
    row = session_row()
    pod_id = (REPORT.get("runpod") or {}).get("pod_id")
    if not pod_id or row.get("pod_id") != pod_id:
        return
    live = float(row.get("estimated_cost") or 0)
    rate = float(row.get("hourly_rate") or (REPORT.get("runpod") or {}).get("price_hour") or 1.09)
    billable = float(row.get("billable_seconds") or 0)
    wall_cost = (billable / 3600.0 * float(rate)) if billable else live
    cost = max(live, wall_cost)
    if billable >= GPU_WALL or cost >= GPU_COST_STOP:
        raise RuntimeError(f"gpu_limit elapsed={int(billable)}s cost={cost}")


def wait_model_ready(api, started, prefs, timeout=720):
    gateway_at = None
    model_at = None
    deadline = time.time() + timeout
    last = {}
    last_search = 0.0
    while time.time() < deadline:
        status = api.client.get("/compute/status", headers=api.headers()).json()
        state = status.get("state")
        session = status.get("session") or {}
        llm = api.client.get("/llm/status", headers=api.headers()).json()
        last = {
            "state": state,
            "error": status.get("error_code"),
            "llm": llm.get("state"),
            "has_session": bool(session),
            "pod": bool(session.get("pod_id")),
        }
        if state in {
            "connecting",
            "starting_llm",
            "ready",
            "generating",
            "starting_pod",
            "starting_environment",
            "mounting_storage",
            "creating",
        } and gateway_at is None:
            gateway_at = utcnow().isoformat()
        if status.get("session"):
            deadline = max(deadline, time.time() + 900)
        if llm.get("state") == "ready" and llm.get("provider") == "llamacpp" and session.get("pod_id"):
            model_at = utcnow().isoformat()
            return {"status": status, "llm": llm, "gateway_at": gateway_at, "model_at": model_at}
        if session.get("pod_id"):
            REPORT.setdefault("runpod", {})["pod_id"] = session.get("pod_id")
            gpu_guard(started)
        elif not status.get("session") and time.time() - started >= 12 * 60:
            raise RuntimeError(f"gpu_wait_timeout:{last}")
        if not session.get("pod_id") and status.get("error_code") in {"no_compatible_gpu", "price_limit"}:
            if time.time() - last_search >= 30:
                options = api.client.get("/compute/options", headers=api.headers()).json().get("options") or []
                l40s = next((item for item in options if item.get("id") == "NVIDIA L40S"), None)
                retry_prefs = dict(prefs)
                if l40s and l40s.get("selectable"):
                    retry_prefs["gpu_id"] = "NVIDIA L40S"
                else:
                    retry_prefs.pop("gpu_id", None)
                log(
                    "retry gpu search l40s=%s selectable=%s"
                    % (
                        (l40s or {}).get("availability"),
                        sum(1 for item in options if item.get("selectable")),
                    )
                )
                api.client.post("/compute/search", headers=api.headers(), json=retry_prefs)
                last_search = time.time()
        if state in {"error", "stopped", "not_configured"} and not session.get("pod_id"):
            if status.get("error_code") not in {"no_compatible_gpu", "price_limit", None}:
                raise RuntimeError(f"compute_failed:{state}:{status.get('error_code')}")
        time.sleep(3)
    raise RuntimeError(f"model_ready_timeout:{last}")


def wait_rag_ready(api, timeout=900):
    last = {}
    deadline = time.time() + timeout
    api.client.post("/rag/model/prepare", headers=api.headers())
    while time.time() < deadline:
        last = api.client.get("/rag/model", headers=api.headers()).json()
        if last.get("ready"):
            return last
        if last.get("state") in {"FAILED", "CANCELLED"} and not last.get("ready"):
            raise RuntimeError(f"rag_failed:{last.get('state')}:{last.get('error')}")
        time.sleep(2)
    raise RuntimeError(f"rag_timeout:{last}")


def wait_document_ready(api, doc_id, timeout=180):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = api.client.get(f"/documents/{doc_id}", headers=api.headers()).json()
        if last.get("status") == "ready" and last.get("indexed_at"):
            return last
        if last.get("status") == "failed":
            raise RuntimeError(f"document_index_failed:{last.get('error_message')}")
        time.sleep(1)
    raise RuntimeError(f"document_index_timeout:{last}")


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


def launch_managed_pod(api):
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
    log("gpu catalog selectable=%s fallback=%s" % (len(selectable), REPORT.get("gpu_fallback", "l40s")))
    gpu_started = time.time()
    log("start ONE managed pod")
    search = api.client.post("/compute/search", headers=api.headers(), json=compute_prefs)
    search_body = search.json() if search.headers.get("content-type", "").startswith("application/json") else {}
    REPORT["compute_search_http"] = search.status_code
    REPORT["compute_search"] = {
        "error": search_body.get("error_code") or (search_body.get("status") or {}).get("error_code"),
        "selected": search_body.get("selected_gpu_id"),
        "existing": search_body.get("existing"),
        "state": (search_body.get("status") or {}).get("state"),
    }
    ready = wait_model_ready(api, gpu_started, compute_prefs)
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
    return gpu_started


def main():
    stamp = utcnow().strftime("%Y%m%dT%H%M%S")
    work = Path(os.environ["TEMP"]) / "alex-llm-real-e2e" / stamp
    work.mkdir(parents=True, exist_ok=True)
    project = work / "alex-coding-e2e"
    host_dir = work / "host-data"
    host_dir.mkdir(parents=True, exist_ok=True)
    evidence = work / "evidence.json"
    hold_flag = work / "hold.flag"
    wrong_flag = work / "wrong-digest.flag"
    log("create disposable coding project")
    coding_meta = create_coding_project(project)
    REPORT["coding_initial"] = {
        "path": str(project),
        "pytest_before": coding_meta["pytest_before_code"],
        "before_sha256": coding_meta["before_sha256"],
        "pytest_before_tail": coding_meta["pytest_before_tail"],
    }
    if coding_meta["pytest_before_code"] == 0:
        raise RuntimeError("coding_fixture_not_failing")
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
    log(f"skip_gpu={skip_gpu}")
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
                "workspace_roots": [str(project), str(work)],
                "device_display_name": "E2E native host",
            },
        )

        log("prepare RAG embedding model")
        rag_model = wait_rag_ready(api)
        REPORT["rag_model"] = {
            "ready": rag_model.get("ready"),
            "state": rag_model.get("state"),
            "model": rag_model.get("model"),
        }
        if not rag_model.get("ready"):
            raise RuntimeError("rag_model_not_ready")

        project_row = api.client.post(
            "/projects", headers=api.headers(), json={"name": f"e2e-{stamp}", "description": "disposable e2e"}
        ).json()
        rag_marker = f"E2E_RAG_08_1_{hashlib.sha256(stamp.encode()).hexdigest()[:12]}"
        upload = api.client.post(
            "/documents",
            headers=api.headers(),
            files={"file": ("e2e-rag.txt", f"Harmless disposable document. Unique marker {rag_marker}\n", "text/plain")},
            data={"project_id": project_row["id"]},
        )
        if upload.status_code >= 400:
            raise RuntimeError(f"rag_upload_failed:{upload.status_code}")
        rag_doc = wait_document_ready(api, upload.json()["id"])
        REPORT["rag_index"] = {
            "status": rag_doc.get("status"),
            "chunk_count": rag_doc.get("chunk_count"),
            "indexed": bool(rag_doc.get("indexed_at")),
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
                "ALEX_WORKSPACE_ROOTS": f"{project};{work}",
                "ALEX_DEVICE_DIR": str(host_dir),
                "ALEX_DEVICE_CREDENTIAL_TARGET": "Alex LLM/e2e-device-credential",
                "ALEX_PYTHON": str(BACKEND / ".venv" / "Scripts" / "python.exe"),
                "ALEX_E2E_HOLD_FLAG": str(hold_flag),
                "ALEX_E2E_WRONG_DIGEST_FLAG": str(wrong_flag),
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
        if skip_gpu:
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
        else:
            smoke["long_run"] = {"skipped": True, "reason": "native_process_stop_already_real_pass"}
        REPORT["local_smoke"] = {name: item.get("ok") if isinstance(item, dict) else item for name, item in smoke.items()}

        log("digest mismatch with live device")
        approver.paused.set()
        hold_flag.write_text("1", encoding="utf-8")
        mismatch = {"tested": False, "paired_device": True}
        api.client.put(
            "/tools/preferences",
            headers=api.headers(),
            json={
                "tor_enabled": True,
                "computer_mode": "ask",
                "workspace_roots": [str(project), str(work)],
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
        for _ in range(80):
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
            host_status = None
            for _ in range(80):
                host_status = api.client.get(f"/tools/runs/{waiting['id']}", headers=api.headers()).json()
                if host_status.get("status") == "waiting_host":
                    break
                time.sleep(0.25)
            wrong_report = wrong_flag.with_suffix(".json")
            wrong_flag.write_text("1", encoding="utf-8")
            hold_flag.unlink(missing_ok=True)
            device_mismatch = None
            for _ in range(80):
                if wrong_report.exists():
                    device_mismatch = json.loads(wrong_report.read_text(encoding="utf-8"))
                    break
                time.sleep(0.25)
            replay = api.client.post(
                f"/tools/runs/{waiting['id']}/confirm", headers=api.headers(), json={"allow": True}
            )
            jwt_only = api.client.post(
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
            mismatch = {
                "tested": True,
                "paired_device": True,
                "correct_device_auth": bool((device_mismatch or {}).get("device_auth")),
                "first": first.status_code,
                "waiting_host": (host_status or {}).get("status"),
                "payload_mutation_expected": 409,
                "payload_mutation_actual": (device_mismatch or {}).get("http"),
                "replay_expected": 409,
                "replay_actual": replay.status_code,
                "jwt_only_expected": 401,
                "jwt_only_actual": jwt_only.status_code,
                "method": "native_host_wrong_digest",
            }
            REPORT["approvals"].append({"id": waiting["id"], "tool": "write_file", "method": "e2e_harness"})
        hold_flag.unlink(missing_ok=True)
        wrong_flag.unlink(missing_ok=True)
        approver.paused.clear()
        REPORT["confirmation"] = mismatch
        api.client.put(
            "/tools/preferences",
            headers=api.headers(),
            json={
                "tor_enabled": True,
                "computer_mode": "trusted",
                "workspace_roots": [str(project), str(work)],
                "default_mode": "off",
                "agent_enabled": False,
                "browser_enabled": False,
            },
        )

        log("tor search/fetch direct provider")
        tor_chat = api.client.post("/chats", headers=api.headers(), json={"title": "tor-direct"}).json()
        tor_search = explicit_tool(
            api,
            tor_chat["id"],
            "tor_search",
            {"query": "Tor Project official onion service"},
            timeout=120,
        )
        REPORT["tor_search_direct"] = tor_search
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

        if skip_gpu:
            REPORT["coding"] = {"skipped": True}
            REPORT["memory_rag"] = {"skipped": True}
            REPORT["web"] = {"agent_calls": 0, "browser_calls": 0, "skipped": True}
        else:
            gpu_started = launch_managed_pod(api)
            log("orcarouter basic")
            chat = api.client.post("/chats", headers=api.headers(), json={"title": "orcarouter-basic"}).json()
            basic = collect_stream(
                api,
                chat["id"],
                "Ответь одним коротким предложением: сколько будет 2+2?",
                web_mode="off",
                computer_mode="off",
            )
            REPORT["orcarouter_basic"] = {
                "chars": basic["chars"],
                "events": [item["event"] for item in basic["events"]],
                "answer_len": basic["chars"],
                "provider": (usage_rows(1) or [{}])[0].get("provider"),
                "usage_status": (usage_rows(1) or [{}])[0].get("status"),
            }
            log("coding agent")
            coding_prompt = (
                "В этой тестовой папке есть небольшой Python-проект. "
                "Проверь его, запусти тесты, найди причину ошибки, "
                "исправь код и снова запусти тесты. "
                "Работу считай завершённой только когда тесты проходят."
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
            all_tools = [item for attempt in attempts for item in attempt["tools"]]
            REPORT["coding"] = {
                "attempts": attempts,
                "attempt_count": len(attempts),
                "final_pytest": attempts[-1]["pytest_after"] if attempts else None,
                "before_sha256": coding_meta["before_sha256"],
                "after_sha256": attempts[-1]["after_sha256"] if attempts else None,
                "diff": diff[:2000],
                "cursor_fixed": False,
                "model_origin": any(item.get("origin") == "model" for item in all_tools),
                "model_patch_or_write": any(
                    item.get("name") in {"patch_file", "write_file"} and item.get("origin") == "model"
                    for item in all_tools
                ),
                "alex_fixed": bool(
                    attempts
                    and attempts[-1]["pytest_after"] == 0
                    and attempts[-1]["after_sha256"] != coding_meta["before_sha256"]
                ),
            }

            log("memory/rag")
            general_marker = f"E2E_GENERAL_MARKER_{stamp}"
            project_marker = f"E2E_PROJECT_MARKER_{stamp}"
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
            rag = {
                "model_ready": rag_status.get("ready"),
                "index_success": REPORT.get("rag_index", {}).get("status") == "ready",
                "asked": False,
            }
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
            rag_chat = api.client.post("/chats", headers=api.headers(), json={"title": "rag"}).json()
            api.client.patch(f"/chats/{rag_chat['id']}", headers=api.headers(), json={"project_id": project_row["id"]})
            rag_answer = collect_stream(
                api,
                rag_chat["id"],
                f"Какой уникальный маркер указан в загруженном harmless disposable document? "
                "Повтори маркер точно, без домыслов.",
                web_mode="off",
                computer_mode="off",
            )
            rag["rag"] = rag_marker in rag_answer["text"]
            assistant_msgs = [
                m
                for m in api.client.get(f"/chats/{rag_chat['id']}/messages", headers=api.headers()).json()
                if m.get("role") == "assistant"
            ]
            rag["d_source"] = False
            if assistant_msgs:
                ctx = api.client.get(
                    f"/messages/{assistant_msgs[-1]['id']}/context", headers=api.headers()
                ).json()
                snaps = ctx.get("sources") or []
                rag["sources"] = [
                    {"label": s.get("label"), "kind": s.get("kind"), "channel": s.get("channel")} for s in snaps
                ]
                rag["d_source"] = any(str(s.get("label") or "").startswith("D") for s in snaps)
                rag["document_count"] = ctx.get("document_count")
            REPORT["memory_rag"] = rag
            api.client.delete(f"/memory/{general_mem['id']}", headers=api.headers())
            api.client.delete(f"/memory/{project_mem['id']}", headers=api.headers())
            api.client.delete(f"/documents/{rag_doc['id']}", headers=api.headers())

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

            combined_ok = REPORT["coding"].get("alex_fixed")
            if combined_ok:
                log("combined web + coding")
                gpu_guard(gpu_started)
                combined_project = work / "alex-coding-combined"
                create_coding_project(combined_project)
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
                        "workspace_roots": [str(combined_project), str(work)],
                    },
                )
                before_combined = hashlib.sha256((combined_project / "calculator.py").read_bytes()).hexdigest()
                comb = api.client.post("/chats", headers=api.headers(), json={"title": "combined"}).json()
                combined = collect_stream(
                    api,
                    comb["id"],
                    "Проверь актуальную официальную документацию Python по нужному поведению этой функции, "
                    "затем проверь тестовый проект, исправь проблему и запусти тесты повторно.",
                    web_mode="auto",
                    computer_mode="trusted",
                )
                after_combined = hashlib.sha256((combined_project / "calculator.py").read_bytes()).hexdigest()
                pytest_combined = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q"], cwd=combined_project, capture_output=True, text=True
                )
                tools = [
                    {"name": r.get("tool_name"), "origin": r.get("origin"), "status": r.get("status")}
                    for r in latest_runs(api, comb["id"])
                ]
                REPORT["combined"] = {
                    "answer_len": combined["chars"],
                    "tools": tools,
                    "web_used": any(item["name"] in {"web_search", "web_fetch"} for item in tools),
                    "local_used": any(
                        item["name"] in {"read_file", "patch_file", "write_file", "run_python", "git_status", "list_directory"}
                        for item in tools
                    ),
                    "source_changed": after_combined != before_combined,
                    "pytest_after": pytest_combined.returncode,
                }
            else:
                REPORT["combined"] = {"skipped": True, "reason": "coding_not_fixed"}

            log("stop generation")
            gpu_guard(gpu_started)
            stop_chat = api.client.post("/chats", headers=api.headers(), json={"title": "stop"}).json()
            stopped = collect_stream(
                api,
                stop_chat["id"],
                "Напиши очень длинный подробный рассказ о числах от 1 до 200, не останавливайся.",
                web_mode="off",
                computer_mode="off",
                abort_after=8,
            )
            time.sleep(2)
            messages = api.client.get(f"/chats/{stop_chat['id']}/messages", headers=api.headers()).json()
            assistant = next((m for m in reversed(messages) if m.get("role") == "assistant"), {})
            stop_usage = next((row for row in usage_rows() if row.get("chat_id") == stop_chat["id"]), {})
            REPORT["stop"] = {
                "aborted": stopped["aborted"],
                "partial_chars": stopped["chars"],
                "message_status": assistant.get("status"),
                "has_partial": bool(assistant.get("content") or stopped["chars"]),
                "cancellation": assistant.get("cancellation"),
                "usage_status": stop_usage.get("status"),
                "read_timeout": False,
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
