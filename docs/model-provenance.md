# Model provenance and the LIVE verdict

`origin="model"` on one row proves nothing: a test, a harness or a bug can write that string. The
1.2.7 autonomy core therefore records a **chain**, and a validator reads it back.

## The chain

```
MODEL_RESPONSE   provider, model, response id, request id, the tool-call ids it proposed
  -> MODEL_TOOL_CALL   the exact tool-call id, tool name, arguments digest
    -> TOOL_RESULT     executed=true, tool_run_id, result digest, error (typed, if any)
      -> VERIFY_PASSED  an independent check observed the result (evidence_ok=true)
```

Every record is stored in `task_events` and sealed with an HMAC over its canonical payload using
the deployment secret (`JWT_SECRET`). An accidental or naive fabrication therefore fails the seal,
and a trace can only be labelled LIVE by the adapter that produced it.

The provider supplies the provenance, not the caller:

| adapter | `provenance_name` | `live_model` | runtime marker |
|---|---|---|---|
| `LlamaCppProvider` | `llamacpp` | yes | `llama.cpp` |
| `GatewayProvider` | `canalla-cloud` | yes | `gateway` |
| `MockLLMProvider` | `mock` | **no** | — |
| an adapter that opts in to nothing | `unknown` | **no** | — |

The tool side is recorded by the orchestrator: `MODEL_TOOL_CALL` before the executor runs, and
`TOOL_RESULT` with the ledger's own digest after it. `verify_result` records
`VERIFY_PASSED`/`VERIFY_FAILED` whichever side asked for it (the model or the controller).

## The verdict

`app/agent/provenance.py::classify(events, secret=..., tool_runs=...)`:

| status | meaning |
|---|---|
| `REAL_MODEL_CONFIRMED` | a named live provider produced a response, its declared tool calls ran, the ledger agrees, and an independent verification passed |
| `MOCK_MODEL` | the recorded responses are mock/offline stand-ins (`live=false`) |
| `SCRIPTED_PLANNER` | tools ran without any recorded model response |
| `INCOMPLETE_TRACE` | a proposed call has no result, or no verification was recorded |
| `INVALID_PROVENANCE` | a seal failed, a call is not declared by its response, the ledger disagrees, or an unrecognised adapter claims to be live |

`MOCK_MODEL` is deliberately not a pass. Neither is `INCOMPLETE_TRACE`: a model that proposed a
tool whose result never came back has not done the work.

## What the tests prove, and what they do not

`apps/backend/tests/test_127_provenance.py` proves the discrimination with the product's own
recorder: a sealed live chain is confirmed, a mock chain is not, a scripted planner is named, a
tampered record fails, a missing result is incomplete, and an unrecognised "live" adapter is
invalid. Those are **validator tests**. They are not a LIVE run and must never be reported as one.

The LIVE verdict is produced only by classifying the journal of a real task after a real provider
turn. `artifacts/acceptance-127/live-qwen/live_acceptance.py` performs that classification and
refuses to pass on anything but `REAL_MODEL_CONFIRMED`.

## Status as of this stage

`FULL LIVE AUTONOMY = NOT VERIFIED`. No real Qwen has produced a task trace yet; that run needs
owner approval for one Pod.
