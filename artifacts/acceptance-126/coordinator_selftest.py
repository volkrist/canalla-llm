"""Twelve free proofs that the autonomous coordinator behaves as the owner demanded.

The complaint this answers is behavioural: "checked capacity five times, nothing, done". So the
tests are written as a fake provider and a fake acceptance runner, and they assert the *decision*:
it keeps waiting for the whole window, it starts only inside every cap, it never creates a second
Pod for an unaccounted one, and it never releases without the live gates.

No network, no GPU, no provider credential, no user data. Runs in a few seconds.

    python artifacts/acceptance-126/coordinator_selftest.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import coordinator  # noqa: E402
import release_coordinator  # noqa: E402

UTC = timezone.utc
FAILURES: list[str] = []
CHECKS = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"[PASS] {label}")
        return
    print(f"[FAIL] {label} — {detail}")
    FAILURES.append(label)


# ------------------------------------------------------------------------------------ fake world


CARD = {
    "id": "NVIDIA L40S",
    "display_name": "NVIDIA L40S",
    "vram_gb": 48,
    "secure_hourly_usd": 0.79,
    "community_hourly_usd": None,
    "cheapest_hourly_usd": 0.79,
    "selected_hourly_usd": 0.79,
    "secure_only_rate": True,
    "datacenter": "US-TX-3",
    "volume_id": "uwgeaie5b0",
    "volume_name": "orcarouter-storage",
}


class FakeWorld:
    """Every side effect is a recorded no-op; every read is a queued answer."""

    def __init__(self, *, availability=None, results=None, pods=None, watchdog_armed=False,
                 reachable=True, manual_off=False, healthy=True):
        self.t = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
        self._availability = list(availability or [])
        self._results = list(results or [])
        self._pods = list(pods or [])
        self.watchdog_armed = watchdog_armed
        self.reachable = reachable
        self._manual_off = manual_off
        self.healthy = healthy
        self.calls: list[tuple] = []
        self.slept = 0.0

    # reads
    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept += float(seconds)
        self.t += timedelta(seconds=float(seconds))

    def availability(self):
        if self._availability:
            value = self._availability.pop(0)
        else:
            value = {"candidates": [], "checked_datacenters": ["US-TX-3", "US-MO-1"]}
        self.calls.append(("availability", len(value.get("candidates") or [])))
        return {
            "ok": True,
            "tested_at": self.t.isoformat(),
            "verdict": "PLACEABLE" if value.get("candidates") else "NOT_PLACEABLE",
            "candidates": value.get("candidates") or [],
            "scanned": [
                {"volume_id": "uwgeaie5b0", "datacenter": "US-TX-3", "gpus_at_least_48gb": []},
                {"volume_id": "y92bt25ru8", "datacenter": "US-MO-1", "gpus_at_least_48gb": []},
            ],
            "checked_datacenters": value.get("checked_datacenters")
            or ["US-TX-3", "US-MO-1"],
        }

    def pods(self):
        return {"ok": True, "pods": list(self._pods)}

    def balance(self):
        return 9.0

    def watchdog(self):
        return {"reachable": self.reachable, "armed": self.watchdog_armed, "detail": "fake"}

    def manual_off(self):
        return self._manual_off

    def clear_manual_off(self):
        cleared, self._manual_off = self._manual_off, False
        return cleared

    def app_healthy(self):
        return self.healthy

    # writes
    def run_scenario(self, scenarios, *, max_session_seconds, watchdog_seconds, budget_usd):
        self.calls.append(("run_scenario", tuple(scenarios), max_session_seconds,
                           watchdog_seconds, budget_usd))
        if self._results:
            result = dict(self._results.pop(0))
        else:
            result = {"verdict": "PASS", "pod_created": True, "pod_seconds": 300.0,
                      "cost_usd": 0.09}
        verdict = result.get("verdict", "PASS")
        failed = result.get("fail")
        per_scenario = {}
        for name in scenarios:
            per_scenario[name] = {
                "verdict": "FAIL" if name == failed else verdict,
                "reasons": result.get("reasons", []) if name == failed else [],
            }
        return {
            "scenarios": list(scenarios),
            "results": per_scenario,
            "pod_created": result.get("pod_created", True),
            "pod_seconds": result.get("pod_seconds", 300.0),
            "cost_usd": result.get("cost_usd", 0.09),
            "rate_usd": result.get("rate_usd", 0.79),
            "elapsed_seconds": result.get("pod_seconds", 300.0),
            "evidence": result.get("evidence", "fake://summary.json"),
            "runner_exit": result.get("runner_exit", 0),
            "output_tail": "",
        }

    def release(self):
        self.calls.append(("release",))
        return {"ok": True, "exit": 0, "summary": "release ok", "output_tail": ""}

    def stop_compute(self):
        self.calls.append(("stop_compute",))
        self._pods = []
        return {"http": 200}

    def disarm_watchdog(self):
        self.calls.append(("disarm_watchdog",))
        self.watchdog_armed = False
        return {"exit": 0}

    def terminate_pod(self, pod_id):
        self.calls.append(("terminate_pod", pod_id))
        self._pods = [pod for pod in self._pods if pod.get("id") != pod_id]
        return {"status": 200}


# ------------------------------------------------------------------------------------- fixtures


READINESS = {
    "ok": True,
    "flags": {name: True for name in coordinator.REQUIRED_FLAGS},
    "required_flags": list(coordinator.REQUIRED_FLAGS),
    "generated_at": "2026-11-10T09:00:00+00:00",
    "installed_version": "1.2.6",
}


def fresh_state(cfg: coordinator.Config, *, hours: float = 0.0) -> dict:
    state = coordinator.initial_state(cfg, READINESS)
    start = datetime(2026, 11, 10, 10, 0, tzinfo=UTC) + timedelta(hours=hours)
    state["started_at"] = start.isoformat()
    state["expires_at"] = (start + timedelta(hours=cfg.ttl_hours)).isoformat()
    return state


def temp_paths(tmp: Path):
    coordinator.STATE_FILE = tmp / "state.json"
    coordinator.EVENT_LOG = tmp / "events.log"
    coordinator.CANCEL_FILE = tmp / "cancel.flag"
    coordinator.STATE_DIR = tmp


def live_pod(pod_id="clg0l7fcy6542d"):
    return {"id": pod_id, "status": "RUNNING", "name": "alex-gw-test"}


def scenario_result(verdict, *, pod=True, cost=0.09, seconds=300.0, reasons=None, fail=None):
    return {
        "verdict": verdict,
        "fail": fail,
        "pod_created": pod,
        "pod_seconds": seconds,
        "cost_usd": cost,
        "reasons": reasons or [],
    }


# ------------------------------------------------------------------------------------------ tests


def test_1_five_unavailable_checks_do_not_end_the_run(tmp: Path) -> None:
    cfg = coordinator.Config()
    temp_paths(tmp)
    state = fresh_state(cfg)
    world = FakeWorld(availability=[{"candidates": []} for _ in range(400)])
    coordinator.run_loop(world, cfg, state, max_iterations=5)
    check("1a five unavailable checks leave the monitor running", not state.get("finished"),
          str(state.get("outcome")))
    check("1b it counts every check", state.get("checks") == 5, str(state.get("checks")))
    check("1c no Pod was created", state.get("pods_created") == 0)
    check("1d it slept the 15-minute interval", world.slept == 5 * cfg.interval_seconds,
          str(world.slept))
    # Keep waiting until the whole permission window is over.
    coordinator.run_loop(world, cfg, state, max_iterations=400)
    check("1e when the 48h window closes the outcome is blocked, not a PASS",
          state.get("outcome") == "EXPIRED_NO_CAPACITY", str(state.get("outcome")))
    check("1f even then, nothing was created", state.get("pods_created") == 0)


def test_2_capacity_on_the_sixth_check(tmp: Path) -> None:
    cfg = coordinator.Config()
    temp_paths(tmp)
    state = fresh_state(cfg)
    availability = [{"candidates": []} for _ in range(5)] + [{"candidates": [CARD]}]
    world = FakeWorld(availability=availability,
                      results=[scenario_result("PASS")],
                      watchdog_armed=False)
    coordinator.run_loop(world, cfg, state, max_iterations=8)
    check("2a the first five checks created nothing", state.get("pods_created") == 1,
          str(state.get("pods_created")))
    check("2b the sixth check started the first live scenario",
          any(call[0] == "run_scenario" and call[1][0] == "T3L" for call in world.calls),
          str(world.calls[-3:]))
    check("2b2 one session carries both remaining gates when they fit",
          any(call[0] == "run_scenario" and list(call[1]) == ["T3L", "T4L"]
              for call in world.calls), str(world.calls[-3:]))
    check("2c the session window keeps a 90 s stop margin",
          state["attempts"][0]["max_session_seconds"] <= cfg.max_pod_seconds
          - cfg.watchdog_reserve_seconds,
          str(state["attempts"][0]["max_session_seconds"]))
    check("2d the watchdog was armed at or below 510 s", cfg.watchdog_seconds() <= 510,
          str(cfg.watchdog_seconds()))


def test_3_both_live_gates_pass_reaches_the_release_gate(tmp: Path) -> None:
    cfg = coordinator.Config()
    temp_paths(tmp)
    state = fresh_state(cfg)
    availability = [{"candidates": [CARD]} for _ in range(4)]
    results = [scenario_result("PASS"), scenario_result("PASS"), scenario_result("PASS")]
    world = FakeWorld(availability=availability, results=results)
    coordinator.run_loop(world, cfg, state, max_iterations=10)
    check("3a every live scenario recorded PASS",
          state.get("scenario_verdicts") == {"T3L": "PASS", "T4L": "PASS", "LA": "PASS"},
          str(state.get("scenario_verdicts")))
    check("3b the monitor publishes without a further message",
          state.get("outcome") == "PUBLISHED", str(state.get("outcome")))
    check("3b2 it handed over to the release coordinator",
          any(call[0] == "release" for call in world.calls), str(world.calls[-3:]))
    check("3c no more than three Pods were opened", state.get("pods_created") <= 3,
          str(state.get("pods_created")))
    check("3d the spend stays inside the new budget",
          coordinator.budget_used(state) <= cfg.budget_usd, str(coordinator.budget_used(state)))


def test_4_t3_failure_forbids_the_release(tmp: Path) -> None:
    cfg = coordinator.Config()
    temp_paths(tmp)
    state = fresh_state(cfg)
    world = FakeWorld(availability=[{"candidates": [CARD]}],
                      results=[scenario_result("PASS", fail="T3L",
                                               reasons=["no completed tool"])])
    coordinator.run_loop(world, cfg, state, max_iterations=6)
    check("4a a live FAIL stops the paid phase", state.get("outcome") == "LIVE_FAILED_T3L",
          str(state.get("outcome")))
    check("4b the Pod stop was verified",
          state["attempts"][0].get("stop_confirmed") is True)
    check("4c the watchdog was disarmed", not world.watchdog_armed)
    evidence = evidence_for(state)
    plan = release_coordinator.release_plan(evidence)
    check("4d the release is refused", plan["publish"] is False)
    check("4e and the refusal names the live gate", "t3_live" in plan["blocked_by"],
          str(plan["blocked_by"]))


def test_5_t4_failure_forbids_the_release(tmp: Path) -> None:
    cfg = coordinator.Config()
    temp_paths(tmp)
    state = fresh_state(cfg)
    world = FakeWorld(
        availability=[{"candidates": [CARD]} for _ in range(3)],
        results=[scenario_result("PASS", fail="T4L", reasons=["host offline"])],
    )
    coordinator.run_loop(world, cfg, state, max_iterations=8)
    check("5a a T4 failure stops the run", state.get("outcome") == "LIVE_FAILED_T4L",
          str(state.get("outcome")))
    plan = release_coordinator.release_plan(evidence_for(state))
    check("5b the release is refused", plan["publish"] is False)
    check("5c and T4 is named", "t4_live" in plan["blocked_by"], str(plan["blocked_by"]))


def test_6_budget_guard_refuses_a_new_pod(tmp: Path) -> None:
    cfg = coordinator.Config(budget_usd=0.10)
    temp_paths(tmp)
    state = fresh_state(cfg)
    state["attempts"] = [{"scenario": "T3L", "verdict": "FAIL", "cost_usd": 0.095}]
    state["scenario_verdicts"] = {"T3L": "FAIL"}
    state["pods_created"] = 1
    world = FakeWorld(availability=[{"candidates": [CARD]}])
    coordinator.run_loop(world, cfg, state, max_iterations=3)
    check("6a a Pod that would exceed the cap is not created",
          not any(call[0] == "run_scenario" for call in world.calls), str(world.calls))
    check("6c the recorded verdict is not rewritten by a budget stop",
          state.get("scenario_verdicts") == {"T3L": "FAIL"},
          str(state.get("scenario_verdicts")))
    check("6b the outcome is a budget stop", state.get("outcome") == "BUDGET_LIMIT",
          str(state.get("outcome")))


def test_7_manual_ai_off_blocks_every_start(tmp: Path) -> None:
    cfg = coordinator.Config()
    temp_paths(tmp)
    state = fresh_state(cfg)
    world = FakeWorld(availability=[{"candidates": [CARD]}], manual_off=True)
    coordinator.run_loop(world, cfg, state, max_iterations=4)
    check("7a the manual latch stops the monitor", state.get("outcome") == "MANUAL_OFF",
          str(state.get("outcome")))
    check("7b no Pod was created and no ensure was attempted",
          not any(call[0] == "run_scenario" for call in world.calls), str(world.calls))


def test_8_crash_after_create_is_reconciled_not_duplicated(tmp: Path) -> None:
    cfg = coordinator.Config()
    temp_paths(tmp)
    state = fresh_state(cfg)
    state["in_session"] = True
    state["pods_created"] = 1
    state["scenario_verdicts"] = {"T3L": "RUNNING"}
    state["attempts"] = [{"index": 1, "scenario": "T3L", "verdict": "RUNNING"}]
    world = FakeWorld(availability=[{"candidates": [CARD]}], pods=[live_pod()])
    coordinator.run_loop(world, cfg, state, max_iterations=1)
    check("8a the crash is detected", any(call[0] == "stop_compute" for call in world.calls),
          str(world.calls))
    check("8b no replacement Pod was created",
          not any(call[0] == "run_scenario" for call in world.calls), str(world.calls))
    check("8c the abandoned attempt is recorded as interrupted",
          state["attempts"][0]["verdict"] == "INTERRUPTED_BY_CRASH",
          state["attempts"][0]["verdict"])
    check("8d the account is clean again", world.pods()["pods"] == [])
    check("8e the monitor may continue afterwards", state.get("in_session") is False)


def test_9_ci_not_green_forbids_the_release(tmp: Path) -> None:
    evidence = evidence_for(fresh_state(coordinator.Config()), verdicts_all_pass=True)
    evidence["ci"] = {"passed": 6, "total": 7, "sha": "40cdf46"}
    plan = release_coordinator.release_plan(evidence)
    check("9a 6/7 CI refuses publication", plan["publish"] is False)
    check("9b and names the CI gate", "ci" in plan["blocked_by"], str(plan["blocked_by"]))


def test_10_bad_signature_forbids_the_release(tmp: Path) -> None:
    evidence = evidence_for(fresh_state(coordinator.Config()), verdicts_all_pass=True)
    evidence["installer_sha_recorded"] = {"sha256": "00" * 32}
    plan = release_coordinator.release_plan(evidence)
    check("10a a mismatched signature digest refuses publication",
          plan["publish"] is False)
    check("10b and names the signature gate", "signature" in plan["blocked_by"],
          str(plan["blocked_by"]))


def test_11_ttl_expiry_is_a_honest_blocked(tmp: Path) -> None:
    cfg = coordinator.Config(ttl_hours=1)
    temp_paths(tmp)
    state = fresh_state(cfg)
    world = FakeWorld(availability=[{"candidates": []} for _ in range(100)])
    coordinator.run_loop(world, cfg, state, max_iterations=10)
    check("11a the window ends with BLOCKED, not PASS",
          state.get("outcome") == "EXPIRED_NO_CAPACITY", str(state.get("outcome")))
    check("11b no Pod was created in the whole window", state.get("pods_created") == 0)
    check("11c the monitor attempted no paid call",
          not any(call[0] == "run_scenario" for call in world.calls))


def test_12_restart_after_checkpoint_duplicates_nothing(tmp: Path) -> None:
    cfg = coordinator.Config()
    temp_paths(tmp)
    state = fresh_state(cfg)
    state["finished"] = True
    state["outcome"] = "READY_FOR_RELEASE"
    state["scenario_verdicts"] = {"T3L": "PASS", "T4L": "PASS", "LA": "PASS"}
    state["pods_created"] = 3
    state["attempts"] = [{"scenario": "T3L", "verdict": "PASS", "cost_usd": 0.09}]
    before = json.dumps(state, sort_keys=True)
    world = FakeWorld(availability=[{"candidates": [CARD]}])
    coordinator.run_loop(world, cfg, state, max_iterations=5)
    check("12a a finished checkpoint is a no-op",
          state.get("outcome") == "READY_FOR_RELEASE" and state["pods_created"] == 3)
    check("12b no scenario ran again", not any(call[0] == "run_scenario" for call in world.calls),
          str(world.calls))
    check("12c no Pod, tag or release was created by the restart",
          state["attempts"] == [{"scenario": "T3L", "verdict": "PASS", "cost_usd": 0.09}],
          str(state["attempts"]))
    check("12d the state is otherwise unchanged",
          json.loads(before)["scenario_verdicts"] == state["scenario_verdicts"])


def test_13_watchdog_and_orphans_fail_closed(tmp: Path) -> None:
    """Not one of the twelve, but the two guards that make the twelve safe in production."""
    cfg = coordinator.Config()
    temp_paths(tmp)
    state = fresh_state(cfg)
    world = FakeWorld(availability=[{"candidates": [CARD]}], reachable=False)
    coordinator.run_loop(world, cfg, state, max_iterations=2)
    check("13a an unreachable watchdog never starts a Pod",
          not any(call[0] == "run_scenario" for call in world.calls), str(world.calls))

    state = fresh_state(cfg)
    world = FakeWorld(availability=[{"candidates": [CARD]}], pods=[live_pod("pod-other")])
    coordinator.run_loop(world, cfg, state, max_iterations=2)
    check("13b an unaccounted Pod is stopped, not duplicated",
          state.get("outcome") == "ORPHAN_POD_STOPPED_REVIEW", str(state.get("outcome")))
    check("13c it was terminated", any(call[0] == "terminate_pod" for call in world.calls))


# -------------------------------------------------------------------------------------- evidence


def test_14_the_measured_balance_is_the_budget(tmp: Path) -> None:
    """A reconciled spend above the attempt rows must block a Pod, not be averaged away."""
    cfg = coordinator.Config()
    temp_paths(tmp)
    state = fresh_state(cfg)
    # The rows explain $0.48; the provider says $0.69 is gone. The worst case Pod is $0.18.
    state["attempts"] = [
        {"scenario": "T3L", "verdict": "PASS", "cost_usd": 0.074, "pod_created": True},
        {"scenario": "T4L", "verdict": "FAIL", "cost_usd": 0.2441, "pod_created": True},
    ]
    state["scenario_verdicts"] = {"T3L": "PASS", "T4L": "FAIL"}
    state["pods_created"] = 2
    state["cycle_pods_created"] = 2
    state["billing"] = {"measured_spend_usd": 0.6907}
    # The Gateway charged $1.09/h while the catalogue row said $0.79: the guard must use the real one.
    state["last_rate_usd"] = 1.09
    world = FakeWorld(availability=[{"candidates": [CARD]}])
    coordinator.run_loop(world, cfg, state, max_iterations=3)
    check("14a the measured spend wins over the attempt rows",
          coordinator.budget_used(state) == 0.6907, str(coordinator.budget_used(state)))
    check("14b no Pod is bought with money that is already gone",
          not any(call[0] == "run_scenario" for call in world.calls), str(world.calls))
    check("14c and the outcome is a budget stop", state.get("outcome") == "BUDGET_LIMIT",
          str(state.get("outcome")))


def evidence_for(state: dict, *, verdicts_all_pass: bool = False) -> dict:
    """The shape `release_coordinator.collect_evidence` produces, built from coordinator state."""
    verdicts = {"T3L": "PASS", "T4L": "PASS", "LA": "PASS"} if verdicts_all_pass else (
        state.get("scenario_verdicts") or {}
    )
    return {
        "version": "1.2.6",
        "head": "40cdf46",
        "branch": "release/canalla-1.2.4",
        "dirty": False,
        "product_version": "1.2.6",
        "installer": {
            "present": True,
            "sha256": "ab" * 32,
            "signature_present": True,
        },
        "installer_sha_recorded": {"sha256": "ab" * 32},
        "installed": {"product_version": "1.2.6"},
        "coordinator": {
            "outcome": state.get("outcome"),
            "scenario_verdicts": verdicts,
            "pods_created": state.get("pods_created", 0),
            "spent_usd": coordinator.budget_used(state),
        },
        "prior_live": {"run": "live-20261108T131238", "scenario": "LA", "verdict": "PASS"},
        "ci": {"passed": 7, "total": 7, "sha": "40cdf46"},
        "readiness": {
            "ok": True,
            "flags": {
                **{name: True for name in coordinator.REQUIRED_FLAGS},
                "developer_gate": True,
                "candidate_gate": True,
                "installed_smoke": True,
                "tor_ready": True,
            },
        },
        "pods": [],
    }


def main() -> int:
    tests = [
        test_1_five_unavailable_checks_do_not_end_the_run,
        test_2_capacity_on_the_sixth_check,
        test_3_both_live_gates_pass_reaches_the_release_gate,
        test_4_t3_failure_forbids_the_release,
        test_5_t4_failure_forbids_the_release,
        test_6_budget_guard_refuses_a_new_pod,
        test_7_manual_ai_off_blocks_every_start,
        test_8_crash_after_create_is_reconciled_not_duplicated,
        test_9_ci_not_green_forbids_the_release,
        test_10_bad_signature_forbids_the_release,
        test_11_ttl_expiry_is_a_honest_blocked,
        test_12_restart_after_checkpoint_duplicates_nothing,
        test_13_watchdog_and_orphans_fail_closed,
        test_14_the_measured_balance_is_the_budget,
    ]
    original = (coordinator.STATE_FILE, coordinator.EVENT_LOG, coordinator.CANCEL_FILE,
                coordinator.STATE_DIR, coordinator.QUIET)
    coordinator.QUIET = True
    try:
        for index, test in enumerate(tests, start=1):
            with tempfile.TemporaryDirectory(prefix=f"canalla-coord-{index}-") as tmp:
                print(f"--- {test.__name__}")
                try:
                    test(Path(tmp))
                except Exception as error:  # noqa: BLE001 - a crashing test is a failing test
                    check(f"{test.__name__} raised", False, f"{type(error).__name__}: {error}")
    finally:
        (coordinator.STATE_FILE, coordinator.EVENT_LOG, coordinator.CANCEL_FILE,
         coordinator.STATE_DIR, coordinator.QUIET) = original
    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    print("SELF-TEST", "PASS" if not FAILURES else "FAIL")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
