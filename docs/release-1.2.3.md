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
| 4 | `busy` meant «generating» and disabled the composer, New Chat, chat selection and Settings; `select()` refused to run while a stream was active. | `useChat.ts`, `Workspace.tsx`, `Composer.tsx`, `Sidebar` usage | `agent-ui.test.tsx` proves the composer is not disabled while streaming; navigation no longer checks the stream lock; second message is queued | PASS (unit) / GUI live BLOCKED on a model |
| 5 | A single failed `/status` poll set `stale=true`, blanked all five chips and showed «Состояние недоступно»; `/health` did the same to the composer. | `useStatus.ts`, `App.tsx`, `StatusChips.tsx`, `AiConnectionBadge.tsx` | 3-failure threshold, backoff, `reconnecting` hint; `agent-ui.test.tsx` keeps «Готово» chips and shows «Переподключение…» | PASS |
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

## Verification

- Backend: `938 passed, 4 skipped`.
- Gateway: `261 passed`.
- Frontend: `306 passed` (includes the new `agent-ui` regressions).
- Rust host: `14 passed`.
- Live web (no GPU): TinyFish search returned 5 sources; 3 fetches returned real titles and text.
- Installed Windows 1.2.3 test build over 1.2.2:
  - `/health`: `version 1.2.3`, protocol 1.
  - 5 chats / 62 messages preserved; compute preferences unchanged.
  - Computer Ready, Tor Ready, Web Configured, AI off, Disconnected — no error banner.
  - RunPod `pods: []`.
  - Data backup before install: `artifacts/backup-1.2.3/alex-before-1.2.3.db`
    (SHA256 `d1b48c3a…e2ac89`).

## Remaining live gate (not approved yet)

One paid Qwen session, ≤ $2.00/h, ≥ 48 GB, ≤ 300 s, with the independent VPS watchdog. The
session must prove: real `tool_call` → browser page result → model answer → Stop → `pods: []`.
The Computer tool call in the same session is optional; it was not part of the paid test in
1.2.2.

## Known limitations

- The test installer is unsigned (no Authenticode).
- Gateway production still runs 1.2.2; `docs/replica-us-mo-1.json` now names 1.2.3 for this
  build, but the replica itself must be updated only by an approved deploy.
- Computer Ready is proven by the authenticated host heartbeat, not by a per-poll tool probe.
