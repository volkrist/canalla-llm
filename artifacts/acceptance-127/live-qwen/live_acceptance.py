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
import http.client as http_client
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "apps" / "backend"
HOST_EXE = (
    REPO
    / "apps"
    / "desktop"
    / "src-tauri"
    / "target"
    / "release"
    / "alex-host-loop.exe"
)
WATCHDOG = REPO / "scripts" / "live_qwen_watchdog.py"
GPU_PROBE = Path(__file__).resolve().parent / "gpu_probe.py"
OUT = Path(__file__).resolve().parent
LIVE = OUT / "final-live"
# The monitor names each attempt so its evidence can never be mistaken for another run's. A shared
# `final-summary.json` was read by a later attempt after a probe had overwritten it, and a real Pod
# then looked like "nothing was created". Every summary now carries the run that wrote it.
RUN_ID = ""

sys.path.insert(0, str(BACKEND))
from app.compute import cost_policy

CONFIRMATION = "I AUTHORIZE ONE PAID POD"
CONFIRMATION_RU = (
    "Разрешаю один платный LIVE-прогон Qwen с бюджетом не более $2.00 и одним GPU Pod."
)
sys.path.insert(0, str(OUT))
from placement_records import (  # noqa: E402
    PLACEMENTS,
    VERIFIED,
    model_status,
    placement_for,
)

# Both paid volumes are approved placements. A placement whose record is not a verified real-model
# answer is still usable - but only through `--self-test-mode`, where the one Pod proves the model
# with a real answer before any TaskDesk work starts.
APPROVED_VOLUMES = tuple(item["volume_id"] for item in PLACEMENTS)
DEFAULT_VOLUME = "y92bt25ru8"
SELFTEST_PROMPT = (
    "Ответь одним коротким предложением, подтвердив, что ты готов выполнять задачи. "
    "Не вызывай инструменты."
)
MAX_BUDGET_USD = cost_policy.MAX_LIVE_BUDGET_USD
MAX_BILLABLE_MINUTES = 90
TARGET_WORK_MINUTES = 80
MIN_WORK_MINUTES = 45
SELFTEST_TIMEOUT_SECONDS = 900
# A read timeout is a *stall detector*, not the run's deadline: the first token after a cold model
# load can take far longer than 30 s, and the run's own deadline plus the watchdog already bound the
# attempt. The old 30-second value killed a paid run the moment its model became ready.
SSE_READ_TIMEOUT_SECONDS = 300
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
    "приложение не работает и все обязательные требования не подтверждены проверками.\n\n"
    "Один ответ модели ограничен по длине, поэтому файлы пиши небольшими частями: первый "
    "вызов write_file создаёт файл, а следующие вызовы с mode=\"append\" дописывают "
    "продолжение в конец. Не пытайся записать весь файл одним вызовом."
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def venv_python() -> str:
    """The project interpreter that has FastAPI, uvicorn, pytest and httpx installed."""
    candidate = BACKEND / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(candidate if candidate.is_file() else Path(sys.executable))


def _venv_can_build() -> tuple[bool, str]:
    """The Computer host's interpreter must be able to run the tests the model writes."""
    try:
        completed = subprocess.run(
            [venv_python(), "-c", "import fastapi, httpx, pytest, uvicorn"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as error:  # noqa: BLE001 - a missing interpreter is a typed check
        return False, str(error)[:200]
    return completed.returncode == 0, (completed.stdout or completed.stderr).strip()[:200]


def http(
    method: str, url: str, body=None, token: str | None = None, timeout: float = 30.0
):
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
        return {
            "ok": False,
            "verdict": "PROBE_FAILED",
            "error": completed.stderr[-300:],
        }
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
            snapshot["local_device_id"] = json.loads(
                device.read_text(encoding="utf-8")
            ).get("device_id", "")
        except ValueError:
            snapshot["local_device_id"] = ""
    if database.is_file():
        import sqlite3

        connection = sqlite3.connect(
            "file:" + database.as_posix() + "?mode=ro", uri=True
        )
        try:
            snapshot["counts"] = {
                table: connection.execute(f"select count(*) from {table}").fetchone()[0]
                for table in (
                    "users",
                    "chats",
                    "messages",
                    "paired_devices",
                    "local_tasks",
                )
            }
            snapshot["integrity"] = connection.execute(
                "pragma integrity_check"
            ).fetchone()[0]
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
        snapshot["installed_app"] = {
            "status": status,
            "version": (health or {}).get("version"),
        }
    except Exception:
        snapshot["installed_app"] = {"status": 0, "version": None}
    return snapshot


def pod_evidence() -> dict:
    """What this run's own isolated database proves about compute.

    The monitor decides whether to restore the owner's permission from `pods_created`. Reading that
    from a lifecycle file the watchdog may not have filled in would let a real Pod look like
    nothing, and a restored permission would permit a second one. The controller's session row is
    the authoritative record of the Pod this run actually used.
    """
    try:
        from app.compute.models import ComputeSession
        from app.database import SessionLocal

        with SessionLocal() as db:
            rows = db.query(ComputeSession).all()
            pod_ids = sorted({row.pod_id for row in rows if row.pod_id})
            estimate = 0.0
            for row in rows:
                try:
                    estimate += float(row.estimated_cost or 0)
                except (TypeError, ValueError):
                    continue
            return {
                "pods_created": len(pod_ids),
                "pod_ids": pod_ids,
                "compute_sessions": [
                    {
                        "pod_id": row.pod_id,
                        "status": row.status,
                        "datacenter": row.datacenter,
                        "gpu": row.gpu_type,
                        "hourly_rate_usd": float(row.hourly_rate or 0),
                        "estimated_cost_usd": float(row.estimated_cost or 0),
                        "started_at": row.started_at.isoformat() if row.started_at else None,
                        "ready_at": row.ready_at.isoformat() if row.ready_at else None,
                        "stopped_at": row.stopped_at.isoformat() if row.stopped_at else None,
                    }
                    for row in rows
                ],
                "estimated_spend_usd": round(estimate, 4),
            }
    except Exception as error:  # noqa: BLE001 - an unreadable record is reported, never assumed clean
        return {"pods_created": None, "pod_ids": [], "error": str(error)[:300]}


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
        "approved_volumes": list(APPROVED_VOLUMES),
        "gpu": selection.get("selected"),
        "placement": {
            "datacenter": (selection.get("selected") or {}).get("datacenter"),
            "volume_id": (selection.get("selected") or {}).get("volume_id"),
            "storage_mode": (selection.get("selected") or {}).get("storage_type"),
        },
        "verified_placements": selection.get("verified_placements"),
        "replica_refusals": selection.get("replica_refusals"),
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
            '--budget-usd 2.00 --confirm "I AUTHORIZE ONE PAID POD"'
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
                "reason": "No paid run was started: " + ", ".join(blocked),
                "detail": selection.get("reason"),
                "authorization": "Owner authorization is a separate prerequisite and was not bypassed.",
            },
        )
        _write_json(
            "cleanup.json",
            {
                "checked_at": utcnow(),
                "pod_count": pods.get("pod_count"),
                "pods": pods.get("pods"),
                "verdict": "CLEAN"
                if pods.get("pod_count") == 0
                else "CRITICAL_UNCONFIRMED",
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
            "no approved L40S in the verified replica's datacenter (CAPACITY_UNAVAILABLE, no queue)",
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
    if RUN_ID and isinstance(payload, dict):
        payload = {**payload, "run_id": RUN_ID}
    (LIVE / name).write_text(
        # Evidence may contain datetimes (a task's verification report does). A crash *after* the
        # model did the work, while writing its evidence, is the worst possible failure: it turned
        # a run with 26 real responses into `CRASHED` with no independent checks at all.
        json.dumps(payload, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )


def _write_jsonl(name: str, rows) -> None:
    LIVE.mkdir(parents=True, exist_ok=True)
    with (LIVE / name).open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _sse_stream(
    url: str,
    token: str,
    body: dict,
    deadline: float,
    on_event=None,
    read_timeout: float | None = None,
) -> list[dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    events: list[dict] = []
    current = None
    # The stall detector is bounded by the run's own deadline: a 300-second read can never outlive
    # the budget the policy and the watchdog already enforce.
    remaining = max(1.0, deadline - time.monotonic())
    timeout = min(float(read_timeout or SSE_READ_TIMEOUT_SECONDS), remaining)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw in response:
                if time.monotonic() > deadline:
                    events.append({"event": "client_timeout"})
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line or line.startswith(":"):
                    continue
                events.append({"line": line[:400]})
                if line.startswith("event: "):
                    current = line[len("event: ") :].strip()
                elif line.startswith("data: ") and on_event is not None:
                    try:
                        payload = json.loads(line[len("data: ") :])
                    except ValueError:
                        continue
                    try:
                        on_event(current, payload)
                    except Exception:  # noqa: BLE001 - a failed reaction must not kill the stream
                        continue
    except (TimeoutError, OSError, http_client.HTTPException) as error:
        # A lost tail is evidence, not a crash. Whatever the server already persisted stays
        # inspectable, and the verdict is still computed from the database and the workspace.
        events.append(
            {
                "event": "stream_read_error",
                "detail": f"{type(error).__name__}: {error}"[:200],
            }
        )
    return events


APPROVALS: list[dict] = []

# How fresh the monitor's own observation must be before the harness will skip its own reads.
# The monitor checks every 30 seconds, so a payload older than this is not "the same moment" any
# more and the full path is used instead. The preflight report may be older because every fact in
# it (installed build, production database, disk, watchdog, provenance) changes on a human scale.
PREPARED_MAX_AGE_SECONDS = 120
PREFLIGHT_MAX_AGE_SECONDS = 900


def _parse_iso(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def prepared_problem(
    prepared,
    budget: float,
    *,
    max_billable_minutes: int = MAX_BILLABLE_MINUTES,
    target_work_minutes: int = TARGET_WORK_MINUTES,
    min_work_minutes: int = MIN_WORK_MINUTES,
    now: datetime | None = None,
) -> str | None:
    """Every reason the monitor's prepared observation must not be used. Empty means usable.

    This is the fast path's whole safety case: the *critical* facts are re-validated here, from a
    payload the monitor built seconds ago after reading the provider. Nothing that could change the
    money or the placement is taken on trust, and a stale payload falls back to the full path
    rather than being used.
    """
    now = now or datetime.now(timezone.utc)
    if not isinstance(prepared, dict):
        return "not_a_payload"
    if prepared.get("schema") != 1:
        return "unknown_schema"

    observed = _parse_iso(prepared.get("observed_at"))
    if observed is None:
        return "missing_observed_at"
    age = (now - observed).total_seconds()
    if age > PREPARED_MAX_AGE_SECONDS:
        return f"stale_observation_{int(age)}s"
    if age < -120:
        return "observation_in_the_future"

    if int(prepared.get("pod_count") or 0) != 0:
        return "existing_pod"

    placement = prepared.get("placement") if isinstance(prepared.get("placement"), dict) else {}
    volume_id = str(placement.get("volume_id") or "")
    if volume_id not in APPROVED_VOLUMES:
        return "placement_not_approved"
    spec = placement_for(volume_id)
    if spec is None or spec["datacenter"] != placement.get("datacenter"):
        return "placement_mismatch"

    preflight = prepared.get("preflight") if isinstance(prepared.get("preflight"), dict) else {}
    if preflight.get("verdict") != "READY":
        return "preflight_not_ready"
    checked = _parse_iso(preflight.get("checked_at"))
    if checked is None:
        return "preflight_missing_age"
    if (now - checked).total_seconds() > PREFLIGHT_MAX_AGE_SECONDS:
        return "preflight_too_old"

    try:
        rate = float(prepared.get("rate_usd_per_hour"))
    except (TypeError, ValueError):
        return "missing_rate"
    if rate <= 0:
        return "invalid_rate"
    declared = prepared.get("budget_usd")
    if declared is not None:
        try:
            if float(declared) > MAX_BUDGET_USD + 1e-9:
                return "prepared_budget_above_cap"
        except (TypeError, ValueError):
            return "invalid_prepared_budget"

    policy = cost_policy.plan(
        budget,
        rate,
        max_billable_seconds=max_billable_minutes * 60,
        target_work_seconds=target_work_minutes * 60,
        min_work_seconds=min_work_minutes * 60,
    )
    if not policy.feasible or not policy.sufficient:
        return "no_work_window"
    return None


def prepared_selection(prepared: dict) -> dict:
    """The shape the rest of run_live already consumes, built without a provider read."""
    placement = prepared["placement"]
    rate = float(prepared["rate_usd_per_hour"])
    return {
        "verdict": "READY",
        "prepared": True,
        "observed_at": prepared.get("observed_at"),
        "selected": {
            "gpu": "NVIDIA L40S",
            "datacenter": placement["datacenter"],
            "volume_id": placement["volume_id"],
            "storage_type": placement.get("storage_mode") or "regional_network_volume",
            "hourly_price_usd": rate,
            "name": "L40S",
        },
        "volume": {
            "id": placement["volume_id"],
            "datacenter": placement["datacenter"],
        },
        "model_state": prepared.get("model_state"),
    }


def _alembic_head() -> str | None:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(BACKEND / "alembic.ini"))
        # `alembic.ini` names the directory relatively; pin it so the head can be read from any cwd.
        config.set_main_option("script_location", str(BACKEND / "alembic"))
        return ScriptDirectory.from_config(config).get_current_head()
    except Exception:  # noqa: BLE001 - an unreadable head simply disables the template
        return None


def _template_is_current(path: Path, head: str | None) -> bool:
    if not head or not path.is_file():
        return False
    try:
        import sqlite3

        connection = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True)
        try:
            row = connection.execute("select version_num from alembic_version").fetchone()
        finally:
            connection.close()
    except Exception:  # noqa: BLE001
        return False
    return bool(row and row[0] == head)


def prepare_database(work: Path, env: dict) -> dict:
    """A migrated isolated database, from a verified template when the schema has not moved.

    Alembic costs ~2.7 s and is on the path between "a card is free" and "a Pod is requested".
    The template is only used when its recorded revision equals the directory's current head, so a
    migration added by a later change invalidates it automatically.
    """
    live_db = work / "live.db"
    head = _alembic_head()
    template = Path(tempfile.gettempdir()) / "canalla-live-template" / "live-template.db"
    started = time.monotonic()
    if _template_is_current(template, head):
        shutil.copy2(template, live_db)
        return {"mode": "template", "head": head, "seconds": round(time.monotonic() - started, 3)}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND),
        env=env,
        check=True,
        capture_output=True,
    )
    elapsed = round(time.monotonic() - started, 3)
    if head:
        try:
            template.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(live_db, template)
        except OSError:
            pass
    return {"mode": "migrated", "head": head, "seconds": elapsed}


def approve_compute_start(*, base: str, token: str, seen: set):
    """Approve the product's own money prompt, exactly once, while the stream is open.

    A direct-mode chat that needs the model asks the owner to confirm the paid start. An unattended
    acceptance is that owner, and the owner's decision is bound to the prompt's digest - so this
    approves the run the server created rather than starting compute behind the product's back.
    Without this the request waits for a click that never comes and ends as a timeout with no Pod.
    """

    def handler(event, payload):
        if event != "tool" or not isinstance(payload, dict):
            return
        if payload.get("tool_name") != "compute.start":
            return
        run_id = payload.get("id")
        if not run_id or run_id in seen:
            return
        seen.add(run_id)
        request = {"allow": True}
        if payload.get("input_digest"):
            request["digest"] = payload["input_digest"]
        try:
            status, answer = http(
                "POST", f"{base}/tools/runs/{run_id}/confirm", request, token=token, timeout=30
            )
        except Exception as error:  # noqa: BLE001 - reported in the evidence, not swallowed
            status, answer = 0, {"error": str(error)[:200]}
        APPROVALS.append(
            {
                "at": utcnow(),
                "run_id": run_id,
                "digest_bound": bool(payload.get("input_digest")),
                "status": status,
                "answer": answer,
            }
        )

    return handler


def selftest_verdict(events: list[dict]) -> dict:
    """Did the model actually answer? A decision the harness can test without a Pod.

    The SSE stream is the evidence: the answer arrives as ``delta`` events, a turn that failed ends
    with an ``error`` event, and a finished turn ends with ``done``. Anything short of a non-empty
    answer plus a done marker is a FAIL - never a partial PASS.
    """
    current = None
    deltas: list[str] = []
    errors: list[str] = []
    done = False
    for row in events if isinstance(events, list) else []:
        line = row.get("line") if isinstance(row, dict) else None
        if not isinstance(line, str):
            continue
        if line.startswith("event: "):
            current = line[len("event: ") :].strip()
        elif line.startswith("data: "):
            payload = line[len("data: ") :]
            if current == "delta":
                try:
                    body = json.loads(payload)
                except ValueError:
                    body = {}
                text = body.get("content") if isinstance(body, dict) else None
                if isinstance(text, str) and text.strip():
                    deltas.append(text)
            elif current == "error":
                errors.append(payload[:400])
            elif current == "done":
                done = True
    answer = "".join(deltas).strip()
    problems: list[str] = []
    if errors:
        problems.append("error_event")
    if not done:
        problems.append("no_done_marker")
    if len(answer) < 8:
        problems.append("no_real_answer")
    return {
        "verdict": "PASS" if not problems else "FAIL",
        "problems": problems,
        "answer_chars": len(answer),
        "answer_preview": answer[:300],
        "delta_events": len(deltas),
        "errors": errors,
    }


def prove_model(*, base: str, token: str, deadline: float, on_event=None) -> dict:
    """One short chat turn through the real backend, in the same Pod TaskDesk will use.

    This does not replace the acceptance: it is the *precondition* a run on a not-yet-verified copy
    must satisfy before it may spend the rest of the budget. A failure here ends the run with the
    Pod stopped, never with a second Pod somewhere else.
    """
    _, chat = http("POST", base + "/chats", {}, token=token)
    chat_id = chat["id"]
    events = _sse_stream(
        f"{base}/chats/{chat_id}/stream",
        token,
        {
            "content": SELFTEST_PROMPT,
            "web_mode": "off",
            "computer_mode": "off",
            "tor_mode": "off",
        },
        deadline,
        on_event=on_event,
    )
    verdict = selftest_verdict(events)
    status_code = 0
    status: dict = {}
    try:
        status_code, status = http("GET", base + "/llm/status", token=token, timeout=30)
    except Exception as error:  # noqa: BLE001 - an unreadable status is a failed proof
        status = {"error": str(error)[:200]}
    diagnostic = (status or {}).get("diagnostic") or {}
    identity = {
        "provider": (status or {}).get("provider"),
        "model": (status or {}).get("model"),
        "pod_id": diagnostic.get("pod_id"),
        "gpu": diagnostic.get("gpu"),
        "datacenter": diagnostic.get("datacenter"),
        "available": (status or {}).get("available"),
    }
    problems = list(verdict["problems"])
    if status_code != 200:
        problems.append("llm_status_unreadable")
    if identity["provider"] != "llamacpp":
        problems.append("provider_not_llamacpp")
    if not identity["pod_id"]:
        problems.append("no_pod_id")
    verdict.update(
        {
            "verdict": "PASS" if not problems else "FAIL",
            "problems": problems,
            "checked_at": utcnow(),
            "chat_id": chat_id,
            "identity": identity,
            "llm_status_code": status_code,
            "raw_events": events[-20:],
        }
    )
    return verdict


def _stream_recovery_wait(events: list[dict], deadline: float, max_seconds: float = 90.0) -> dict:
    """After a lost stream tail, give the *same* backend a bounded chance to finish the turn.

    The chat backend continues a turn detached when its client disconnects, so a read timeout does
    not prove the model stopped working. Killing a paid Pod while it is still producing the answer
    is the expensive mistake; waiting a bounded 90 seconds against the same Pod is not.
    """
    marker = str((events[-1] if events else {}).get("event") or "")
    if marker not in {"stream_read_error", "client_timeout"}:
        return {"waited_seconds": 0.0, "reason": None, "finished": None}
    from app.tools.local import machine
    from app.database import SessionLocal
    from app.tools.models import LocalTask

    started = time.monotonic()
    limit = min(float(max_seconds), max(0.0, deadline - started))
    while time.monotonic() - started < limit:
        time.sleep(3)
        try:
            with SessionLocal() as db:
                task = db.query(LocalTask).order_by(LocalTask.started_at.desc()).first()
                status = task.status if task is not None else None
        except Exception:  # noqa: BLE001 - a busy database is not a conclusion
            status = None
        if status in machine.TERMINAL:
            return {
                "waited_seconds": round(time.monotonic() - started, 1),
                "reason": marker,
                "finished": status,
            }
    return {
        "waited_seconds": round(time.monotonic() - started, 1),
        "reason": marker,
        "finished": None,
    }


def run_live(
    *,
    confirm: str,
    budget: float | None,
    max_billable_minutes: int,
    target_work_minutes: int,
    min_work_minutes: int,
    volume: str | None = DEFAULT_VOLUME,
    self_test_mode: bool = False,
    prepared: dict | None = None,
    startup_probe: bool = False,
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

    timings: dict = {"started_at": utcnow()}
    prepared_reason = None
    prepared_used = False
    if prepared is not None:
        # Fast path: the monitor read the provider moments ago. Only the facts that could change
        # the money or the placement are re-checked, and they are checked against the payload's
        # own timestamps - a stale observation falls through to the full path below.
        prepared_reason = prepared_problem(
            prepared,
            budget,
            max_billable_minutes=max_billable_minutes,
            target_work_minutes=target_work_minutes,
            min_work_minutes=min_work_minutes,
        )
        if prepared_reason is None:
            prepared_used = True

    if prepared_used:
        timings["prepared_loaded_at"] = utcnow()
        before = prepared["preflight"]["report"]
        pods = {
            "ok": True,
            "pod_count": prepared["pod_count"],
            "pods": [],
            "balance_usd": prepared.get("balance_usd"),
            "fast_path": True,
        }
        selection = prepared_selection(prepared)
    else:
        if prepared is not None:
            timings["prepared_rejected"] = prepared_reason
        before = preflight()
        timings["preflight_at"] = utcnow()
        if before.get("verdict") != "READY":
            raise SystemExit(f"preflight is not READY: {before.get('blockers')}")
        pods = pods_state()
        if pods.get("ok") is not True or pods.get("pod_count") != 0:
            raise SystemExit(
                "refusing: the provider state is not a confirmed zero-Pod account "
                f"(ok={pods.get('ok')}, pods={pods.get('pod_count')})"
            )

        selection = gpu_probe()
    timings["selected_at"] = utcnow()
    _write_json(
        "authorization.json",
        {
            "generated_at": utcnow(),
            "confirmation_phrase": confirm,
            "owner_phrase_expected_ru": CONFIRMATION_RU,
            "budget_usd": budget,
            "max_billable_minutes": max_billable_minutes,
            "gpu_verdict": selection.get("verdict"),
            "status": "AUTHORIZED"
            if selection.get("verdict") == "READY"
            else "BLOCKED",
            "blocked_by": None
            if selection.get("verdict") == "READY"
            else selection.get("verdict"),
            "path": "prepared" if prepared_used else "full",
            "prepared_observed_at": prepared.get("observed_at") if prepared_used else None,
            "prepared_rejected": prepared_reason,
        },
    )
    if selection.get("verdict") != "READY" or not selection.get("selected"):
        # No Pod exists and none is created: this is the typed external blocker, not a retry.
        _write_json(
            "final-summary.json",
            {
                "generated_at": utcnow(),
                "verdict": selection.get("verdict") or "CAPACITY_UNAVAILABLE",
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
        # A blocked run leaves its own account state, not the previous run's. Nothing was created,
        # so the cleanup is a read-back confirmation rather than a termination claim.
        clean = pods.get("pod_count") == 0
        _write_json(
            "cleanup.json",
            {
                "checked_at": utcnow(),
                "pod_count": pods.get("pod_count"),
                "pods": pods.get("pods"),
                "balance_usd": pods.get("balance_usd"),
                "verdict": "CLEAN" if clean else "CRITICAL_UNCONFIRMED",
                "note": "The harness created nothing: the placement was not bookable at re-check.",
            },
        )
        _write_json(
            "billing-summary.json",
            {
                "checked_at": utcnow(),
                "spend_usd": 0.0,
                "balance_usd": pods.get("balance_usd"),
                "note": "No Pod was created; storage charges on the existing volumes are independent.",
            },
        )
        return 2

    rate = float(selection["selected"]["hourly_price_usd"])
    placement = {
        "datacenter": str(selection["selected"].get("datacenter") or ""),
        "volume_id": str(selection["selected"].get("volume_id") or ""),
        "storage_mode": str(
            selection["selected"].get("storage_type") or "regional_network_volume"
        ),
    }
    if not placement["volume_id"] or not placement["datacenter"]:
        raise SystemExit(
            f"refusing: the selected candidate has no placement: {selection.get('selected')}"
        )
    if placement["volume_id"] not in APPROVED_VOLUMES:
        raise SystemExit(
            f"refusing: {placement['volume_id']} is not an approved placement "
            f"({', '.join(APPROVED_VOLUMES)}); a new placement needs its own approval"
        )
    if volume and placement["volume_id"] != volume:
        raise SystemExit(
            f"refusing: the selected placement {placement['volume_id']} is not the one this run "
            f"was started for ({volume})"
        )
    model_state, model_reason = model_status(placement["volume_id"])
    # A copy whose record is not a verified real-model answer is proved *in this same Pod* before
    # any TaskDesk work. The explicit flag can only add the proof, never remove it.
    selftest_required = bool(
        self_test_mode
        or model_state != VERIFIED
        or (prepared_used and prepared.get("requires_selftest"))
    )
    policy = cost_policy.plan(
        budget,
        rate,
        max_billable_seconds=max_billable_minutes * 60,
        target_work_seconds=target_work_minutes * 60,
        min_work_seconds=min_work_minutes * 60,
    )
    _write_json("cost-policy.json", policy.to_dict())
    if not policy.feasible or not policy.sufficient:
        raise SystemExit(
            f"refusing: the selected card leaves no usable window: {policy.to_dict()}"
        )

    sys.path.insert(0, str(REPO / "scripts"))
    import runpod_api_tools

    key = runpod_api_tools.read_provider_secret()
    if not key:
        raise SystemExit(
            "no RunPod credential in the installed product's credential store"
        )

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
        # The placement the probe proved is available *and* holds a verified copy of the model.
        # Without these two the backend would keep scheduling in the configured default volume's
        # datacenter, which is exactly the single-datacenter assumption this run removes.
        "RUNPOD_NETWORK_VOLUME_ID": placement["volume_id"],
        "RUNPOD_DATACENTER": placement["datacenter"],
        "RUNPOD_STORAGE_MODE": placement["storage_mode"],
        "COMPUTE_BACKGROUND_ENABLED": "true",
        "ALLOW_USER_COMPUTE_START": "true",
        # A fresh Pod's first inference is cold. The product's 20-second first-line default is a
        # chat latency budget, not a model-load budget; keeping it would abort the run's very first
        # generation at the moment the model becomes ready.
        "LLM_STREAM_FIRST_LINE_SECONDS": "120",
        # An application build is a long tool task, not a 3-minute chat turn. The product's own
        # default (30 min / 40 calls) is a sensible interactive budget but smaller than the owner's
        # 45-80 minute acceptance window, so the run would be cut off after the money was spent.
        "TOOLS_TASK_MAX_RUNTIME": "4800",
        "TOOLS_TASK_MAX_CALLS": "80",
        "TINYFISH_API_KEY": "",
        "ADMIN_EMAILS": "[]",
        "DOCUMENT_STORAGE_DIR": str(work / "documents"),
        "ALEX_LLM_DATA_DIR": str(work / "app-data"),
    }
    # The parent reads the task journal itself. Without this its SessionLocal would resolve the
    # parent's DATABASE_URL, which is not the isolated database the child backend uses.
    os.environ.update(env)
    database = prepare_database(work, env)
    timings["database_at"] = utcnow()
    timings["database"] = database
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
        "placement": placement,
        "model_state": model_state,
        "model_reason": model_reason,
        "selftest_required": selftest_required,
        "volume": selection.get("volume"),
        "policy": policy.to_dict(),
        "balance_before_usd": pods.get("balance_usd"),
        "path": "prepared" if prepared_used else "full",
        "prepared_observed_at": prepared.get("observed_at") if prepared_used else None,
        "prepared_rejected": prepared_reason,
        "database": database,
        "timings": timings,
    }
    _write_json("production-before.json", before)
    try:
        healthy = False
        for _ in range(300):
            try:
                if http("GET", base + "/health")[0] == 200:
                    healthy = True
                    break
            except OSError:
                pass
            time.sleep(0.2)
        timings["backend_ready_at"] = utcnow()
        timings["backend_healthy"] = healthy
        email = f"live-{nonce}@example.com"
        _, registered = http(
            "POST",
            base + "/auth/register",
            {"email": email, "password": "live-pass-123"},
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
        timings["session_ready_at"] = utcnow()
        if startup_probe:
            # Free measurement: everything a paid attempt needs *before* the request that can create
            # a Pod. No chat message is sent, so no compute exists afterwards.
            summary["verdict"] = "STARTUP_PROBE"
            summary["reason"] = "measured the path to the first request without sending it"
            _write_json("startup-timings.json", timings)
            _write_json("final-summary.json", summary)
            print(json.dumps({"verdict": "STARTUP_PROBE", "timings": timings}, ensure_ascii=False))
            return 0
        host_env = {
            **env,
            "ALEX_ACCEPTANCE_RUN": "1",
            "ALEX_BACKEND_URL": base,
            "ALEX_TOKEN": token,
            "ALEX_DEVICE_NAME": f"LIVE Qwen {nonce}",
            "ALEX_DEVICE_DIR": str(work / "device"),
            "ALEX_DEVICE_CREDENTIAL_TARGET": f"Canalla Acceptance/live-{nonce}",
            "ALEX_WORKSPACE_ROOTS": str(workspace),
            # `run_python` on the Computer host must be an interpreter that can actually build and
            # test the generated app. Without this the host picked the bare system Python, whose
            # `pip list` has no pytest and no FastAPI: every `python -m pytest` exited 4 and a
            # written, plausible project could never be verified.
            "ALEX_PYTHON": venv_python(),
        }
        host_log = (work / "host.log").open("w", encoding="utf-8", errors="replace")
        host = subprocess.Popen(
            [str(HOST_EXE)],
            env=host_env,
            stdout=host_log,
            stderr=subprocess.STDOUT,
            text=True,
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
                "--runner-pid",
                str(os.getpid()),
                "--run-id",
                RUN_ID,
                "--out",
                str(watchdog_out),
            ],
            stdout=(work / "watchdog.log").open(
                "w", encoding="utf-8", errors="replace"
            ),
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = (
            time.monotonic()
            + policy.hard_wall_seconds
            + policy.termination_grace_seconds
        )
        timings["first_request_at"] = utcnow()
        _write_json("startup-timings.json", timings)
        APPROVALS.clear()
        approve = approve_compute_start(base=base, token=token, seen=set())
        if selftest_required:
            # The same Pod proves the model before it spends the acceptance window. A failure here
            # is a typed result, not a reason to try elsewhere: `finally` stops the Pod and the
            # removal is confirmed before this returns.
            proof = prove_model(
                base=base,
                token=token,
                deadline=min(deadline, time.monotonic() + SELFTEST_TIMEOUT_SECONDS),
                on_event=approve,
            )
            proof["mode"] = "self_test"
            proof["placement"] = placement
            _write_json("selftest-evidence.json", proof)
            _write_json("compute-confirmations.json", APPROVALS)
            summary["model_selftest"] = {
                "verdict": proof["verdict"],
                "problems": proof["problems"],
                "answer_chars": proof["answer_chars"],
                "identity": proof["identity"],
            }
            if proof["verdict"] != "PASS":
                summary["verdict"] = "MODEL_SELFTEST_FAILED"
                summary["reasons"] = proof["problems"]
                summary.update(pod_evidence())
                _write_json("final-summary.json", summary)
                print(json.dumps(summary, ensure_ascii=False, indent=1)[-4000:])
                return 3
        events = _sse_stream(
            f"{base}/chats/{chat_id}/stream",
            token,
            {
                "content": SCENARIO,
                # The acceptance has no TinyFish credential, so a web turn can only fail and waste
                # model steps (one real attempt spent two calls on `provider_not_configured`).
                "web_mode": "off",
                "computer_mode": "trusted",
                "tor_mode": "off",
            },
            deadline,
            on_event=approve,
        )
        _write_json("compute-confirmations.json", APPROVALS)
        summary["stream_events"] = len(events)
        summary["stream_end"] = events[-1] if events else None
        # A lost tail is not a lost turn: the backend keeps generating when its client disconnects.
        summary["stream_recovery"] = _stream_recovery_wait(events, deadline)
        _write_json("stream-recovery.json", summary["stream_recovery"])

        from app.agent import provenance as provenance_module
        from app.database import SessionLocal
        from app.tools.models import LocalTask, ToolRun

        with SessionLocal() as db:
            tasks = (
                db.query(LocalTask).order_by(LocalTask.started_at.desc()).limit(1).all()
            )
            task = tasks[0] if tasks else None
            if task is None:
                summary["verdict"] = "FAIL"
                summary["reasons"] = ["no task was created"]
                summary.update(pod_evidence())
                # Written before returning: the monitor reads this file to decide whether anything
                # was bought, and a missing summary used to leave a stale verdict in its place.
                _write_json("final-summary.json", summary)
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
        summary.update(pod_evidence())
        _write_json("final-summary.json", summary)
        return 0 if summary["verdict"] == "PASS" else 1
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001 - a typed verdict, never a silent death
        # A crash after the Pod exists must still leave a verdict and the accounting. Without this,
        # the run wrote only `run-<id>.json` with `INCOMPLETE`, and the reason lived in a log nobody
        # read. This is the path that the 30-second SSE timeout took.
        summary["verdict"] = "CRASHED"
        summary["reasons"] = [f"{type(error).__name__}: {error}"[:300]]
        try:
            summary.update(pod_evidence())
        except Exception:  # noqa: BLE001
            pass
        try:
            _write_json("final-summary.json", summary)
        except Exception:  # noqa: BLE001 - the finally block still records the accounting
            pass
        return 1
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
                "verdict": "CLEAN"
                if final_pods.get("pod_count") == 0
                else "CRITICAL_UNCONFIRMED",
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
        # The run's own logs are the only way to explain a failure after the fact. They live in a
        # temporary directory, so they are copied into the evidence before it is removed. The
        # 08:55 attempt failed with a generic `stream_interrupted` and left no backend log at all.
        logs_dir = LIVE / "logs"
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            for name in ("backend.log", "host.log", "watchdog.log"):
                source = work / name
                if source.is_file():
                    shutil.copy2(source, logs_dir / name)
            (logs_dir / "run-context.json").write_text(
                json.dumps(
                    {
                        "captured_at": utcnow(),
                        "placement": placement,
                        "model_state": model_state,
                        "model_reason": model_reason,
                        "selftest_required": selftest_required,
                        "budget_usd": budget,
                        "hourly_rate_usd": rate,
                        "verdict": summary.get("verdict"),
                        "reasons": summary.get("reasons"),
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
        # The authoritative accounting for *this* run, written while its isolated database still
        # exists. The monitor reads this record, not a shared "latest summary" that a later process
        # (or a probe) may have replaced.
        try:
            evidence = pod_evidence()
            accounting = {
                "run_id": RUN_ID or None,
                "verdict": summary.get("verdict") or "INCOMPLETE",
                "pods_created": evidence.get("pods_created"),
                "pod_ids": evidence.get("pod_ids") or [],
                "spend_usd": evidence.get("estimated_spend_usd") or 0.0,
                "balance_after_usd": balance_after,
                "hourly_rate_usd": rate,
                "finished_at": utcnow(),
                "error": evidence.get("error"),
            }
            if RUN_ID:
                _write_json(f"run-{RUN_ID}.json", accounting)
        except Exception:  # noqa: BLE001 - accounting must never mask the run's own result
            pass
        shutil.rmtree(work, ignore_errors=True)
        print(json.dumps(summary, ensure_ascii=False, indent=1)[-4000:])


def _local_stream(
    base: str, body: dict, *, on_event=None, deadline_seconds: float = 10.0
) -> list[dict]:
    """The stream client, with one retry for a local Windows connection abort.

    `ConnectionAbortedError 10053` is the test host refusing a loopback connection from a reused
    ephemeral port, not the stream logic failing. It carries no data and is retried once here so a
    self-test cannot flake; the production client never retries a stream (a generation is not
    idempotent).
    """
    events: list[dict] = []
    for attempt in range(2):
        events = _sse_stream(
            base + "/stream",
            "token",
            body,
            time.monotonic() + deadline_seconds,
            on_event=on_event,
        )
        aborted = (
            bool(events)
            and events[-1].get("event") == "stream_read_error"
            and "10053" in str(events[-1].get("detail"))
            and len(events) == 1
        )
        if not aborted:
            return events
        time.sleep(0.2)
    return events


def stream_timing_self_test() -> list[dict]:
    """Cold start and stall behaviour of the stream reader, with a real local HTTP server.

    A first token that takes a second is normal after a cold model load; a stalled reader is
    evidence, never an uncaught exception that kills a paid run.
    """

    def serve(delay: float):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'event: meta\ndata: {}\n\n')
                self.wfile.flush()
                time.sleep(delay)
                try:
                    self.wfile.write(
                        (
                            'event: delta\ndata: {"content": "готово к работе"}\n\n'
                            'event: done\ndata: {"message_id": "m1"}\n\n'
                        ).encode()
                    )
                    self.wfile.flush()
                except OSError:
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    checks: list[dict] = []
    server = serve(1.0)
    try:
        events = _local_stream(
            f"http://127.0.0.1:{server.server_address[1]}", {}, deadline_seconds=10.0
        )
    finally:
        server.shutdown()
        server.server_close()
    verdict = selftest_verdict(events)
    checks.append(
        {
            "check": "a_delayed_first_token_is_not_a_failure",
            "ok": verdict["verdict"] == "PASS" and verdict["answer_chars"] > 8,
            "detail": str(verdict)[:160],
        }
    )

    server = serve(2.0)
    try:
        stalled = _sse_stream(
            f"http://127.0.0.1:{server.server_address[1]}/stream",
            "token",
            {},
            time.monotonic() + 30,
            read_timeout=0.5,
        )
    finally:
        server.shutdown()
        server.server_close()
    checks.append(
        {
            "check": "a_stalled_stream_is_evidence_not_a_crash",
            "ok": bool(stalled) and stalled[-1].get("event") == "stream_read_error",
            "detail": str(stalled[-1] if stalled else None)[:160],
        }
    )

    def serve_script(body: bytes, delay: float):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                time.sleep(delay)
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
                except OSError:
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    # G. The run's own deadline wins over a slow upstream, and it is typed.
    server = serve_script(b'event: meta\ndata: {}\n\n', 2.0)
    started_at = time.monotonic()
    try:
        late = _sse_stream(
            f"http://127.0.0.1:{server.server_address[1]}/stream",
            "token",
            {},
            time.monotonic() + 0.5,
            read_timeout=10,
        )
    finally:
        bounded = time.monotonic() - started_at
        server.shutdown()
        server.server_close()
    checks.append(
        {
            "check": "the_global_deadline_is_a_typed_stop",
            # The read timeout is capped by the remaining deadline, so the stop is either the
            # deadline marker or a bounded read timeout - never an unbounded wait.
            "ok": bool(late)
            and late[-1].get("event") in {"client_timeout", "stream_read_error"}
            and bounded < 1.5,
            "detail": f"{late[-1] if late else None} after {bounded:.2f}s",
        }
    )

    # H. A progress heartbeat is not a model token and can never be a PASS.
    heartbeat = selftest_verdict(
        [
            {"line": "event: progress"},
            {"line": 'data: {"state": "starting_ai", "text": "Запускаю AI…"}'},
            {"line": "event: done"},
            {"line": "data: {}"},
        ]
    )
    checks.append(
        {
            "check": "a_heartbeat_is_not_a_model_response",
            "ok": heartbeat["verdict"] == "FAIL"
            and "no_real_answer" in heartbeat["problems"],
            "detail": str(heartbeat)[:160],
        }
    )
    return checks


def approval_self_test() -> list[dict]:
    """Offline: the money prompt is approved exactly once, and a real answer still decides PASS."""
    calls: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):  # noqa: D401 - silence the test server
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if self.path.endswith("/confirm"):
                calls.append(json.loads(self.rfile.read(length) or b"{}"))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "approved"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(
                (
                    'event: tool\ndata: {"id": "run-1", "tool_name": "compute.start", '
                    '"input_digest": "d1"}\n\n'
                    'event: delta\ndata: {"content": "готов отвечать на задачи"}\n\n'
                    'event: done\ndata: {"message_id": "m1"}\n\n'
                ).encode()
            )
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        APPROVALS.clear()
        events = _local_stream(
            base,
            {"content": "hi"},
            on_event=approve_compute_start(base=base, token="token", seen=set()),
        )
    finally:
        server.shutdown()
        server.server_close()
    verdict = selftest_verdict(events)
    interpreter_ok, interpreter_detail = _venv_can_build()
    return [
        {
            "check": "the_money_prompt_is_approved_once",
            "ok": calls == [{"allow": True, "digest": "d1"}],
            "detail": str(calls)[:200],
        },
        {
            "check": "an_approved_prompt_yields_a_real_answer",
            "ok": verdict["verdict"] == "PASS" and verdict["answer_chars"] > 8,
            "detail": str(verdict)[:200],
        },
        {
            "check": "the_approval_is_recorded_for_the_evidence",
            "ok": len(APPROVALS) == 1 and APPROVALS[0].get("run_id") == "run-1",
            "detail": str(APPROVALS)[:200],
        },
        {
            "check": "the_host_python_can_build_and_test_the_app",
            "ok": interpreter_ok,
            "detail": interpreter_detail,
        },
    ]


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

    screenshots = sorted(
        str(path.relative_to(workspace)) for path in workspace.rglob("*.png")
    )
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
            (
                run.tool_name,
                (run.result_metadata or {}).get("exit_code"),
                run.started_at,
            )
            for run in runs
            if run.tool_name in {"run_python", "run_process"}
        ]
    failures = [
        index for index, (_, code, _) in enumerate(codes) if code not in (None, 0)
    ]
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
    database_check: dict = {
        "databases": [str(path.name) for path in databases],
        "tables": {},
    }
    rows_found = False
    for path in databases[:5]:
        import sqlite3

        try:
            connection = sqlite3.connect(
                "file:" + path.as_posix() + "?mode=ro", uri=True
            )
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
                    count = connection.execute(
                        f'select count(*) from "{table}"'
                    ).fetchone()[0]
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
                    http_check.update(
                        {"status": status, "url": f"http://127.0.0.1:{port}/"}
                    )
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
            {
                "check": "independent_gui_load",
                "ok": False,
                "detail": "playwright unavailable",
            }
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
    global RUN_ID, LIVE
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--gpu-probe", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--volume", default=DEFAULT_VOLUME, choices=list(APPROVED_VOLUMES))
    parser.add_argument(
        "--self-test-mode",
        action="store_true",
        help="prove the model with a real answer in the same Pod before the acceptance",
    )
    parser.add_argument(
        "--prepared",
        default="",
        help="the monitor's fresh observation: skips the repeated free checks",
    )
    parser.add_argument(
        "--startup-probe",
        action="store_true",
        help="measure the path to the first request and stop before sending it",
    )
    parser.add_argument("--run-id", default="", help="the monitor's attempt id")
    parser.add_argument(
        "--evidence-dir",
        default="",
        help="write evidence here instead of final-live (probes must never clobber a live run)",
    )
    parser.add_argument(
        "--max-billable-minutes", type=int, default=MAX_BILLABLE_MINUTES
    )
    parser.add_argument("--target-work-minutes", type=int, default=TARGET_WORK_MINUTES)
    parser.add_argument("--min-work-minutes", type=int, default=MIN_WORK_MINUTES)
    args = parser.parse_args()
    RUN_ID = args.run_id or RUN_ID
    if args.evidence_dir:
        LIVE = Path(args.evidence_dir)

    if args.gpu_probe:
        print(json.dumps(gpu_probe(), ensure_ascii=False, indent=1)[:4000])
        return 0
    if args.prepare:
        return prepare()
    if args.plan:
        print(
            json.dumps(
                plan_report(args.budget_usd or MAX_BUDGET_USD, 1.09),
                ensure_ascii=False,
                indent=1,
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
        answered = selftest_verdict(
            [
                {"line": "event: delta"},
                {"line": 'data: {"content": "готов отвечать на задачи"}'},
                {"line": "event: done"},
                {"line": 'data: {"message_id": "m1"}'},
            ]
        )
        errored = selftest_verdict(
            [
                {"line": "event: delta"},
                {"line": 'data: {"content": "ok"}'},
                {"line": "event: error"},
                {"line": 'data: {"code": "stream_interrupted"}'},
                {"line": "event: done"},
                {"line": "data: {}"},
            ]
        )
        truncated = selftest_verdict(
            [
                {"line": "event: delta"},
                {"line": 'data: {"content": "частичный ответ модели"}'},
            ]
        )
        fresh_prepared = {
            "schema": 1,
            "observed_at": utcnow(),
            "pod_count": 0,
            "placement": {
                "datacenter": "US-MO-1",
                "volume_id": "y92bt25ru8",
                "storage_mode": "regional_network_volume",
            },
            "rate_usd_per_hour": 1.09,
            "budget_usd": 2.00,
            "requires_selftest": False,
            "preflight": {"verdict": "READY", "checked_at": utcnow(), "report": {}},
        }

        def prepared_variant(**changes):
            return {**fresh_prepared, **changes}

        def aged(seconds: int) -> str:
            return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(
                timespec="seconds"
            )

        checks += [
            {
                "check": "a_prepared_fast_path",
                "ok": prepared_problem(fresh_prepared, 2.00) is None,
            },
            {
                "check": "a_stale_observation_falls_back",
                "ok": str(
                    prepared_problem(
                        prepared_variant(observed_at=aged(PREPARED_MAX_AGE_SECONDS + 5)), 2.00
                    )
                ).startswith("stale_observation"),
            },
            {
                "check": "a_prepared_placement_must_match_its_datacenter",
                "ok": prepared_problem(
                    prepared_variant(
                        placement={
                            "datacenter": "US-MO-1",
                            "volume_id": "uwgeaie5b0",
                            "storage_mode": "regional_network_volume",
                        }
                    ),
                    2.00,
                )
                == "placement_mismatch",
            },
            {
                "check": "a_prepared_payload_with_an_existing_pod_is_refused",
                "ok": prepared_problem(prepared_variant(pod_count=1), 2.00) == "existing_pod",
            },
            {
                "check": "a_prepared_rate_without_a_work_window_is_refused",
                "ok": prepared_problem(prepared_variant(rate_usd_per_hour=9.99), 2.00)
                == "no_work_window",
            },
            {
                "check": "a_stale_preflight_falls_back",
                "ok": prepared_problem(
                    prepared_variant(
                        preflight={
                            "verdict": "READY",
                            "checked_at": aged(PREFLIGHT_MAX_AGE_SECONDS + 5),
                            "report": {},
                        }
                    ),
                    2.00,
                )
                == "preflight_too_old",
            },
            {
                "check": "a_prepared_payload_above_the_budget_is_refused",
                "ok": prepared_problem(prepared_variant(budget_usd=3.00), 2.00)
                == "prepared_budget_above_cap",
            },
        ]

        # A lost stream tail is evidence, never an exception: the 30-second read timeout killed a
        # paid run at the moment its model became ready.
        lost = _sse_stream(
            "http://127.0.0.1:1/none", "token", {"content": "x"}, time.monotonic() + 5
        )
        checks += [
            {
                "check": "a_lost_stream_is_evidence_not_a_crash",
                "ok": bool(lost) and lost[-1].get("event") == "stream_read_error",
                "detail": str(lost[-1] if lost else None)[:160],
            },
            {
                "check": "the_stream_read_timeout_outlives_a_cold_first_token",
                "ok": SSE_READ_TIMEOUT_SECONDS >= 180,
                "detail": str(SSE_READ_TIMEOUT_SECONDS),
            },
        ]
        checks += [
            {
                "check": "ninety_minute_billable_ceiling",
                "ok": policy["requested_max_billed_seconds"] == 5400,
            },
            {
                "check": "eighty_minute_work_target",
                "ok": policy["target_work_seconds"] == 4800,
            },
            {
                "check": "worst_case_within_two_dollars",
                "ok": policy["worst_case_usd"] <= 2.00,
            },
            {
                "check": "capacity_wait_is_sixty_seconds",
                "ok": policy["capacity_timeout_seconds"] == 60,
            },
            {
                "check": "no_twenty_minute_capacity_wait",
                "ok": "1200" not in json.dumps(plan_report(2.00, 1.09)),
            },
            {
                "check": "both_paid_volumes_are_approved",
                "ok": set(APPROVED_VOLUMES) == {"uwgeaie5b0", "y92bt25ru8"}
                and model_status("uwgeaie5b0")[0] == "UNVERIFIED_NEEDS_LIVE_CHECK"
                and model_status("y92bt25ru8")[0] == "VERIFIED",
            },
            {
                "check": "a_real_answer_is_the_only_selftest_pass",
                "ok": answered["verdict"] == "PASS" and answered["answer_chars"] > 8,
            },
            {
                "check": "an_error_event_is_never_a_pass",
                "ok": errored["verdict"] == "FAIL" and "error_event" in errored["problems"],
            },
            {
                "check": "a_truncated_stream_is_never_a_pass",
                "ok": truncated["verdict"] == "FAIL"
                and "no_done_marker" in truncated["problems"],
            },
        ]
        checks += stream_timing_self_test()
        checks += approval_self_test()
        report = {
            "checks": checks,
            "verdict": "PASS" if all(item["ok"] for item in checks) else "FAIL",
        }
        _write_json("self-test.json", report)
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["verdict"] == "PASS" else 1
    if args.preflight:
        print(json.dumps(preflight(), ensure_ascii=False, indent=1))
        return 0
    if args.live:
        prepared = None
        if args.prepared:
            try:
                prepared = json.loads(Path(args.prepared).read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise SystemExit(f"refusing: prepared payload is unreadable: {error}") from None
        return run_live(
            confirm=args.confirm,
            budget=args.budget_usd,
            max_billable_minutes=args.max_billable_minutes,
            target_work_minutes=args.target_work_minutes,
            min_work_minutes=args.min_work_minutes,
            volume=args.volume,
            self_test_mode=args.self_test_mode,
            prepared=prepared,
            startup_probe=args.startup_probe,
        )
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
