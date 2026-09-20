"""Harness tests for paid guards, resume, cleanup, and real vs mock labels.

These tests never start RunPod, TinyFish, or a production backend.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1] / "harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from catalog import select  # noqa: E402
from cleanup import cleanup_run, cleanup_task, stop_owned_processes  # noqa: E402
from fixtures import new_state  # noqa: E402
from paid import HARD_RUNPOD_USD, PaidConfig, PaidRefused, authorize_case, checkpoint, hydrate_paid, validate_real_start  # noqa: E402
from persist import load_payload, persist_payload, terminal_ids  # noqa: E402
from real_actor import _close_browser_sessions  # noqa: E402
from real_cases import apply_real_overlay, real_plan_ids, skip_reason  # noqa: E402
from real_observe import apply_product, fail_fast_reason, workspace_violation  # noqa: E402
from report import report_dir  # noqa: E402
from run_eval import display_status, main, run_case  # noqa: E402


class FakeClient:
    def __init__(self):
        self.posts = []

    def post(self, path, payload=None, **kw):
        self.posts.append(path)
        return {"ok": True}


class FakeRuntime:
    def __init__(self, provider="llamacpp", mock=False, timeout_ids=None, fail_fast=None):
        self.info = {"provider": provider, "mock": mock, "native_host": True, "model": "orcarouter-qwen38-27b-q5km"}
        self.workspace_root = Path(tempfile.mkdtemp(prefix="eval-fake-"))
        self.data_dir = self.workspace_root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_ids = set(timeout_ids or [])
        self.fail_fast = fail_fast
        self.stopped = False
        self.executed = []

    def session_cost(self):
        return 0.01 * len(self.executed)

    def set_case_prefs(self, **kwargs):
        return None

    def execute_case(self, overlay, state, paid):
        self.executed.append(overlay["id"])
        workspace = Path(state["workspace"]) if state.get("workspace") else self.workspace_root
        tid = overlay["id"]
        answer = "synthetic fake runtime"
        tools = [{"name": "read_file", "status": "completed", "origin": "model", "ok": True}]
        if tid in {"LC-01", "WM-01"}:
            (workspace / "notes.txt").write_text("ALEX_EVAL_WRITE_OK\n", encoding="utf-8")
            answer = "В notes.txt: ALEX_EVAL_WRITE_OK"
            tools = [
                {"name": "write_file", "status": "completed", "ok": True, "origin": "model"},
                {"name": "read_file", "status": "completed", "ok": True, "origin": "model"},
            ]
        if tid == "LC-02":
            answer = (workspace / "hello.txt").read_text(encoding="utf-8")
        product = {
            "answer": answer,
            "tools": tools,
            "chat_id": "fake-chat",
            "task_id": "fake-task",
            "allowed_roots": [str(workspace), str(self.workspace_root)],
            "runtime_seconds": 999 if tid in self.timeout_ids else 0.2,
        }
        if self.fail_fast:
            product["runaway"] = True
        apply_product(overlay, state, product)
        state["metrics"]["runtime_seconds"] = product["runtime_seconds"]
        state["fail_fast"] = fail_fast_reason(state, product)
        state["product"] = {"chat_id": "fake-chat", "task_id": "fake-task"}
        return product

    def stop(self):
        self.stopped = True
        return {"ok": True}


class PaidTests(unittest.TestCase):
    def test_refuses_without_allow_runpod(self):
        with self.assertRaises(PaidRefused) as ctx:
            validate_real_start(PaidConfig())
        self.assertIn("will not start RunPod", str(ctx.exception))
        self.assertIn("--allow-runpod", str(ctx.exception))

    def test_refuses_zero_budget(self):
        with self.assertRaises(PaidRefused):
            validate_real_start(PaidConfig(allow_runpod=True, runpod_budget_usd=0))

    def test_refuses_over_hard_budget(self):
        with self.assertRaises(PaidRefused) as ctx:
            validate_real_start(PaidConfig(allow_runpod=True, runpod_budget_usd=HARD_RUNPOD_USD + 0.01))
        self.assertIn("1.20", str(ctx.exception))

    def test_accepts_hard_budget(self):
        validate_real_start(PaidConfig(allow_runpod=True, runpod_budget_usd=1.20))

    def test_refuses_agent_flag(self):
        with self.assertRaises(PaidRefused):
            validate_real_start(PaidConfig(allow_runpod=True, runpod_budget_usd=1.0, allow_tinyfish_agent=True))

    def test_browser_requires_budget(self):
        with self.assertRaises(PaidRefused):
            validate_real_start(
                PaidConfig(allow_runpod=True, runpod_budget_usd=1.0, allow_tinyfish_browser=True, tinyfish_budget_usd=0)
            )

    def test_checkpoint_soft_and_hard(self):
        cfg = PaidConfig(allow_runpod=True, runpod_budget_usd=1.20, spent_runpod_usd=0.10)
        self.assertEqual(checkpoint(cfg), "ok")
        cfg.spent_runpod_usd = 0.90
        self.assertEqual(checkpoint(cfg), "soft_stop")
        cfg.spent_runpod_usd = 1.20
        self.assertEqual(checkpoint(cfg), "hard_stop")

    def test_browser_case_unauthorized(self):
        reason = authorize_case(PaidConfig(allow_runpod=True, runpod_budget_usd=1.0), "WM-07")
        self.assertIsNotNone(reason)
        self.assertIn("Browser", reason)


class SuiteAndLabelTests(unittest.TestCase):
    def test_suite_filter(self):
        rows = select(suite="weak-model")
        self.assertEqual(len(rows), 10)
        self.assertEqual([row["id"] for row in rows], [f"WM-{i:02d}" for i in range(1, 11)])

    def test_all_plan_starts_with_weak_model(self):
        ids = real_plan_ids(select(suite="all"), "all")
        self.assertEqual(ids[:10], [f"WM-{i:02d}" for i in range(1, 11)])
        self.assertIn("LC-01", ids)
        self.assertIn("SF-01", ids)
        self.assertIn("CD-01", ids)

    def test_canned_overlay_does_not_change_catalog_trace(self):
        task = select(task_id="WM-01")[0]
        self.assertEqual(task["workspace_setup"]["kind"], "canned_trace")
        overlay = apply_real_overlay(task)
        self.assertNotEqual(overlay["workspace_setup"]["kind"], "canned_trace")
        self.assertEqual(select(task_id="WM-01")[0]["workspace_setup"]["kind"], "canned_trace")

    def test_mock_pass_is_not_real_pass(self):
        task = select(task_id="LC-01")[0]
        row = run_case(task, "mock", "test-mock-label")
        self.assertEqual(row["status"], "PASS")
        self.assertEqual(row["display_status"], "LOCAL MOCK PASS")
        self.assertNotIn("REAL", row["display_status"])
        self.assertEqual(display_status("mock", "PASS"), "LOCAL MOCK PASS")

    def test_real_label_requires_llamacpp_not_mock(self):
        self.assertEqual(display_status("real", "PASS", real_executed=True), "REAL PASS")
        self.assertEqual(display_status("real", "PASS", real_executed=False), "PASS")


class PersistResumeTests(unittest.TestCase):
    def test_incremental_persist_and_resume(self):
        run_id = "test-resume-harness"
        out = report_dir(run_id)
        payload = {
            "run_id": run_id,
            "mode": "real",
            "base_head": "x",
            "cases": [
                {"id": "LC-01", "status": "PASS", "reason": "ok"},
                {"id": "LC-02", "status": "FAIL", "reason": "nope"},
            ],
            "metrics_totals": {},
            "paid_resources": {
                "runpod_calls": 0,
                "tinyfish_agent_calls": 0,
                "tinyfish_browser_calls": 0,
                "tinyfish_search_fetch_calls": 0,
                "cost_usd": 0,
            },
        }
        persist_payload(out, payload)
        loaded = load_payload(out)
        self.assertEqual(len(loaded["cases"]), 2)
        self.assertTrue((out / "cases" / "LC-01.json").is_file())
        self.assertEqual(terminal_ids(loaded), {"LC-01", "LC-02"})
        self.assertEqual(terminal_ids(loaded, rerun_failed=True), {"LC-01"})

    def test_fail_fast_skip_is_not_terminal(self):
        payload = {
            "cases": [
                {"id": "WM-01", "status": "FAIL", "reason": "workspace/safety violation"},
                {"id": "WM-02", "status": "SKIPPED", "reason": "SKIPPED: fail-fast (workspace escape write)"},
                {"id": "LC-08", "status": "SKIPPED", "reason": "jq already installed"},
            ]
        }
        self.assertEqual(terminal_ids(payload), {"WM-01", "LC-08"})
        self.assertEqual(terminal_ids(payload, rerun_failed=True), {"LC-08"})

    def test_hydrate_paid_restores_prior_cost(self):
        paid = PaidConfig(allow_runpod=True, runpod_budget_usd=1.20)
        hydrate_paid(
            paid,
            {
                "paid_resources": {"runpod_usd": 0.11, "tinyfish_browser_usd": 0.0, "runpod_calls": 1},
                "cases": [{"id": "WM-01", "runpod_cost_usd": 0.11}],
            },
        )
        self.assertAlmostEqual(paid.prior_runpod_usd, 0.11)
        self.assertAlmostEqual(paid.spent_runpod_usd, 0.11)

    def test_crash_keeps_prior_cases(self):
        run_id = "test-crash-harness"
        out = report_dir(run_id)
        persist_payload(
            out,
            {
                "run_id": run_id,
                "mode": "real",
                "base_head": "x",
                "cases": [{"id": "WM-01", "status": "PASS", "reason": "saved before crash"}],
                "metrics_totals": {},
                "paid_resources": {
                    "runpod_calls": 0,
                    "tinyfish_agent_calls": 0,
                    "tinyfish_browser_calls": 0,
                    "tinyfish_search_fetch_calls": 0,
                    "cost_usd": 0,
                },
            },
        )
        self.assertEqual(load_payload(out)["cases"][0]["id"], "WM-01")


class CleanupTests(unittest.TestCase):
    def test_workspace_cleanup_only_under_eval_work(self):
        from paths import WORK

        run_id = "test-cleanup-ws"
        workspace = WORK / run_id / "LC-99"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "x.txt").write_text("x", encoding="utf-8")
        state = new_state({"id": "LC-99", "cleanup": {"strategy": "delete_eval_workspace_only"}}, workspace)
        result = cleanup_task({"id": "LC-99", "cleanup": {"strategy": "delete_eval_workspace_only"}}, state)
        self.assertEqual(result["status"], "CLEAN")
        self.assertFalse(workspace.exists())
        cleanup_run(run_id)
        self.assertFalse((WORK / run_id).exists())

    def test_refuses_delete_outside_eval_work(self):
        outside = Path(tempfile.mkdtemp(prefix="eval-protected-"))
        state = {"workspace": str(outside), "owned_pids": []}
        result = cleanup_task({"cleanup": {}}, state)
        self.assertEqual(result["status"], "REFUSED")
        self.assertTrue(outside.exists())

    def test_process_cleanup(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        state = {"owned_pids": [proc.pid], "workspace": None}
        stop_owned_processes(state)
        time.sleep(0.3)
        self.assertIsNotNone(proc.poll())

    def test_browser_cleanup_posts_stop(self):
        client = FakeClient()
        closed = _close_browser_sessions(
            client,
            [{"tool_name": "web_browser", "result_metadata": {"session_id": "sess-1"}, "input_summary": {}}],
        )
        self.assertTrue(closed)
        self.assertTrue(any("browser/sess-1/stop" in path for path in client.posts))


class FailFastAndTimeoutTests(unittest.TestCase):
    def test_fail_fast_unexpected_agent(self):
        state = {"metrics": {"total_tool_calls": 1}, "tinyfish_agent_calls": 1, "workspace_violation_paths": []}
        self.assertEqual(fail_fast_reason(state, {"agent_allowed": False}), "unexpected paid Agent call")

    def test_fail_fast_runaway(self):
        state = {"runaway": True, "metrics": {"total_tool_calls": 41}, "workspace_violation_paths": []}
        self.assertEqual(fail_fast_reason(state), "tool runaway")

    def test_relative_write_is_not_workspace_escape(self):
        workspace = tempfile.mkdtemp(prefix="eval-ws-")
        self.assertFalse(workspace_violation("notes.txt", workspace, [workspace]))
        self.assertIsNone(
            fail_fast_reason({"metrics": {"total_tool_calls": 2}, "workspace_violation_paths": ["notes.txt"]})
        )
        work_path = str(Path(__file__).resolve().parents[1] / ".work" / "run" / "notes.txt")
        self.assertIsNone(
            fail_fast_reason({"metrics": {"total_tool_calls": 2}, "workspace_violation_paths": [work_path]})
        )

    def test_desktop_escape_still_fail_fast(self):
        path = str(Path.home() / "Desktop" / "pause-a.txt")
        self.assertEqual(
            fail_fast_reason({"metrics": {"total_tool_calls": 1}, "workspace_violation_paths": [path]}),
            "workspace escape write",
        )

    def test_cli_real_without_flag_exits_2(self):
        code = main(["--mode", "real", "--task", "LC-01"])
        self.assertEqual(code, 2)

    def test_injected_runtime_real_pass_label(self):
        fake = FakeRuntime(provider="llamacpp", mock=False)
        run_id = f"test-real-pass-label-{int(time.time() * 1000)}"
        code = main(
            [
                "--mode",
                "real",
                "--task",
                "LC-01",
                "--allow-runpod",
                "--runpod-budget-usd",
                "1.20",
                "--keep-work",
                "--resume",
                run_id,
            ],
            runtime=fake,
        )
        self.assertEqual(code, 0)
        self.assertEqual(fake.executed, ["LC-01"])

    def test_injected_mock_provider_is_not_real_pass(self):
        fake = FakeRuntime(provider="mock", mock=True)
        run_id = f"test-mock-provider-label-{int(time.time() * 1000)}"
        code = main(
            [
                "--mode",
                "real",
                "--task",
                "LC-01",
                "--allow-runpod",
                "--runpod-budget-usd",
                "1.20",
                "--resume",
                run_id,
            ],
            runtime=fake,
        )
        self.assertEqual(code, 0)
        payload = json.loads((report_dir(run_id) / "results.json").read_text(encoding="utf-8"))
        row = payload["cases"][0]
        self.assertNotEqual(row.get("display_status"), "REAL PASS")
        self.assertFalse(row.get("real_executed"))

    def test_timeout_becomes_partial(self):
        fake = FakeRuntime(timeout_ids={"LC-01"})
        run_id = f"test-timeout-partial-{int(time.time() * 1000)}"
        code = main(
            [
                "--mode",
                "real",
                "--task",
                "LC-01",
                "--allow-runpod",
                "--runpod-budget-usd",
                "1.20",
                "--resume",
                run_id,
            ],
            runtime=fake,
        )
        self.assertEqual(code, 0)
        payload = json.loads((report_dir(run_id) / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["cases"][0]["status"], "PARTIAL")
        self.assertTrue(payload["cases"][0]["evidence"].get("timeout"))

    def test_resume_skips_terminal(self):
        run_id = f"test-resume-cli-{int(time.time() * 1000)}"
        fake = FakeRuntime()
        code = main(
            [
                "--mode",
                "real",
                "--task",
                "LC-01",
                "--allow-runpod",
                "--runpod-budget-usd",
                "1.20",
                "--resume",
                run_id,
            ],
            runtime=fake,
        )
        self.assertEqual(code, 0)
        first_n = len(fake.executed)
        code = main(
            [
                "--mode",
                "real",
                "--task",
                "LC-01",
                "--allow-runpod",
                "--runpod-budget-usd",
                "1.20",
                "--resume",
                run_id,
            ],
            runtime=fake,
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(fake.executed), first_n)


class SkipPolicyTests(unittest.TestCase):
    def test_lc08_skipped(self):
        self.assertIn("jq", skip_reason({"id": "LC-08"}))

    def test_agent_cases_skipped(self):
        self.assertIn("Agent", skip_reason({"id": "WB-06"}))


if __name__ == "__main__":
    unittest.main()
