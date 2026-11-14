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
            and M.read_grant(rdir)["reason"] == "already_used",
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
    finally:
        shutil.rmtree(root, ignore_errors=True)

    report = {
        "checks": checks,
        "verdict": "PASS" if all(item["ok"] for item in checks) else "FAIL",
    }
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["verdict"] == "PASS" else 1
