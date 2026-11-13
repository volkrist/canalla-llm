"""Model provenance: a trace must distinguish a real model from a mock or a script.

These tests build journals with the product's own recorder and read them back with the
validator. A synthetic trace is *not* a LIVE run: it only proves that the classification rules
discriminate. The LIVE verdict is produced by classifying a real task journal after a real model
turn, and nothing here is reported as such.
"""

from __future__ import annotations

from sqlalchemy import select

from app.agent import provenance
from app.agent import queue as agent_queue
from app.config import get_settings
from app.database import SessionLocal
from app.models import User
from app.tools.local.journal import append_event
from app.tools.models import ToolRun

SECRET = get_settings().jwt_secret


def _task(db, *, model_run=True):
    user = db.scalar(select(User).limit(1))
    if user is None:
        user = User(email="provenance@example.com", password_hash="test-only")
        db.add(user)
        db.commit()
    from app.models import Chat

    chat = Chat(user_id=user.id, title="provenance")
    db.add(chat)
    db.flush()
    task = agent_queue.enqueue(db, user_id=user.id, prompt="prove the tool call")
    if model_run:
        db.add(
            ToolRun(
                user_id=user.id,
                chat_id=chat.id,
                tool_name="write_file",
                provider="local_device",
                risk_level="normal",
                input_digest=provenance.arguments_digest({"path": "a.txt", "content": "hi"}, "write_file"),
                result_digest="c" * 64,
                origin="model",
                status="completed",
                task_id=task.id,
            )
        )
    db.commit()
    return task


def _tool_run(db, task_id):
    return db.scalar(select(ToolRun).where(ToolRun.task_id == task_id))


def _write_trace(db, task_id, *, live=True, provider="llamacpp", response_id="resp-1", tool_run_id=""):
    call_id = "call-1"
    args_digest = provenance.arguments_digest({"path": "a.txt", "content": "hi"}, "write_file")
    provenance.record(
        db,
        task_id,
        provenance.MODEL_RESPONSE,
        provenance.response_payload(
            model_request_id="req-1",
            provider=provider,
            model="qwen3.8-27b",
            response_id=response_id,
            live=live,
            tool_call_ids=[call_id],
            task_id=task_id,
            step=1,
            runtime="llama.cpp" if provider == "llamacpp" else "gateway",
        ),
        SECRET,
    )
    provenance.record(
        db,
        task_id,
        provenance.MODEL_TOOL_CALL,
        {
            "model_request_id": "req-1",
            "provider": provider,
            "model": "qwen3.8-27b",
            "response_id": response_id,
            "tool_call_id": call_id,
            "tool": "write_file",
            "arguments_digest": args_digest,
            "task_id": task_id,
            "step": 1,
        },
        SECRET,
    )
    provenance.record(
        db,
        task_id,
        provenance.TOOL_RESULT,
        {
            "tool_call_id": call_id,
            "tool": "write_file",
            "arguments_digest": args_digest,
            "executed": True,
            "result_digest": "c" * 64,
            "tool_run_id": tool_run_id,
            "task_id": task_id,
        },
        SECRET,
        tool_run_id or None,
    )
    provenance.record(
        db,
        task_id,
        provenance.VERIFY_PASSED,
        {"task_id": task_id, "evidence_ok": True, "summary": "file exists"},
        SECRET,
    )
    db.commit()
    return call_id, args_digest


def _classify(task_id, **kwargs):
    with SessionLocal() as db:
        events, runs = provenance.from_task(db, task_id)
    return provenance.classify(events, secret=SECRET, **kwargs)


def test_a_live_chain_with_verification_is_confirmed():
    with SessionLocal() as db:
        task = _task(db)
        _write_trace(db, task.id, tool_run_id=_tool_run(db, task.id).id)
        events, runs = provenance.from_task(db, task.id)
    verdict = provenance.classify(events, secret=SECRET, tool_runs=runs)
    assert verdict.status == provenance.REAL_MODEL_CONFIRMED
    assert verdict.ok is True
    assert verdict.live_responses == 1
    assert verdict.linked_results == 1
    assert verdict.providers == ["llamacpp"]


def test_a_mock_turn_is_never_confirmed():
    with SessionLocal() as db:
        task = _task(db)
        _write_trace(db, task.id, live=False, provider="mock", response_id="")
        events, runs = provenance.from_task(db, task.id)
    verdict = provenance.classify(events, secret=SECRET, tool_runs=runs)
    assert verdict.status == provenance.MOCK_MODEL
    assert verdict.ok is False


def test_tools_without_a_model_response_are_a_scripted_planner():
    with SessionLocal() as db:
        task = _task(db, model_run=False)
        append_event(db, task.id, "TOOL_COMPLETED", {"tool": "write_file"})
        db.commit()
        events, runs = provenance.from_task(db, task.id)
    verdict = provenance.classify(events, secret=SECRET, tool_runs=runs)
    assert verdict.status == provenance.SCRIPTED_PLANNER
    assert verdict.ok is False


def test_an_origin_model_run_without_a_model_response_is_invalid():
    with SessionLocal() as db:
        task = _task(db)
        append_event(db, task.id, "TOOL_COMPLETED", {"tool": "write_file"})
        db.commit()
        events, runs = provenance.from_task(db, task.id)
    verdict = provenance.classify(events, secret=SECRET, tool_runs=runs)
    # The `_task` fixture writes one origin=model ToolRun, so a missing MODEL_RESPONSE is a
    # provenance violation rather than an honest script.
    assert verdict.status == provenance.INVALID_PROVENANCE


def test_a_missing_tool_result_is_incomplete():
    with SessionLocal() as db:
        task = _task(db)
        _write_trace(db, task.id, tool_run_id=_tool_run(db, task.id).id)
        # Drop the result and the verification: a proposed call is not a performed one.
        from app.tools.models import TaskEvent

        for row in db.scalars(select(TaskEvent).where(TaskEvent.task_id == task.id)).all():
            if row.kind in {"TOOL_RESULT", "VERIFY_PASSED"}:
                db.delete(row)
        db.commit()
        events, runs = provenance.from_task(db, task.id)
    verdict = provenance.classify(events, secret=SECRET, tool_runs=runs)
    assert verdict.status == provenance.INCOMPLETE_TRACE
    assert not any(item["kind"] == "TOOL_RESULT" for item in verdict.chain)


def test_a_tool_call_not_declared_by_the_response_is_invalid():
    with SessionLocal() as db:
        task = _task(db)
        provenance.record(
            db,
            task.id,
            provenance.MODEL_RESPONSE,
            provenance.response_payload(
                model_request_id="req-2",
                provider="llamacpp",
                model="qwen3.8-27b",
                response_id="resp-2",
                live=True,
                tool_call_ids=["other-call"],
                task_id=task.id,
                step=1,
            ),
            SECRET,
        )
        provenance.record(
            db,
            task.id,
            provenance.MODEL_TOOL_CALL,
            {
                "model_request_id": "req-2",
                "provider": "llamacpp",
                "tool_call_id": "call-x",
                "tool": "write_file",
                "arguments_digest": "d" * 64,
            },
            SECRET,
        )
        db.commit()
        events, runs = provenance.from_task(db, task.id)
    verdict = provenance.classify(events, secret=SECRET, tool_runs=runs)
    assert verdict.status == provenance.INVALID_PROVENANCE


def test_a_tampered_record_fails_the_seal():
    with SessionLocal() as db:
        task = _task(db)
        _write_trace(db, task.id, tool_run_id=_tool_run(db, task.id).id)
        from app.tools.models import TaskEvent

        row = db.scalars(
            select(TaskEvent).where(TaskEvent.task_id == task.id, TaskEvent.kind == provenance.MODEL_RESPONSE)
        ).first()
        row.payload = {**row.payload, "provider": "mock"}
        db.commit()
        events, runs = provenance.from_task(db, task.id)
    verdict = provenance.classify(events, secret=SECRET, tool_runs=runs)
    assert verdict.status == provenance.INVALID_PROVENANCE
    assert verdict.tampered >= 1


def test_a_tool_result_without_a_call_is_invalid():
    with SessionLocal() as db:
        task = _task(db)
        _write_trace(db, task.id, tool_run_id=_tool_run(db, task.id).id)
        provenance.record(
            db,
            task.id,
            provenance.TOOL_RESULT,
            {
                "tool_call_id": "ghost",
                "tool": "write_file",
                "arguments_digest": "e" * 64,
                "executed": True,
                "result_digest": "f" * 64,
            },
            SECRET,
        )
        db.commit()
        events, runs = provenance.from_task(db, task.id)
    verdict = provenance.classify(events, secret=SECRET, tool_runs=runs)
    assert verdict.status == provenance.INVALID_PROVENANCE


def test_the_ledger_is_cross_checked_against_the_journal():
    with SessionLocal() as db:
        task = _task(db)
        _write_trace(db, task.id, tool_run_id=_tool_run(db, task.id).id)
        run = db.scalar(select(ToolRun).where(ToolRun.task_id == task.id))
        run.input_digest = "9" * 64  # the ledger disagrees with the sealed call
        db.commit()
        events, runs = provenance.from_task(db, task.id)
    verdict = provenance.classify(events, secret=SECRET, tool_runs=runs)
    assert verdict.status == provenance.INVALID_PROVENANCE


# ------------------------------------------------------------------- orchestrator recording path
def _recorder_ctx(task, run):
    from types import SimpleNamespace

    return SimpleNamespace(
        task_id=task.id,
        provenance_step=0,
        model_provenance={},
        current_tool_call_id="",
        run_id=run.id,
    )


def test_the_orchestrator_records_the_chain_for_a_declared_live_adapter():
    """Wiring contract, not a LIVE run: no model is contacted and nothing here is a pass.

    It proves the recorder writes a chain the validator can confirm, so a real run has no
    latent recording bug. The LIVE verdict still requires a real provider turn.
    """
    from app.tools.orchestrator import ToolOrchestrator

    with SessionLocal() as db:
        task = _task(db)
        run = _tool_run(db, task.id)
        args = {"path": "a.txt", "content": "hi"}
        digest = provenance.arguments_digest(args, "write_file")
        run.input_digest = digest
        db.commit()

        orchestrator = ToolOrchestrator(None, None)
        ctx = _recorder_ctx(task, run)
        provider = type(
            "StubLive",
            (),
            {"provenance_name": "llamacpp", "live_model": True, "model": "qwen3.8-27b"},
        )()
        decision = {
            "tool_calls": [
                {
                    "id": "call-w",
                    "type": "function",
                    "function": {"name": "write_file", "arguments": '{"path": "a.txt"}'},
                }
            ],
            "_provenance": {
                "model_request_id": "req-w",
                "provider": "llamacpp",
                "model": "qwen3.8-27b",
                "response_id": "resp-w",
                "live": True,
                "runtime": "llama.cpp",
            },
        }
        orchestrator._record_model_response(ctx, provider, decision)
        ctx.current_tool_call_id = "call-w"
        orchestrator._record_tool_call(ctx, "write_file", digest)
        orchestrator._record_tool_result(ctx, "write_file", digest, True, digest="c" * 64)
        orchestrator._record_verification(ctx, "verify_result", {"metadata": {"ok": True}})

        events, runs = provenance.from_task(db, task.id)

    verdict = provenance.classify(events, secret=SECRET, tool_runs=runs)
    assert verdict.status == provenance.REAL_MODEL_CONFIRMED
    assert verdict.tool_calls == 1 and verdict.linked_results == 1
    assert verdict.providers == ["llamacpp"] and verdict.models == ["qwen3.8-27b"]


def test_the_same_recording_path_never_confirms_an_unrecognised_adapter():
    from app.tools.orchestrator import ToolOrchestrator

    with SessionLocal() as db:
        task = _task(db)
        run = _tool_run(db, task.id)
        digest = provenance.arguments_digest({}, "write_file")
        orchestrator = ToolOrchestrator(None, None)
        ctx = _recorder_ctx(task, run)
        provider = type(
            "StubDoubt", (), {"provenance_name": "contract-double", "live_model": True, "model": "x"}
        )()
        decision = {
            "tool_calls": [
                {"id": "call-d", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}
            ],
            "_provenance": {
                "model_request_id": "req-d",
                "provider": "contract-double",
                "model": "x",
                "response_id": "resp-d",
                "live": True,
                "runtime": "contract",
            },
        }
        orchestrator._record_model_response(ctx, provider, decision)
        ctx.current_tool_call_id = "call-d"
        orchestrator._record_tool_call(ctx, "write_file", digest)
        orchestrator._record_tool_result(ctx, "write_file", digest, True, digest="c" * 64)
        orchestrator._record_verification(ctx, "verify_result", {"metadata": {"ok": True}})
        events, runs = provenance.from_task(db, task.id)

    verdict = provenance.classify(events, secret=SECRET, tool_runs=runs)
    assert verdict.status == provenance.INVALID_PROVENANCE
    assert verdict.ok is False


def test_provenance_arguments_digest_matches_the_ledger():
    """The chain is only verifiable if both sides hash the same material."""
    from app.tools.security import digest as ledger_digest

    args = {"path": "a.txt", "content": "hi"}
    assert provenance.arguments_digest(args, "write_file") == ledger_digest(args, "write_file")
    other = {"query": "пример", "limit": 3}
    assert provenance.arguments_digest(other, "web_search") == ledger_digest(other, "web_search")
