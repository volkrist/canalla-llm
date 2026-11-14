"""The monitor's free lifecycle matrix: every decision the paid path can take, without a Pod.

The owner's A-L matrix is the contract this file proves. Nothing here touches RunPod, the
production database, the installed application or the owner's credentials: the provider is a
fixture, the launcher is a function, and the state directory is a temporary folder.

    python artifacts/acceptance-127/live-qwen/monitor/monitor.py --self-test
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import monitor as M

PAIR_TX = ("US-TX-3", "uwgeaie5b0")
PAIR_MO = ("US-MO-1", "y92bt25ru8")
BOTH = [PAIR_TX, PAIR_MO]


def _grant_payload(*, budget: float = 2.00, days: int = 30, placements=None) -> dict:
    pairs = list(placements if placements is not None else BOTH)
    now = datetime.now(timezone.utc)
    return {
        "schema": M.GRANT_SCHEMA,
        "grant_id": uuid.uuid4().hex,
        "phrase": M.PHRASE,
        "budget_usd": budget,
        "max_pods": 1,
        "allowed_model": M.MODEL_ID,
        "allowed_gpu": M.GPU_ID,
        "allowed_placements": [
            {"datacenter": dc, "volume_id": vid, "gpu": M.GPU_ID} for dc, vid in pairs
        ],
        "scope": "one LIVE Qwen acceptance: TaskDesk, one Pod, 90 billable minutes",
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(days=days)).isoformat(timespec="seconds"),
        "used": False,
        "used_at": None,
        "revoked": False,
    }


def _write_grant(rdir: Path, *, budget: float = 2.00, days: int = 30, placements=None) -> dict:
    rdir.mkdir(parents=True, exist_ok=True)
    grant = _grant_payload(budget=budget, days=days, placements=placements)
    M.save_json(M.grant_path(rdir), grant)
    return grant


def _provider(**gpu) -> M.FixtureProvider:
    return M.FixtureProvider({"gpu": gpu})


def run_self_test() -> int:
    from app.compute import cost_policy  # noqa: F401 - proves the shared policy is importable

    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    root = Path(tempfile.mkdtemp(prefix="canalla-monitor-selftest-"))
    try:
        # ---- A. no card in either datacenter ------------------------------------------------
        rdir = root / "case_a"
        result = M.run_check(provider=_provider(availability_by_dc={"US-MO-1": "NONE", "US-TX-3": "NONE"}), rdir=rdir)
        state = result["state"]
        check(
            "A_no_gpu_in_either_datacenter_waits_without_spending",
            state["live_execution_state"] == "WAITING_GPU"
            and state["paid_run_authorized"] is False
            and state["paid_run_already_attempted"] is False
            and result["run"] is None
            and len(state["placements"]) == 2,
        )

        # ---- no grant, even with a card: authorization is a separate fact --------------------
        rdir = root / "case_no_grant"
        result = M.run_check(provider=_provider(availability_by_dc={"US-MO-1": "LOW", "US-TX-3": "NONE"}), rdir=rdir)
        check(
            "a_card_without_a_grant_does_not_run",
            result["state"]["live_execution_state"] == "WAITING_AUTHORIZATION"
            and result["run"] is None
            and result["state"]["last_gpu_status"] == "AVAILABLE",
        )

        # ---- B. only MO-1 is bookable ---------------------------------------------------------
        rdir = root / "case_b"
        _write_grant(rdir)
        result = M.run_check(
            provider=_provider(availability_by_dc={"US-MO-1": "LOW", "US-TX-3": "NONE"}),
            rdir=rdir,
            dry_run=True,
        )
        command = (result["run"] or {}).get("would_launch") or []
        check(
            "B_only_mo1_is_chosen_and_needs_no_selftest",
            result["state"]["selected_volume"] == "y92bt25ru8"
            and result["state"]["selection_mode"] == "AUTO_LIVE"
            and result["state"]["requires_selftest"] is False
            and "y92bt25ru8" in command
            and "--self-test-mode" not in command
            and M.read_grant(rdir)["valid"],
        )

        # ---- C. only TX-3 is bookable: allowed, but the same Pod must prove the model ---------
        rdir = root / "case_c"
        _write_grant(rdir)
        result = M.run_check(
            provider=_provider(availability_by_dc={"US-MO-1": "NONE", "US-TX-3": "LOW"}),
            rdir=rdir,
            dry_run=True,
        )
        command = (result["run"] or {}).get("would_launch") or []
        check(
            "C_tx3_is_eligible_only_with_a_model_selftest",
            result["state"]["selected_datacenter"] == "US-TX-3"
            and result["state"]["selected_volume"] == "uwgeaie5b0"
            and result["state"]["selection_mode"] == "AUTO_LIVE_WITH_MODEL_SELFTEST"
            and result["state"]["requires_selftest"] is True
            and "uwgeaie5b0" in command
            and "--self-test-mode" in command,
        )

        # ---- D. both bookable: the proven copy wins -------------------------------------------
        rdir = root / "case_d"
        _write_grant(rdir)
        result = M.run_check(
            provider=_provider(availability_by_dc={"US-MO-1": "LOW", "US-TX-3": "MEDIUM"}),
            rdir=rdir,
            dry_run=True,
        )
        check(
            "D_both_bookable_prefers_the_verified_copy",
            result["state"]["selected_volume"] == "y92bt25ru8"
            and result["state"]["selection_mode"] == "AUTO_LIVE"
            and all(item["live_authorized"] for item in result["state"]["placements"]),
        )

        # ---- model evidence is per volume, never inherited ------------------------------------
        check(
            "model_evidence_is_read_from_each_volumes_own_record",
            M.model_status("y92bt25ru8")[0] == "VERIFIED"
            and M.model_status("uwgeaie5b0")[0] == "UNVERIFIED_NEEDS_LIVE_CHECK"
            and M.model_status("unknown-volume")[0] == "UNKNOWN",
        )

        # ---- E. a TX-3 self-test failure buys nothing more -------------------------------------
        rdir = root / "case_e"
        _write_grant(rdir)
        tx3 = _provider(availability_by_dc={"US-MO-1": "NONE", "US-TX-3": "LOW"})
        result = M.run_check(
            provider=tx3,
            rdir=rdir,
            launch=lambda **_: {
                "verdict": "FAIL",
                "pods_created": 1,
                "spend_usd": 0.04,
                "exit_code": 3,
            },
            remaining_reader=list,
        )
        second = M.run_check(provider=tx3, rdir=rdir)
        check(
            "E_a_failed_tx3_selftest_is_terminal_and_creates_no_second_pod",
            result["state"]["live_execution_state"] == "FAILED"
            and result["state"]["paid_run_already_attempted"] is True
            and second["run"] is None
            and M.read_grant(rdir)["reason"] == "already_used",
        )

        # ---- F. a TX-3 self-test pass continues in the same Pod --------------------------------
        rdir = root / "case_f"
        _write_grant(rdir)
        result = M.run_check(
            provider=tx3,
            rdir=rdir,
            launch=lambda **_: {
                "verdict": "COMPLETED",
                "pods_created": 1,
                "spend_usd": 1.21,
                "exit_code": 0,
            },
            remaining_reader=list,
        )
        check(
            "F_a_passed_tx3_selftest_completes_on_the_same_pod",
            result["state"]["live_execution_state"] == "COMPLETED"
            and result["state"]["final_result"]["pods_created"] == 1
            and result["state"]["pod_count"] == 0,
        )

        # ---- G. a consumed grant can never start another Pod -----------------------------------
        rdir = root / "case_g"
        _write_grant(rdir)
        M.mark_grant(rdir, used=True, run_id="test")
        result = M.run_check(
            provider=_provider(availability_by_dc={"US-MO-1": "LOW", "US-TX-3": "LOW"}), rdir=rdir
        )
        check(
            "G_a_used_grant_is_refused",
            result["run"] is None
            and result["state"]["live_execution_state"] == "WAITING_AUTHORIZATION"
            and M.read_grant(rdir)["reason"] == "already_used",
        )

        # ---- an attempt already started is never duplicated on a restart -----------------------
        rdir = root / "case_restart"
        _write_grant(rdir)
        started = M.run_check(
            provider=_provider(availability_by_dc={"US-MO-1": "LOW", "US-TX-3": "NONE"}),
            rdir=rdir,
            launch=lambda **_: {
                "verdict": "PREFLIGHT_BLOCKED",
                "pods_created": 0,
                "spend_usd": 0.0,
                "blocked_by": ["disk"],
            },
        )
        check(
            "a_blocked_preflight_creates_nothing_and_restores_the_grant",
            started["state"]["live_execution_state"] == "PREFLIGHT_BLOCKED"
            and started["run"]["pods_created"] == 0
            and M.read_grant(rdir)["valid"],
        )

        # ---- H. a price that leaves no work window is not a run --------------------------------
        rdir = root / "case_h"
        _write_grant(rdir)
        result = M.run_check(
            provider=_provider(
                availability_by_dc={"US-MO-1": "LOW", "US-TX-3": "LOW"},
                price_usd_per_hour=9.99,
            ),
            rdir=rdir,
        )
        check(
            "H_a_price_over_the_budget_blocks_the_run",
            result["run"] is None
            and result["state"]["live_execution_state"] == "WAITING_GPU"
            and all(not item["budget_allowed"] for item in result["state"]["placements"]),
        )

        # ---- I. an unreachable provider is UNKNOWN, never a clean account ----------------------
        rdir = root / "case_i"
        result = M.run_check(provider=M.FixtureProvider({"error": "network down"}), rdir=rdir)
        check(
            "I_provider_unreachable_is_unknown_not_clean",
            result["state"]["live_execution_state"] == "ERROR"
            and result["state"]["last_gpu_status"] == "UNKNOWN"
            and result["state"]["consecutive_failures"] == 1
            and result["run"] is None,
        )

        rdir = root / "case_i429"
        _write_grant(rdir)
        result = M.run_check(
            provider=M.FixtureProvider({"rate_limited": True, "retry_after": 7}), rdir=rdir
        )
        check(
            "I_a_rate_limit_is_unknown_and_backs_off",
            result["state"]["last_gpu_status"] == "UNKNOWN"
            and result["state"]["retry_after_seconds"] == 7
            and result["run"] is None,
        )

        # ---- J. a restart changes nothing and starts nothing -----------------------------------
        rdir = root / "case_j"
        _write_grant(rdir)
        fixture = _provider(availability_by_dc={"US-MO-1": "NONE", "US-TX-3": "NONE"})
        M.run_check(provider=fixture, rdir=rdir)
        M.run_check(provider=fixture, rdir=rdir)
        reloaded = M.load_state(rdir)
        rows = (rdir / "checks.jsonl").read_text(encoding="utf-8").strip().splitlines()
        check(
            "J_state_survives_a_reload_and_checks_accumulate",
            reloaded["checks"] == 2
            and reloaded["last_gpu_status"] == "UNAVAILABLE"
            and len(rows) == 2
            and reloaded["paid_run_already_attempted"] is False,
        )

        # ---- K. an ambiguous create is reconciled from the provider, never retried -------------
        rdir = root / "case_k"
        _write_grant(rdir)
        result = M.run_check(
            provider=_provider(availability_by_dc={"US-MO-1": "LOW", "US-TX-3": "NONE"}),
            rdir=rdir,
            launch=lambda **_: {
                "verdict": "FAIL",
                "pods_created": 1,
                "spend_usd": 0.0,
                "exit_code": 1,
            },
            remaining_reader=lambda: [{"id": "pod-unknown", "status": "RUNNING"}],
        )
        check(
            "K_a_pod_that_survives_the_run_is_critical_and_the_grant_stays_used",
            result["state"]["live_execution_state"] == "CRITICAL_TERMINATION_UNCONFIRMED"
            and result["state"]["pod_count"] == 1
            and M.read_grant(rdir)["reason"] == "already_used"
            and bool(
                json.loads(M.grant_path(rdir).read_text(encoding="utf-8")).get("confirmed_at")
            ),
        )

        rdir = root / "case_k_unknown"
        _write_grant(rdir)
        result = M.run_check(
            provider=_provider(availability_by_dc={"US-MO-1": "LOW", "US-TX-3": "NONE"}),
            rdir=rdir,
            launch=lambda **_: {
                "verdict": "FAIL",
                "pods_created": 1,
                "spend_usd": 0.0,
            },
            remaining_reader=lambda: None,
        )
        check(
            "K_an_unreadable_provider_after_the_run_is_unconfirmed_not_clean",
            result["state"]["live_execution_state"] == "CRITICAL_TERMINATION_UNCONFIRMED",
        )

        # ---- an attempt recorded before a crash is never re-launched ---------------------------
        rdir = root / "case_k_restart"
        _write_grant(rdir)
        state = M.load_state(rdir)
        state["paid_run_already_attempted"] = True
        state["live_execution_state"] = "AUTO_LIVE"
        result = M.run_check(
            provider=_provider(availability_by_dc={"US-MO-1": "LOW", "US-TX-3": "NONE"}),
            rdir=rdir,
            state_override=state,
            launch=lambda **_: (_ for _ in ()).throw(AssertionError("must not launch twice")),
        )
        check(
            "an_attempted_run_is_never_launched_twice_after_a_restart",
            result["run"] is None
            and result["state"]["live_execution_state"] == "LIVE_RETEST_REQUIRED",
        )

        # ---- the observation this pass made is what the harness is given -----------------------
        rdir = root / "case_prepared"
        _write_grant(rdir)
        result = M.run_check(
            provider=_provider(availability_by_dc={"US-MO-1": "LOW", "US-TX-3": "NONE"}),
            rdir=rdir,
            dry_run=True,
        )
        observation = result["observation"]
        chosen = observation["chosen"]
        payload = M.prepared_payload(
            observation,
            chosen,
            M.read_grant(rdir)["grant"],
            {"verdict": "READY", "checked_at": M.utcnow(), "blockers": [], "report": {}},
            "run-test",
        )
        check(
            "the_prepared_payload_carries_this_pass_observation",
            payload["observed_at"] == observation["observed_at"]
            and payload["placement"]["volume_id"] == chosen["volume_id"]
            and payload["rate_usd_per_hour"] == observation["gpu"]["price_usd_per_hour"]
            and payload["pod_count"] == 0,
        )
        command = M._live_command(
            M.read_grant(rdir)["grant"], chosen, rdir / "live-prepared.json"
        )
        check(
            "the_fast_path_command_passes_the_prepared_payload",
            "--prepared" in command
            and str(rdir / "live-prepared.json") in command
            and "--self-test-mode" not in command,
        )

        # ---- a fresh READY preflight is reused, a stale one is re-run ---------------------------
        rdir = root / "case_preflight_cache"
        calls: list[int] = []
        original_run_json = M._run_json
        M._run_json = lambda *_a, **_k: (calls.append(1), {"verdict": "READY"})[1]
        try:
            first, first_reused = M.cached_preflight(rdir)
            second, second_reused = M.cached_preflight(rdir)
            M.save_json(
                rdir / "preflight-cache.json",
                {
                    "checked_at": (
                        datetime.now(timezone.utc) - timedelta(seconds=M.PREFLIGHT_TTL_SECONDS + 5)
                    ).isoformat(timespec="seconds"),
                    "report": {"verdict": "READY"},
                },
            )
            third, third_reused = M.cached_preflight(rdir)
        finally:
            M._run_json = original_run_json
        check(
            "a_fresh_preflight_is_reused_and_a_stale_one_is_rerun",
            first_reused is False
            and second_reused is True
            and third_reused is False
            and len(calls) == 2,
            detail=f"calls={len(calls)}",
        )

        # ---- the real launcher: cached preflight, prepared payload, claim, one command ---------
        rdir = root / "case_launch"
        _write_grant(rdir)
        observation = M.run_check(
            provider=_provider(availability_by_dc={"US-MO-1": "LOW", "US-TX-3": "NONE"}),
            rdir=rdir,
            dry_run=True,
        )["observation"]
        evidence_dir = root / "case_launch_evidence"
        seen: dict = {}
        original_run_json = M._run_json
        original_subprocess_run = M.subprocess.run
        original_evidence = M.EVIDENCE

        def fake_run_json(*_a, **_k):
            return {
                "verdict": "READY",
                "checked_at": M.utcnow(),
                "blockers": [],
            }

        def fake_run(command, **_kwargs):
            seen["command"] = command
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        M._run_json = fake_run_json
        M.subprocess.run = fake_run
        M.EVIDENCE = evidence_dir
        try:
            payload = M.launch_live(
                rdir=rdir,
                state=M.load_state(rdir),
                grant=M.read_grant(rdir)["grant"],
                observation=observation,
            )
            command = seen.get("command") or []
            prepared_file = rdir / "live-prepared.json"
            prepared = json.loads(prepared_file.read_text(encoding="utf-8"))
            used = M.read_grant(rdir)["reason"]
        finally:
            M._run_json = original_run_json
            M.subprocess.run = original_subprocess_run
            M.EVIDENCE = original_evidence
        check(
            "the_launcher_claims_before_it_runs_and_passes_the_observation",
            "--prepared" in command
            and "--run-id" in command
            and prepared["placement"]["volume_id"] == observation["chosen"]["volume_id"]
            and prepared["observed_at"] == observation["observed_at"]
            and used == "already_used"
            and payload["timings"]["detection_to_launch_seconds"] is not None
            and payload["timings"]["preflight_reused"] is False,
            detail=f"command={command[-3:]}",
        )
        check(
            "the_second_launch_reuses_the_cached_preflight",
            M.PREFLIGHT_TTL_SECONDS == 900
            and (rdir / "preflight-cache.json").is_file(),
        )

        # ---- the claim time is written once, and a confirmed Pod is never un-used --------------
        rdir = root / "case_grant_claim"
        _write_grant(rdir)
        M.mark_grant(rdir, used=True, run_id="r1")
        first_used_at = (M.read_grant(rdir)["grant"] or {}).get("used_at")
        M.mark_grant(rdir, used=True, run_id="r1", confirmed=True)
        confirmed = M.read_grant(rdir)["grant"]
        check(
            "a_claim_keeps_its_first_timestamp_and_marks_confirmation",
            bool(first_used_at)
            and confirmed.get("used_at") == first_used_at
            and bool(confirmed.get("confirmed_at")),
            detail=f"used_at={confirmed.get('used_at')} confirmed={confirmed.get('confirmed_at')}",
        )
        M.mark_grant(rdir, used=False, run_id="r1")
        check(
            "a_confirmed_permission_is_never_restored",
            json.loads(M.grant_path(rdir).read_text(encoding="utf-8"))["used"] is True,
        )
        rdir = root / "case_grant_other"
        _write_grant(rdir)
        M.mark_grant(rdir, used=True, run_id="r1")
        M.mark_grant(rdir, used=False, run_id="r2")
        check(
            "another_run_cannot_clear_a_claim",
            json.loads(M.grant_path(rdir).read_text(encoding="utf-8"))["used"] is True,
        )

        # ---- a *new* permission re-arms a terminal outcome; the old one never does -------------
        rdir = root / "case_rearm"
        old_grant = _write_grant(rdir)
        state = M.load_state(rdir)
        state["live_execution_state"] = "FAILED"
        state["paid_run_already_attempted"] = True
        state["terminal_grant_id"] = old_grant["grant_id"]
        M.save_json(rdir / "state.json", state)
        M.mark_grant(rdir, used=True, run_id="old", confirmed=True)
        result = M.run_check(
            provider=_provider(availability_by_dc={"US-MO-1": "LOW", "US-TX-3": "NONE"}),
            rdir=rdir,
        )
        check(
            "a_consumed_permission_does_not_rearm",
            result["state"]["live_execution_state"] == "FAILED" and result["run"] is None,
        )
        new_grant = _write_grant(rdir)
        result = M.run_check(
            provider=_provider(availability_by_dc={"US-MO-1": "LOW", "US-TX-3": "NONE"}),
            rdir=rdir,
            dry_run=True,
        )
        check(
            "a_new_permission_rearms_a_terminal_state",
            result["state"]["live_execution_state"] == "DRY_RUN_OK"
            and result["state"]["paid_run_already_attempted"] is False
            and new_grant["grant_id"] != old_grant["grant_id"],
        )

        # ---- a new owner decision archives the consumed record, never edits it -----------------
        import authorize

        rdir = root / "case_replace"
        consumed = _write_grant(rdir)
        M.mark_grant(rdir, used=True, run_id="r0", confirmed=True)
        replaced = authorize.main(
            [
                "--confirm",
                M.PHRASE,
                "--budget-usd",
                "2.00",
                "--replace-consumed",
                "--state-dir",
                str(rdir),
            ]
        )
        after = json.loads(M.grant_path(rdir).read_text(encoding="utf-8"))
        archived = list(rdir.glob("authorization-grant.consumed-*.json"))
        check(
            "a_new_owner_decision_replaces_a_consumed_permission",
            replaced == 0
            and after["grant_id"] != consumed["grant_id"]
            and after["used"] is False
            and len(archived) == 1,
            detail=f"archived={len(archived)}",
        )
        refused = authorize.main(
            [
                "--confirm",
                M.PHRASE,
                "--budget-usd",
                "2.00",
                "--replace-consumed",
                "--state-dir",
                str(rdir),
            ]
        )
        check("replace_refuses_an_unconsumed_permission", refused != 0)

        # ---- accounting is attributed to the run, never to "the latest file" -------------------
        record_ok = {"run_id": "r1", "pods_created": 1, "spend_usd": 0.04, "verdict": "FAIL"}
        record_none = {"run_id": "r1", "pods_created": 0, "verdict": "CAPACITY_UNAVAILABLE"}
        foreign = {"run_id": "other", "pods_created": 0, "verdict": "STARTUP_PROBE"}
        resolved = M.resolve_run_accounting(
            run_id="r1",
            record=record_ok,
            final=foreign,
            billing={"balance_before_usd": 10.0, "balance_after_usd": 9.96},
            rate=1.09,
            pod_id=None,
        )
        check(
            "a_run_scoped_record_reports_the_pod_it_created",
            resolved["trusted"] is True
            and resolved["pods_created"] == 1
            and resolved["billed_usd"] == 0.04,
        )
        resolved = M.resolve_run_accounting(
            run_id="r1",
            record={},
            final=foreign,
            billing={},
            rate=1.09,
            pod_id=None,
        )
        check(
            "a_foreign_summary_with_no_money_truth_is_not_clean",
            resolved["trusted"] is False and resolved["pods_created"] == 1,
        )
        resolved = M.resolve_run_accounting(
            run_id="r1",
            record={},
            final=foreign,
            billing={"balance_before_usd": 10.0, "balance_after_usd": 10.0},
            rate=1.09,
            pod_id=None,
        )
        check(
            "an_unchanged_balance_restores_after_a_nothing_bought",
            resolved["pods_created"] == 0 and resolved["billed_usd"] == 0.0,
        )
        resolved = M.resolve_run_accounting(
            run_id="r1",
            record=record_none,
            final=foreign,
            billing={"balance_before_usd": 10.0, "balance_after_usd": 9.98},
            rate=1.09,
            pod_id=None,
        )
        check(
            "a_balance_drop_means_a_pod_even_if_the_summary_says_zero",
            resolved["pods_created"] == 1,
        )
        resolved = M.resolve_run_accounting(
            run_id="r1",
            record=record_none,
            final=record_none,
            billing={},
            rate=1.09,
            pod_id=None,
        )
        check(
            "a_run_that_counted_zero_restores_without_a_balance_read",
            resolved["pods_created"] == 0 and resolved["trusted"] is True,
        )

        # ---- the evidence, not the summary, decides whether a Pod was bought -------------------
        rdir = root / "case_evidence"
        original_evidence = M.EVIDENCE
        M.EVIDENCE = rdir
        try:
            M.save_json(rdir / "final-summary.json", {"verdict": "FAIL", "pod_ids": ["pod-abc"]})
            check(
                "a_pod_id_in_the_evidence_counts_as_a_created_pod",
                M._pod_id_from_evidence() == "pod-abc",
            )
            M.save_json(rdir / "final-summary.json", {"verdict": "FAIL"})
            check("no_pod_id_means_nothing_was_bought", M._pod_id_from_evidence() is None)
        finally:
            M.EVIDENCE = original_evidence

        # ---- L. production identity is not in this monitor's write set -------------------------
        default_dir = M.runtime_dir(None)
        check(
            "L_the_state_directory_is_outside_the_production_data_root",
            default_dir.name == "CanallaMonitor"
            and "Alex LLM" not in str(default_dir)
            and "alex.db" not in str(default_dir),
        )

        # ---- two checks per pass, thirty seconds apart -----------------------------------------
        rdir = root / "case_pass"
        delays: list[float] = []
        results = M.run_pass(
            provider_factory=lambda: _provider(availability_by_dc={"US-MO-1": "NONE", "US-TX-3": "NONE"}),
            rdir=rdir,
            checks=2,
            gap=30,
            sleep=delays.append,
        )
        rows = (rdir / "checks.jsonl").read_text(encoding="utf-8").strip().splitlines()
        check(
            "a_pass_takes_two_checks_thirty_seconds_apart",
            len(results) == 2
            and len(rows) == 2
            and len(delays) == 1
            and 25.0 <= delays[0] <= 30.0,
            detail=str(delays),
        )

        # ---- a terminal outcome does not stop the 30-second cadence -----------------------------
        rdir = root / "case_terminal_pass"
        state = M.load_state(rdir)
        state["live_execution_state"] = "FAILED"
        state["paid_run_already_attempted"] = True
        state["last_gpu_status"] = "UNAVAILABLE"
        M.save_json(rdir / "state.json", state)
        results = M.run_pass(
            provider_factory=lambda: _provider(
                availability_by_dc={"US-MO-1": "NONE", "US-TX-3": "NONE"}
            ),
            rdir=rdir,
            checks=2,
            gap=30,
            sleep=lambda _seconds: None,
            warm=False,
        )
        rows = (rdir / "checks.jsonl").read_text(encoding="utf-8").strip().splitlines()
        check(
            "a_terminal_outcome_keeps_two_checks_per_pass",
            len(results) == 2 and len(rows) == 2,
            detail=f"rows={len(rows)}",
        )

        # ---- a pass after a sighting keeps the free preflight warm -------------------------------
        rdir = root / "case_warm"
        state = M.load_state(rdir)
        state["last_gpu_status"] = "AVAILABLE"
        M.save_json(rdir / "state.json", state)
        original_run_json = M._run_json
        M._run_json = lambda *_a, **_k: {"verdict": "READY", "checked_at": M.utcnow()}
        try:
            M.run_pass(
                provider_factory=lambda: _provider(
                    availability_by_dc={"US-MO-1": "NONE", "US-TX-3": "NONE"}
                ),
                rdir=rdir,
                checks=1,
                gap=0,
                sleep=lambda _seconds: None,
            )
        finally:
            M._run_json = original_run_json
        check(
            "a_pass_after_a_sighting_warms_the_preflight_cache",
            (rdir / "preflight-cache.json").is_file(),
        )

        # ---- one instance, one journal line per distinct observation ---------------------------
        rdir = root / "case_lock"
        lock = M.FileLock(rdir / "monitor.lock")
        lock.acquire()
        try:
            fixture_path = rdir / "fixture.json"
            M.save_json(fixture_path, {"gpu": {"availability_by_dc": {"US-MO-1": "NONE", "US-TX-3": "NONE"}}})
            second = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parent / "monitor.py"),
                    "--once",
                    "--state-dir",
                    str(rdir),
                    "--fixture",
                    str(fixture_path),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            payload = json.loads((second.stdout or "").strip() or "{}")
            check("a_second_instance_is_refused", payload.get("skipped") == "another_instance_running")
        finally:
            lock.release()

        rdir = root / "case_journal"
        fixture = _provider(availability_by_dc={"US-MO-1": "NONE", "US-TX-3": "NONE"})
        M.run_check(provider=fixture, rdir=rdir)
        M.run_check(provider=fixture, rdir=rdir)
        events = (rdir / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
        check("duplicate_observations_do_not_repeat_notifications", len(events) == 1)

        # ---- the authorizer: exact phrase, exact budget, one grant, atomic scope ---------------
        import authorize
        import batch as B

        denied = authorize.main(
            ["--confirm", M.PHRASE, "--budget-usd", "2.01", "--state-dir", str(root / "case_auth")]
        )
        check("the_authorizer_refuses_over_budget", denied != 0)

        auth_dir = root / "case_auth_ok"
        allowed = authorize.main(
            [
                "--confirm",
                M.PHRASE,
                "--budget-usd",
                "2.00",
                "--scope",
                "both",
                "--state-dir",
                str(auth_dir),
            ]
        )
        stored = M.read_grant(auth_dir)
        first = stored.get("grant") or {}
        check(
            "the_authorizer_stores_one_valid_two_placement_grant",
            allowed == 0
            and stored["valid"]
            and first.get("max_pods") == 1
            and len(first.get("allowed_placements") or []) == 2
            and {tuple(sorted((item["datacenter"], item["volume_id"]))) for item in first["allowed_placements"]}
            == {tuple(sorted(PAIR_TX)), tuple(sorted(PAIR_MO))},
        )

        upgraded = authorize.main(
            [
                "--confirm",
                M.PHRASE,
                "--budget-usd",
                "2.00",
                "--scope",
                "mo1",
                "--state-dir",
                str(auth_dir),
            ]
        )
        after = M.read_grant(auth_dir)
        kept = after.get("grant") or {}
        check(
            "a_scope_change_replaces_the_grant_in_place_atomically",
            upgraded == 0
            and after["valid"]
            and kept.get("grant_id") == first.get("grant_id")
            and kept.get("created_at") == first.get("created_at")
            and kept.get("expires_at") == first.get("expires_at")
            and kept.get("used") is False
            and len(kept.get("allowed_placements") or []) == 1
            and not (auth_dir / "authorization-grant.json.new").exists(),
        )

        again = authorize.main(
            [
                "--confirm",
                M.PHRASE,
                "--budget-usd",
                "2.00",
                "--scope",
                "both",
                "--state-dir",
                str(auth_dir),
            ]
        )
        check(
            "a_second_call_never_creates_a_second_permission",
            again == 0
            and len((M.read_grant(auth_dir).get("grant") or {}).get("allowed_placements") or []) == 2
            and sorted(path.name for path in auth_dir.glob("authorization-grant*"))
            == ["authorization-grant.json"],
        )

        used_dir = root / "case_auth_used"
        M.save_json(M.grant_path(used_dir), _grant_payload())
        M.mark_grant(used_dir, used=True, run_id="test")
        refused = authorize.main(
            ["--confirm", M.PHRASE, "--budget-usd", "2.00", "--state-dir", str(used_dir)]
        )
        check(
            "the_authorizer_refuses_to_revive_a_consumed_grant",
            refused != 0 and (M.read_grant(used_dir).get("grant") or {}).get("used") is True,
        )

        # ---- the scope checks: another SKU, an unknown placement, a legacy grant --------------
        bad_sku = _grant_payload()
        bad_sku["allowed_gpu"] = "NVIDIA A40"
        bad_placement = _grant_payload(placements=[("US-KS-2", "vol-other")])
        legacy = _grant_payload()
        legacy.pop("allowed_placements")
        legacy.update({"schema": 1, "allowed_volume": "y92bt25ru8", "allowed_datacenter": "US-MO-1"})
        check(
            "a_grant_for_another_sku_or_an_unknown_placement_is_refused",
            M.validate_grant(bad_sku)["reason"] == "scope_mismatch"
            and M.validate_grant(bad_placement)["reason"] == "scope_mismatch",
        )
        check(
            "a_legacy_single_placement_grant_is_still_honoured",
            M.validate_grant(legacy)["valid"] is True
            and M.validate_grant(legacy)["placements"] == [PAIR_MO],
        )

        # ---- an expired grant is not armed --------------------------------------------------
        expired = _grant_payload(days=-1)
        check(
            "an_expired_grant_is_not_armed",
            M.validate_grant(expired)["reason"] == "expired",
        )

        # ---- the bounded series: one budget, one Pod at a time, no reset on failure -----------
        wrong = authorize.main(
            ["--batch", "--confirm", "I AUTHORIZE A BATCH", "--budget-usd", "2.00", "--state-dir", str(root / "case_batch_wrong")]
        )
        check("the_batch_authorizer_requires_the_exact_phrase", wrong != 0)

        over = authorize.main(
            [
                "--batch",
                "--confirm",
                B.BATCH_PHRASE,
                "--budget-usd",
                "2.01",
                "--state-dir",
                str(root / "case_batch_over"),
            ]
        )
        check("the_batch_authorizer_refuses_more_than_two_dollars", over != 0)

        batch_dir = root / "case_batch"
        made = authorize.main(
            [
                "--batch",
                "--confirm",
                B.BATCH_PHRASE,
                "--budget-usd",
                "2.00",
                "--scope",
                "both",
                "--state-dir",
                str(batch_dir),
            ]
        )
        created_batch = B.read(batch_dir)
        second_batch = authorize.main(
            [
                "--batch",
                "--confirm",
                B.BATCH_PHRASE,
                "--budget-usd",
                "2.00",
                "--state-dir",
                str(batch_dir),
            ]
        )
        check(
            "the_batch_series_is_one_record_with_both_placements",
            made == 0
            and second_batch != 0
            and B.status(created_batch) == "active"
            and created_batch["maximum_total_gpu_spend_usd"] == 2.00
            and created_batch["maximum_concurrent_pods"] == 1
            and B.remaining_usd(created_batch) == 2.00
            and len(created_batch["allowed_placements"]) == 2,
        )

        # A batch with a bookable card creates exactly one one-shot child and does not spend yet.
        card = _provider(availability_by_dc={"US-MO-1": "LOW", "US-TX-3": "NONE"})
        first_child = M.run_check(provider=card, rdir=batch_dir, dry_run=True)
        child = M.read_grant(batch_dir)
        held = B.read(batch_dir)
        check(
            "a_batch_creates_one_child_grant_for_the_visible_card",
            first_child["run"] is not None
            and child["valid"]
            and (child.get("grant") or {}).get("batch_id") == created_batch["batch_id"]
            and (child.get("grant") or {}).get("budget_usd") == 2.00
            and held.get("spent_gpu_usd") == 0.0
            and M.automatic_live(first_child["state"]) == "ARMED",
        )
        first_child_id = (child.get("grant") or {}).get("grant_id")

        # An attempt that bought a Pod spends real money, is counted once, and re-arms the series.
        bought = M.run_check(
            provider=card,
            rdir=batch_dir,
            launch=lambda **_: {
                "run_id": "run-batch-1",
                "verdict": "FAIL",
                "pods_created": 1,
                "spend_usd": 0.25,
                "exit_code": 7,
            },
            remaining_reader=list,
        )
        after_buy = B.read(batch_dir)
        check(
            "a_failed_attempt_records_its_spend_and_may_try_again",
            bought["state"]["live_execution_state"] == "WAITING_GPU"
            and bought["state"]["paid_run_already_attempted"] is False
            and after_buy["spent_gpu_usd"] == 0.25
            and after_buy["pods_created"] == 1
            and after_buy["active_run_id"] is None
            and after_buy["reconciliation_status"] == "clean"
            and B.remaining_usd(after_buy) == 1.75
            and M.automatic_live(bought["state"]) == "ARMED",
        )
        journal = [
            json.loads(line)
            for line in (batch_dir / B.BATCH_JOURNAL).read_text(encoding="utf-8").splitlines()
        ]
        check(
            "the_spend_journal_records_the_attempt_and_its_stop",
            len(journal) == 1
            and journal[0]["run_id"] == "run-batch-1"
            and journal[0]["spend_usd"] == 0.25
            and journal[0]["remaining_usd"] == 1.75,
        )

        second_child = M.run_check(provider=card, rdir=batch_dir, dry_run=True)
        new_child = M.read_grant(batch_dir).get("grant") or {}
        check(
            "the_next_attempt_gets_a_new_child_sized_to_the_remainder",
            new_child.get("grant_id") != first_child_id
            and new_child.get("budget_usd") == 1.75
            and new_child.get("batch_id") == created_batch["batch_id"]
            and second_child["run"] is not None
            and B.read(batch_dir)["spent_gpu_usd"] == 0.25,
        )

        # Nothing created means nothing bought: the reservation is released and no Pod is counted.
        nothing = M.run_check(
            provider=card,
            rdir=batch_dir,
            launch=lambda **_: {
                "run_id": "run-batch-2",
                "verdict": "FAIL",
                "pods_created": 0,
                "spend_usd": 0.0,
                "exit_code": 4,
            },
            remaining_reader=list,
        )
        after_nothing = B.read(batch_dir)
        check(
            "an_attempt_that_created_nothing_costs_nothing",
            nothing["state"]["paid_run_already_attempted"] is False
            and after_nothing["spent_gpu_usd"] == 0.25
            and after_nothing["pods_created"] == 1
            and after_nothing["active_run_id"] is None
            and after_nothing["reconciliation_status"] == "clean",
        )

        # A Pod may still exist: the series is unresolved and the next card is not bought.
        unresolved = M.run_check(
            provider=card,
            rdir=batch_dir,
            launch=lambda **_: {
                "run_id": "run-batch-3",
                "verdict": "FAIL",
                "pods_created": 1,
                "spend_usd": 0.10,
                "exit_code": 0,
            },
            remaining_reader=lambda: [{"id": "pod-still-there"}],
        )
        after_unresolved = B.read(batch_dir)
        busy = M.FixtureProvider(
            {
                "gpu": {"availability_by_dc": {"US-MO-1": "LOW", "US-TX-3": "NONE"}},
                "pods": [{"id": "pod-still-there", "status": "RUNNING"}],
            }
        )
        blocked_state = M.run_check(provider=busy, rdir=batch_dir, dry_run=True)
        check(
            "a_pod_that_may_still_exist_blocks_the_next_attempt",
            unresolved["state"]["live_execution_state"] == "CRITICAL_TERMINATION_UNCONFIRMED"
            and after_unresolved["reconciliation_status"] == "unconfirmed"
            and B.can_start(after_unresolved, 1.09)[0] is False
            and blocked_state["run"] is None
            and blocked_state["state"]["pod_count"] == 1,
        )
        # A later clean zero-Pod read clears it; an unknown count never does.
        still = B.reconcile(dict(after_unresolved), 1)
        cleared = B.reconcile(dict(after_unresolved), 0)
        probe = dict(after_unresolved)
        probe["active_run_id"] = None
        check(
            "an_unknown_pod_count_is_not_evidence_of_termination",
            still["reconciliation_status"] == "unconfirmed"
            and cleared["reconciliation_status"] == "clean"
            and B.can_start(probe, 1.09)[0] is False
            and B.can_start({**cleared, "active_run_id": None}, 1.09)[0] is True,
        )

        # A monitor that died after writing the result still settles from durable evidence.
        crashed_dir = root / "case_batch_crash"
        B.save(
            crashed_dir,
            {**created_batch, "batch_id": "batch-crash", "active_run_id": "run-crash", "reconciliation_status": "in_flight", "reserved_gpu_usd": 2.0},
        )
        M.save_json(
            crashed_dir / "last-run.json",
            {"run_id": "run-crash", "verdict": "FAIL", "pods_created": 1, "spend_usd": 0.12},
        )
        recovered = M.run_check(provider=card, rdir=crashed_dir, dry_run=True)
        after_crash = B.read(crashed_dir)
        check(
            "a_crash_after_the_result_is_settled_from_disk_not_rebought",
            after_crash["active_run_id"] is None
            and after_crash["spent_gpu_usd"] == 0.12
            and after_crash["pods_created"] == 1
            and B.remaining_usd(after_crash) == 1.88
            and recovered["run"] is not None,
        )

        # A terminal outcome from the previous single grant does not block a fresh series.
        prior_dir = root / "case_batch_after_terminal"
        B.save(prior_dir, {**created_batch, "batch_id": "batch-new"})
        stale = M.initial_state()
        stale.update(
            {
                "live_execution_state": "FAILED",
                "paid_run_already_attempted": True,
                "terminal_grant_id": "old-grant",
                "last_check_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        M.save_json(prior_dir / "state.json", stale)
        revived = M.run_check(provider=card, rdir=prior_dir, dry_run=True)
        check(
            "a_fresh_batch_lifts_the_previous_single_grant_outcome_once",
            revived["run"] is not None
            and revived["state"]["live_execution_state"] == "DRY_RUN_OK"
            and revived["state"]["paid_run_already_attempted"] is False
            and revived["state"]["terminal_batch_id"] == "batch-new",
        )

        # Concurrency: an attempt in flight is never joined by a second create.
        flight_dir = root / "case_batch_flight"
        B.save(flight_dir, {**created_batch, "batch_id": "batch-flight"})
        in_flight = B.begin(B.read(flight_dir), "run-live", reserved_usd=2.0)
        B.save(flight_dir, in_flight)
        flying = M.run_check(provider=card, rdir=flight_dir, dry_run=True)
        check(
            "one_attempt_at_a_time_even_after_a_restart",
            flying["run"] is None
            and flying["state"]["live_execution_state"] == "ATTEMPT_IN_FLIGHT"
            and not (flight_dir / "authorization-grant.json").exists(),
        )

        # The budget is a hard ceiling, independent of how many attempts it takes.
        spent_dir = root / "case_batch_spent"
        B.save(spent_dir, {**created_batch, "batch_id": "batch-spent", "spent_gpu_usd": 2.0})
        exhausted = M.run_check(provider=card, rdir=spent_dir, dry_run=True)
        check(
            "a_spent_batch_refuses_another_pod",
            exhausted["run"] is None
            and exhausted["state"]["live_execution_state"] == "PAID_BATCH_EXHAUSTED"
            and not (spent_dir / "authorization-grant.json").exists()
            and B.status(B.read(spent_dir)) == "exhausted",
        )

        # Spend only ever grows; a completed series stops for good.
        ledger = B.create(
            root / "case_batch_ledger",
            phrase=B.BATCH_PHRASE,
            budget_usd=2.00,
            days=7,
            placements=[],
            allowed_gpu=M.GPU_ID,
            model_id=M.MODEL_ID,
        )
        first_record = B.record(ledger, run_id="r1", spend_usd=0.40, pod_ids=["p1"], confirmed_pod=True)
        second_record = B.record(first_record, run_id="r2", spend_usd=0.35, pod_ids=["p2"], confirmed_pod=True)
        finished = B.mark_completed(second_record)
        check(
            "spend_never_resets_and_completion_is_final",
            second_record["spent_gpu_usd"] == 0.75
            and second_record["pods_created"] == 2
            and B.status(finished) == "completed"
            and B.can_start(finished, 1.09)[0] is False
            and B.child_budget(second_record) == 1.25,
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)

    report = {
        "checks": checks,
        "verdict": "PASS" if all(item["ok"] for item in checks) else "FAIL",
    }
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["verdict"] == "PASS" else 1
