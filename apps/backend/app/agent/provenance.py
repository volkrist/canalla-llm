"""Verifiable provenance: which model response produced which real tool execution.

`origin="model"` on a single row proves nothing on its own — a test, a harness or a bug could write
it. This module records a *chain* instead, and a validator that can read the chain back:

    MODEL_RESPONSE  (provider, model, response id, request id, the tool-call ids it proposed)
      -> MODEL_TOOL_CALL (the exact tool-call id, tool name, arguments digest)
        -> TOOL_RESULT (executed=true, tool_run_id, result digest)
          -> VERIFY_PASSED (an independent check observed the result, evidence_ok=true)

Every record is individually sealed with an HMAC over its canonical payload using the deployment's
own secret. An accidental or naive fabrication therefore fails validation, and switching a trace
from mock to live requires a deliberate change of the code path that produced it.

The validator is independent of the agent's own conclusion: it never reads the assistant's answer,
only the journal rows and the tool ledger. It classifies a trace as one of:

    REAL_MODEL_CONFIRMED / MOCK_MODEL / SCRIPTED_PLANNER / INCOMPLETE_TRACE / INVALID_PROVENANCE

`INCOMPLETE_TRACE` is deliberately not a pass: a model that proposed a tool but whose result never
came back has not done the work, and neither has a trace whose verification is missing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

MODEL_RESPONSE = "MODEL_RESPONSE"
MODEL_TOOL_CALL = "MODEL_TOOL_CALL"
TOOL_RESULT = "TOOL_RESULT"
VERIFY_PASSED = "VERIFY_PASSED"
VERIFY_FAILED = "VERIFY_FAILED"

PROVENANCE_KINDS = {MODEL_RESPONSE, MODEL_TOOL_CALL, TOOL_RESULT, VERIFY_PASSED, VERIFY_FAILED}

# Provider identifiers that describe a scripted or offline stand-in rather than an inference server.
MOCK_PROVIDERS = {"mock", "scripted", "fake", "fake-host", "test"}

# The only adapters whose claim of a live model is accepted. A new adapter must be added here on
# purpose, together with the runtime marker it writes; an unknown "live" claim is not confirmed.
REAL_PROVIDERS = {"llamacpp", "canalla-cloud"}
REAL_RUNTIMES = {"llama.cpp", "gateway"}

REAL_MODEL_CONFIRMED = "REAL_MODEL_CONFIRMED"
MOCK_MODEL = "MOCK_MODEL"
SCRIPTED_PLANNER = "SCRIPTED_PLANNER"
INCOMPLETE_TRACE = "INCOMPLETE_TRACE"
INVALID_PROVENANCE = "INVALID_PROVENANCE"


def _canonical(payload: Mapping[str, Any]) -> bytes:
    """A stable byte string: sorted keys, no whitespace, no NaN surprises."""
    return json.dumps(
        {str(key): payload[key] for key in sorted(payload)},
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8", "replace")


def seal(secret: str, payload: Mapping[str, Any]) -> str:
    return hmac.new(
        (secret or "").encode("utf-8", "replace"), _canonical(payload), hashlib.sha256
    ).hexdigest()


def _sealed(secret: str, payload: dict[str, Any]) -> dict[str, Any]:
    # `append_event` stores every value as a string, so the seal is computed over the same shape
    # the journal will actually persist. None is dropped, never stored as the word "None".
    body = {str(key): str(value) for key, value in payload.items() if key != "seal" and value is not None}
    return {**body, "seal": seal(secret, body)}


def digest_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8", "replace")).hexdigest()


def arguments_digest(arguments: Any, tool_name: str = "") -> str:
    """Digest of the *parsed* arguments, identical to the executor's ledger digest.

    The ledger hashes `{"action", "command", "arguments"}` (and the path when present) because a
    tool name is part of what was requested. The provenance records must use the same formula, or
    a live run would cross-check a genuinely linked call against a different digest and report
    `INVALID_PROVENANCE`. `test_provenance_arguments_digest_matches_the_ledger` pins this.
    """
    try:
        parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (TypeError, ValueError):
        parsed = arguments
    if not isinstance(parsed, (dict, list)):
        parsed = {"value": parsed}
    if tool_name:
        payload: dict[str, Any] = {
            "action": tool_name,
            "command": tool_name,
            "arguments": parsed,
        }
        if isinstance(parsed, dict) and parsed.get("path") is not None:
            payload["path"] = parsed["path"]
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return hashlib.sha256(_canonical(parsed)).hexdigest()


def result_digest(text: str, metadata: Mapping[str, Any] | None = None, errors: Any = None) -> str:
    material = {
        "text": (text or "")[:2000],
        "metadata": {
            key: (metadata or {}).get(key)
            for key in (
                "before_sha256",
                "after_sha256",
                "sha256",
                "digest",
                "exit_code",
                "files_changed",
                "pid",
                "path",
                "verified",
            )
            if (metadata or {}).get(key) is not None
        },
        "errors": list(errors or [])[:10],
    }
    return hashlib.sha256(_canonical(material)).hexdigest()


def response_payload(
    *,
    model_request_id: str,
    provider: str,
    model: str,
    response_id: str,
    live: bool,
    tool_call_ids: Iterable[str] = (),
    task_id: str = "",
    step: int = 0,
    elapsed_ms: int | None = None,
    runtime: str = "",
    endpoint_fingerprint: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    return {
        "model_request_id": str(model_request_id),
        "provider": str(provider),
        "model": str(model),
        "response_id": str(response_id),
        "live": bool(live),
        "tool_call_ids": ",".join(str(item) for item in tool_call_ids if item),
        "task_id": str(task_id),
        "step": int(step),
        "elapsed_ms": int(elapsed_ms) if elapsed_ms is not None else None,
        "runtime": str(runtime),
        "endpoint_fingerprint": str(endpoint_fingerprint),
        "request_id": str(request_id),
        "kind_version": 1,
    }


def normalize_provenance(provider, decision: Mapping[str, Any] | None) -> dict[str, Any]:
    """What the provider said about the turn, never what a caller wishes it were.

    A provider that does not attach provenance is recorded honestly as `unknown` and not live: an
    adapter must opt in, so a new adapter cannot silently inherit a live claim.
    """
    import uuid

    given = dict((decision or {}).get("_provenance") or {})
    calls = (decision or {}).get("tool_calls") or []
    ids = [str(call.get("id") or "") for call in calls if isinstance(call, dict)]
    name = str(given.get("provider") or getattr(provider, "provenance_name", "") or "unknown")
    live = given.get("live")
    if live is None:
        live = bool(getattr(provider, "live_model", False))
    return {
        # A stand-in provider may not produce an id; one is still generated so the journal rows
        # of a single turn can be linked. `live=False` is what keeps it out of a LIVE verdict.
        "model_request_id": str(given.get("model_request_id") or f"{name}-{uuid.uuid4().hex}"),
        "provider": name,
        "model": str(given.get("model") or getattr(provider, "model", "") or ""),
        "response_id": str(given.get("response_id") or ""),
        "live": bool(live),
        "runtime": str(given.get("runtime") or ""),
        "endpoint_fingerprint": str(given.get("endpoint_fingerprint") or ""),
        "tool_call_ids": ids,
        "elapsed_ms": given.get("elapsed_ms"),
    }


def record(db, task_id: str, kind: str, payload: Mapping[str, Any], secret: str, tool_run_id=None):
    """Append one sealed provenance row to the task journal."""
    from ..tools.local.journal import append_event  # lazy: this module stays importable alone

    row = append_event(db, task_id, kind, _sealed(secret, dict(payload)), (), tool_run_id)
    return row


# --------------------------------------------------------------------------------- validation


@dataclass
class Verdict:
    status: str
    reasons: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    model_responses: int = 0
    live_responses: int = 0
    tool_calls: int = 0
    linked_results: int = 0
    verified: bool = False
    tampered: int = 0
    chain: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == REAL_MODEL_CONFIRMED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ok": self.ok,
            "reasons": self.reasons,
            "providers": self.providers,
            "models": self.models,
            "model_responses": self.model_responses,
            "live_responses": self.live_responses,
            "tool_calls": self.tool_calls,
            "linked_results": self.linked_results,
            "verified": self.verified,
            "tampered": self.tampered,
            "chain": self.chain,
        }


def _flag(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _payload(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return dict(payload) if isinstance(payload, Mapping) else {}


def classify(
    events: Iterable[Mapping[str, Any]],
    *,
    secret: str = "",
    tool_runs: Iterable[Mapping[str, Any]] = (),
) -> Verdict:
    """Read a journal back and decide what actually produced the side effects.

    `events` are `task_events` rows as mappings (`kind`, `payload`, `tool_run_id`). `tool_runs`
    are the corresponding ledger rows (`id`, `origin`, `tool_name`, `input_digest`,
    `result_digest`, `status`); when supplied, they are cross-checked against the journal.
    """
    rows = [dict(event) for event in events]
    runs = {str(run.get("id")): dict(run) for run in tool_runs}

    responses: dict[str, dict] = {}
    model_calls: dict[str, dict] = {}
    results: dict[str, dict] = {}
    verification_ok = False
    tampered = 0
    tool_activity = False
    order: list[dict[str, Any]] = []
    providers: list[str] = []
    models: list[str] = []

    for row in rows:
        kind = str(row.get("kind") or "")
        if kind in {"TOOL_COMPLETED", "TOOL_REQUESTED", "FILE_CHANGED"}:
            tool_activity = True
        if kind not in PROVENANCE_KINDS:
            continue
        payload = _payload(row)
        if secret:
            given = str(payload.pop("seal", "") or "")
            if given != seal(secret, payload):
                tampered += 1
        if kind == MODEL_RESPONSE:
            request_id = str(payload.get("model_request_id") or "")
            responses[request_id] = payload
            if payload.get("provider") and payload["provider"] not in providers:
                providers.append(str(payload["provider"]))
            if payload.get("model") and payload["model"] not in models:
                models.append(str(payload["model"]))
            order.append({"kind": kind, "model_request_id": request_id})
        elif kind == MODEL_TOOL_CALL:
            model_calls[str(payload.get("tool_call_id") or "")] = payload
            if str(payload.get("provider") or "") and payload["provider"] not in providers:
                providers.append(str(payload["provider"]))
            order.append(
                {
                    "kind": kind,
                    "model_request_id": payload.get("model_request_id"),
                    "tool_call_id": payload.get("tool_call_id"),
                    "tool": payload.get("tool"),
                }
            )
        elif kind == TOOL_RESULT:
            results[str(payload.get("tool_call_id") or "")] = payload
            order.append(
                {
                    "kind": kind,
                    "tool_call_id": payload.get("tool_call_id"),
                    "tool_run_id": payload.get("tool_run_id") or row.get("tool_run_id"),
                }
            )
        elif kind == VERIFY_PASSED:
            verification_ok = verification_ok or _flag(payload.get("evidence_ok"))
            order.append({"kind": kind, "evidence_ok": _flag(payload.get("evidence_ok"))})
        elif kind == VERIFY_FAILED:
            order.append({"kind": kind})

    verdict = Verdict(
        status=INCOMPLETE_TRACE,
        providers=providers,
        models=models,
        model_responses=len(responses),
        live_responses=sum(1 for item in responses.values() if _flag(item.get("live"))),
        tool_calls=len(model_calls),
        verified=verification_ok,
        tampered=tampered,
        chain=order,
    )

    if tampered:
        verdict.status = INVALID_PROVENANCE
        verdict.reasons.append(f"{tampered} provenance record(s) failed the seal check")
        return verdict

    origin_model_runs = [run for run in runs.values() if str(run.get("origin") or "") == "model"]

    if not responses:
        if origin_model_runs:
            verdict.status = INVALID_PROVENANCE
            verdict.reasons.append("tool runs claim origin=model but no MODEL_RESPONSE was recorded")
        elif runs or model_calls or results or tool_activity:
            verdict.status = SCRIPTED_PLANNER
            verdict.reasons.append("tools were executed without a recorded model response")
        else:
            verdict.status = INCOMPLETE_TRACE
            verdict.reasons.append("no model response and no tool execution in the trace")
        return verdict

    if any(
        not _flag(item.get("live")) or str(item.get("provider") or "") in MOCK_PROVIDERS
        for item in responses.values()
    ):
        verdict.status = MOCK_MODEL
        verdict.reasons.append("the recorded model responses are mock or offline stand-ins")
        return verdict

    if any(not str(item.get("response_id") or "").strip() for item in responses.values()):
        verdict.status = INVALID_PROVENANCE
        verdict.reasons.append("a live model response has no response id")
        return verdict

    unknown = sorted(
        {
            f"{item.get('provider')}/{item.get('runtime')}"
            for item in responses.values()
            if str(item.get("provider") or "") not in REAL_PROVIDERS
            or str(item.get("runtime") or "") not in REAL_RUNTIMES
        }
    )
    if unknown:
        # A "live" claim from an adapter nobody recognised is not evidence of a model. It is a
        # provenance error, and the acceptance run must never turn it into a pass.
        verdict.status = INVALID_PROVENANCE
        verdict.reasons.append(f"unrecognised live provider/runtime: {', '.join(unknown)}")
        return verdict

    if not model_calls:
        verdict.status = INCOMPLETE_TRACE
        verdict.reasons.append("the model produced no tool call")
        return verdict

    for call_id, call in model_calls.items():
        request_id = str(call.get("model_request_id") or "")
        response = responses.get(request_id)
        if response is None:
            verdict.status = INVALID_PROVENANCE
            verdict.reasons.append(f"tool call {call_id} names no recorded model response")
            return verdict
        declared = str(response.get("tool_call_ids") or "")
        if call_id and call_id not in declared.split(","):
            verdict.status = INVALID_PROVENANCE
            verdict.reasons.append(f"tool call {call_id} is not declared by model response {request_id}")
            return verdict
        if not str(call.get("arguments_digest") or ""):
            verdict.status = INCOMPLETE_TRACE
            verdict.reasons.append(f"tool call {call_id} has no arguments digest")
            return verdict
        result = results.get(call_id)
        if result is None:
            verdict.status = INCOMPLETE_TRACE
            verdict.reasons.append(f"tool call {call_id} has no recorded result")
            return verdict
        if not _flag(result.get("executed")):
            # A typed tool failure is still a real execution attempt: the tool ran and refused.
            # It is not a linked success, but it must not turn the whole trace into a phantom.
            continue
        if not str(result.get("result_digest") or ""):
            verdict.status = INCOMPLETE_TRACE
            verdict.reasons.append(f"tool call {call_id} has no result digest")
            return verdict
        if str(result.get("arguments_digest") or "") != str(call.get("arguments_digest") or ""):
            verdict.status = INVALID_PROVENANCE
            verdict.reasons.append(f"tool call {call_id} result answers different arguments")
            return verdict
        run_id = str(result.get("tool_run_id") or "")
        if runs and run_id and run_id in runs:
            run = runs[run_id]
            if str(run.get("origin") or "") != "model":
                verdict.status = INVALID_PROVENANCE
                verdict.reasons.append(f"tool run {run_id} is not model-origin")
                return verdict
            if str(run.get("input_digest") or "") != str(call.get("arguments_digest") or ""):
                verdict.status = INVALID_PROVENANCE
                verdict.reasons.append(f"tool run {run_id} input digest does not match the call")
                return verdict
            saved = str(run.get("result_digest") or "")
            if saved and saved != str(result.get("result_digest") or ""):
                verdict.status = INVALID_PROVENANCE
                verdict.reasons.append(f"tool run {run_id} result digest does not match")
                return verdict
        verdict.linked_results += 1

    extra = set(results) - set(model_calls)
    if extra:
        verdict.status = INVALID_PROVENANCE
        verdict.reasons.append(f"{len(extra)} tool result(s) have no model tool call")
        return verdict

    if verdict.linked_results < 1:
        verdict.status = INCOMPLETE_TRACE
        verdict.reasons.append("no model tool call has a confirmed execution")
        return verdict

    if not verification_ok:
        verdict.status = INCOMPLETE_TRACE
        verdict.reasons.append("no passing independent verification was recorded")
        return verdict

    verdict.status = REAL_MODEL_CONFIRMED
    verdict.reasons.append(
        f"{verdict.live_responses} live model response(s) produced "
        f"{verdict.linked_results} executed tool call(s), independently verified"
    )
    return verdict


def from_task(db, task_id: str) -> tuple[list[dict], list[dict]]:
    """Read the journal and the tool ledger for one task, ordered by creation time."""
    from sqlalchemy import select

    from ..tools.models import TaskEvent, ToolRun

    events = db.scalars(
        select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.created_at, TaskEvent.id)
    ).all()
    runs = db.scalars(select(ToolRun).where(ToolRun.task_id == task_id).order_by(ToolRun.started_at)).all()
    return (
        [
            {
                "kind": row.kind,
                "payload": row.payload or {},
                "tool_run_id": row.tool_run_id,
                "created_at": row.created_at.isoformat() if row.created_at else "",
            }
            for row in events
        ],
        [
            {
                "id": row.id,
                "origin": row.origin,
                "tool_name": row.tool_name,
                "input_digest": row.input_digest,
                "result_digest": row.result_digest,
                "status": row.status,
            }
            for row in runs
        ],
    )
