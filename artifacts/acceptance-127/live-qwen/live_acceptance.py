"""The single authorised LIVE Qwen acceptance run — prepared, guarded, not started here.

The point of this harness is to be *runnable* once the owner approves and capacity exists:

    python artifacts/acceptance-127/live-qwen/gpu_probe.py
    python artifacts/acceptance-127/live-qwen/live_acceptance.py --preflight
    python artifacts/acceptance-127/live-qwen/live_acceptance.py --plan --budget-usd 2.00
    python artifacts/acceptance-127/live-qwen/live_acceptance.py --live \
        --budget-usd 2.00 --confirm "I AUTHORIZE ONE PAID POD"

It sends ONE user goal to a real Qwen through the real backend. The model chooses every tool call;
the harness only defines the goal, the workspace, the budget and the PASS criteria. Nothing here
replays a recorded plan, and a missing model ends the run with a typed failure, never a PASS.

Isolation: a temporary data root and database, a temporary workspace, an isolated device identity
(`ALEX_ACCEPTANCE_RUN=1` plus a unique credential target). The installed 1.2.6 application, its
database, its device and its credentials are never touched. The parent process applies the same
isolated `DATABASE_URL` before it imports the application, so its journal read can never land on the
production database.

Cost: the GPU probe reads the live catalogue and the model volume's datacenter; `cost_policy`
derives the hard wall from the selected card's *current* price, the 90-minute billable ceiling and
the approved budget. An external watchdog starts before the request and stops the one Pod.
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
GPU_PROBE = Path(__file__).resolve().parent / "gpu_probe.py"
OUT = Path(__file__).resolve().parent
LIVE = OUT / "final-live"

sys.path.insert(0, str(BACKEND))
from app.compute import cost_policy  # noqa: E402
CONFIRMATION = "I AUTHORIZE ONE PAID POD"
CONFIRMATION_RU = (
    "Разрешаю один платный LIVE-прогон Qwen с бюджетом не более $2.00 и одним GPU Pod."
)
MAX_BUDGET_USD = cost_policy.MAX_LIVE_BUDGET_USD
MAX_BILLABLE_MINUTES = 90
TARGET_WORK_MINUTES = 80
MIN_WORK_MINUTES = 45
SCENARIO = (
    "Создай полноценное локальное веб-приложение TaskDesk для управления задачами.\n\n"
    "Приложение должно иметь:\n"
    "1. Локальную базу данных SQLite.\n"
    "2. Создание задач.\n"
    "3. Просмотр списка задач.\n"
    "4. Поиск задач без учёта регистра.\n"
    "5. Изменение статуса задачи.\n"
    "6. Веб-интерфейс.\n"
    "7. Backend API.\n"
    "8. Автоматические тесты.\n"
    "9. Возможность локального запуска.\n\n"
    "Самостоятельно выбери подходящую архитектуру в пределах разрешённых инструментов. "
    "При необходимости исследуй документацию. Создай проект, установи необходимые доступные "
    "зависимости, запусти приложение, проверь работу API и интерфейса. Если обнаружишь ошибку, "
    "изучи причину, исправь её и повтори тесты. Проверь реальное сохранение данных. Используй "
    "доступные GUI-инструменты. Сохрани результаты и доказательства. Не завершай задачу, пока "
    "приложение не работает и все обязательные требования не подтверждены проверками."
)


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
        return {
            "verdict": "BLOCKED",
            "blockers": ["preflight_unreadable"],
            "stderr": completed.stderr[-300:],
        }


def gpu_probe() -> dict:
    completed = subprocess.run(
        [sys.executable, str(GPU_PROBE)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    path = LIVE / "gpu-selection.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "verdict": "PROBE_FAILED", "error": completed.stderr[-300:]}
    report["probe_exit"] = completed.returncode
    return report


def production_snapshot() -> dict:
    """The installed application's data, read-only. Never a secret, never a write."""
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "Alex LLM"
    database = root / "data" / "alex.db"
    device = root / "device.json"
    snapshot: dict = {
        "captured_at": utcnow(),
        "database_present": database.is_file(),
        "data_root": str(root),
    }
    if device.is_file():
        try:
            snapshot["local_device_id"] = json.loads(device.read_text(encoding="utf-8")).get(
                "device_id", ""
            )
        except ValueError:
            snapshot["local_device_id"] = ""
    if database.is_file():
        import sqlite3

        connection = sqlite3.connect("file:" + database.as_posix() + "?mode=ro", uri=True)
        try:
            snapshot["counts"] = {
                table: connection.execute(f"select count(*) from {table}").fetchone()[0]
                for table in ("users", "chats", "messages", "paired_devices", "local_tasks")
            }
            snapshot["integrity"] = connection.execute("pragma integrity_check").fetchone()[0]
            snapshot["foreign_key_violations"] = len(
                connection.execute("pragma foreign_key_check").fetchall()
            )
            snapshot["active_device_id"] = (
                connection.execute(
                    "select id from paired_devices where revoked_at is null "
                    "order by created_at desc limit 1"
                ).fetchone()
                or [""]
            )[0]
        finally:
            connection.close()
    try:
        status, health = http("GET", "http://127.0.0.1:8000/health", timeout=10)
        snapshot["installed_app"] = {"status": status, "version": (health or {}).get("version")}
    except Exception:
        snapshot["installed_app"] = {"status": 0, "version": None}
    return snapshot


def prepare() -> int:
    """Free preparation: preflight, GPU read, and the exact authorization request."""
    pre = preflight()
    selection = gpu_probe()
    pods = pods_state()
    policy = None
    if selection.get("selected"):
        policy = cost_policy.plan(
            MAX_BUDGET_USD,
            float(selection["selected"]["hourly_price_usd"]),
            max_billable_seconds=MAX_BILLABLE_MINUTES * 60,
            target_work_seconds=TARGET_WORK_MINUTES * 60,
            min_work_seconds=MIN_WORK_MINUTES * 60,
        )
    blocked = []
    if pre.get("verdict") != "READY":
        blocked.extend(pre.get("blockers") or ["preflight"])
    if pods.get("pod_count") not in (0, None):
        blocked.append("existing_pod")
    if selection.get("verdict") != "READY":
        blocked.append(selection.get("verdict") or "gpu_probe_failed")
    if policy is not None and not policy.sufficient:
        blocked.append("no_meaningful_work_window")
    request = {
        "generated_at": utcnow(),
        "status": "READY_FOR_OWNER_AUTHORIZATION" if not blocked else "BLOCKED",
        "blocked_by": blocked,
        "owner_phrase_ru": CONFIRMATION_RU,
        "confirmation_phrase": CONFIRMATION,
        "budget_usd": MAX_BUDGET_USD,
        "max_billable_minutes": MAX_BILLABLE_MINUTES,
        "target_work_minutes": TARGET_WORK_MINUTES,
        "min_work_minutes": MIN_WORK_MINUTES,
        "model": "orcarouter-qwen38-27b-q5km",
        "context_window": 32768,
        "gpu": selection.get("selected"),
        "reference_gpu": selection.get("reference"),
        "reference_policy": selection.get("reference_policy"),
        "gpu_verdict": selection.get("verdict"),
        "volume": selection.get("volume"),
        "policy": policy.to_dict() if policy else None,
        "preflight_verdict": pre.get("verdict"),
        "pods": pods.get("pod_count"),
        "balance_usd": pods.get("balance_usd"),
        "command": (
            "python artifacts\\acceptance-127\\live-qwen\\live_acceptance.py --live "
            "--budget-usd 2.00 --confirm \"I AUTHORIZE ONE PAID POD\""
        ),
        "production": production_snapshot(),
        "spend_usd": 0.0,
        "pods_created": 0,
    }
    _write_json("authorization.json", request)
    _write_json("preflight.json", pre)
    if policy is not None:
        _write_json("cost-policy.json", policy.to_dict())
    if blocked:
        _write_json(
            "final-summary.json",
            {
                "generated_at": utcnow(),
                "verdict": "LIVE NOT RUN",
                "live_run": False,
                "blocked_by": blocked,
                "pods_created": 0,
                "spend_usd": 0.0,
                "reason": "Owner authorization is required and the external blocker above exists.",
            },
        )
        _write_json(
            "cleanup.json",
            {
                "checked_at": utcnow(),
                "pod_count": pods.get("pod_count"),
                "pods": pods.get("pods"),
                "verdict": "CLEAN" if pods.get("pod_count") == 0 else "CRITICAL_UNCONFIRMED",
                "note": "No paid run was started; this is the pre-run account state.",
            },
        )
        _write_json(
            "billing-summary.json",
            {
                "checked_at": utcnow(),
                "spend_usd": 0.0,
                "balance_usd": pods.get("balance_usd"),
                "note": "No Pod was created; storage charges on the existing volume are independent.",
            },
        )
    print(json.dumps(request, ensure_ascii=False, indent=1)[:5000])
    # A blocked preparation is a valid result (CAPACITY_UNAVAILABLE is an external blocker).
    return 0


def plan_report(budget: float, rate: float) -> dict:
    policy = cost_policy.plan(
        budget,
        rate,
        max_billable_seconds=MAX_BILLABLE_MINUTES * 60,
        target_work_seconds=TARGET_WORK_MINUTES * 60,
        min_work_seconds=MIN_WORK_MINUTES * 60,
    )
    return {
        "scenario": SCENARIO,
        "policy": policy.to_dict(),
        "steps": [
            "preflight (read-only): gateway, pods, balance, installed app, device, disk, watchdog",
            "gpu probe (read-only): live catalogue, volume datacenter, current price",
            "isolated backend 1.2.7 on a free port, temporary data root and database",
            "isolated packaged host (Computer Connect) with ALEX_ACCEPTANCE_RUN=1",
            "external watchdog process starts first and watches for exactly one Pod",
            "one user message; the model plans, researches, builds, runs, tests, drives the GUI",
            "classify the task journal: REAL_MODEL_CONFIRMED is required",
            "independent checks: files, pytest, HTTP, SQLite, GUI screenshot, repair cycle",
            "stop compute, confirm pods==[], record the actual balance delta",
        ],
        "pass_criteria": [
            "provenance verdict REAL_MODEL_CONFIRMED",
            "task status COMPLETED with verification.evidence_ok=true",
            "application files exist and its tests pass",
            "the application was observed over real HTTP",
            "SQLite contains the expected rows",
            "a GUI screenshot was produced by a real browser",
            "a failing test run preceded a passing one (detect → fix → retest)",
            "no duplicate tool payloads",
            "pods==[] after the run and the billed cost stayed inside the budget",
            "the installed 1.2.6 data and device are unchanged",
        ],
        "abort_rules": [
            "any preflight blocker",
            "an existing Pod before the run",
            "no compatible GPU in the volume's datacenter (CAPACITY_UNAVAILABLE, no queue)",
            "the planned worst case exceeds the approved budget",
            "the watchdog reports MULTIPLE_PODS or TERMINATION_UNCONFIRMED",
            "the provenance verdict is anything other than REAL_MODEL_CONFIRMED",
        ],
    }


def authorization_problem(confirm: str, budget: float | None) -> str:
    """Every reason a paid run must not start. Empty string means the guard passed."""
    if confirm != CONFIRMATION:
        return (
            "refusing a paid run: pass --confirm "
            f'"{CONFIRMATION}" only after the owner approved it'
        )
    if budget is None:
        return "refusing a paid run: --budget-usd is required (the approved amount is explicit)"
    if budget <= 0:
        return "refusing a paid run: the budget must be positive"
    if budget > MAX_BUDGET_USD + 1e-9:
        return f"refusing a paid run: ${budget:.2f} exceeds the ${MAX_BUDGET_USD:.2f} ceiling"
    return ""


def _write_json(name: str, payload) -> None:
    LIVE.mkdir(parents=True, exist_ok=True)
    (LIVE / name).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _write_jsonl(name: str, rows) -> None:
    LIVE.mkdir(parents=True, exist_ok=True)
    with (LIVE / name).open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


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


def run_live(
    *,
    confirm: str,
    budget: float | None,
    max_billable_minutes: int,
    target_work_minutes: int,
    min_work_minutes: int,
) -> int:
    problem = authorization_problem(confirm, budget)
    if problem:
        raise SystemExit(problem)
    if max_billable_minutes > MAX_BILLABLE_MINUTES:
        raise SystemExit(
            f"refusing: {max_billable_minutes} billable minutes exceeds the "
            f"{MAX_BILLABLE_MINUTES}-minute policy"
        )
    assert budget is not None

    before = preflight()
    if before.get("verdict") != "READY":
        raise SystemExit(f"preflight is not READY: {before.get('blockers')}")
    pods = pods_state()
    if pods.get("ok") is not True or pods.get("pod_count") != 0:
        raise SystemExit(
            "refusing: the provider state is not a confirmed zero-Pod account "
            f"(ok={pods.get('ok')}, pods={pods.get('pod_count')})"
        )

    selection = gpu_probe()
    _write_json(
        "authorization.json",
        {
            "generated_at": utcnow(),
            "confirmation_phrase": confirm,
            "owner_phrase_expected_ru": CONFIRMATION_RU,
            "budget_usd": budget,
            "max_billable_minutes": max_billable_minutes,
            "gpu_verdict": selection.get("verdict"),
            "status": "AUTHORIZED" if selection.get("verdict") == "READY" else "BLOCKED",
            "blocked_by": None
            if selection.get("verdict") == "READY"
            else selection.get("verdict"),
        },
    )
    if selection.get("verdict") != "READY" or not selection.get("selected"):
        # No Pod exists and none is created: this is the typed external blocker, not a retry.
        _write_json(
            "final-summary.json",
            {
                "generated_at": utcnow(),
                "verdict": "CAPACITY_UNAVAILABLE",
                "live_run": False,
                "pods_created": 0,
                "spend_usd": 0.0,
                "reason": selection.get("reason"),
                "gpu_selection": selection.get("selected"),
                "volume": selection.get("volume"),
            },
        )
        print(
            json.dumps(
                {
                    "verdict": "CAPACITY_UNAVAILABLE",
                    "reason": selection.get("reason"),
                    "pods_created": 0,
                    "spend_usd": 0.0,
                },
                ensure_ascii=False,
            )
        )
        return 2

    rate = float(selection["selected"]["hourly_price_usd"])
    policy = cost_policy.plan(
        budget,
        rate,
        max_billable_seconds=max_billable_minutes * 60,
        target_work_seconds=target_work_minutes * 60,
        min_work_seconds=min_work_minutes * 60,
    )
    _write_json("cost-policy.json", policy.to_dict())
    if not policy.feasible or not policy.sufficient:
        raise SystemExit(f"refusing: the selected card leaves no usable window: {policy.to_dict()}")

    sys.path.insert(0, str(REPO / "scripts"))
    import runpod_api_tools

    key = runpod_api_tools.read_provider_secret()
    if not key:
        raise SystemExit("no RunPod credential in the installed product's credential store")

    work = Path(tempfile.mkdtemp(prefix="canalla-live-"))
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
    # The parent reads the task journal itself. Without this its SessionLocal would resolve the
    # parent's DATABASE_URL, which is not the isolated database the child backend uses.
    os.environ.update(env)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND),
        env=env,
        check=True,
        capture_output=True,
    )
    backend_log = (work / "backend.log").open("w", encoding="utf-8", errors="replace")
    backend = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(BACKEND),
        env=env,
        stdout=backend_log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    host = None
    watchdog = None
    token = None
    summary: dict = {
        "started_at": utcnow(),
        "budget_usd": budget,
        "hourly_rate_usd": rate,
        "gpu": selection.get("selected"),
        "volume": selection.get("volume"),
        "policy": policy.to_dict(),
        "balance_before_usd": pods.get("balance_usd"),
    }
    _write_json("production-before.json", before)
    try:
        for _ in range(300):
            try:
                if http("GET", base + "/health")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.2)
        email = f"live-{nonce}@example.com"
        _, registered = http(
            "POST", base + "/auth/register", {"email": email, "password": "live-pass-123"}
        )
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
                "max_hourly_price": rate,
                "session_budget": budget,
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
        watchdog_out = LIVE / "pod-lifecycle.json"
        watchdog = subprocess.Popen(
            [
                sys.executable,
                str(WATCHDOG),
                "--live",
                "--budget-usd",
                str(budget),
                "--hourly-rate",
                str(rate),
                "--max-billable-minutes",
                str(max_billable_minutes),
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
        from app.tools.models import LocalTask, ToolRun

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
                "workspace": task.workspace,
            }
            summary["provenance"] = verdict.to_dict()
            tools = db.query(ToolRun).order_by(ToolRun.started_at).all()
            _write_jsonl(
                "model-responses.jsonl",
                [
                    {
                        "kind": row["kind"],
                        "payload": row["payload"],
                        "created_at": row["created_at"],
                    }
                    for row in events_rows
                    if row["kind"] == provenance_module.MODEL_RESPONSE
                ],
            )
            _write_jsonl(
                "tool-calls.jsonl",
                [
                    {
                        "kind": row["kind"],
                        "payload": row["payload"],
                        "created_at": row["created_at"],
                    }
                    for row in events_rows
                    if row["kind"] == provenance_module.MODEL_TOOL_CALL
                ],
            )
            _write_jsonl(
                "tool-execution.jsonl",
                [
                    {
                        "tool": run.tool_name,
                        "origin": run.origin,
                        "status": run.status,
                        "input_digest": run.input_digest,
                        "result_digest": run.result_digest,
                        "started_at": run.started_at,
                        "finished_at": run.finished_at,
                        "error": run.error_code,
                    }
                    for run in tools
                ],
            )
            repair_runs = [
                {
                    "tool": run.tool_name,
                    "exit_code": (run.result_metadata or {}).get("exit_code"),
                    "at": run.started_at,
                    "text": str((run.result_metadata or {}).get("text") or "")[:800],
                }
                for run in tools
                if run.tool_name in {"run_python", "run_process", "verify_result"}
            ]
            task_state = {
                "id": task.id,
                "status": task.status,
                "verification_ok": bool((task.verification or {}).get("evidence_ok")),
                "workspace": task.workspace,
                "files_changed": task.files_changed,
                "tool_calls_used": task.tool_calls_used,
                "last_error": task.last_error,
                "completion_summary": (task.completion_summary or "")[:2000],
            }
            verification = dict(task.verification or {})
            identity = next(
                (
                    {
                        "provider": row["payload"].get("provider"),
                        "model": row["payload"].get("model"),
                        "runtime": row["payload"].get("runtime"),
                    }
                    for row in events_rows
                    if row["kind"] == provenance_module.MODEL_RESPONSE
                ),
                {},
            )

        _write_json("provenance-verdict.json", verdict.to_dict())
        _write_json("task-state.json", task_state)
        _write_json("verification.json", verification)
        _write_json("model-identity.json", identity)
        (LIVE / "repair-evidence").mkdir(parents=True, exist_ok=True)
        _write_json("repair-evidence/command-runs.json", repair_runs)

        checks, check_payloads = run_independent_checks(workspace, summary)
        summary["checks"] = checks
        for name, payload in check_payloads.items():
            _write_json(name, payload)
        _write_json("production-after.json", production_snapshot())
        summary["balance_after_usd"] = pods_state().get("balance_usd")
        summary["verdict"] = (
            "PASS"
            if verdict.ok
            and task_state["status"] == "COMPLETED"
            and task_state["verification_ok"]
            and all(item["ok"] for item in checks)
            else "FAIL"
        )
        _write_json("final-summary.json", summary)
        return 0 if summary["verdict"] == "PASS" else 1
    finally:
        # The watchdog owns the Pod stop; the harness only confirms it and stops anything it can.
        if watchdog is not None:
            try:
                watchdog.wait(timeout=policy.termination_grace_seconds + 180)
            except subprocess.TimeoutExpired:
                watchdog.terminate()
        try:
            http("POST", base + "/compute/stop", {}, token=token, timeout=60)
        except Exception:
            pass
        time.sleep(3)
        final_pods = pods_state()
        _write_json(
            "cleanup.json",
            {
                "checked_at": utcnow(),
                "pod_count": final_pods.get("pod_count"),
                "pods": final_pods.get("pods"),
                "balance_usd": final_pods.get("balance_usd"),
                "verdict": "CLEAN" if final_pods.get("pod_count") == 0 else "CRITICAL_UNCONFIRMED",
            },
        )
        balance_after = final_pods.get("balance_usd")
        _write_json(
            "billing-summary.json",
            {
                "checked_at": utcnow(),
                "budget_usd": budget,
                "hourly_rate_usd": rate,
                "balance_before_usd": summary.get("balance_before_usd"),
                "balance_after_usd": balance_after,
                "estimated_policy_worst_case_usd": policy.worst_case_usd,
                "note": "The balance delta includes background storage charges; Pod cost is the policy figure.",
            },
        )
        if host is not None:
            host.terminate()
        backend.terminate()
        backend_log.close()
        shutil.rmtree(work, ignore_errors=True)
        print(json.dumps(summary, ensure_ascii=False, indent=1)[-4000:])


def run_independent_checks(workspace: Path, summary: dict) -> tuple[list[dict], dict]:
    """Checks the agent cannot influence: files, tests, SQLite, HTTP, GUI, repair cycle."""
    checks: list[dict] = []
    payloads: dict = {}

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
        timeout=900,
    )
    payloads["http-check.json"] = {
        "pytest_exit_code": completed.returncode,
        "pytest_tail": (completed.stdout or "")[-1000:],
    }
    check("tests_pass", completed.returncode == 0, (completed.stdout or "")[-300:])

    screenshots = sorted(str(path.relative_to(workspace)) for path in workspace.rglob("*.png"))
    check("gui_screenshot_exists", bool(screenshots), screenshots[:5])
    gui_dir = LIVE / "gui-evidence"
    gui_dir.mkdir(parents=True, exist_ok=True)
    for relative in screenshots[:20]:
        source = workspace / relative
        target = gui_dir / relative.replace("\\", "_").replace("/", "_")
        try:
            shutil.copy2(source, target)
        except OSError:
            pass

    from app.database import SessionLocal
    from app.tools.models import ToolRun

    with SessionLocal() as db:
        runs = db.query(ToolRun).order_by(ToolRun.started_at).all()
        codes = [
            (run.tool_name, (run.result_metadata or {}).get("exit_code"), run.started_at)
            for run in runs
            if run.tool_name in {"run_python", "run_process"}
        ]
    failures = [index for index, (_, code, _) in enumerate(codes) if code not in (None, 0)]
    check(
        "a_failure_preceded_a_pass",
        bool(failures) and any(code in (0,) for _, code, _ in codes[max(failures) :]),
        codes[-10:],
    )
    (LIVE / "repair-evidence").mkdir(parents=True, exist_ok=True)
    (LIVE / "repair-evidence" / "command-runs.json").write_text(
        json.dumps(
            [{"tool": tool, "exit_code": code, "at": at} for tool, code, at in codes],
            ensure_ascii=False,
            indent=1,
            default=str,
        ),
        encoding="utf-8",
    )
    payload_list = [(run.tool_name, run.input_digest) for run in runs]
    duplicates = {
        item
        for item in payload_list
        if payload_list.count(item) > 1 and item[0] not in {"process_status"}
    }
    check("no_duplicate_tool_payloads", not duplicates, list(duplicates)[:5])

    # SQLite: the application's own database must contain rows after the GUI/API work.
    databases = [path for path in workspace.rglob("*.db") if path.is_file()]
    database_check: dict = {"databases": [str(path.name) for path in databases], "tables": {}}
    rows_found = False
    for path in databases[:5]:
        import sqlite3

        try:
            connection = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            tables = [
                row[0]
                for row in connection.execute(
                    "select name from sqlite_master where type='table'"
                ).fetchall()
            ]
            for table in tables[:20]:
                try:
                    count = connection.execute(f'select count(*) from "{table}"').fetchone()[0]
                except sqlite3.Error:
                    continue
                database_check["tables"][f"{path.name}:{table}"] = count
                if count > 0 and "task" in table.lower():
                    rows_found = True
        finally:
            connection.close()
    payloads["database-check.json"] = database_check
    check("database_contains_task_rows", rows_found, database_check["tables"])

    main = workspace / "main.py"
    http_check: dict = {"started_by_harness": False, "status": None}
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
            for _ in range(150):
                try:
                    status, _ = http("GET", f"http://127.0.0.1:{port}/")
                    http_check.update({"status": status, "url": f"http://127.0.0.1:{port}/"})
                    if status == 200:
                        ok = True
                        break
                except OSError:
                    pass
                time.sleep(0.2)
            check("application_serves_http", ok, f"127.0.0.1:{port}")
            http_check["started_by_harness"] = True
            independent_gui(f"http://127.0.0.1:{port}/", checks)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    else:
        check("application_serves_http", False, "main.py missing")
    payloads["http-check.json"].update(http_check)
    return checks, payloads


def independent_gui(url: str, checks: list[dict]) -> None:
    """A browser load the model did not perform: page renders, screenshot, optional search."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        checks.append(
            {"check": "independent_gui_load", "ok": False, "detail": "playwright unavailable"}
        )
        return
    target = LIVE / "gui-evidence" / "independent-page.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.screenshot(path=str(target), full_page=True)
            text = page.inner_text("body")[:2000]
            search = page.locator(
                "input[type='search'], input[placeholder*='earch'], input[name*='q'], "
                "input[aria-label*='earch'], input[id*='search']"
            ).first
            searched = False
            if search.count() > 0:
                search.fill("ALPHA")
                page.wait_for_timeout(800)
                body = page.inner_text("body").lower()
                searched = "alpha" in body
            browser.close()
        checks.append(
            {
                "check": "independent_gui_load",
                "ok": bool(text.strip()),
                "detail": f"screenshot={target.name} search_input={searched}",
            }
        )
    except Exception as error:  # noqa: BLE001 - a browser failure is a typed observation
        checks.append(
            {"check": "independent_gui_load", "ok": False, "detail": str(error)[:300]}
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--gpu-probe", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--max-billable-minutes", type=int, default=MAX_BILLABLE_MINUTES)
    parser.add_argument("--target-work-minutes", type=int, default=TARGET_WORK_MINUTES)
    parser.add_argument("--min-work-minutes", type=int, default=MIN_WORK_MINUTES)
    args = parser.parse_args()

    if args.gpu_probe:
        print(json.dumps(gpu_probe(), ensure_ascii=False, indent=1)[:4000])
        return 0
    if args.prepare:
        return prepare()
    if args.plan:
        print(
            json.dumps(
                plan_report(args.budget_usd or MAX_BUDGET_USD, 1.09), ensure_ascii=False, indent=1
            )
        )
        return 0
    if args.self_test:
        checks = [
            {
                "check": "confirmation_is_required",
                "ok": bool(authorization_problem("", 2.00)),
            },
            {
                "check": "budget_is_explicit",
                "ok": bool(authorization_problem(CONFIRMATION, None)),
            },
            {
                "check": "budget_ceiling_is_enforced",
                "ok": bool(authorization_problem(CONFIRMATION, 2.01)),
            },
            {
                "check": "the_approved_envelope_passes",
                "ok": not authorization_problem(CONFIRMATION, 2.00),
            },
        ]
        policy = plan_report(2.00, 1.09)["policy"]
        checks += [
            {"check": "ninety_minute_billable_ceiling", "ok": policy["requested_max_billed_seconds"] == 5400},
            {"check": "eighty_minute_work_target", "ok": policy["target_work_seconds"] == 4800},
            {"check": "worst_case_within_two_dollars", "ok": policy["worst_case_usd"] <= 2.00},
            {"check": "capacity_wait_is_sixty_seconds", "ok": policy["capacity_timeout_seconds"] == 60},
            {"check": "no_twenty_minute_capacity_wait", "ok": "1200" not in json.dumps(plan_report(2.00, 1.09))},
        ]
        report = {"checks": checks, "verdict": "PASS" if all(item["ok"] for item in checks) else "FAIL"}
        _write_json("self-test.json", report)
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["verdict"] == "PASS" else 1
    if args.preflight:
        print(json.dumps(preflight(), ensure_ascii=False, indent=1))
        return 0
    if args.live:
        return run_live(
            confirm=args.confirm,
            budget=args.budget_usd,
            max_billable_minutes=args.max_billable_minutes,
            target_work_minutes=args.target_work_minutes,
            min_work_minutes=args.min_work_minutes,
        )
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
