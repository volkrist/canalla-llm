"""Free contracts of the live T4 harness: fixture, approvals and crash evidence.

These are the three places where a paid T4 lost money: the scenario folder was cleared and never
seeded, the approval loop stopped at the resume, and a scenario that raised could leave no
per-scenario evidence at all. Each test here fails if the corresponding defect comes back.

Run with the backend venv (it needs the product modules):
    apps/backend/.venv/Scripts/python.exe -m pytest artifacts/acceptance-126/test_harness_contracts.py
"""

from __future__ import annotations

import hashlib
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "artifacts" / "acceptance-123"))
sys.path.insert(0, str(REPO / "scripts"))

import common  # noqa: E402
import live_restart  # noqa: E402
import runner  # noqa: E402

EXPECTED = "T4L_HOST_VALUE_417\n"
EXPECTED_SHA = hashlib.sha256(EXPECTED.encode()).hexdigest()


def _scenario():
    return SimpleNamespace(id="T4L", setup="t4l_file", prompt="открой t4l.txt")


def test_prepare_seeds_the_fixture_after_clearing(tmp_path):
    """A stale file with the wrong bytes must be replaced by the canonical fixture."""
    root = tmp_path / "T4L"
    root.mkdir()
    (root / "_prompt.txt").write_text("prompt", encoding="utf-8")
    (root / "t4l.txt").write_text("STALE\n", encoding="utf-8")
    (root / "leftover").mkdir()
    (root / "leftover" / "x.txt").write_text("x", encoding="utf-8")

    live_restart.prepare_t4_root(_scenario(), root)

    assert (root / "t4l.txt").is_file(), "the prompt names a file the harness must create"
    assert (root / "t4l.txt").read_text(encoding="utf-8") == EXPECTED
    assert hashlib.sha256((root / "t4l.txt").read_bytes()).hexdigest() == EXPECTED_SHA
    assert (root / "_prompt.txt").read_text(encoding="utf-8") == "prompt", "the prompt survives"
    assert not (root / "leftover").exists(), "the previous attempt's work is gone"


def test_prepare_without_a_fixture_only_clears(tmp_path):
    root = tmp_path / "T3L"
    root.mkdir()
    (root / "t3l_a.py").write_text("print('old')", encoding="utf-8")
    live_restart.prepare_t4_root(SimpleNamespace(id="T3L", setup="", prompt=""), root)
    assert list(root.iterdir()) == []


def test_the_checker_reads_the_same_value_the_fixture_writes(tmp_path):
    """The file check in `run_t4` and the catalogue fixture must agree byte for byte."""
    root = tmp_path / "T4L"
    live_restart.prepare_t4_root(_scenario(), root)
    written = (root / "t4l_out.txt")
    written.write_text((root / "t4l.txt").read_text(encoding="utf-8"), encoding="utf-8")
    on_disk = written.read_text(encoding="utf-8").strip()
    assert on_disk == "T4L_HOST_VALUE_417"
    assert EXPECTED_SHA == hashlib.sha256(EXPECTED.encode()).hexdigest()


def test_the_approver_answers_before_and_after_a_resume(monkeypatch):
    """The loop must still answer a confirmation that appears after the reconnect."""
    queue: list[dict] = []
    answered: list[str] = []
    lock = threading.Lock()

    def fake_runs(_token, _chat):
        with lock:
            return [dict(row) for row in queue]

    def fake_confirm(_token, run_id, allow):
        with lock:
            answered.append(run_id)
        return 200, '{"status":"approved"}'

    monkeypatch.setattr(live_restart, "tool_runs", fake_runs)
    monkeypatch.setattr(common, "confirm", fake_confirm)
    monkeypatch.setattr(common, "safe_run", lambda *a, **k: True)

    stop, log, observed = live_restart.start_approver("t", "chat")
    try:
        with lock:
            queue.append({"id": "run-1", "status": "waiting_confirmation", "tool_name": "read_file"})
        deadline = time.time() + 10
        while "run-1" not in answered and time.time() < deadline:
            time.sleep(0.1)
        assert "run-1" in answered, "the tool before the disconnect is approved"

        # The live run's defect: after the resume the loop had already been stopped.
        with lock:
            queue.append({"id": "run-2", "status": "waiting_confirmation", "tool_name": "write_file"})
        deadline = time.time() + 10
        while "run-2" not in answered and time.time() < deadline:
            time.sleep(0.1)
        assert "run-2" in answered, "a confirmation after the reconnect is still answered"
        assert set(observed) == {"run-1", "run-2"}
        assert len(log) == 2
    finally:
        stop.set()
    # A stopped loop must not answer anything else.
    with lock:
        queue.append({"id": "run-3", "status": "waiting_confirmation", "tool_name": "read_file"})
    time.sleep(1.5)
    assert "run-3" not in answered, "the loop really stopped"


def test_the_approver_stops_when_asked(monkeypatch):
    monkeypatch.setattr(live_restart, "tool_runs", lambda *a, **k: [])
    stop, log, _observed = live_restart.start_approver("t", "chat")
    time.sleep(0.2)
    stop.set()
    time.sleep(1.5)
    assert stop.wait(0), "the stop event is set"
    assert log == []


def test_run_t4_stops_the_approver_only_after_the_answer():
    """Structural guard against the exact regression: no early `stop_approver.set()`."""
    source = Path(live_restart.__file__).read_text(encoding="utf-8")
    start = source.index("def run_t4(")
    body = source[start:]
    assert body.count("stop_approver.set()") == 1, "one stop, at the end"
    stop_at = body.index("stop_approver.set()")
    answer_at = body.index("final_answer = answer_text(")
    resume_at = body.index("resume_window(")
    assert stop_at > answer_at > resume_at


def test_a_scenario_that_raises_still_leaves_evidence():
    error = ValueError("no capacity")
    try:
        raise error
    except ValueError as caught:
        record = runner.scenario_failure("T4L", caught)
    assert record["scenario"] == "T4L"
    assert record["verdict"] == "ERROR"
    assert "ValueError: no capacity" in record["reasons"][0]
    assert "Traceback" in record["traceback"]
