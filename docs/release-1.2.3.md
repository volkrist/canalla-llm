# Canalla LLM 1.2.3 — agent reliability patch

Status: **candidate**. Not published, not merged into `main`, no production Gateway deploy.
Version: `1.2.3`. Branch: `integration/canalla-1.2.1`.

## Why 1.2.3 exists

Nine real defects were reported from the installed Windows app. This patch fixes the
controller side; the one paid Qwen tool-calling session remains a separately approved gate.

| # | Confirmed cause | Changed | Proof | Status |
|---|---|---|---|---|
| 1 | Web steps were marked `completed` even when the provider returned only errors; a later step failure stayed on screen as «Поиск не удался» after successful fetches; search/fetch had no timeout retry. | `tinyfish/client.py`, `tinyfish/web.py`, `useChat.ts`, `ToolActivity.tsx`, `tools.ts` | `test_123_agent_runtime.py` (timeout retry, error-only search is failed); live search + 3 live fetches returned `Example Domain`, `Welcome to Python.org`, `Python 3.15 documentation` with real excerpts | PASS |
| 2 | `read_file` and `run_process` did not have their own timeout problem: the **whole task** had a 180 s budget. Once it was spent, the next local call failed instantly as `timeout`. The model also lost local tools when the host heartbeat lapsed or when a prompt heuristic did not list `run_process`. | `chat_stream.py`, `executor.py`, `policy.py`, `orchestrator.py`, `local/provider.py` | `test_123_agent_runtime.py` asserts `task_runtime_limit` (not a tool timeout); planner test asserts `run_process`/`run_python` without magic words; 938 backend tests pass | PASS (free) |
| 3 | Same 180 s task budget; the per-call timeout was `min(tool_timeout, remaining)`. Long work needs `wait=false` + `process_status`. | `policy.py`, `executor.py`, `local/provider.py`, `orchestrator.py` | Unit tests; local tool timeout is 300 s, task runtime 1800 s; startup timeout untouched at 300 s | PASS (free) |
| 4 | `busy` meant «generating» and disabled the composer, New Chat, chat selection and Settings; `select()` refused to run while a stream was active. | `useChat.ts`, `Workspace.tsx`, `Composer.tsx`, `Sidebar.tsx` | `agent-ui.test.tsx` and the new `navigation-not-blocked.test.tsx`; the free Playwright test `e2e/generation-navigation.spec.ts` drives New Chat, chat switching, typing, Settings and Stop during a long mock stream | PASS |
| 5 | A single failed `/status` poll set `stale=true`, blanked all five chips and showed «Состояние недоступно»; `/health` did the same to the composer. | `useStatus.ts`, `App.tsx`, `StatusChips.tsx`, `AiConnectionBadge.tsx` | One fast retry plus a second failure marks stale; `reconnecting` hint until then; `agent-ui.test.tsx` keeps «Готово» chips and shows «Переподключение…»; status poll recovers within 10 s | PASS |
| 6 | The local host heartbeat and job execution shared one frontend loop; a failed heartbeat skipped jobs, and a stale task device binding raised `host_offline` while Computer was Ready. | `Workspace.tsx`, `executor.py` | Heartbeat and jobs are independent (8 s / 2 s); bound device is reused while online, otherwise rebound; 15 s blink grace before typed `host_offline` | PASS (unit/Rust) |
| 7 | The badge could say Connected from a stale `ai.state=ready` while compute said off; no client-side Stop action; cloud disconnect is enrollment-level. | `ai-connection.ts`, `AiConnectionBadge.tsx`, `Workspace.tsx` | Contradiction test; header «Отключить AI» calls `/cloud/compute/stop` (manual latch, no enrollment revocation), with direct-mode fallback | PASS (unit) |
| 8 | The model said it had no shell/SSH/browser; local tools were filtered by prompt heuristics and the system prompt did not enumerate capabilities. | `orchestrator.py`, `policy.py`, `local/provider.py` | Planner test with a generic prompt; prompt now states the installed app is the host and forbids «no shell» / «open the companion» | PASS (unit) |
| 9 | Live Qwen tool calling after the fixes. | — | Requires one paid GPU Pod, explicitly not approved in this task | BLOCKED |

## Timeouts — exact answer

- **What fired:** `ToolLimits.max_seconds = 180` for a tool-using turn not classified as
  autonomous. `executor.execute` then computed `min(tool_timeout, remaining)` and, at
  `remaining == 0`, raised an instant `timeout`. The logs show `read_file`/`run_process`
  failed with `error_code=timeout` while neighbouring calls completed.
- **What changed:** every turn that exposes tools now gets the task runtime (1800 s ceiling),
  each call is bounded by its own definition (300 s for local tools), a zero task budget is
  typed `task_runtime_limit`, and long builds use `wait=false` + `process_status`.
- **Unchanged:** the 300 s model startup deadline (`COMPUTE_STARTUP_TIMEOUT_SECONDS`) is a
  different mechanism and was not touched.

## Follow-up after the 20 Oct live run

A real Qwen run proved `web_fetch` (real `example.com` content) and Computer `read_file`
(`CANALLA_123_READ_OK`) but failed at the programming step. Two controller defects were found
and fixed without a model:

| Defect | Confirmed cause | Change | Proof |
|---|---|---|---|
| Task directory with a space | `infer_primary()` truncated `C:\\...\\Canalla E2E 1.2.3\\agent-live` at the first space, so every `write_file` was refused as `workspace_scope`. | `apps/backend/app/tools/local/scope.py`: the longest existing on-disk prefix wins (quoted paths, cyrillic, trailing separators kept), and the boundary compares `realpath` so `..`, junctions and absolute `argv` cannot escape. | `test_scope_keeps_a_directory_with_spaces`, `test_orchestrator_uses_a_directory_with_spaces` (real orchestrator + HTTP host executor: `4\\n`, `6\\n`), escape/junction/argv tests |
| Stopped task kept a live confirmation | `LocalTaskController.stop()` cancelled steps but left the pending `ToolRun` in `waiting_confirmation`, which also holds the shared compute session. | `apps/backend/app/tools/local/task.py`: stop now cancels `waiting_confirmation`/`approved` runs for that task (`task_stopped`, `reserved_budget=0`); a later Allow is `confirmation_already_used`. | `test_stop_cancels_a_pending_confirmation`; the stuck live task `ccae6e68-…` was closed through the real API and is now `STOPPED` |

The installed application is now proven to execute programs itself, with no model and no GPU:
`POST /tools/execute` drove `write_file -> run_python -> write_file(fix) -> run_python` through
the installed backend and the in-app native host, with every action confirmed through the real
`computer_mode=ask` endpoint. stdout was `4\\n` then `6\\n`, and the file on disk ended as
`print(2 * 3)\\n` (`artifacts/free-host-live-20261020T205514Z/host-chain.json`).

## Second live run (12:21 UTC) and the follow-up fix

The owner approved one short paid attempt under the standing limits ($2/h, one Pod, 700 s,
≤$0.40). It ran on an NVIDIA L40S 48 GB ($1.09/h, US-TX-3) for **726 s** and cost about
**$0.225**; it stopped cleanly (`pods []`, `active_session=null`, watchdog `closed`, one Pod).
B (Computer `read_file`) and the UI navigation check passed. A, C and D failed; the evidence
identified two separate causes:

| Finding | Evidence | Fix |
|---|---|---|
| The first planner request after model readiness took **240 s** (prefill of the full tool system prompt). The harness client timeout was exactly 240 s, so A aborted 4 s before the `200` arrived; B, immediately after, took 6 s. | Gateway journal: `POST /v1/chat/completions` at 12:22:38 completed at 12:26:38; the next at 12:26:44 completed quickly. A's chat stayed `stopped` with an empty assistant message. | The harness per-prompt timeout is now **300 s**; the 700 s watchdog and 60 s allocation bound are unchanged. This is a test-harness limit, not a Gateway failure. |
| The deterministic local route wrote the **filename** (`agent_hello.py`, taken from the quoted `argv` entry) as the file body and then only verified/read it. The model answered `4`/`6` without ever running Python. | `tool_runs` for C/D: `origin=server_policy`, `host_args.content="agent_hello.py"`; no `run_python` row; C task `COMPLETED`, D `FAILED verification_required`. | `local/intent.py` now takes the body only from an explicit content phrase (`content is exactly …`, `содержимым …`), never from a quoted token; an unknown body defers to the model. When the prompt asks to run a written `.py`, the route schedules `run_python` itself. `local/plan.py` recognises `write_file`/`run_python`/`run_process` and `create <name>.py` as computer signals. |

New regressions: `test_write_route_takes_content_not_the_argv_filename` and
`test_deterministic_write_runs_the_written_script` (real orchestrator + host executor,
`stdout == "4\n"`). The fixed build is installed as installer `9dbad23a…ecf9d`
(95,460,778 bytes), sidecar `497ed954…5e18`, `source_digest=dc25973f…799c`; the free explicit
host chain still returns `4\n`/`6\n` on that build.

## Verification

- Backend: `954 passed, 4 skipped` (the sidecar stamp test passes after a rebuild).
- Gateway: `261 passed`.
- Frontend: `309 passed` (includes `navigation-not-blocked`).
- GUI: Playwright `e2e/generation-navigation.spec.ts` passed against a real mock backend; the
  installed-window script `artifacts/ui-nav-during-gen.ps1` passed with validated 1216×859 frames.
- Rust host: `14 passed`.
- Developer gate: 9 steps, 0 FAIL, 0 BLOCKED.
- Live web (no GPU): TinyFish search returned 5 sources; 3 fetches returned real titles and text.
- Installed Windows 1.2.3 test build over 1.2.2:
  - Installer `9dbad23a…ecf9d`, 95,460,778 bytes; sidecar `497ed954…5e18`,
    `source_digest=dc25973f…799c`. Previous builds are kept under `artifacts/` for rollback.
  - `/health`: `version 1.2.3`, protocol 1.
  - 17 chats preserved; preferences unchanged (`computer_mode=ask`, 48 GB, ≤$2/h, idle 10).
  - Computer Ready (heartbeat 1.9 s, real host), Tor Ready, Web Configured, Memory Ready,
    AI off, cloud Disconnected; compute `stopped`.
  - RunPod `pods: []`, balance `$10.52`, watchdog `closed`; the 726 s L40S session cost
  about `$0.225` and no second Pod was created.
  - Data backups before installs: verified in-app backups `20261020T115130Z-manual` and
    `20261020T132002Z-manual`, plus raw SQLite copies with `integrity_check=ok`.

## Remaining live gate (not approved yet)

One paid Qwen session, ≤ $2.00/h, ≥ 48 GB, ≤ 700 s (watchdog; the model startup deadline stays
300 s), budget ≤ $0.40, in US-TX-3 or US-MO-1. The session must prove: real `tool_call` →
web page result → Computer `read_file` → the model writes, runs, fixes and reruns a Python file
→ Stop → `pods: []`. The host chain and the deterministic write/run route are already proven
without a model; what remains is Qwen's own multi-step tool calling on the fixed build. A fresh
owner approval is required because the one-Pod rule still applies.

## Known limitations

- The test installer is unsigned (no Authenticode).
- Gateway production still runs 1.2.2; `docs/replica-us-mo-1.json` now names 1.2.3 for this
  build, but the replica itself must be updated only by an approved deploy.
- Computer Ready is proven by the authenticated host heartbeat, not by a per-poll tool probe.
