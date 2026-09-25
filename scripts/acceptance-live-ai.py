"""Live AI acceptance, phase 1: what the product actually offers, and why it said `price_limit`.

This reads the *live* provider catalogue through the product's own code path (`GET /compute/options`
is `RunPodController.list_gpu_options`, which calls the provider with the user's own policy applied) —
no price in its output is written down by hand anywhere, and no Pod is created.

It answers three questions the release report could only answer in prose:

* what the active policy is (`GET /compute/preferences`, plus the operator's own stored row read
  read-only from the product database);
* which candidates exist right now, their VRAM, tier, availability, price, and whether the policy
  admits them — with the reason the product itself assigned;
* therefore exactly which candidates were refused *only* because of the hourly maximum, and what the
  cheapest admissible ceiling would have to be. That last number is derived from the catalogue, never
  invented.

Usage:
    python scripts/acceptance-live-ai.py policy
    python scripts/acceptance-live-ai.py policy --keep
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXE_CANDIDATES = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Programs"
    / "Canalla LLM"
    / "alex-llm.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Alex LLM" / "alex-llm.exe",
)
OPERATOR_ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "Alex LLM"
PASSWORD = "live-ai-acceptance-passphrase"
EMAIL = "live-ai-acceptance@example.com"


def installed_exe() -> Path:
    for candidate in EXE_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit("no installed Canalla LLM found")


def post(url: str, payload: dict, token: str = "", runtime_token: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if runtime_token:
        headers["X-Alex-Runtime-Token"] = runtime_token
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def get(url: str, token: str = "") -> tuple[int, str]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def wait_for_backend(deadline_seconds: float) -> int:
    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        for port in range(8000, 8010):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=2
                ) as response:
                    body = json.loads(response.read().decode("utf-8"))
                if body.get("product") == "alex-llm":
                    return port
            except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError):
                continue
        time.sleep(0.5)
    raise SystemExit("the installed backend never answered /health")


def operator_stored_policy() -> dict:
    """The operator's own stored policy, read read-only. Counts and prices only, never a secret."""
    db = OPERATOR_ROOT / "data" / "alex.db"
    if not db.is_file():
        return {"available": False}
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        facts: dict = {"available": True}
        try:
            row = connection.execute(
                'SELECT user_id, "values" FROM compute_preferences ORDER BY rowid DESC LIMIT 1'
            ).fetchone()
            facts["latest_row"] = (
                {"user_id": row[0], "values": json.loads(row[1])} if row else None
            )
        except sqlite3.Error as error:
            facts["compute_preferences_error"] = str(error)
        try:
            rows = connection.execute(
                "SELECT gpu_type, datacenter, hourly_rate, status, created_at "
                "FROM compute_sessions ORDER BY created_at DESC LIMIT 12"
            ).fetchall()
            facts["recent_sessions"] = [
                {
                    "gpu_type": row[0],
                    "datacenter": row[1],
                    "hourly_rate": row[2],
                    "status": row[3],
                    "created_at": row[4],
                }
                for row in rows
            ]
        except sqlite3.Error as error:
            facts["compute_sessions_error"] = str(error)
        try:
            row = connection.execute(
                "SELECT search_settings, search_state, error_code, next_search_at "
                "FROM compute_control WHERE id=1"
            ).fetchone()
            facts["control"] = (
                {
                    "search_state": row[1],
                    "error_code": row[2],
                    "next_search_at": row[3],
                    "search_settings": json.loads(row[0]) if row[0] else None,
                }
                if row
                else None
            )
        except sqlite3.Error as error:
            facts["compute_control_error"] = str(error)
        return facts
    finally:
        connection.close()


def price_table(options: list[dict], ceiling: Decimal) -> tuple[list[dict], dict]:
    """The product's own options, ordered by price, with the price verdict derived here."""
    rows = []
    for option in options:
        rate = Decimal(str(option["hourly_rate"]))
        within = rate <= ceiling
        rows.append(
            {
                "gpu": option["name"],
                "id": option["id"],
                "vram_gb": option["vram_gb"],
                "price": rate,
                "availability": option["availability"],
                "compatible": option["compatible"],
                "selectable": option["selectable"],
                "reason": option.get("reason"),
                "within_ceiling": within,
                # Exactly the case §2 asks for: compatible hardware that the hourly maximum alone
                # keeps out — the product's own `selectable` is false, its `compatible` is true, and
                # the price is the only thing standing between the two.
                "refused_only_by_price": bool(
                    option["compatible"] and not option["selectable"] and not within
                ),
            }
        )
    rows.sort(key=lambda row: row["price"])
    compatible = [row for row in rows if row["compatible"]]
    admissible = [row for row in compatible if row["within_ceiling"]]
    bookable = [row for row in admissible if row["availability"] != "NONE"]
    return rows, {
        "compatible": compatible,
        "admissible_within_ceiling": admissible,
        "admissible_and_bookable": bookable,
        "cheapest_compatible": compatible[0] if compatible else None,
        "cheapest_admissible_bookable": bookable[0] if bookable else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("policy",))
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    exe = installed_exe()
    root = Path(tempfile.mkdtemp(prefix="canalla-live-ai-data-"))
    device = Path(tempfile.mkdtemp(prefix="canalla-live-ai-device-"))
    stamp = str(int(time.time()))
    env = {
        **os.environ,
        "ALEX_LLM_DATA_DIR": str(root),
        "ALEX_DEVICE_DIR": str(device),
        "ALEX_DEVICE_CREDENTIAL_TARGET": f"Alex LLM/device-credential-live-ai-{stamp}",
        "ALEX_GATEWAY_CREDENTIAL_NAME": f"live-ai-{stamp}",
        "ALEX_AI_MODE": "direct",
    }

    print("LIVE AI ACCEPTANCE — PHASE 1: POLICY AND CATALOGUE (no Pod is created)")
    print(f"  app   {exe}\n")

    child = subprocess.Popen(
        [str(exe)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        port = wait_for_backend(120)
        base = f"http://127.0.0.1:{port}"

        runtime_token = ""
        token_file = root / "runtime" / "shutdown.token"
        deadline = time.time() + 30
        while time.time() < deadline and not runtime_token:
            try:
                runtime_token = token_file.read_text(encoding="utf-8").strip()
            except OSError:
                time.sleep(0.5)

        token = post(
            f"{base}/auth/bootstrap",
            {"email": EMAIL, "password": PASSWORD},
            runtime_token=runtime_token,
        )["access_token"]

        print("== ACTIVE POLICY (this run, isolated root, product defaults) ==")
        status, preferences_body = get(f"{base}/compute/preferences", token)
        preferences = json.loads(preferences_body)
        print(json.dumps(preferences, indent=2, ensure_ascii=False, sort_keys=True))

        status, llm_body = get(f"{base}/llm/status", token)
        model = json.loads(llm_body)
        print("\n== MODEL AND PROVIDER ==")
        print(
            json.dumps(
                {
                    "provider": model.get("provider"),
                    "model": model.get("model"),
                    "ai": model.get("ai"),
                    "state": model.get("state"),
                },
                indent=2,
                ensure_ascii=False,
            )
        )

        print(
            "\n== OPERATOR'S OWN STORED POLICY (read-only, from the product database) =="
        )
        operator = operator_stored_policy()
        print(json.dumps(operator, indent=2, ensure_ascii=False, sort_keys=True))

        print(
            "\n== LIVE CATALOGUE (GET /compute/options -> provider, application of the policy) =="
        )
        status, options_body = get(f"{base}/compute/options", token)
        if status != 200:
            print(f"  options failed: {status} {options_body[:300]}")
            return 2
        options = json.loads(options_body).get("options") or []
        ceiling = Decimal(str(preferences["max_hourly_price"]))
        rows, summary = price_table(options, ceiling)

        header = (
            f"{'GPU':<26}{'VRAM':>6}  {'AVAIL':<8}{'COMPAT':<8}{'SEL':<6}"
            f"{'PRICE':>9}  {'<=MAX':<6}REASON"
        )
        print(header)
        print("-" * len(header))
        for row in rows:
            print(
                f"{row['gpu'][:25]:<26}{row['vram_gb']:>6}  {row['availability']:<8}"
                f"{row['compatible']!s:<8}{row['selectable']!s:<6}"
                f"{row['price']!s:>9}  {row['within_ceiling']!s:<6}{row['reason'] or ''}"
            )

        print(
            "\n== PRICE-ONLY REFUSALS (compatible, not selectable, above the ceiling) =="
        )
        only_price = [row for row in rows if row["refused_only_by_price"]]
        if only_price:
            for row in only_price:
                print(
                    f"  {row['gpu']}  {row['vram_gb']} GB  {row['price']}/h  "
                    f"ceiling {ceiling}/h  over by {row['price'] - ceiling}/h"
                )
        else:
            print("  none")

        print("\n== SUMMARY ==")
        print(f"  min_vram_gb            {preferences['min_vram_gb']}")
        print(f"  current hourly maximum {ceiling}")
        print(f"  selection              {preferences['selection']}")
        print(f"  allow_community        {preferences['allow_community']}")
        print(f"  session budget         {preferences['session_budget']}")
        for label, row in (
            ("cheapest compatible (any availability)", summary["cheapest_compatible"]),
            (
                "cheapest admissible within the ceiling",
                summary["cheapest_admissible_bookable"],
            ),
        ):
            print(
                f"  {label}: "
                + (
                    f"{row['gpu']} {row['vram_gb']} GB {row['price']}/h ({row['availability']})"
                    if row
                    else "none"
                )
            )
        if (
            not summary["cheapest_admissible_bookable"]
            and summary["cheapest_compatible"]
        ):
            cheapest = summary["cheapest_compatible"]
            print(
                "\n  RECOMMENDED TEST MAXIMUM (derived, not invented): "
                f"{cheapest['price']} - the cheapest compatible card in the live catalogue"
            )
        print("\n  pods created: 0   (this phase only reads the catalogue)")
    finally:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            child.kill()
        time.sleep(2)
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(device, ignore_errors=True)
        else:
            print(f"  kept {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
