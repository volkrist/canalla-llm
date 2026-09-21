#!/usr/bin/env python
r"""REAL read-only RunPod shared-balance acceptance (Alex LLM 0.9.3).

Runs the real backend in-process against an isolated temporary database and the
REAL RunPod GraphQL endpoint, using the credential the product itself resolves
(settings / environment / `.env`). It performs at most TWO read-only account
queries and never creates, resumes, stops, adopts or deletes anything.

The compute controller is never invoked for the balance, the mock LLM provider is
used, and the background balance poller is disabled, so exactly the two controlled
refreshes below can happen. No Pod, no GPU, no Network Volume change.

Usage (repo root):
  apps\backend\.venv\Scripts\python.exe scripts\acceptance-runpod-balance.py

Exit codes: 0 pass, 2 credential not configured, 1 failure.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "apps" / "backend"
sys.path.insert(0, str(BACKEND))

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def money(value: Decimal | None) -> str:
    """UI rounding only: never print more precision than the user sees."""
    return "unavailable" if value is None else f"${value.quantize(Decimal('0.01'))}"


def main() -> int:
    workdir = tempfile.TemporaryDirectory(prefix="alex-balance-accept-")
    try:
        return run(workdir)
    finally:
        workdir.cleanup()


def run(workdir: tempfile.TemporaryDirectory) -> int:
    root = Path(workdir.name)
    # Isolated runtime for the whole acceptance. The credential is deliberately NOT
    # set here: it comes from the product's own settings resolution.
    os.environ["APP_ENV"] = "test"
    os.environ["JWT_SECRET"] = "acceptance-secret-not-for-deployment-" + "b" * 32
    os.environ["DATABASE_URL"] = "sqlite:///" + (root / "alex.db").as_posix()
    os.environ["ALEX_LLM_DATA_DIR"] = str(root)
    os.environ["DOCUMENT_STORAGE_DIR"] = str(root / "documents")
    os.environ["LLM_PROVIDER"] = "mock"
    os.environ["COMPUTE_BACKGROUND_ENABLED"] = "false"
    os.environ["BALANCE_BACKGROUND_ENABLED"] = "false"
    os.environ["CORS_ORIGINS"] = '["http://127.0.0.1:1420"]'

    from fastapi.testclient import TestClient
    from sqlalchemy import func, select

    from app.compute.models import ComputeSession
    from app.compute.runpod_api import RunPodAPI
    from app.config import get_settings
    from app.database import Base, SessionLocal, engine
    from app.main import app
    from app.models import User

    # Isolated schema for the acceptance run (the product migrates on startup; here the
    # models are authoritative and nothing else touches this database).
    Base.metadata.create_all(engine)

    settings = get_settings()
    secret = settings.runpod_api_key.get_secret_value()
    print("REAL read-only RunPod balance acceptance")
    print(f"isolated data root: {root}")
    if not secret:
        print("  [SKIP] no RunPod credential is configured through the product settings path")
        print("  Configure it in Alex (Settings -> provider secret -> RunPod) and re-run.")
        return 2

    # Hard block on every mutating supplier surface, installed before anything runs.
    def forbidden(*args, **kwargs):
        raise AssertionError("the balance acceptance must never mutate compute")

    for name in ("create_pod", "terminate_pod"):
        setattr(RunPodAPI, name, forbidden)

    with TestClient(app) as client:
        compute = app.state.compute
        for name in ("start_compute", "stop_compute", "search_gpu", "_ensure_on_demand_locked"):
            setattr(compute, name, forbidden)
        service = app.state.balance

        def register(email: str) -> dict:
            response = client.post(
                "/auth/register", json={"email": email, "password": "acceptance-password-123"}
            )
            assert response.status_code == 201, response.text
            return {"Authorization": "Bearer " + response.json()["access_token"]}

        user_a = register("balance-a@example.com")
        user_b = register("balance-b@example.com")

        print("1. first authenticated read (User A) — one upstream snapshot")
        response = client.get("/status", headers=user_a)
        check("User A /status returns 200", response.status_code == 200, str(response.status_code))
        payload = response.json()
        balance_a = payload["balance"]
        check("balance.configured is true", balance_a["configured"] is True)
        check("balance.available is true", balance_a["available"] is True)
        check("balance_usd is present", balance_a["balance_usd"] is not None)
        check("balance_usd parses as Decimal", isinstance(Decimal(str(balance_a["balance_usd"])), Decimal))
        check(
            "shared_account and read_only flags",
            balance_a["shared_account"] is True and balance_a["read_only"] is True,
        )
        check("exactly one upstream call after User A", service.fetches == 1, f"fetches={service.fetches}")
        check("no provider key in the API payload", secret not in response.text)
        first_value = Decimal(str(balance_a["balance_usd"]))

        print("2. second authenticated read (User B) must reuse the shared cache")
        response_b = client.get("/status", headers=user_b)
        check("User B /status returns 200", response_b.status_code == 200, str(response_b.status_code))
        balance_b = response_b.json()["balance"]
        check("User B sees the same shared balance", balance_b["balance_usd"] == balance_a["balance_usd"])
        check("no extra upstream call for User B", service.fetches == 1, f"fetches={service.fetches}")
        check("no provider key in User B payload", secret not in response_b.text)

        print("3. second read-only refresh after the cache TTL expires")
        # Shrink the shared-cache window so the next authoritative read performs a real
        # upstream refresh instead of waiting 15 s. The call still goes through the
        # product path: GET /status -> cache TTL -> RunPodAPI.account_balance().
        service.idle_interval = 0.1
        service.active_interval = 0.1
        time.sleep(0.2)
        response_c = client.get("/status", headers=user_a)
        check("refreshed /status returns 200", response_c.status_code == 200, str(response_c.status_code))
        check("second upstream snapshot received", service.fetches == 2, f"fetches={service.fetches}")
        check(
            "refreshed balance is available and parses as Decimal",
            response_c.json()["balance"]["available"] is True
            and isinstance(Decimal(str(response_c.json()["balance"]["balance_usd"])), Decimal),
        )
        # Restore the product cadence before asserting cache reuse.
        service.idle_interval = 15.0
        service.active_interval = 5.0
        response_d = client.get("/status", headers=user_b)
        check(
            "User B sees the same refreshed balance",
            response_d.json()["balance"]["balance_usd"] == response_c.json()["balance"]["balance_usd"],
        )
        check("no further upstream call on the next read", service.fetches == 2, f"fetches={service.fetches}")

        print("4. compute safety")
        with SessionLocal() as db:
            sessions = db.scalar(select(func.count()).select_from(ComputeSession))
            users = db.scalar(select(func.count()).select_from(User))
        check("no compute session row exists", sessions == 0, f"sessions={sessions}")
        check("only the two acceptance users exist", users == 2, f"users={users}")
        state = client.get("/compute/status", headers=user_a).json()
        check("compute is not running", state["state"] in {"offline", "not_configured"}, state["state"])
        check("no session adopted", state["session"] is None)

        print("5. secret hygiene")
        leaked = [path for path in root.rglob("*") if path.is_file() and secret.encode() in path.read_bytes()]
        check("no credential material written under the isolated data root", leaked == [], str(leaked))
        check("upstream calls are exactly two", service.fetches == 2, f"fetches={service.fetches}")

    print()
    print(f"RunPod balance (UI rounded): {money(first_value)}")
    engine.dispose()
    if failures:
        print(f"BALANCE ACCEPTANCE FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("BALANCE ACCEPTANCE PASS — read-only, no Pod, no GPU, volume untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
