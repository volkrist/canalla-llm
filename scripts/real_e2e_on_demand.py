"""Isolated on-demand GPU E2E. Does not touch port 8000 or the developer DB. TinyFish unused."""

from __future__ import annotations

import json
import os
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
VOLUME_ID = "uwgeaie5b0"
PORT = 8018
BASE = f"http://127.0.0.1:{PORT}"
HARD_USD = 0.30
TARGET_USD = 0.20
PROMPT = "Сколько будет 2+2? Ответь одним коротким предложением."


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


def sse_events(response):
    event, data = "message", []
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


class Approver(threading.Thread):
    def __init__(self, api):
        super().__init__(daemon=True)
        self.api = api
        self.stop = threading.Event()
        self.approved = []

    def run(self):
        while not self.stop.wait(0.4):
            try:
                runs = self.api.client.get("/tools/runs", headers=self.api.headers(), params={"limit": 50}).json()
            except Exception:
                continue
            for run in runs:
                if run.get("status") != "waiting_confirmation":
                    continue
                if run.get("tool_name") != "compute.start":
                    continue
                body = {"allow": True}
                digest = run.get("input_digest")
                if digest:
                    body["digest"] = digest
                response = self.api.client.post(
                    f"/tools/runs/{run['id']}/confirm",
                    headers=self.api.headers(),
                    json=body,
                )
                self.approved.append({"id": run["id"], "http": response.status_code})
                log("confirmed compute.start http=%s" % response.status_code)


class Api:
    def __init__(self, base=BASE):
        self.base = base
        self.client = httpx.Client(base_url=base, timeout=120.0)
        self.token = None

    def headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def register(self, email, password):
        response = self.client.post("/auth/register", json={"email": email, "password": password})
        response.raise_for_status()
        self.token = response.json()["access_token"]
        return self.client.get("/auth/me", headers=self.headers()).json()

    def login(self, email, password):
        response = self.client.post("/auth/login", json={"email": email, "password": password})
        response.raise_for_status()
        self.token = response.json()["access_token"]
        return self.client.get("/auth/me", headers=self.headers()).json()


def runpod_headers(key):
    return {"Authorization": "Bearer " + key}


def volume_pods(key):
    response = httpx.get("https://api.runpod.io/v2/pods", headers=runpod_headers(key), timeout=30)
    response.raise_for_status()
    data = response.json()
    pods = data.get("pods") if isinstance(data, dict) else data
    live = []
    for pod in pods or []:
        status = str(pod.get("status") or "")
        if status in {"EXITED", "TERMINATED"}:
            continue
        mounts = ((pod.get("mounts") or {}).get("network")) or []
        if any(mount.get("volumeId") == VOLUME_ID for mount in mounts):
            live.append(
                {
                    "id": pod.get("id"),
                    "name": pod.get("name"),
                    "status": status,
                    "cost": pod.get("cost"),
                    "gpu": (pod.get("gpu") or {}).get("id"),
                    "dc": pod.get("dataCenterId") or pod.get("dataCenter"),
                }
            )
    return live


def volume_exists(key):
    response = httpx.get(
        f"https://api.runpod.io/v2/network-volumes/{VOLUME_ID}",
        headers=runpod_headers(key),
        timeout=30,
    )
    return response.status_code == 200, response.status_code


def start_isolated(work: Path, file_env: dict, runtime_token: str):
    db = work / "e2e.db"
    data = work / "data"
    data.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": "sqlite:///" + db.as_posix(),
            "JWT_SECRET": "on-demand-e2e-jwt-secret-value-not-for-prod-32",
            "APP_ENV": "development",
            "LLM_PROVIDER": "llamacpp",
            "LLM_CONNECTION_MODE": "runpod",
            "LLM_MODEL": file_env.get("LLM_MODEL") or "orcarouter-qwen38-27b-q5km",
            "LLM_API_KEY": file_env.get("LLM_API_KEY") or "",
            "RUNPOD_API_KEY": file_env.get("RUNPOD_API_KEY") or "",
            "RUNPOD_NETWORK_VOLUME_ID": VOLUME_ID,
            "RUNPOD_DATACENTER": "US-TX-3",
            "TINYFISH_API_KEY": "",
            "COMPUTE_BACKGROUND_ENABLED": "true",
            "COMPUTE_POLL_SECONDS": "5",
            "ALLOW_USER_COMPUTE_START": "false",
            "ADMIN_EMAILS": "[]",
            "ALEX_LLM_DATA_DIR": str(data),
            "ALEX_RUNTIME_TOKEN": runtime_token,
            "DOCUMENT_STORAGE_DIR": str(work / "documents"),
            "CORS_ORIGINS": f'["http://127.0.0.1:{PORT}"]',
        }
    )
    python = BACKEND / ".venv" / "Scripts" / "python.exe"
    upgrade = subprocess.run(
        [str(python), "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND),
        env=env,
        capture_output=True,
        text=True,
    )
    if upgrade.returncode != 0:
        raise RuntimeError("alembic_failed:" + (upgrade.stderr or upgrade.stdout)[-500:])
    log_path = work / "backend.log"
    handle = open(log_path, "ab")
    process = subprocess.Popen(
        [
            str(python),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--workers",
            "1",
        ],
        cwd=str(BACKEND),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    return process, handle, env, db


def wait_health(timeout=90):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = httpx.get(BASE + "/health", timeout=2).json()
            if last.get("status") == "ok":
                return last
        except Exception as error:
            last = type(error).__name__
        time.sleep(0.4)
    raise RuntimeError(f"backend_unhealthy:{last}")


def stream_message(api: Api, chat_id: str, content: str, timeout=900):
    parts, events, progress = [], [], []
    client = httpx.Client(
        base_url=api.base, timeout=httpx.Timeout(connect=10.0, read=timeout, write=10.0, pool=10.0)
    )
    try:
        with client.stream(
            "POST",
            f"/chats/{chat_id}/stream",
            headers=api.headers(),
            json={
                "content": content,
                "web_mode": "off",
                "computer_mode": "off",
                "tor_mode": "off",
            },
        ) as response:
            if response.status_code != 200:
                raise RuntimeError(f"stream_http_{response.status_code}:{response.read()[:300]!r}")
            for event, payload in sse_events(response):
                events.append(event)
                if event == "delta" and isinstance(payload, dict):
                    parts.append(payload.get("content") or "")
                if event == "progress" and isinstance(payload, dict):
                    progress.append(payload.get("text") or payload.get("state"))
                if event in {"done", "error"}:
                    return {
                        "text": "".join(parts),
                        "events": events,
                        "progress": progress,
                        "terminal": event,
                        "payload": payload if isinstance(payload, dict) else {},
                    }
    finally:
        client.close()
    return {
        "text": "".join(parts),
        "events": events,
        "progress": progress,
        "terminal": "closed",
        "payload": {},
    }


def wait_complete(api: Api, chat_id: str, timeout=900):
    deadline = time.time() + timeout
    last = []
    while time.time() < deadline:
        last = api.client.get(f"/chats/{chat_id}/messages", headers=api.headers()).json()
        assistant = next((row for row in reversed(last) if row.get("role") == "assistant"), None)
        if assistant and assistant.get("status") == "complete" and (assistant.get("content") or "").strip():
            return last, assistant
        time.sleep(2)
    raise RuntimeError("message_not_complete")


def set_idle_one_minute(db: Path):
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE compute_sessions SET auto_stop_minutes=1 WHERE stopped_at IS NULL AND managed=1"
        )
        connection.commit()


def estimated(api: Api):
    status = api.client.get("/llm/status", headers=api.headers()).json()
    diagnostic = status.get("diagnostic") or {}
    try:
        return float(diagnostic.get("estimated_spend") or 0), status
    except (TypeError, ValueError):
        return 0.0, status


def stop_backend(process, handle):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
    handle.close()


def main():
    report = {
        "version": "0.9.3",
        "tinyfish": 0,
        "initial_gpu": None,
        "final_gpu": None,
        "volume_preserved": None,
        "duplicate_pod": None,
        "recovery": "skipped",
    }
    file_env = env_file_map()
    key = file_env.get("RUNPOD_API_KEY") or ""
    llm_key = file_env.get("LLM_API_KEY") or ""
    if len(key) < 16 or len(llm_key) < 32:
        raise SystemExit("missing_runpod_or_llm_key")
    live = volume_pods(key)
    report["initial_gpu"] = len(live)
    report["initial_pods"] = live
    log("initial volume pods=%s" % len(live))
    if live:
        report["aborted"] = "gpu_not_zero"
        Path(os.environ["TEMP"], "alex-on-demand-e2e.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        raise SystemExit("start_state_gpu_not_zero")
    ok, volume_http = volume_exists(key)
    if not ok:
        raise SystemExit(f"volume_missing:{volume_http}")
    work = Path(os.environ["TEMP"]) / "alex-llm-on-demand-e2e" / utcnow().strftime("%Y%m%dT%H%M%S")
    work.mkdir(parents=True, exist_ok=True)
    runtime_token = "on-demand-e2e-runtime-token-value-32chars"
    process = handle = None
    api = Api()
    approver = None
    try:
        process, handle, env, db = start_isolated(work, file_env, runtime_token)
        health = wait_health()
        log("isolated backend ready provider=%s llm_ready=%s" % (health.get("provider"), health.get("llm_ready")))
        report["health"] = {"provider": health.get("provider"), "llm_ready": health.get("llm_ready")}
        if health.get("provider") == "mock":
            raise RuntimeError("mock_masquerade")
        email = f"on-demand-{int(time.time())}@example.com"
        password = "test-password-123"
        user = api.register(email, password)
        report["user_id"] = user["id"]
        api.client.put(
            "/compute/preferences",
            headers=api.headers(),
            json={
                "selection": "automatic",
                "min_vram_gb": 48,
                "max_hourly_price": "1.20",
                "session_budget": "0.30",
                "auto_stop_minutes": 10,
                "auto_connect": False,
                "auto_search": False,
                "search_interval": 30,
            },
        )
        before = api.client.get("/llm/status", headers=api.headers()).json()
        report["ai_before"] = {"ai": before.get("ai"), "available": before.get("available")}
        if before.get("ai") == "ready" and before.get("provider") == "mock":
            raise RuntimeError("mock_ready")
        chat = api.client.post("/chats", headers=api.headers(), json={"title": "on-demand"}).json()
        report["chat_id"] = chat["id"]
        approver = Approver(api)
        approver.start()
        started = time.time()
        first = stream_message(api, chat["id"], PROMPT)
        report["first_stream"] = {
            "terminal": first["terminal"],
            "chars": len(first["text"]),
            "progress": first["progress"][:8],
            "has_starting_copy": any("Запускаю AI" in str(item) for item in first["progress"]),
            "seconds": round(time.time() - started, 1),
        }
        messages, assistant = wait_complete(api, chat["id"])
        users = [row for row in messages if row.get("role") == "user"]
        assistants = [row for row in messages if row.get("role") == "assistant"]
        report["task"] = {
            "user_messages": len(users),
            "assistant_messages": len(assistants),
            "assistant_id": assistant.get("id"),
            "status": assistant.get("status"),
            "answer": (assistant.get("content") or "")[:240],
        }
        spend, status = estimated(api)
        diagnostic = status.get("diagnostic") or {}
        report["runpod"] = {
            "pod_id": diagnostic.get("pod_id"),
            "gpu": diagnostic.get("gpu"),
            "dc": diagnostic.get("datacenter") or diagnostic.get("dc"),
            "price": diagnostic.get("price_per_hour"),
            "estimated": spend,
            "ai": status.get("ai"),
            "managed": diagnostic.get("managed"),
        }
        mid = volume_pods(key)
        report["pods_after_first"] = mid
        if len(mid) != 1:
            raise RuntimeError(f"expected_one_pod:{len(mid)}")
        second_chat = api.client.post("/chats", headers=api.headers(), json={"title": "second"}).json()
        second = stream_message(api, second_chat["id"], "Назови столицу Франции одним словом.", timeout=180)
        _, second_assistant = wait_complete(api, second_chat["id"], timeout=180)
        after_second = volume_pods(key)
        report["second"] = {
            "chars": len(second["text"] or second_assistant.get("content") or ""),
            "answer": (second_assistant.get("content") or "")[:160],
            "pods": len(after_second),
        }
        report["duplicate_pod"] = len(after_second) != 1
        if report["duplicate_pod"]:
            raise RuntimeError("duplicate_pod")
        spend, _ = estimated(api)
        if spend <= 0.15:
            log("recovery: restart isolated backend")
            pod_before = (after_second[0] or {}).get("id")
            stop_backend(process, handle)
            process, handle, env, db = start_isolated(work, file_env, runtime_token)
            wait_health()
            api = Api()
            api.login(email, password)
            recovered = api.client.get("/llm/status", headers=api.headers()).json()
            recovered_pods = volume_pods(key)
            report["recovery"] = {
                "pod_before": pod_before,
                "pod_after": ((recovered.get("diagnostic") or {}).get("pod_id")),
                "pods": len(recovered_pods),
                "ai": recovered.get("ai"),
                "same_pod": pod_before == ((recovered.get("diagnostic") or {}).get("pod_id"))
                or (len(recovered_pods) == 1 and recovered_pods[0]["id"] == pod_before),
            }
            if len(recovered_pods) != 1:
                raise RuntimeError("recovery_duplicate")
        else:
            report["recovery"] = "skipped_budget"
        set_idle_one_minute(db)
        log("idle override auto_stop_minutes=1 on isolated session")
        idle_deadline = time.time() + 150
        while time.time() < idle_deadline:
            live_now = volume_pods(key)
            spend, status = estimated(api)
            if spend >= HARD_USD:
                log("hard budget reached, stopping")
                break
            if not live_now:
                break
            time.sleep(5)
        httpx.post(
            BASE + "/runtime/shutdown",
            headers={"X-Alex-Runtime-Token": runtime_token},
            timeout=20,
        )
        time.sleep(8)
        final = volume_pods(key)
        if final:
            # last-resort managed stop through API if idle/shutdown lagged
            try:
                api.client.post("/compute/stop", headers=api.headers(), json={"after_generation": False})
                time.sleep(8)
                final = volume_pods(key)
            except Exception as error:
                report["stop_error"] = type(error).__name__
        vol_ok, vol_http = volume_exists(key)
        report["final_gpu"] = len(final)
        report["final_pods"] = final
        report["volume_preserved"] = vol_ok
        report["volume_http"] = vol_http
        report["approvals"] = approver.approved if approver else []
        report["cost_target"] = TARGET_USD
        report["cost_hard"] = HARD_USD
        spend, _ = estimated(api)
        report["estimated_spend"] = spend
    finally:
        if approver:
            approver.stop.set()
        if process:
            stop_backend(process, handle)
        leftover = volume_pods(key)
        report["gpu_after_process_exit"] = len(leftover)
        if leftover:
            report["cleanup_warning"] = leftover
        out = Path(os.environ["TEMP"]) / "alex-on-demand-e2e.json"
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        (work / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        log("report %s" % out)
    if report.get("final_gpu") != 0 or not report.get("volume_preserved"):
        raise SystemExit("cleanup_failed")
    log("PASS estimated=%s final_gpu=0 volume=preserved" % report.get("estimated_spend"))


if __name__ == "__main__":
    main()
