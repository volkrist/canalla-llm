"""Isolated eval backend + native host + optional RunPod via product APIs.

Does not import production Python modules. Does not touch the user DB.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from http_client import Client, HttpError
from paid import HARD_RUNPOD_USD, MAX_HOURLY_USD, MODEL_ALIAS, PREFERRED_DC, PREFERRED_GPU, VOLUME_ID, PaidConfig
from paths import WORKTREE_ROOT

BACKEND = WORKTREE_ROOT / "apps" / "backend"
DESKTOP = WORKTREE_ROOT / "apps" / "desktop" / "src-tauri"
MAIN_BACKEND = WORKTREE_ROOT.parent / "alex-llm" / "apps" / "backend"
MAIN_DESKTOP = WORKTREE_ROOT.parent / "alex-llm" / "apps" / "desktop" / "src-tauri"
RUNPOD_BASE = "https://api.runpod.io/v2"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_file_map() -> dict[str, str]:
    values = {}
    configured = os.environ.get("ALEX_BACKEND_ENV", "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend([BACKEND / ".env", MAIN_BACKEND / ".env"])
    path = next((item for item in candidates if item.is_file()), None)
    if path is None:
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def secret_present(values: dict, key: str) -> bool:
    return bool(values.get(key))


def venv_python() -> Path:
    for candidate in (
        BACKEND / ".venv" / "Scripts" / "python.exe",
        MAIN_BACKEND / ".venv" / "Scripts" / "python.exe",
    ):
        if candidate.exists():
            return candidate
    raise RuntimeError("backend_venv_missing")


def host_bin() -> Path:
    for root in (DESKTOP, MAIN_DESKTOP):
        debug = root / "target" / "debug" / "alex-host-loop.exe"
        release = root / "target" / "release" / "alex-host-loop.exe"
        if debug.exists():
            return debug
        if release.exists():
            return release
    raise RuntimeError("host_bin_missing")


def free_port(start: int = 8010) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no_free_eval_port")


def sqlite_url(path: Path) -> str:
    return "sqlite:///" + path.resolve().as_posix()


def list_runpod_pods(api_key: str) -> list[dict]:
    if not api_key:
        raise RuntimeError("runpod_key_missing")
    python = venv_python()
    script = (
        "import json,os,sys\n"
        "import httpx\n"
        "key=os.environ.get('RUNPOD_API_KEY','')\n"
        "response=httpx.get('https://api.runpod.io/v2/pods', headers={'Authorization':'Bearer '+key}, timeout=30.0, follow_redirects=False)\n"
        "sys.stdout.write(json.dumps({'status': response.status_code, 'body': response.json() if 'json' in response.headers.get('content-type','') else response.text[:300]}))\n"
    )
    env = os.environ.copy()
    env["RUNPOD_API_KEY"] = api_key
    proc = subprocess.run([str(python), "-c", script], env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"runpod_list_failed:{proc.stderr[-200:]}")
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as error:
        raise RuntimeError("runpod_list_malformed") from error
    if payload.get("status") != 200:
        raise RuntimeError(f"runpod_list_http_{payload.get('status')}")
    body = payload.get("body") or {}
    if isinstance(body, dict) and isinstance(body.get("pods"), list):
        return body["pods"]
    raise RuntimeError("runpod_list_malformed")


def terminate_pod(api_key: str, pod_id: str) -> dict:
    """Terminate one GPU pod. Never accepts the network volume id."""
    if not api_key:
        raise RuntimeError("runpod_key_missing")
    if not pod_id or pod_id == VOLUME_ID:
        raise RuntimeError("refusing_volume_or_empty_pod")
    python = venv_python()
    script = (
        "import json,os,sys,httpx\n"
        "pod=sys.argv[1]\n"
        "key=os.environ['RUNPOD_API_KEY']\n"
        "r=httpx.post('https://api.runpod.io/v2/pods/'+pod+'/action', headers={'Authorization':'Bearer '+key}, json={'action':'terminate'}, timeout=30.0)\n"
        "sys.stdout.write(json.dumps({'status': r.status_code, 'text': r.text[:300]}))\n"
    )
    env = os.environ.copy()
    env["RUNPOD_API_KEY"] = api_key
    proc = subprocess.run([str(python), "-c", script, pod_id], env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"runpod_terminate_failed:{proc.stderr[-200:]}")
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"raw": (proc.stdout or "")[:200]}


def wait_gpu_zero(api_key: str, timeout: float = 90) -> int:
    deadline = time.time() + timeout
    running = -1
    while time.time() < deadline:
        running = running_gpu_count(list_runpod_pods(api_key))
        if running == 0:
            return 0
        time.sleep(3)
    return running


def running_gpu_count(pods: list[dict]) -> int:
    n = 0
    for pod in pods:
        status = str(pod.get("desiredStatus") or pod.get("desired_status") or pod.get("status") or "").upper()
        if status in {"RUNNING", "STARTING", "PROVISIONING"}:
            n += 1
    return n


class RealSession:
    def __init__(self, work_dir: Path, paid: PaidConfig, port: int | None = None, workspace_root: Path | None = None):
        self.work_dir = Path(work_dir)
        self.paid = paid
        self.port = port or free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.client = Client(self.base, timeout=120)
        self.backend_proc = None
        self.backend_log = None
        self.host_proc = None
        self.host_log = None
        self.gpu_started_at = None
        self.pod_id = None
        self.user = None
        self.email = None
        self.workspace_root = Path(workspace_root) if workspace_root else self.work_dir / "workspaces"
        self.data_dir = self.work_dir / "app-data"
        self.db_path = self.work_dir / "alex.db"
        self.docs_dir = self.work_dir / "documents"
        self.device_dir = self.work_dir / "device"
        self.env_values = env_file_map()
        self.info: dict = {
            "provider": None,
            "model": None,
            "mock": None,
            "pod_id": None,
            "gpu": None,
            "dc": None,
            "hourly": None,
            "volume": VOLUME_ID,
            "native_host": False,
            "backend_port": self.port,
        }
        self.sentinel_pid = None

    def start(self, gpu: bool = True, host: bool = True) -> dict:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.device_dir.mkdir(parents=True, exist_ok=True)
        if gpu:
            self._assert_gpu_clear()
        print("eval backend starting", flush=True)
        self._start_backend()
        print("eval backend health wait", flush=True)
        self._wait_health()
        print("eval register synthetic user", flush=True)
        self._register()
        self._baseline_prefs()
        if host:
            print("eval native host starting", flush=True)
            self._start_host()
        if gpu:
            self.start_gpu()
        print("eval runtime ready pod=%s gpu=%s" % (self.pod_id, gpu), flush=True)
        return self.info

    def _assert_gpu_clear(self) -> None:
        if not secret_present(self.env_values, "RUNPOD_API_KEY") or not secret_present(self.env_values, "LLM_API_KEY"):
            raise RuntimeError("missing compute credentials in backend .env")
        pods = list_runpod_pods(self.env_values["RUNPOD_API_KEY"])
        running = running_gpu_count(pods)
        self.info["runpod_running_before"] = running
        managed = [item for item in (self.info.get("managed_pod_ids") or []) if item and item != VOLUME_ID]
        if running:
            leftover = [pod for pod in pods if pod.get("id") in managed]
            leftover_running = running_gpu_count(leftover)
            if leftover and leftover_running == running and running <= 1:
                print("eval stopping leftover managed pod %s" % leftover[0].get("id"), flush=True)
                terminate_pod(self.env_values["RUNPOD_API_KEY"], leftover[0]["id"])
                remaining = wait_gpu_zero(self.env_values["RUNPOD_API_KEY"])
                if remaining:
                    raise RuntimeError(f"leftover_gpu_did_not_stop:{remaining}")
                running = 0
                self.info["runpod_running_before"] = 0
            else:
                raise RuntimeError(f"existing_gpu_must_not_start_second_pod:{running}")

    def start_gpu(self) -> dict:
        self._assert_gpu_clear()
        print("eval GPU starting", flush=True)
        self._start_gpu()
        print("eval orcarouter sanity", flush=True)
        self._sanity()
        return self.info

    def _start_backend(self) -> None:
        python = venv_python()
        jwt = secrets.token_hex(32)
        env = os.environ.copy()
        # Isolated profile: never inherit the user DATABASE_URL.
        env.pop("DATABASE_URL", None)
        env.update(
            {
                "DATABASE_URL": sqlite_url(self.db_path),
                "ALEX_LLM_DATA_DIR": str(self.data_dir),
                "DOCUMENT_STORAGE_DIR": str(self.docs_dir),
                "JWT_SECRET": jwt,
                "APP_ENV": "development",
                "LLM_PROVIDER": "llamacpp",
                "LLM_CONNECTION_MODE": "runpod",
                "LLM_MODEL": MODEL_ALIAS,
                "LLM_API_KEY": self.env_values.get("LLM_API_KEY", ""),
                "RUNPOD_API_KEY": self.env_values.get("RUNPOD_API_KEY", ""),
                "RUNPOD_NETWORK_VOLUME_ID": self.env_values.get("RUNPOD_NETWORK_VOLUME_ID") or VOLUME_ID,
                "RUNPOD_DATACENTER": self.env_values.get("RUNPOD_DATACENTER") or PREFERRED_DC,
                "RUNPOD_MAX_HOURLY_PRICE": str(MAX_HOURLY_USD),
                "RUNPOD_MAX_SESSION_BUDGET": str(self.paid.hard_runpod or HARD_RUNPOD_USD),
                "TINYFISH_API_KEY": self.env_values.get("TINYFISH_API_KEY", ""),
                "COMPUTE_BACKGROUND_ENABLED": "true",
                "ALLOW_USER_COMPUTE_START": "true",
                "TOOLS_MAX_LOCAL_CALLS": "24",
                "TOOLS_TASK_MAX_CALLS": "40",
            }
        )
        upgrade = subprocess.run(
            [str(python), "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND),
            env=env,
            capture_output=True,
            text=True,
        )
        if upgrade.returncode != 0:
            raise RuntimeError(f"alembic_failed:{upgrade.stderr[-400:]}")
        log_path = self.work_dir / "backend.log"
        self.backend_log = open(log_path, "ab")
        self.backend_proc = subprocess.Popen(
            [
                str(python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--workers",
                "1",
            ],
            cwd=str(BACKEND),
            env=env,
            stdout=self.backend_log,
            stderr=subprocess.STDOUT,
        )

    def _wait_health(self, timeout: float = 90) -> dict:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                last = self.client.get("/health", timeout=3)
                if isinstance(last, dict) and last.get("status") == "ok":
                    self.info["backend_health"] = last
                    return last
            except Exception as error:
                last = type(error).__name__
                time.sleep(0.4)
        raise RuntimeError(f"backend_unhealthy:{last}")

    def _register(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        self.email = f"eval-093-{stamp}@example.com"
        password = "eval-" + secrets.token_hex(8)
        payload = self.client.post("/auth/register", {"email": self.email, "password": password})
        self.client.token = payload["access_token"]
        self.user = self.client.get("/auth/me")
        try:
            self.client.post("/projects", {"name": "EVAL-093", "description": "synthetic evaluation project"})
        except HttpError:
            pass
        self.client.patch(
            "/profile",
            {
                "display_name": "Eval User",
                "custom_instructions": "EVAL synthetic user. Do not use personal data.",
                "use_memory": True,
                "relevant_memory": True,
                "max_memories": 12,
            },
        )

    def _baseline_prefs(self, extra: dict | None = None) -> None:
        body = {
            "search_enabled": False,
            "fetch_enabled": False,
            "default_mode": "off",
            "agent_mode": "off",
            "browser_mode": "off",
            "agent_enabled": False,
            "browser_enabled": False,
            "tor_enabled": False,
            "computer_mode": "trusted",
            "workspace_roots": [str(self.workspace_root.resolve())],
            "device_display_name": "EVAL native host 093",
            "tinyfish_paid_task_budget_usd": min(self.paid.tinyfish_budget_usd or 0.05, 0.05),
            "browser_max_sessions": 1,
            "browser_max_minutes": 4,
        }
        if extra:
            body.update(extra)
        self.client.put("/tools/preferences", body)
        compute = {
            "selection": "automatic",
            "min_vram_gb": 48,
            "max_hourly_price": str(MAX_HOURLY_USD),
            "session_budget": str(self.paid.hard_runpod),
            "auto_stop_minutes": 10,
            "gpu_id": PREFERRED_GPU,
            "auto_connect": True,
            "auto_search": False,
            "search_interval": 30,
        }
        self.client.put("/compute/preferences", compute)

    def set_case_prefs(self, *, computer: bool, search: bool, browser: bool, agent: bool = False) -> None:
        extra = {
            "computer_mode": "trusted" if computer else "off",
            "search_enabled": bool(search),
            "fetch_enabled": bool(search),
            "default_mode": "on" if (search or browser) else "off",
            "browser_enabled": bool(browser),
            "browser_mode": "auto" if browser else "off",
            "agent_enabled": False,
            "agent_mode": "off",
            "tor_enabled": False,
            "workspace_roots": [str(self.workspace_root.resolve())],
        }
        if agent:
            extra["agent_enabled"] = False
        self._baseline_prefs(extra)

    def _start_host(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "ALEX_BACKEND_URL": self.base,
                "ALEX_TOKEN": self.client.token or "",
                "ALEX_DEVICE_NAME": "EVAL native host 093",
                "ALEX_WORKSPACE_ROOTS": str(self.workspace_root.resolve()),
                "ALEX_DEVICE_DIR": str(self.device_dir),
                "ALEX_DEVICE_CREDENTIAL_TARGET": "Alex LLM/eval-device-credential-093",
                "ALEX_PYTHON": str(venv_python()),
            }
        )
        self.host_log = open(self.work_dir / "host.log", "ab")
        self.host_proc = subprocess.Popen(
            [str(host_bin())],
            cwd=str(DESKTOP),
            env=env,
            stdout=self.host_log,
            stderr=subprocess.STDOUT,
        )
        for _ in range(50):
            try:
                devices = self.client.get("/tools/devices")
            except Exception:
                devices = []
            if devices and devices[0].get("online"):
                self.info["native_host"] = True
                self.info["device_id_prefix"] = (devices[0].get("device_id") or "")[:8]
                return
            time.sleep(0.4)
        raise RuntimeError("native_host_offline")

    def _wait_l40s(self, timeout: float = 25 * 60) -> dict:
        deadline = time.time() + timeout
        last = []
        while time.time() < deadline:
            try:
                options = self.client.get("/compute/options").get("options") or []
            except Exception:
                time.sleep(15)
                continue
            if options:
                last = options
            l40s = next((item for item in options if item.get("id") == PREFERRED_GPU), None)
            price = float((l40s or {}).get("hourly_rate") or 99)
            selectable = bool(l40s and l40s.get("selectable") and price <= MAX_HOURLY_USD)
            self.info["gpu_options"] = [
                {
                    "id": item.get("id"),
                    "availability": item.get("availability"),
                    "selectable": item.get("selectable"),
                    "price": item.get("hourly_rate"),
                }
                for item in options
                if item.get("id") in {PREFERRED_GPU, "NVIDIA A40", "NVIDIA RTX 6000 Ada"}
            ]
            if selectable:
                return l40s
            time.sleep(20)
        raise RuntimeError(f"no_l40s_after_wait:{last[:3]}")

    def _start_gpu(self) -> None:
        pods = list_runpod_pods(self.env_values["RUNPOD_API_KEY"])
        if running_gpu_count(pods):
            raise RuntimeError("existing_gpu_must_not_start_second_pod")
        self._wait_l40s()
        prefs = {
            "selection": "automatic",
            "min_vram_gb": 48,
            "max_hourly_price": str(MAX_HOURLY_USD),
            "session_budget": str(self.paid.hard_runpod),
            "auto_stop_minutes": 10,
            "gpu_id": PREFERRED_GPU,
            "auto_connect": True,
            "auto_search": False,
            "search_interval": 30,
        }
        self.client.put("/compute/preferences", prefs)
        self.gpu_started_at = time.time()
        search = self.client.post("/compute/search", prefs)
        self.paid.runpod_calls += 1
        self.info["compute_search"] = {
            "error": (search or {}).get("error_code") if isinstance(search, dict) else None,
            "state": ((search or {}).get("status") or {}).get("state") if isinstance(search, dict) else None,
        }
        deadline = time.time() + 900
        last = {}
        while time.time() < deadline:
            status = self.client.get("/compute/status")
            llm = self.client.get("/llm/status")
            session = (status or {}).get("session") or {}
            last = {"state": status.get("state"), "llm": llm.get("state"), "pod": session.get("pod_id")}
            pods_now = list_runpod_pods(self.env_values["RUNPOD_API_KEY"])
            if running_gpu_count(pods_now) > 1:
                raise RuntimeError("unexpected_second_pod")
            if (
                status.get("state") in {"ready", "generating"}
                and llm.get("state") == "ready"
                and llm.get("provider") == "llamacpp"
                and session.get("pod_id")
            ):
                self.pod_id = session.get("pod_id")
                self.info.update(
                    {
                        "provider": llm.get("provider"),
                        "model": llm.get("model") or MODEL_ALIAS,
                        "mock": False,
                        "pod_id": self.pod_id,
                        "gpu": session.get("gpu_type") or PREFERRED_GPU,
                        "dc": session.get("datacenter") or PREFERRED_DC,
                        "hourly": session.get("hourly_rate"),
                        "volume": session.get("network_volume_id") or VOLUME_ID,
                    }
                )
                return
            if status.get("state") in {"error", "stopped"} and not session.get("pod_id"):
                raise RuntimeError(f"compute_failed:{status.get('error_code')}")
            time.sleep(3)
        raise RuntimeError(f"model_ready_timeout:{last}")

    def _sanity(self) -> None:
        chat = self.client.post("/chats", {"title": "eval-sanity"})
        text = collect_answer(self.client, chat["id"], "Сколько будет 2+2?", timeout=90, computer_mode="off", web_mode="off")
        usage = self._latest_usage()
        provider = usage.get("provider")
        mock = provider == "mock"
        self.info["sanity"] = {"chars": len(text or ""), "provider": provider, "mock": mock, "answer": (text or "")[:80]}
        self.info["provider"] = provider or self.info.get("provider")
        self.info["mock"] = mock
        if mock or not text:
            raise RuntimeError("orcarouter_sanity_failed")

    def _latest_usage(self) -> dict:
        if not self.db_path.exists():
            return {}
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT provider, status, total_tokens FROM generation_usage ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else {}

    def session_cost(self) -> float:
        if not self.db_path.exists():
            if self.gpu_started_at and self.info.get("hourly"):
                return (time.time() - self.gpu_started_at) / 3600.0 * float(self.info["hourly"])
            return 0.0
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT estimated_cost, hourly_rate, started_at, stopped_at, billable_seconds "
                "FROM compute_sessions ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return 0.0
        live = float(row["estimated_cost"] or 0)
        rate = float(row["hourly_rate"] or self.info.get("hourly") or 1.09)
        billable = float(row["billable_seconds"] or 0)
        started = row["started_at"]
        if started and not row["stopped_at"]:
            try:
                stamp = str(started).replace("Z", "")
                started_dt = datetime.fromisoformat(stamp)
                if started_dt.tzinfo is not None:
                    started_dt = started_dt.astimezone(timezone.utc).replace(tzinfo=None)
                billable = max(billable, (datetime.utcnow() - started_dt).total_seconds())
            except (TypeError, ValueError):
                pass
        wall = billable / 3600.0 * rate if billable else live
        return max(live, wall)

    def stop_gpu(self) -> dict:
        result = {"ok": False}
        try:
            result = self.client.post("/compute/stop", {"after_generation": False}) or {}
        except Exception as error:
            result = {"error": type(error).__name__}
        # Never delete the network volume. Only stop/delete the managed Pod via product API.
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                pods = list_runpod_pods(self.env_values.get("RUNPOD_API_KEY") or "")
                running = running_gpu_count(pods)
            except Exception:
                running = -1
            if running == 0:
                self.info["running_gpu_final"] = 0
                return result
            time.sleep(3)
        self.info["running_gpu_final"] = running_gpu_count(list_runpod_pods(self.env_values.get("RUNPOD_API_KEY") or ""))
        return result

    def stop(self) -> dict:
        cleanup = {"gpu": None, "host": None, "backend": None}
        try:
            cleanup["gpu"] = self.stop_gpu()
        except Exception as error:
            cleanup["gpu"] = {"error": type(error).__name__}
        for proc, handle, key in (
            (self.host_proc, self.host_log, "host"),
            (self.backend_proc, self.backend_log, "backend"),
        ):
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except Exception:
                    proc.kill()
            if handle:
                try:
                    handle.close()
                except Exception:
                    pass
            cleanup[key] = "stopped"
        time.sleep(1.5)
        return cleanup


def collect_answer(client: Client, chat_id: str, content: str, timeout: float = 180, computer_mode="trusted", web_mode="off", tor_mode="off") -> str:
    parts = []
    try:
        for event, payload in client.stream_sse(
            f"/chats/{chat_id}/stream",
            {
                "content": content,
                "web_mode": web_mode,
                "computer_mode": computer_mode,
                "tor_mode": tor_mode,
            },
            timeout=timeout,
        ):
            if event == "delta" and isinstance(payload, dict):
                parts.append(payload.get("content") or "")
            if event in {"done", "error"}:
                break
    except TimeoutError:
        pass
    return "".join(parts)


def latest_runs(client: Client, chat_id: str | None = None) -> list:
    path = "/tools/runs?limit=100"
    if chat_id:
        path += f"&chat_id={chat_id}"
    data = client.get(path)
    return data if isinstance(data, list) else []


def latest_tasks(client: Client, chat_id: str | None = None) -> list:
    path = "/tasks?limit=50"
    if chat_id:
        path += f"&chat_id={chat_id}"
    data = client.get(path)
    return data if isinstance(data, list) else []
