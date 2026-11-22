"""Free checks for the extended batch authorization ($5 series, Pod cap, reserve floor).

Run from this directory:

    ../../../../apps/backend/.venv/Scripts/python.exe test_batch_budget.py

Nothing here talks to RunPod and nothing here can spend money.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import batch as B

CHECKS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append({"check": name, "ok": bool(ok), "detail": detail})


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="canalla-batch-"))
    try:
        phrase = "I AUTHORIZE A PAID LIVE BATCH WITH A TOTAL GPU BUDGET OF USD 5.00"
        directory = root / "five"
        created = B.create(
            directory,
            phrase=phrase,
            budget_usd=5.00,
            days=7,
            placements=[{"datacenter": "US-MO-1", "volume_id": "y92bt25ru8", "gpu": "L40S"}],
            allowed_gpu="NVIDIA L40S",
            model_id="orcarouter-qwen38-27b-q5km",
            starting_balance_usd=13.5552,
            minimum_balance_usd=8.50,
            max_attempt_seconds=5400,
            maximum_total_pods=3,
        )
        check(
            "a_five_dollar_series_records_its_ceiling_reserve_and_pod_cap",
            created["maximum_total_gpu_spend_usd"] == 5.00
            and created["minimum_balance_usd"] == 8.50
            and created["starting_balance_usd"] == 13.5552
            and created["maximum_total_pods"] == 3
            and created["max_attempt_seconds"] == 5400
            and created["maximum_concurrent_pods"] == 1
            and B.remaining_usd(created) == 5.00,
        )

        check("the_phrase_must_name_the_exact_amount", B.phrase_for(5.00) == phrase)
        try:
            B.create(
                root / "wrong-phrase",
                phrase=B.BATCH_PHRASE,
                budget_usd=5.00,
                days=7,
                placements=[],
                allowed_gpu="NVIDIA L40S",
                model_id="m",
            )
            wrong = False
        except B.BatchError as error:
            wrong = str(error) == "wrong_phrase"
        check("a_two_dollar_phrase_cannot_authorize_five_dollars", wrong)

        try:
            B.create(
                root / "too-much",
                phrase=B.phrase_for(5.01),
                budget_usd=5.01,
                days=7,
                placements=[],
                allowed_gpu="NVIDIA L40S",
                model_id="m",
            )
            over = False
        except B.BatchError as error:
            over = str(error) == "budget_out_of_range"
        check("the_hard_ceiling_is_five_dollars", over)

        spent_three = {
            **created,
            "pods_created": 3,
            "spent_gpu_usd": 0.5,
        }
        allowed, reason, _need = B.can_start(spent_three, 1.09)
        check(
            "three_created_pods_end_the_series_even_with_money_left",
            allowed is False and reason == "PAID_BATCH_EXHAUSTED" and B.status(spent_three) == "exhausted",
        )

        allowed, reason, need = B.can_start(created, 1.09)
        check(
            "a_fresh_five_dollar_series_may_start_one_attempt",
            allowed is True and reason == "ok" and abs(need - 0.413) < 0.02,
            f"need={need}",
        )

        flying = B.begin(created, "run-1", 0.5)
        B.save(directory, flying)
        try:
            B.archive(directory)
            refused = False
        except B.BatchError as error:
            refused = str(error) == "attempt_in_flight"
        check("a_batch_in_flight_can_never_be_archived", refused)
        settled = B.record(flying, run_id="run-1", spend_usd=0.42, pod_ids=["pod-1"], confirmed_pod=True)
        check(
            "settling_moves_the_reservation_into_spend",
            settled["spent_gpu_usd"] == 0.42
            and settled["reserved_gpu_usd"] == 0.0
            and settled["pods_created"] == 1
            and B.remaining_usd(settled) == 4.58,
        )

        B.save(directory, B.mark_completed(settled))
        archived = B.archive(directory)
        check(
            "a_finished_series_is_archived_not_rewritten",
            Path(archived).is_file() and B.read(directory) == {},
        )
        journal = B.journal_path(directory)
        journal_before = journal.read_text(encoding="utf-8") if journal.is_file() else ""
        check("archiving_never_touches_the_spend_journal", journal_before == "")

        unknown = {**created, "active_run_id": None, "reconciliation_status": "unconfirmed"}
        directory_two = root / "unknown"
        B.save(directory_two, unknown)
        try:
            B.archive(directory_two)
            unknown_refused = False
        except B.BatchError as error:
            unknown_refused = str(error) == "previous_outcome_unknown"
        check("an_unknown_outcome_blocks_a_new_series", unknown_refused)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    verdict = "PASS" if all(item["ok"] for item in CHECKS) else "FAIL"
    print(json.dumps({"checks": CHECKS, "verdict": verdict}, ensure_ascii=False, indent=1))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
