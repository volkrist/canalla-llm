# Autonomous Task Agent

0.9 extends the existing Local Computer / Coding Agent stack. It does **not**
add a second ToolRegistry, ToolPolicy, ToolExecutor, ToolOrchestrator,
ContextBuilder, CodingWorkspace, or RunPod/Tor/Memory/RAG implementation.

A large natural-language request becomes one persistent `LocalTask` with a
plan, journal, checkpoints, budgets, Pause/Resume/Stop, and a final
verification gate.

## Lifecycle

```text
TASK → PLAN → INSPECT → RESEARCH → EXECUTE → VERIFY
  → if FAIL: DIAGNOSE → FIX → VERIFY AGAIN
  → FINAL REVIEW → COMPLETED
```

Typical coding path:

inspect git → understand project → discover test/build commands from
`pyproject.toml` / `package.json` / `Cargo.toml` / Makefile → baseline tests →
diagnose → edit with `expected_before_sha256` → targeted tests → git diff
review → compact report.

The model is not told which tools to call. The orchestrator exposes the
relevant subset (Memory/RAG/Web/Tor/Local/Coding) and the planner chooses.
Web is not used for a local syntax error; Tor is used only when the request
asks for Tor/.onion; RAG is used when uploaded documents matter.

## State machine

`CREATED → PLANNING → READY → INSPECTING | RESEARCHING | EXECUTING | VERIFYING | RETRYING`

Waiting: `WAITING_CONFIRMATION`, `WAITING_DEVICE`, `WAITING_LLM`, `CONFLICT`

Control: `PAUSED`, `STOPPING`, `INTERRUPTED`, `RECOVERING`

Terminal: `COMPLETED`, `FAILED`, `STOPPED`

Illegal transitions raise `invalid_task_transition`. `COMPLETED → EXECUTING`
is forbidden. `STOPPED` / `FAILED` resume only through an explicit Resume
that goes `RECOVERING → READY`.

After a backend crash, non-waiting in-flight tasks become `INTERRUPTED`.
`PAUSED`, `WAITING_CONFIRMATION`, `WAITING_DEVICE`, `WAITING_LLM`, and
`CONFLICT` are left as-is so the UI keeps a specific recoverable message.

## Plan

On open, Alex stores an actionable `TaskPlan` of steps (inspect, git, discover
commands, baseline tests, diagnose, optional research, edit, retest, git
review, final report). Steps describe *what to do*, not an invented bug cause.

If verification fails, a bounded plan revision can add investigate / fix-again
/ retest-again steps. Revision count is capped (`tools_task_max_plan_revisions`).

## Task loop

`ToolOrchestrator.prepare` already loops tool calls. 0.9 adds task-level
control around that loop:

- halt on Pause / Stop / budget / device / LLM wait
- compact tool results (keep FAILED/ERROR lines)
- forced verification when the model returns no calls but tests or git review
  are still missing
- `conclude` only after structured final review

There is no infinite loop: tool-call, runtime, file, search/fetch, retry, and
same-payload ceilings are hard.

## Context

The planner sees a bounded prompt: goal, current plan/step, important facts
(paths, errors, test names, hashes, exit codes), memory/project/RAG/Web/Tor
sources, recent compact tool results. Full pytest/npm/page dumps are not
replayed every turn. Truncation sets `truncated=true`.

## Journal and checkpoints

`task_events` records `TASK_CREATED`, `PLAN_*`, `STEP_*`, `TOOL_*`,
`FILE_CHANGED`, confirmation, `CHECKPOINT_CREATED`, `VERIFY_*`, `RETRY`,
Pause/Resume/Stop, `COMPLETED`/`FAILED`. Payloads are sanitized; secrets are
dropped.

Checkpoints store plan revision, completed steps, workspace, git HEAD/branch
/dirty when available, changed-file hashes, verification. They do not store a
filesystem snapshot.

## Idempotency and crash safety

Local Computer `ToolRun` rows carry `task_id` and `input_digest`. After
Resume, a completed local action with the same digest is not executed again
on the host.

Network tools (Web/Tor) are not replayed that way: a second fetch of a visited
onion URL still hits loop protection.

Write/patch still require `expected_before_sha256`. If the file changed under
Alex, the host returns `conflict`; the task enters `CONFLICT` and must re-read
before patching. No overwrite.

## Pause / Resume / Stop

- **Pause** — no new tools; status `PAUSED`; Resume continues the same plan.
- **Resume** — `POST /tasks/{id}/resume` streams the existing request without
  inserting a new user message. Completed local actions are not duplicated.
- **Stop** — stop scheduling, cancel generation, mark remaining steps
  cancelled, persist `STOPPED`. Unrelated OS processes are not touched.
  Task-owned Tor Browser sessions are closed in the orchestrator `finally`.

## Budgets

User-facing defaults (hard caps in parentheses):

| Limit | Default | Hard |
|---|---|---|
| Runtime | 30 min | 120 min |
| Tool calls | 40 | 100 |
| File changes | 20 | 100 |
| Web Search | 5 | — |
| Web Fetch | 12 | — |
| Tor Search | 3 | 3 |
| Tor Fetch/Browser | existing Tor ceilings | existing |

Exhaustion is `FAILED` with `task_budget` / `task_runtime_limit` /
`task_file_limit` and a user-visible "Budget exhausted". Same tool + same
payload is capped separately. There is no unlimited mode.

RunPod: a task does not start a GPU. It uses the current user-approved
managed session. If the LLM disappears mid-task, status is `WAITING_LLM`
until AI is started again.

## Safety / Unattended

Autonomous mode means "do not ask about ordinary safe steps". It does **not**
disable confirmations.

| Risk | Trusted workspace |
|---|---|
| READ | automatic |
| NORMAL_CHANGE in-scope | automatic |
| SENSITIVE | always confirm |
| CRITICAL | always one-time explicit confirm |

`WAITING_CONFIRMATION` shows task, step, action, target, risk, expected
effect. Approval is bound to the immutable action digest.

No auto email, forms, purchases, or new destructive Windows capabilities.

## Workspace and device

One exclusive WRITE lock per workspace. A second autonomous writer gets
`workspace_busy` ("Workspace занят другой задачей"). Read-only work can
overlap.

Local Computer actions stay on the paired device recorded on the task. If
that host is offline: `WAITING_DEVICE`. Alex does not silently switch
devices.

## UI

The chat shows a Task panel: title, `n / m` steps, live plan marks, status
message, elapsed time, tool and file budgets, Pause / Resume / Stop.
`task` and `task_status` SSE events update the panel without polling.

Task history lists recent tasks. Opening a task shows plan, journal (no
secrets), and the completion summary.

User-visible waits: Device offline, Waiting for confirmation, Tool timed out,
Budget exhausted, Tests still failing, file conflict, Tor/Web/LLM unavailable.

## Completion

`COMPLETED` is not "the model said готово". Final review requires:

- original request addressed
- coding: tests passed **and** git status/diff reviewed
- research: collected D/W/T sources when the request asked for live docs

Otherwise `FAILED` with a concrete reason. The completion summary lists what
was done, files changed, checks, sources, and remaining limits.

Git commit/push is never automatic unless the user asked and policy/confirmation
allow it.

## Recovery tests covered locally

- pause blocks new tools, resume continues
- stop cancels remaining steps
- tool-call budget of 3 blocks the 4th call
- backend reconcile → `INTERRUPTED`, completed `list_directory` not rerun
- device offline → `WAITING_DEVICE`
- stale patch SHA → `CONFLICT`, file unchanged
- transient conflict then successful patch
- mock coding: fail tests → patch → retest → git review → `COMPLETED`
- mock web+coding: official-docs search, first fix, remaining failure, plan
  revision, second fix, tests pass

## Known limitations (0.9)

- TinyFish Agent/Browser are not used.
- Live plan-step labels can lag the tools until final review closes leftover
  PENDING steps as SKIPPED. Completion is gated by tests/git/sources, not by
  every step row turning green mid-flight.
- After Pause the in-progress model turn may emit a raw tool-call fragment
  before Resume continues the same task.
- Web search quality follows the model's query; official docs are preferred
  but not guaranteed if the local test output already contains the contract.
- Two WRITE tasks on one workspace: the second fails immediately rather than
  queueing.
- Desktop restart reconstructs task state from the backend; it does not keep
  an in-flight LLM generation alive by itself.
- No automatic git commit/push.
- No automatic email, form submit, or purchase side effects.
