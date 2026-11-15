#!/usr/bin/env python
"""Re-read a finished LIVE run's recorded trace through the current classifier.

This is a *replay of the evidence*, never a new LIVE and never a merge of different runs:

* it does not invent a model response, a tool call or a verification event;
* it does not claim a PASS the run did not produce;
* it re-derives the verdict from `model-responses.jsonl` + `tool-calls.jsonl` +
  `tool-execution.jsonl` with the classifier in this checkout, and compares it with
  `provenance-verdict.json` written by the run itself.

The provenance seal is not re-checked: it is keyed by the run's own database secret, which is
deleted with the run's database. `tampered` is reported as `null` (not re-derivable), never as 0.

The root cause of a failed suite is read from the run's own `http-check.json` instead of being
guessed. The final LIVE (`e1845955`) failed its tests from the workspace root because the
editable backend `app` package shadowed the project's `app`; the verifier now runs a package
layout from the package root (see the regression check inside `live_acceptance.py --self-test`).

    python replay-verifier.py [--run <run-id>] [--evidence <dir>] [--out <file>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
LIVE = OUT / "final-live"
REPO = Path(__file__).resolve().parents[3]

sys.path.insert(0, str(REPO / "apps" / "backend"))

from app.agent import provenance  # noqa: E402  (path is set up above)


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def replay(evidence: Path) -> dict:
    responses = read_jsonl(evidence / "model-responses.jsonl")
    calls = read_jsonl(evidence / "tool-calls.jsonl")
    executions = read_jsonl(evidence / "tool-execution.jsonl")
    journal = read_jsonl(evidence / "provenance-events.jsonl")
    recorded_path = evidence / "provenance-verdict.json"
    recorded = json.loads(recorded_path.read_text(encoding="utf-8")) if recorded_path.is_file() else {}

    # A run from this commit writes the whole journal. An older run (e1845955) only wrote the
    # model side, so its TOOL_RESULT rows are rebuilt by matching the ledger's input digest to the
    # call that produced it. Nothing is invented: every rebuilt row names a real recorded call and
    # a real ledger row, and an unmatched execution stays unmatched (never a passing result).
    reconstruction = "journal" if journal else "digest_match"
    if journal:
        events = journal
        tool_runs = [
            {
                "id": row.get("id"),
                "origin": row.get("origin"),
                "tool_name": row.get("tool"),
                "input_digest": row.get("input_digest"),
                "result_digest": row.get("result_digest"),
                "status": row.get("status"),
            }
            for row in executions
            if row.get("id")
        ]
        unmatched_runs = []
    else:
        rebuilt, unmatched_runs = _rebuild_results(calls, executions)
        events = responses + calls + rebuilt
        tool_runs = []

    verdict = provenance.classify(events, tool_runs=tool_runs)
    payload = verdict.as_dict() if hasattr(verdict, "as_dict") else dict(verdict.__dict__)

    recorded_core = {
        key: recorded.get(key)
        for key in (
            "status",
            "model_responses",
            "live_responses",
            "tool_calls",
            "linked_results",
            "verified",
        )
    }
    replayed_core = {
        key: payload.get(key)
        for key in (
            "status",
            "model_responses",
            "live_responses",
            "tool_calls",
            "linked_results",
            "verified",
        )
    }

    http_path = evidence / "http-check.json"
    http_check = json.loads(http_path.read_text(encoding="utf-8")) if http_path.is_file() else {}
    database_path = evidence / "database-check.json"
    database_check = (
        json.loads(database_path.read_text(encoding="utf-8")) if database_path.is_file() else {}
    )

    # Counterfactual, clearly labelled: with the *recorded* trace untouched, one verification
    # event is the whole difference between INCOMPLETE_TRACE and REAL_MODEL_CONFIRMED. This is a
    # check of the classifier against real data; it is never reported as a LIVE pass.
    counterfactual = None
    if not payload.get("verified"):
        sample = events + [
            {
                "kind": provenance.VERIFY_PASSED,
                "payload": {
                    "evidence_ok": True,
                    "summary": "counterfactual only: not an event of this run",
                },
            }
        ]
        hypothetical = provenance.classify(sample, tool_runs=tool_runs)
        counterfactual = {
            "status": hypothetical.status,
            "note": "not part of the run; proves the recorded trace lacks only the verification row",
        }

    executed = [row for row in executions if row.get("origin") == "model"]
    return {
        "kind": "evidence_replay",
        "evidence_dir": str(evidence),
        "replayed_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(timespec="seconds"),
        "classifier": "app.agent.provenance.classify (this checkout)",
        "event_reconstruction": reconstruction,
        "unmatched_model_executions": unmatched_runs,
        "seal_rechecked": False,
        "seal_note": "the run's provenance secret died with its database; tampered is not re-derived",
        "recorded_verdict": recorded_core,
        "replayed_verdict": replayed_core,
        "verdict_matches": recorded_core == replayed_core,
        "reasons": payload.get("reasons") or [],
        "only_missing_link": (
            None
            if payload.get("verified")
            else "a passing independent verification event (VERIFY_PASSED)"
        ),
        "executed_model_tools": len(executed),
        "counterfactual_with_one_verification": counterfactual,
        "executed_tool_names": sorted({str(row.get("tool")) for row in executed}),
        "pytest_exit_code": http_check.get("pytest_exit_code"),
        "pytest_root_cause": _root_cause(http_check.get("pytest_tail") or ""),
        "application_serves_http": http_check.get("status") == 200,
        "independent_create_status": (http_check.get("created_task") or {}).get("status"),
        "sqlite_tables": database_check.get("tables") or {},
    }


def _rebuild_results(calls: list[dict], executions: list[dict]) -> tuple[list[dict], list[str]]:
    """Pair each model-origin ledger row with the call that asked for it, by input digest."""
    pending: dict[str, list[dict]] = {}
    for row in calls:
        payload = row.get("payload") or {}
        digest = str(payload.get("arguments_digest") or "")
        if digest:
            pending.setdefault(digest, []).append(payload)
    rebuilt: list[dict] = []
    unmatched: list[str] = []
    for execution in executions:
        if execution.get("origin") != "model":
            continue
        digest = str(execution.get("input_digest") or "")
        waiting = pending.get(digest) or []
        if not waiting:
            unmatched.append(f"{execution.get('tool')}:{digest[:12]}")
            continue
        call = waiting.pop(0)
        executed = str(execution.get("status") or "") == "completed"
        rebuilt.append(
            {
                "kind": "TOOL_RESULT",
                "payload": {
                    "tool_call_id": call.get("tool_call_id"),
                    "tool": execution.get("tool"),
                    "arguments_digest": digest,
                    "executed": executed,
                    "result_digest": str(execution.get("result_digest") or ""),
                    "tool_run_id": "",
                },
            }
        )
    return rebuilt, unmatched


def _root_cause(tail: str) -> str:
    for line in tail.splitlines():
        text = line.strip()
        if text.startswith(("E ", "ImportError", "ModuleNotFoundError")):
            return text[:300]
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default="e184595544d24f14a140bd9298e81ea1")
    parser.add_argument("--evidence", default=str(LIVE))
    parser.add_argument("--out", default=str(OUT / "verifier-replay" / "replay-e1845955.json"))
    args = parser.parse_args()

    evidence = Path(args.evidence)
    result = replay(evidence)
    result["run_id"] = args.run
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("verdict_matches", "recorded_verdict", "only_missing_link", "pytest_root_cause")}, ensure_ascii=False, indent=1))
    return 0 if result["verdict_matches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
