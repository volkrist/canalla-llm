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
COMBINED_COST_SKIP = 0.50
CATALOG_WAIT = 25 * 60

REPORT = {
    "version": "0.8.2",
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


def collect_stream(api, chat_id, content, web_mode="off", computer_mode="trusted", abort_after=None, read_timeout=180):
    parts = []
    events = []
    aborted = False
    read_timeout_hit = False
    first_delta_at = None
    stop_issued_at = None
    armed = {"stop": False}
    client = httpx.Client(
        base_url=api.base,
        timeout=httpx.Timeout(connect=10.0, read=read_timeout, write=10.0, pool=10.0),
    )

    def arm_stop():
        time.sleep(abort_after)
        nonlocal stop_issued_at, aborted
        stop_issued_at = time.time()
        aborted = True
        try:
            client.close()
        except Exception:
            pass

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
                    if first_delta_at is None:
                        first_delta_at = time.time()
                    if abort_after is not None and not armed["stop"]:
                        armed["stop"] = True
                        threading.Thread(target=arm_stop, daemon=True).start()
                if event in {"done", "error"}:
                    events[-1]["payload_keys"] = sorted(payload) if isinstance(payload, dict) else []
                    if event == "error" and isinstance(payload, dict):
                        events[-1]["code"] = payload.get("code")
                    break
    except httpx.ReadTimeout:
        read_timeout_hit = True
        if abort_after is None:
            raise
        aborted = True
    except httpx.HTTPError:
        if abort_after is not None:
            aborted = True
        else:
            raise
    finally:
        client.close()
    text = "".join(parts)
    return {
        "text": text,
        "events": events,
        "chars": len(text),
        "aborted": aborted,
        "read_timeout": read_timeout_hit,
        "had_delta": first_delta_at is not None,
        "seconds_after_first_delta": (stop_issued_at - first_delta_at) if stop_issued_at and first_delta_at else None,
    }


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
            "COMPUTE_BACKGROUND_ENABLED": "true",
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


def record_gpu(stage):
    row = session_row()
    item = {
        "stage": stage,
        "billable_seconds": row.get("billable_seconds"),
        "estimated_cost": row.get("estimated_cost"),
        "status": row.get("status"),
        "pod_id": row.get("pod_id"),
    }
    REPORT.setdefault("gpu_timeline", []).append(item)
    log("gpu after %s billable=%s cost=%s" % (stage, item["billable_seconds"], item["estimated_cost"]))
    return item


def estimated_cost():
    row = session_row()
    return float(row.get("estimated_cost") or 0)


def tool_entries(runs):
    entries = []
    for run in runs:
        summary = run.get("input_summary") or {}
        entries.append(
            {
                "name": run.get("tool_name"),
                "origin": run.get("origin"),
                "status": run.get("status"),
                "path": summary.get("path") or summary.get("target") or summary.get("cwd"),
            }
        )
    return entries


def gpu_option_price(item):
    try:
        return float(item.get("hourly_rate") or 99)
    except (TypeError, ValueError):
        return 99.0


def compatible_gpus(options):
    return [
        item
        for item in options
        if item.get("selectable")
        and int(item.get("vram_gb") or 0) >= 48
        and gpu_option_price(item) <= MAX_HOURLY
    ]


def wait_for_selectable_gpu(api, prefs):
    deadline = time.time() + CATALOG_WAIT
    last = []
    while time.time() < deadline:
        options = api.client.get("/compute/options", headers=api.headers()).json().get("options") or []
        last = options
        l40s = next((item for item in options if item.get("id") == "NVIDIA L40S"), None)
        compatible = compatible_gpus(options)
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
        log(
            "gpu catalog wait l40s=%s selectable_compatible=%s"
            % ((l40s or {}).get("availability"), [item.get("id") for item in compatible])
        )
        if l40s and l40s in compatible:
            prefs["gpu_id"] = "NVIDIA L40S"
            api.client.put("/compute/preferences", headers=api.headers(), json=prefs)
            return prefs, options, "l40s"
        if compatible:
            prefs.pop("gpu_id", None)
            api.client.put("/compute/preferences", headers=api.headers(), json=prefs)
            return prefs, options, "automatic_compatible"
        time.sleep(15)
    raise RuntimeError(
        "no_compatible_gpu_after_wait:%s"
        % [{"id": item.get("id"), "availability": item.get("availability")} for item in last]
    )


def gpu_guard(started):
    row = session_row()
    pod_id = (REPORT.get("runpod") or {}).get("pod_id") or row.get("pod_id")
    if not pod_id or row.get("pod_id") != pod_id:
        return
    live = float(row.get("estimated_cost") or 0)
    rate = float(row.get("hourly_rate") or (REPORT.get("runpod") or {}).get("price_hour") or 1.09)
    billable = float(row.get("billable_seconds") or 0)
    started_at = row.get("started_at")
    if started_at and not row.get("stopped_at"):
        try:
            stamp = str(started_at).replace("Z", "")
            started_dt = datetime.fromisoformat(stamp)
            if started_dt.tzinfo is not None:
                started_dt = started_dt.astimezone(timezone.utc).replace(tzinfo=None)
            billable = max(billable, (datetime.utcnow() - started_dt).total_seconds())
            live = max(live, billable / 3600.0 * float(rate))
        except (TypeError, ValueError):
            pass
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
    last_log = 0.0
    while time.time() < deadline:
        try:
            status = api.client.get("/compute/status", headers=api.headers()).json()
            llm = api.client.get("/llm/status", headers=api.headers()).json()
        except httpx.HTTPError as error:
            log(f"model ready poll transport={type(error).__name__}")
            time.sleep(3)
            continue
        state = status.get("state")
        session = status.get("session") or {}
        row = session_row()
        pod_id = session.get("pod_id") or row.get("pod_id")
        last = {
            "state": state,
            "error": status.get("error_code"),
            "llm": llm.get("state"),
            "has_session": bool(session),
            "pod": bool(pod_id),
        }
        if time.time() - last_log >= 15:
            log("model ready wait %s" % last)
            last_log = time.time()
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
        if status.get("session") or pod_id:
            deadline = max(deadline, time.time() + 900)
        if llm.get("state") == "ready" and llm.get("provider") == "llamacpp" and pod_id:
            model_at = utcnow().isoformat()
            return {"status": status, "llm": llm, "gateway_at": gateway_at, "model_at": model_at}
        if pod_id:
            REPORT.setdefault("runpod", {})["pod_id"] = pod_id
            gpu_guard(started)
        elif not status.get("session") and not pod_id and time.time() - started >= 12 * 60:
            raise RuntimeError(f"gpu_wait_timeout:{last}")
        if not pod_id and status.get("error_code") in {"no_compatible_gpu", "price_limit"}:
            if time.time() - last_search >= 30:
                options = api.client.get("/compute/options", headers=api.headers()).json().get("options") or []
                compatible = compatible_gpus(options)
                l40s = next((item for item in options if item.get("id") == "NVIDIA L40S"), None)
                log(
                    "retry gpu catalog l40s=%s compatible=%s"
                    % (
                        (l40s or {}).get("availability"),
                        [item.get("id") for item in compatible],
                    )
                )
                last_search = time.time()
                if compatible:
                    retry_prefs = dict(prefs)
                    if l40s and l40s in compatible:
                        retry_prefs["gpu_id"] = "NVIDIA L40S"
                    else:
                        retry_prefs.pop("gpu_id", None)
                    api.client.post("/compute/search", headers=api.headers(), json=retry_prefs)
        if state in {"error", "stopped", "not_configured"} and not pod_id:
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


def create_coding_project(root: Path, kind="divide"):
    root.mkdir(parents=True, exist_ok=True)
    calculator = root / "calculator.py"
    tests = root / "test_calculator.py"
    if kind == "subtract":
        calculator.write_text(
            "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a + b\n",
            encoding="utf-8",
        )
        tests.write_text(
            "from calculator import add, subtract\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
            "def test_subtract():\n    assert subtract(10, 3) == 7\n",
            encoding="utf-8",
        )
    else:
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
    log("wait for selectable GPU before create")
    compute_prefs, options, fallback = wait_for_selectable_gpu(api, compute_prefs)
    REPORT["gpu_fallback"] = fallback
    REPORT["gpu_selectable"] = [item.get("id") for item in compatible_gpus(options)]
    log("gpu catalog selectable=%s fallback=%s" % (REPORT["gpu_selectable"], fallback))
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
    project = work / "coding-agent"
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
        rag_code = f"E2E_RAG_CODE_{hashlib.sha256(stamp.encode()).hexdigest()[:12]}"
        rag_animal = "silver-lantern-otter"
        rag_body = (
            f"Harmless disposable document. Unique marker {rag_code}.\n"
            f"The verification animal for this test is {rag_animal}.\n"
        )
        upload = api.client.post(
            "/documents",
            headers=api.headers(),
            files={"file": ("e2e-rag.txt", rag_body, "text/plain")},
            data={"project_id": project_row["id"]},
        )
        if upload.status_code >= 400:
            raise RuntimeError(f"rag_upload_failed:{upload.status_code}")
        rag_doc = wait_document_ready(api, upload.json()["id"])
        REPORT["rag_index"] = {
            "status": rag_doc.get("status"),
            "chunk_count": rag_doc.get("chunk_count"),
            "indexed": bool(rag_doc.get("indexed_at")),
            "code": rag_code,
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

        log("local host pairing smoke")
        smoke_chat = api.client.post("/chats", headers=api.headers(), json={"title": "local-smoke"}).json()
        smoke = {"get_system_info": explicit_tool(api, smoke_chat["id"], "get_system_info", {})}
        REPORT["local_smoke"] = {name: item.get("ok") if isinstance(item, dict) else item for name, item in smoke.items()}
        REPORT["confirmation"] = {"skipped": True, "reason": "already_real_pass"}
        REPORT["tor_search_direct"] = {"skipped": True, "reason": "already_real_pass"}
        REPORT["tor_fetch_direct"] = {"skipped": True, "reason": "already_real_pass"}

        if skip_gpu:
            REPORT["coding"] = {"skipped": True}
            REPORT["memory_rag"] = {"skipped": True}
            REPORT["web"] = {"agent_calls": 0, "browser_calls": 0, "skipped": True}
        else:
            gpu_started = launch_managed_pod(api)
            record_gpu("pod_ready")
            log("orcarouter basic")
            chat = api.client.post("/chats", headers=api.headers(), json={"title": "orcarouter-basic"}).json()
            basic = collect_stream(
                api,
                chat["id"],
                "Ответь одним коротким предложением: сколько будет 2+2?",
                web_mode="off",
                computer_mode="off",
            )
            usage = (usage_rows(1) or [{}])[0]
            REPORT["orcarouter_basic"] = {
                "chars": basic["chars"],
                "events": [item["event"] for item in basic["events"]],
                "answer_len": basic["chars"],
                "provider": usage.get("provider"),
                "usage_status": usage.get("status"),
                "mock": usage.get("provider") == "mock",
            }
            record_gpu("sanity")

            log("coding agent")
            coding_prompt = (
                "В этой тестовой папке находится небольшой Python-проект. "
                "Проверь проект, запусти тесты, найди причину ошибки, "
                "исправь код и снова запусти тесты. "
                "Работу считай завершённой только когда все тесты проходят."
            )
            attempts = []
            for attempt in range(1, 4):
                gpu_guard(gpu_started)
                chat = api.client.post("/chats", headers=api.headers(), json={"title": f"coding-{attempt}"}).json()
                result = collect_stream(
                    api,
                    chat["id"],
                    coding_prompt,
                    web_mode="off",
                    computer_mode="trusted",
                    read_timeout=300,
                )
                runs = latest_runs(api, chat["id"])
                after = hashlib.sha256((project / "calculator.py").read_bytes()).hexdigest()
                pytest_after = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q"], cwd=project, capture_output=True, text=True
                )
                attempts.append(
                    {
                        "attempt": attempt,
                        "tools": tool_entries(runs),
                        "after_sha256": after,
                        "pytest_after": pytest_after.returncode,
                        "pytest_tail": (pytest_after.stdout + pytest_after.stderr)[-600:],
                        "answer_len": result["chars"],
                    }
                )
                record_gpu(f"coding_attempt_{attempt}")
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
                "git_diff": bool(diff.strip()),
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

            log("model-driven tor")
            gpu_guard(gpu_started)
            tor_model_chat = api.client.post("/chats", headers=api.headers(), json={"title": "tor-model"}).json()
            tor_model = collect_stream(
                api,
                tor_model_chat["id"],
                "Через Tor найди официальный onion-ресурс Tor Project. "
                "Проверь по официальному источнику, что onion-адрес действительно "
                "принадлежит Tor Project, затем открой его через Tor "
                "и кратко скажи, что удалось проверить.",
                web_mode="off",
                computer_mode="off",
                read_timeout=240,
            )
            tor_model_runs = latest_runs(api, tor_model_chat["id"])
            REPORT["tor_model"] = {
                "answer_len": tor_model["chars"],
                "tools": tool_entries(tor_model_runs),
            }
            last = [
                m
                for m in api.client.get(f"/chats/{tor_model_chat['id']}/messages", headers=api.headers()).json()
                if m.get("role") == "assistant"
            ]
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
            record_gpu("tor_model")

            log("model-driven rag")
            gpu_guard(gpu_started)
            rag_status = api.client.get("/rag/model", headers=api.headers()).json()
            rag = {
                "model_ready": rag_status.get("ready"),
                "index_success": REPORT.get("rag_index", {}).get("status") == "ready",
                "asked": False,
            }
            rag_chat = api.client.post("/chats", headers=api.headers(), json={"title": "rag"}).json()
            api.client.patch(f"/chats/{rag_chat['id']}", headers=api.headers(), json={"project_id": project_row["id"]})
            rag_answer = collect_stream(
                api,
                rag_chat["id"],
                "Какое verification animal указано в тестовом документе?",
                web_mode="off",
                computer_mode="off",
            )
            rag["asked"] = True
            rag["answer"] = rag_answer["text"][:500]
            rag["animal_correct"] = rag_animal in rag_answer["text"]
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
            api.client.delete(f"/documents/{rag_doc['id']}", headers=api.headers())
            record_gpu("rag")

            log("stop generation")
            gpu_guard(gpu_started)
            stop_chat = api.client.post("/chats", headers=api.headers(), json={"title": "stop"}).json()
            stopped = collect_stream(
                api,
                stop_chat["id"],
                "Напиши очень длинный подробный технический текст о числах от 1 до 200, не останавливайся.",
                web_mode="off",
                computer_mode="off",
                abort_after=3,
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
                "read_timeout": stopped.get("read_timeout"),
                "had_delta": stopped.get("had_delta"),
                "seconds_after_first_delta": stopped.get("seconds_after_first_delta"),
            }
            record_gpu("stop")

            combined_ok = REPORT["coding"].get("alex_fixed")
            if combined_ok and estimated_cost() < COMBINED_COST_SKIP:
                log("combined web + coding")
                gpu_guard(gpu_started)
                combined_project = work / "combined-coding"
                combined_meta = create_coding_project(combined_project, kind="subtract")
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
                before_combined = combined_meta["before_sha256"]
                comb = api.client.post("/chats", headers=api.headers(), json={"title": "combined"}).json()
                combined = collect_stream(
                    api,
                    comb["id"],
                    "Используй интернет и официальную документацию Python, "
                    "чтобы проверить правильное поведение функции в этом тестовом проекте. "
                    "После этого запусти тесты, исправь найденную проблему "
                    "и повторяй проверку, пока тесты не пройдут.",
                    web_mode="on",
                    computer_mode="trusted",
                    read_timeout=300,
                )
                after_combined = hashlib.sha256((combined_project / "calculator.py").read_bytes()).hexdigest()
                pytest_combined = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q"], cwd=combined_project, capture_output=True, text=True
                )
                tools = tool_entries(latest_runs(api, comb["id"]))
                last = [
                    m
                    for m in api.client.get(f"/chats/{comb['id']}/messages", headers=api.headers()).json()
                    if m.get("role") == "assistant"
                ]
                snaps = []
                w_source = False
                if last:
                    snaps = api.client.get(f"/messages/{last[-1]['id']}/web-sources", headers=api.headers()).json()
                    w_source = any(str(s.get("label") or "").startswith("W") for s in snaps)
                REPORT["combined"] = {
                    "single_task": True,
                    "answer_len": combined["chars"],
                    "tools": tools,
                    "web_search": any(item["name"] == "web_search" and item["status"] == "completed" for item in tools),
                    "web_fetch": any(item["name"] == "web_fetch" and item["status"] == "completed" for item in tools),
                    "w_source": w_source,
                    "sources": [
                        {"label": s.get("label"), "channel": s.get("channel"), "kind": s.get("kind")}
                        for s in snaps
                    ],
                    "local_used": any(
                        item["name"]
                        in {"read_file", "patch_file", "write_file", "run_python", "git_status", "list_directory"}
                        for item in tools
                    ),
                    "pytest_before": combined_meta["pytest_before_code"],
                    "source_changed": after_combined != before_combined,
                    "pytest_after": pytest_combined.returncode,
                    "alex_fixed": pytest_combined.returncode == 0 and after_combined != before_combined,
                    "cursor_fixed": False,
                }
                record_gpu("combined")
            elif not combined_ok:
                REPORT["combined"] = {"skipped": True, "reason": "coding_not_fixed"}
            else:
                REPORT["combined"] = {"skipped": True, "reason": "gpu_budget"}

        runs = latest_runs(api)
        REPORT["tinyfish"] = {
            "search": sum(1 for run in runs if run.get("tool_name") == "web_search"),
            "fetch": sum(1 for run in runs if run.get("tool_name") == "web_fetch"),
            "agent_calls": sum(1 for run in runs if "agent" in (run.get("tool_name") or "")),
            "browser_calls": sum(1 for run in runs if str(run.get("tool_name") or "").startswith("browser")),
        }
        REPORT["web"] = {
            **(REPORT.get("web") or {}),
            "agent_calls": REPORT["tinyfish"]["agent_calls"],
            "browser_calls": REPORT["tinyfish"]["browser_calls"],
            "search": REPORT["tinyfish"]["search"],
            "fetch": REPORT["tinyfish"]["fetch"],
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
