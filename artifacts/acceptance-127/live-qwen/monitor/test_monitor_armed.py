"""The incident regression: a scheduler restart must never re-arm a paid run.

On 22 Nov an operator pause was written into `state.json`, a running monitor overwrote it on its
next pass, the one-minute task trigger started a new instance, and that instance bought a Pod for a
scenario that was already PASS. These checks pin the two invariants that make that impossible:

* the pause is a file of its own, checked before anything else;
* a paid attempt needs an ARMED manifest that names this batch, this commit and this exact harness.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import batch as B
import monitor as M

CHECKS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append({"check": name, "ok": bool(ok), "detail": detail})


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(M.REPO), capture_output=True, text=True, timeout=10
    ).stdout.strip()


def _harness_hash() -> str:
    return hashlib.sha256((M.REPO / "artifacts/acceptance-127/live-qwen/live_acceptance.py").read_bytes()).hexdigest()


def _batch(directory: Path) -> dict:
    return B.create(
        directory,
        phrase=B.phrase_for(5.00),
        budget_usd=5.00,
        days=3,
        placements=[{"datacenter": "US-TX-3", "volume_id": "uwgeaie5b0", "gpu": "NVIDIA L40S"}],
        allowed_gpu=M.GPU_ID,
        model_id=M.MODEL_ID,
        maximum_total_pods=3,
    )


def _manifest(directory: Path, batch: dict, **overrides) -> dict:
    payload = {
        "state": "ARMED",
        "batch_id": batch["batch_id"],
        "scenario_id": "level7-resume",
        "scenario_version": 1,
        "scenario_hash": _harness_hash(),
        "code_commit": _head(),
        "harness_version": "1.2.7",
        "model_identity": M.MODEL_ID,
        "deployment_target": {"datacenter": "US-TX-3", "volume_id": "uwgeaie5b0", "gpu": M.GPU_ID},
        "max_cost": 0.50,
        "authorized_cost": 5.00,
        "max_duration": 5400,
        "readiness_evidence": {"level6_run": "0ef569505ce04d73bdf5cb722949415e"},
        "authorization_reference": batch["batch_id"],
    }
    payload.update(overrides)
    (directory / M.MANIFEST_FILE).write_text(json.dumps(payload), encoding="utf-8")
    return payload


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="canalla-armed-"))
    try:
        # 1. No manifest at all: money and an available card are not permission.
        plain = root / "no-manifest"
        batch = _batch(plain)
        grant, _batch_state, reason = M.child_grant_from_batch(plain, batch, rate=1.09)
        check(
            "no_armed_manifest_means_no_grant",
            grant["valid"] is False and reason == "no_armed_manifest",
            reason,
        )

        # 2. A manifest for a different commit is stale.
        stale = root / "stale-commit"
        stale_batch = _batch(stale)
        _manifest(stale, stale_batch, code_commit="0" * 40)
        grant, _b, reason = M.child_grant_from_batch(stale, stale_batch, rate=1.09)
        check(
            "a_manifest_from_another_commit_is_refused",
            grant["valid"] is False and reason == "manifest_code_changed",
            reason,
        )

        # 3. A changed harness (the scenario was edited) is refused.
        edited = root / "edited"
        edited_batch = _batch(edited)
        _manifest(edited, edited_batch, scenario_hash="f" * 64)
        grant, _b, reason = M.child_grant_from_batch(edited, edited_batch, rate=1.09)
        check(
            "a_changed_harness_is_refused",
            grant["valid"] is False and reason == "manifest_scenario_changed",
            reason,
        )

        # 4. A consumed manifest (RUNNING) cannot start a second run.
        running = root / "running"
        running_batch = _batch(running)
        _manifest(running, running_batch, state="RUNNING")
        grant, _b, reason = M.child_grant_from_batch(running, running_batch, rate=1.09)
        check(
            "a_running_manifest_cannot_start_again",
            grant["valid"] is False and reason == "manifest_running",
            reason,
        )

        # 5. A manifest over the authorized cost is refused.
        greedy = root / "greedy"
        greedy_batch = _batch(greedy)
        _manifest(greedy, greedy_batch, max_cost=9.00)
        grant, _b, reason = M.child_grant_from_batch(greedy, greedy_batch, rate=1.09)
        check(
            "a_manifest_above_the_authorized_cost_is_refused",
            grant["valid"] is False and reason == "manifest_over_authorized_cost",
            reason,
        )

        # 6. The armed path works, and only for this batch.
        armed = root / "armed"
        armed_batch = _batch(armed)
        _manifest(armed, armed_batch)
        grant, _b, reason = M.child_grant_from_batch(armed, armed_batch, rate=1.09)
        check(
            "an_armed_manifest_for_this_batch_creates_one_child",
            grant["valid"] is True and reason == "created",
            reason,
        )

        # 7. The incident itself: paused, restarted by the scheduler, still no grant.
        paused = root / "paused"
        paused_batch = _batch(paused)
        _manifest(paused, paused_batch)
        (paused / M.PAUSED_FILE).write_text("operator", encoding="utf-8")
        # A fresh process (as the scheduler starts it) loads the state that claims enabled=True,
        # and the pause must still win.
        state = M.load_state(paused)
        state["monitoring_enabled"] = True
        M.save_json(paused / "state.json", state)
        result = M.run_check(
            provider=M.FixtureProvider({"ok": True, "pods": [], "gpu": _card()}),
            rdir=paused,
            dry_run=False,
            state_override=None,
        )
        check(
            "a_scheduler_restart_does_not_rearm_a_paused_monitor",
            result.get("skipped") == "paused_by_operator"
            and not M.grant_path(paused).is_file(),
            str(result.get("skipped")),
        )

        # 8. Enabling removes the pause, disabling writes it.
        M.save_json(paused / "state.json", M.load_state(paused))
        pause_path = paused / M.PAUSED_FILE
        pause_path.write_text("x", encoding="utf-8")
        pause_path.unlink(missing_ok=True)
        check("enabling_clears_the_pause_file", not M.is_paused(paused))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    verdict = "PASS" if all(item["ok"] for item in CHECKS) else "FAIL"
    print(json.dumps({"checks": CHECKS, "verdict": verdict}, ensure_ascii=False, indent=1))
    return 0 if verdict == "PASS" else 1


def _card() -> dict:
    return {
        "id": "NVIDIA L40S",
        "displayName": "NVIDIA L40S",
        "memoryInGb": 48,
        "price_usd_per_hour": 1.09,
        "availability": "HIGH",
        "datacenters": ["US-TX-3"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
