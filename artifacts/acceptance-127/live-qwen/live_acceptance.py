"""The single authorised LIVE Qwen acceptance run — prepared, guarded, not started here.

The point of this harness is to be *runnable* once the owner approves, with no further engineering:

    python artifacts/acceptance-127/live-qwen/live_acceptance.py --preflight
    python artifacts/acceptance-127/live-qwen/live_acceptance.py --plan
    python artifacts/acceptance-127/live-qwen/live_acceptance.py --live --confirm "I AUTHORIZE ONE PAID POD"

It sends ONE user goal to a real Qwen through the real backend. The model chooses every tool call;
the harness only defines the goal, the workspace, the budget and the PASS criteria. Nothing here
replays a recorded plan, and a missing model ends the run with a typed failure, never a PASS.

Isolation: a temporary data root and database, a temporary workspace, an isolated device identity
(`ALEX_ACCEPTANCE_RUN=1` plus a unique credential target). The installed 1.2.6 application, its
database, its device and its credentials are never touched.

Cost: one Pod, `app.compute.cost_policy` derives the hard wall (450 s at the measured $1.09/h rate,
with 480 s billed worst case = $0.1453 ≤ $0.15), an external watchdog stops it at 440 s, and the
harness refuses to start if the provider offers no card inside the budget.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "apps" / "backend"
HOST_EXE = REPO / "apps" / "desktop" / "src-tauri" / "target" / "release" / "alex-host-loop.exe"
WATCHDOG = REPO / "scripts" / "live_qwen_watchdog.py"
OUT = Path(__file__).resolve().parent
CONFIRMATION = "I AUTHORIZE ONE PAID POD"
BUDGET_USD = 0.15
HOURLY_RATE = 1.09
SCENARIO = (
    "Создай на моём компьютере приложение TaskDesk для управления задачами. Оно должно иметь "
    "локальную базу данных, добавление задач, поиск, изменение статуса и веб-интерфейс. "
    "Исследуй необходимые сведения, спланируй проект, создай файлы, запусти приложение, проверь "
    "его работоспособность, открой настоящий интерфейс, найди и исправь найденный дефект, "
    "повторно проверь результат и заверши задачу только после подтверждения всех требований."
)

sys.path.insert(0, str(BACKEND))
from app.compute import cost_policy  # noqa: E402


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def http(method: str, url: str, body=None, token: str | None = None, timeout: float = 30.0):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
        try:
            return response.status, json.loads(payload or b"{}")
        except ValueError:
            return response.status, {}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def pods_state() -> dict:
    completed = subprocess.run(
        [sys.executable, str(REPO / "artifacts" / "readonly-runpod-state.py")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    try:
        return json.loads(completed.stdout)
    except ValueError:
        return {"ok": False, "reason": (completed.stderr or "")[-300:]}


def preflight() -> dict:
    completed = subprocess.run(
        [sys.executable, str(OUT / "preflight.py")],
        capture_output=True,
        text=True,
        timeout=300,
    )
    try:
        return json.loads((OUT / "preflight.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"verdict": "BLOCKED", "blockers": ["preflight_unreadable"], "stderr": completed.stderr[-300:]}


def plan_report() -> dict:
    policy = cost_policy.plan(BUDGET_USD, HOURLY_RATE)
    return {
        "scenario": SCENARIO,
        "policy": policy.to_dict(),
        "steps": [
            "preflight (read-only): gateway, pods, balance, installed app, device, disk",
            "isolated backend 1.2.7 on a free port, temporary data root and database",
            "isolated packaged host (Computer Connect) with ALEX_ACCEPTANCE_RUN=1",
            "external watchdog process starts first and watches for exactly one Pod",
            "one user message; the model plans, researches, builds, runs, tests, drives the GUI",
            "classify the task journal: REAL_MODEL_CONFIRMED is required",
            "independent checks: files, pytest, HTTP, GUI screenshots, repair cycle",
            "stop compute, confirm pods==[], record the actual balance delta",
        ],
        "pass_criteria": [
            "provenance verdict REAL_MODEL_CONFIRMED",
            "task status COMPLETED with verification.evidence_ok=true",
            "application files exist and its tests pass",
            "the application was observed over real HTTP",
            "a GUI screenshot was produced by a real browser",
            "a failing test run preceded a passing one (detect → fix → retest)",
            "no duplicate tool payloads",
            "pods==[] after the run and the billed cost stayed inside the budget",
            "the installed 1.2.6 data and device are unchanged",
        ],
        "abort_rules": [
            "any preflight blocker",
            "an existing Pod before the run",
            "no compatible GPU inside the budget (CAPACITY_UNAVAILABLE, no queue)",
            "the watchdog reports MULTIPLE_PODS or TERMINATION_UNCONFIRMED",
            "the provenance verdict is anything other than REAL_MODEL_CONFIRMED",
        ],
    }


def _sse_stream(url: str, token: str, body: dict, deadline: float) -> list[dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    events: list[dict] = []
    with urllib.request.urlopen(request, timeout=30) as response:
        for raw in response:
            if time.monotonic() > deadline:
                events.append({"event": "client_timeout"})
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue
            events.append({"line": line[:400]})
    return events


def run_live(confirm: str) -> int:
    if confirm != CONFIRMATION:
        raise SystemExit(
            f"refusing a paid run: pass --confirm \"{CONFIRMATION}\" only after the owner approved it"
        )
    before = preflight()
    if before.get("verdict") != "READY":
        raise SystemExit(f"preflight is not READY: {before.get('blockers')}")
    pods = pods_state()
    if pods.get("pod_count") not in (0, None):
        raise SystemExit(f"refusing: {pods.get('pod_count')} Pod(s) already exist")
    policy = cost_policy.plan(BUDGET_USD, HOURLY_RATE)
    if not policy.feasible:
        raise SystemExit(f"refusing: budget infeasible at ${HOURLY_RATE}/h: {policy.to_dict()}")

    sys.path.insert(0, str(REPO / "scripts"))
    import runpod_api_tools

    key = runpod_api_tools.read_provider_secret()
    if not key:
        raise SystemExit("no RunPod credential in the installed product's credential store")

    work = Path(tempfile.mkdtemp(prefix="canalla-live-"))
    run_dir = OUT / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    workspace = work / "workspace"
    workspace.mkdir()
    nonce = secrets.token_hex(5)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "DATABASE_URL": "sqlite:///" + (work / "live.db").as_posix(),
        "JWT_SECRET": secrets.token_urlsafe(48),
        "APP_ENV": "test",
        "ALEX_AI_MODE": "direct",
        "LLM_PROVIDER": "llamacpp",
        "LLM_CONNECTION_MODE": "runpod",
        "LLM_MODEL": "orcarouter-qwen38-27b-q5km",
        "RUNPOD_API_KEY": key,
        "RUNPOD_NETWORK_VOLUME_ID": "uwgeaie5b0",
        "COMPUTE_BACKGROUND_ENABLED": "true",
        "ALLOW_USER_COMPUTE_START": "true",
        "TINYFISH_API_KEY": "",
        "ADMIN_EMAILS": "[]",
        "DOCUMENT_STORAGE_DIR": str(work / "documents"),
        "ALEX_LLM_DATA_DIR": str(work / "app-data"),
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND),
        env=env,
        check=True,
        capture_output=True,
    )
    backend_log = (work / "backend.log").open("w", encoding="utf-8", errors="replace")
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BACKEND),
        env=env,
        stdout=backend_log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    host = None
    watchdog = None
    summary: dict = {
        "started_at": utcnow(),
        "budget_usd": BUDGET_USD,
        "hourly_rate_usd": HOURLY_RATE,
        "policy": policy.to_dict(),
        "balance_before_usd": pods.get("balance_usd"),
    }
    try:
        for _ in range(300):
            try:
                if http("GET", base + "/health")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.2)
        email = f"live-{nonce}@example.com"
        _, registered = http("POST", base + "/auth/register", {"email": email, "password": "live-pass-123"})
        token = registered["access_token"]
        _, chat = http("POST", base + "/chats", {}, token=token)
        chat_id = chat["id"]
        http(
            "PUT",
            base + "/tools/preferences",
            {
                "computer_mode": "trusted",
                "default_mode": "on",
                "workspace_roots": [str(workspace)],
            },
            token=token,
        )
        http(
            "PUT",
            base + "/compute/preferences",
            {
                "selection": "automatic",
                "strategy": "cheapest",
                "min_vram_gb": 48,
                "max_hourly_price": HOURLY_RATE,
                "session_budget": BUDGET_USD,
                "auto_stop_minutes": 10,
                "auto_connect": False,
                "auto_search": True,
            },
            token=token,
        )
        host_env = {
            **env,
            "ALEX_ACCEPTANCE_RUN": "1",
            "ALEX_BACKEND_URL": base,
            "ALEX_TOKEN": token,
            "ALEX_DEVICE_NAME": f"LIVE Qwen {nonce}",
            "ALEX_DEVICE_DIR": str(work / "device"),
            "ALEX_DEVICE_CREDENTIAL_TARGET": f"Canalla Acceptance/live-{nonce}",
            "ALEX_WORKSPACE_ROOTS": str(workspace),
        }
        host_log = (work / "host.log").open("w", encoding="utf-8", errors="replace")
        host = subprocess.Popen(
            [str(HOST_EXE)], env=host_env, stdout=host_log, stderr=subprocess.STDOUT, text=True
        )
        watchdog_out = work / "watchdog.json"
        watchdog = subprocess.Popen(
            [
                sys.executable,
                str(WATCHDOG),
                "--live",
                "--budget-usd",
                str(BUDGET_USD),
                "--hourly-rate",
                str(HOURLY_RATE),
                "--wait-for-pod-seconds",
                "90",
                "--out",
                str(watchdog_out),
            ],
            stdout=(work / "watchdog.log").open("w", encoding="utf-8", errors="replace"),
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + policy.hard_wall_seconds + policy.termination_grace_seconds
        events = _sse_stream(
            f"{base}/chats/{chat_id}/stream",
            token,
            {"content": SCENARIO, "web_mode": "on", "computer_mode": "trusted", "tor_mode": "off"},
            deadline,
        )
        summary["stream_events"] = len(events)
        summary["stream_end"] = events[-1] if events else None

        from app.agent import provenance as provenance_module
        from app.database import SessionLocal
        from app.tools.models import LocalTask

        with SessionLocal() as db:
            tasks = db.query(LocalTask).order_by(LocalTask.started_at.desc()).limit(1).all()
            task = tasks[0] if tasks else None
            if task is None:
                summary["verdict"] = "FAIL"
                summary["reasons"] = ["no task was created"]
                return 1
            events_rows, runs = provenance_module.from_task(db, task.id)
            verdict = provenance_module.classify(
                events_rows, secret=env["JWT_SECRET"], tool_runs=runs
            )
            summary["task"] = {
                "id": task.id,
                "status": task.status,
                "verification_ok": bool((task.verification or {}).get("evidence_ok")),
                "last_error": task.last_error,
            }
            summary["provenance"] = verdict.to_dict()

        checks = run_independent_checks(workspace, summary)
        summary["checks"] = checks
        summary["balance_after_usd"] = pods_state().get("balance_usd")
        summary["verdict"] = (
            "PASS"
            if verdict.ok
            and summary["task"]["status"] == "COMPLETED"
            and summary["task"]["verification_ok"]
            and all(item["ok"] for item in checks)
            else "FAIL"
        )
        return 0 if summary["verdict"] == "PASS" else 1
    finally:
        # The watchdog owns the Pod stop; the harness only confirms it and stops anything it can.
        if watchdog is not None:
            try:
                watchdog.wait(timeout=policy.termination_grace_seconds + 120)
            except subprocess.TimeoutExpired:
                watchdog.terminate()
        try:
            http("POST", base + "/compute/stop", {}, token=locals().get("token"), timeout=60)
        except Exception:
            pass
        time.sleep(3)
        final_pods = pods_state()
        summary["pods_after"] = {"count": final_pods.get("pod_count"), "detail": final_pods.get("pods")}
        summary["finished_at"] = utcnow()
        try:
            (run_dir / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception:
            pass
        if host is not None:
            host.terminate()
        backend.terminate()
        backend_log.close()
        shutil.rmtree(work, ignore_errors=True)
        print(json.dumps(summary, ensure_ascii=False, indent=1)[-4000:])


def run_independent_checks(workspace: Path, summary: dict) -> list[dict]:
    """Checks the agent cannot influence: files, tests, HTTP, screenshots, repair cycle."""
    checks: list[dict] = []

    def check(name: str, ok: bool, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})

    python_files = sorted(path.name for path in workspace.rglob("*.py"))
    check("application_files_exist", len(python_files) >= 2, python_files)
    tests = [name for name in python_files if name.startswith("test_")]
    check("tests_were_written", bool(tests), tests)

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=600,
    )
    check("tests_pass", completed.returncode == 0, (completed.stdout or "")[-300:])

    screenshots = sorted(str(path.relative_to(workspace)) for path in workspace.rglob("*.png"))
    check("gui_screenshot_exists", bool(screenshots), screenshots[:5])

    from app.database import SessionLocal
    from app.tools.models import ToolRun

    with SessionLocal() as db:
        runs = db.query(ToolRun).order_by(ToolRun.started_at).all()
        codes = [
            (run.tool_name, (run.result_metadata or {}).get("exit_code"))
            for run in runs
            if run.tool_name in {"run_python", "run_process"}
        ]
    failures = [index for index, (_, code) in enumerate(codes) if code not in (None, 0)]
    check(
        "a_failure_preceded_a_pass",
        bool(failures) and any(code in (0,) for _, code in codes[max(failures) :]),
        codes[-10:],
    )
    payloads = [
        (run.tool_name, run.input_digest)
        for run in runs
    ]
    duplicates = {
        item for item in payloads if payloads.count(item) > 1 and item[0] not in {"process_status"}
    }
    check("no_duplicate_tool_payloads", not duplicates, list(duplicates)[:5])

    # The application must answer over real HTTP. Start it ourselves if it is not already running.
    main = workspace / "main.py"
    if main.is_file():
        port = free_port()
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=str(workspace),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            ok = False
            for _ in range(100):
                try:
                    if http("GET", f"http://127.0.0.1:{port}/")[0] == 200:
                        ok = True
                        break
                except OSError:
                    pass
                time.sleep(0.2)
            check("application_serves_http", ok, f"127.0.0.1:{port}")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    else:
        check("application_serves_http", False, "main.py missing")

    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    if args.plan:
        print(json.dumps(plan_report(), ensure_ascii=False, indent=1))
        return 0
    if args.self_test:
        policy = cost_policy.plan(BUDGET_USD, HOURLY_RATE)
        checks = [
            {"check": "budget_fits_the_rate", "ok": policy.feasible},
            {"check": "hard_wall_is_inside_the_budget", "ok": policy.worst_case_usd <= BUDGET_USD},
            {"check": "watchdog_stops_before_the_wall", "ok": policy.stop_request_seconds < policy.hard_wall_seconds},
            {"check": "capacity_wait_is_bounded", "ok": policy.capacity_timeout_seconds == 60},
            {"check": "no_capacity_timeout_1200", "ok": "1200" not in json.dumps(plan_report())},
        ]
        report = {"checks": checks, "verdict": "PASS" if all(item["ok"] for item in checks) else "FAIL"}
        (OUT / "self-test.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["verdict"] == "PASS" else 1
    if args.preflight:
        print(json.dumps(preflight(), ensure_ascii=False, indent=1))
        return 0
    if args.live:
        return run_live(args.confirm)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
