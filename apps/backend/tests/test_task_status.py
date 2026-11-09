"""The task status the UI renders: one authority, and it never over-claims.

The reported defect was a screen that said all of these at once: «Ожидаем первый ответ модели»,
«Модель обрабатывает запрос… 680 с», «Working», «3/3 steps» и «Завершено». Three independent
sources each described a different level — transport, plan progress and one tool family — and
the user could not tell whether the task was over.

These tests pin the payload contract: `phase` is derived from the persisted row and its journal,
a finished step counter is not an outcome, and a limit names the budget that ran out.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

from app.tools.local import machine
from app.tools.local import status as task_status

NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)


def row(status, *, last_error=None, tool_calls_used=0, facts=None, stop_requested=False, quiet=5):
    return SimpleNamespace(
        id="task-1",
        status=status,
        current_phase=status,
        last_error=last_error,
        tool_calls_used=tool_calls_used,
        facts=facts or {},
        stop_requested=stop_requested,
        pause_requested=False,
        current_step="",
        title="Fix the parser",
        tool_budget=40,
        files_changed=0,
        file_change_budget=20,
        elapsed_runtime=100,
        runtime_budget=1800,
        started_at=NOW - timedelta(minutes=10),
        updated_at=NOW - timedelta(seconds=quiet),
        finished_at=None,
    )


def step(title, state):
    return SimpleNamespace(title=title, status=state)


# ------------------------------------------------------------------ the phase is the outcome


def test_plan_progress_is_not_task_completion():
    """Test B: 3/3 steps with verification still running is VERIFYING, never COMPLETED."""
    described = task_status.describe(
        row(machine.VERIFYING),
        [step("Inspect", "COMPLETED"), step("Write", "COMPLETED"), step("Verify", "COMPLETED")],
        reference=NOW,
    )
    assert described["phase"] == "verifying"
    assert described["terminal"] is False
    assert described["completed_count"] == 3
    assert described["remaining_count"] == 0


def test_a_finished_web_search_never_finishes_the_task():
    """Test A: the web family being done says nothing about the whole task."""
    described = task_status.describe(
        row(machine.EXECUTING),
        [step("Research", "COMPLETED"), step("Build", "RUNNING")],
        reference=NOW,
    )
    assert described["phase"] == "executing"
    assert described["terminal"] is False
    assert described["current_action"] == "Build"


def test_only_a_closed_task_is_completed():
    described = task_status.describe(
        row(machine.COMPLETED),
        [step("Inspect", "COMPLETED"), step("Write", "COMPLETED")],
        reference=NOW,
    )
    assert described["phase"] == "completed"
    assert described["terminal"] is True
    assert described["resume_available"] is False


def test_a_failed_task_with_real_work_is_partial_not_empty():
    described = task_status.describe(
        row(machine.FAILED, last_error="tests_not_verified", tool_calls_used=7),
        [step("Inspect", "COMPLETED"), step("Verify", "PENDING")],
        reference=NOW,
    )
    assert described["phase"] == "partial"
    assert described["stop_reason"] == "tests_not_verified"
    assert described["remaining_count"] == 1
    assert described["resume_available"] is True


def test_a_failed_task_that_did_nothing_stays_failed():
    described = task_status.describe(
        row(machine.FAILED, last_error="no_actions"),
        [step("Inspect", "PENDING")],
        reference=NOW,
    )
    assert described["phase"] == "failed"


def test_skipped_steps_are_not_completed_work():
    """A plan the controller skipped did nothing; that is a failure, not a partial success."""
    described = task_status.describe(
        row(machine.FAILED, last_error="no_actions", tool_calls_used=0),
        [step("Inspect", "SKIPPED"), step("Write", "SKIPPED")],
        reference=NOW,
    )
    assert described["phase"] == "failed"
    assert described["worked_count"] == 0


def test_a_stop_is_a_cancellation_not_a_failure():
    described = task_status.describe(
        row(machine.STOPPED, stop_requested=True),
        [step("Inspect", "COMPLETED"), step("Write", "PENDING")],
        reference=NOW,
    )
    assert described["phase"] == "cancelled"
    assert described["stop_reason"] == "user"
    assert described["resume_available"] is True


# ------------------------------------------------------------ waits say what they wait for


def test_waiting_for_compute_is_not_a_model_outage():
    described = task_status.describe(
        row(machine.WAITING_LLM, last_error="loading_model", facts={"waiting_for_compute": True}),
        [step("Inspect", "PENDING")],
        reference=NOW,
    )
    assert described["phase"] == "waiting_resource"
    assert described["waiting"] is True


def test_a_dead_provider_is_a_model_wait_with_a_real_idle_time():
    """Test C: no first token for 680 s is reported as a model wait, with the idle time."""
    described = task_status.describe(
        row(machine.WAITING_LLM, last_error="llm_timeout", quiet=680),
        [step("Inspect", "PENDING")],
        reference=NOW,
    )
    assert described["phase"] == "waiting_model"
    assert described["stalled"] is True
    assert described["idle_seconds"] >= 180
    assert described["error_code"] == "llm_timeout"


def test_waiting_for_permission_is_never_stalled():
    described = task_status.describe(
        row(machine.WAITING_CONFIRMATION),
        [step("Write", "RUNNING")],
        reference=NOW,
    )
    assert described["phase"] == "waiting_permission"
    assert described["stalled"] is False
    assert described["resume_available"] is True


def test_activity_comes_from_the_journal_not_the_page_timer():
    last_event = NOW - timedelta(seconds=3)
    described = task_status.describe(
        row(machine.EXECUTING),
        [step("Build", "RUNNING")],
        last_event_at=last_event,
        reference=NOW,
    )
    assert described["last_progress_at"] == last_event.isoformat()
    assert described["idle_seconds"] == 3
    assert described["stalled"] is False


def test_a_limit_names_the_budget_that_ran_out():
    """Test E: the payload carries the real counters, not a bare code."""
    described = task_status.describe(
        row(
            machine.RESEARCHING,
            facts={
                "last_limit": {
                    "code": "page_limit",
                    "fetches": 3,
                    "max_fetch": 12,
                    "pages": 10,
                    "max_pages": 10,
                }
            },
        ),
        [step("Research", "RUNNING")],
        reference=NOW,
    )
    assert described["limit"]["pages"] == 10
    assert described["limit"]["max_pages"] == 10
    assert described["limit"]["fetches"] == 3


# --------------------------------------------------------------------------- page budgets


def test_a_tor_fetch_never_spends_the_direct_page_budget():
    """Defect: «0 Fetch» next to «Достигнут лимит загруженных страниц»."""
    from app.tools.policy import ToolLimits

    class Definition:
        def __init__(self, capability):
            self.capability = capability

    limits = ToolLimits(max_pages=10, max_fetch=12)
    limits.consume(Definition("tor_fetch"), SimpleNamespace(urls=["a", "b", "c"]))

    assert limits.tor_pages == 3
    assert limits.pages == 0
    assert limits.fetches == 0

    limits.consume(Definition("fetch"), SimpleNamespace(urls=["d", "e", "f"]))
    assert limits.pages == 3
    assert limits.fetches == 1


def test_a_task_page_budget_is_not_smaller_than_its_fetch_budget():
    """A legal 12-fetch task must not be refused by an unnamed 10-page cap first."""
    from app.config import Settings

    settings = Settings()
    assert settings.tools_task_max_pages >= settings.tools_task_max_fetch
    # One fetch may ask for three URLs, so the page budget also has to admit three of them.
    assert settings.tools_task_max_pages >= settings.tools_task_max_fetch * 3


# ------------------------------------------------------------- the real route, not a helper


def _make_task(client, *, state, steps, tool_calls_used=0, facts=None):
    from app.database import SessionLocal
    from app.models import User, now
    from app.tools.models import LocalTask, TaskStep

    with SessionLocal() as db:
        user = db.scalar(select(User))
        task = LocalTask(
            user_id=user.id,
            status=state,
            current_phase=state,
            title="Fix the parser",
            original_user_request="Исправь парсер и проверь тесты",
            tool_calls_used=tool_calls_used,
            facts=facts or {},
            started_at=now(),
            updated_at=now(),
        )
        db.add(task)
        db.flush()
        for index, (title, step_status) in enumerate(steps):
            db.add(
                TaskStep(
                    task_id=task.id,
                    position=index,
                    title=title,
                    status=step_status,
                )
            )
        task_id = task.id
        db.commit()
    return task_id


def test_the_tasks_route_publishes_the_authoritative_phase(client, auth):
    headers = auth()
    task_id = _make_task(
        client,
        state=machine.RESEARCHING,
        steps=[("Research", "COMPLETED"), ("Build", "RUNNING")],
    )

    payload = client.get(f"/tasks/{task_id}", headers=headers).json()

    assert payload["phase"] == "researching"
    assert payload["terminal"] is False
    assert payload["current_action"] == "Build"
    assert payload["completed_count"] == 1
    assert payload["remaining_count"] == 1
    assert payload["last_progress_at"]
    assert payload["budgets"]["tools_max"] > 0
    assert "web" in payload


def test_every_plan_step_done_is_still_not_completed_on_the_route(client, auth):
    headers = auth()
    task_id = _make_task(
        client,
        state=machine.VERIFYING,
        steps=[("Research", "COMPLETED"), ("Build", "COMPLETED"), ("Verify", "COMPLETED")],
    )

    payload = client.get(f"/tasks/{task_id}", headers=headers).json()

    assert payload["status"] == machine.VERIFYING
    assert payload["phase"] == "verifying"
    assert payload["completed_count"] == 3
    assert payload["remaining_count"] == 0
    assert payload["terminal"] is False


def test_the_journal_restores_the_timeline_after_a_restart(client, auth):
    """Test F: a relaunched UI rebuilds the run from the backend, not from a component timer."""
    from app.database import SessionLocal
    from app.tools.local.journal import append_event

    headers = auth()
    task_id = _make_task(
        client,
        state=machine.EXECUTING,
        steps=[("Research", "COMPLETED"), ("Build", "RUNNING")],
    )
    with SessionLocal() as db:
        append_event(db, task_id, "TASK_CREATED", {"title": "Fix the parser"})
        append_event(db, task_id, "STEP_COMPLETED", {"summary": "research done"})
        append_event(db, task_id, "STEP_STARTED", {"summary": "build started"})
        db.commit()

    # A fresh request is a fresh session: nothing is cached in the old component.
    payload = client.get(f"/tasks/{task_id}", headers=headers).json()

    kinds = [event["kind"] for event in payload["events"]]
    assert kinds == ["TASK_CREATED", "STEP_COMPLETED", "STEP_STARTED"]
    assert all(event["created_at"] for event in payload["events"])
    assert payload["phase"] == "executing"
    assert payload["terminal"] is False
    assert payload["last_progress_at"]
