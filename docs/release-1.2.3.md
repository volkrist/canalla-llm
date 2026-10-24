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
identified four separate causes:

| Finding | Evidence | Fix |
|---|---|---|
| The first planner request after model readiness took **240 s** (prefill of the full tool system prompt). The harness client timeout was exactly 240 s, so A aborted 4 s before the `200` arrived; B, immediately after, took 6 s. | Gateway journal: `POST /v1/chat/completions` at 12:22:38 completed at 12:26:38; the next at 12:26:44 completed quickly. A's chat stayed `stopped` with an empty assistant message. | The harness per-prompt timeout is now **300 s**; the 700 s watchdog and 60 s allocation bound are unchanged. This is a test-harness limit, not a Gateway failure. |
| The deterministic local route wrote the **filename** (`agent_hello.py`, taken from the quoted `argv` entry) as the file body and then only verified/read it. The model answered `4`/`6` without ever running Python. | `tool_runs` for C/D: `origin=server_policy`, `host_args.content="agent_hello.py"`; no `run_python` row; C task `COMPLETED`, D `FAILED verification_required`. | `local/intent.py` now takes the body only from an explicit content phrase (`content is exactly …`, `содержимым …`), never from a quoted token; an unknown body defers to the model. When the prompt asks to run a written `.py`, the route schedules `run_python` itself. `local/plan.py` recognises `write_file`/`run_python`/`run_process` and `create <name>.py` as computer signals. |
| After the clean Stop the AI chip showed «Ресурс больше не существует в RunPod» (`not_found`): the Gateway finalizes the terminated Pod as `provider_missing`, and the client relayed that code even though the state was off. | `/llm/status`: `ai=off`, `compute_state=stopped`, `typed_error=not_found`; `/status` chip `off` with a provider-error message. | `cloud/state.py` and `status/snapshot.py` clear `not_found`/`provider_missing` only when the state is `offline`/`stopped` and no session is active; the chip and `/llm/status` now say «AI не запущен». |
| A capacity wait survived Stop: the second attempt found no GPU, the Gateway parked `waiting_capacity`, and `/compute/stop` left the durable wait active around 13 minutes later. | `/llm/status`: `wait_active=true`, `wait_elapsed_seconds=784`, `state=waiting_capacity` after the harness stop. | `GatewayClient.cancel_wait` + `/cloud/compute/stop` now call `/compute/wait/cancel` (the Gateway's own route); an older Gateway without it is tolerated. The stuck wait was cancelled with the installation credential (`state=offline`, `wait=null`); `test_a_manual_stop_cancels_a_persistent_capacity_wait` pins it. |

New regressions: `test_write_route_takes_content_not_the_argv_filename` and
`test_deterministic_write_runs_the_written_script` (real orchestrator + host executor,
`stdout == "4\n"`), plus `test_a_clean_stop_does_not_show_the_vanished_pod_as_an_error`,
`test_the_raw_llm_status_does_not_report_a_stopped_pod_as_an_error` and
`test_a_manual_stop_cancels_a_persistent_capacity_wait`. The final build is installed as
installer `4a4407bf…c3563` (95,425,039 bytes), sidecar `007d6ca3…a015b`,
`source_digest=66a92af3…deef2`; the free explicit host chain still returns `4\n`/`6\n`, and
`/llm/status` is clean (`last_error=null`, `typed_error=null`, `wait_active=false`).

## Acceptance system for the remaining Qwen gates

The 20 Oct runs exposed two defects in the **test system itself**, not only in the product:

- the watchdog checked the 700 s cap every 15 s and sent the terminate at the cap, so the Pod
  was confirmed stopped at **726 s**;
- the final answer stream did not disable hidden thinking (the planner did), and the surviving
  logs could not separate queue, prefill and generation time.

The watchdog now stops at `created + 655 s` (45 s reserve) and records
`confirmed_elapsed_seconds`/`within_cap`; `--self-test` pins the arithmetic. The installed
watchdog was updated on the VPS with a backup of the previous file. `GatewayProvider` now logs
`gateway_plan_start/headers/first_token/done` and
`gateway_stream_start/headers/first_token/done` with elapsed milliseconds, and sends
`chat_template_kwargs: {enable_thinking: false}` on the final stream as well as the planner.

`artifacts/acceptance-123/` is the executable acceptance system: 28 scenarios (S1–S7, M1–M7,
H1–H6, T1–T8), each with its own prompt, timeout, expected tools, files, independent checker
and verdict. `catalog.py --self-test` proves a `server_policy` action can never be reported as
a model-origin PASS. The free preflight (`preflight.py --with-ui`) creates all 28 fixtures,
checks the seed projects (M2 expected `200/9/20/120`, H2 fails before the fix, H3 passes),
runs the installed host smoke (`write_file → read_file → run_python` → `PREFLIGHT_HOST_OK`),
confirms the host runtime (Python 3.12.9 + pytest 9.1.1), the installed-window UI frames, the
model-origin rule, RunPod `pods: []`, balance and the disarmed watchdog. It does not start
compute and does not claim a scenario PASS.

The live plan and budgets are in `artifacts/acceptance-123/plan.md`: 11 possible sessions,
3 gated packages, a maximum of about **$2.33** at $1.09/h, and a likely 6–8 sessions once the
measured first turn is known. No paid Pod was started while building this system.

## Package A live acceptance (21 Oct 2026)

Owner-approved package: S1–S7 in sequential Pods, **$0.32** total, ≤ 700 s per Pod, real Qwen.
The first two attempts failed before the package could be called green, and both failures were
product defects, not model behaviour:

| Attempt | Result | Cause found |
|---|---|---|
| simple-a (10:30 UTC) | two free capacity attempts (`price_limit`, no Pod), then a Pod; S1/S2 PASS, S3 FAIL | `coerce_file_argument` rewrote the model's correct `C:\...\readme with spaces.txt` into `spaces.txt`, so the host refused a valid read with `path_denied` |
| simple-a (11:03 UTC) | the model-origin `read_file` completed, the final answer still said it had no filesystem access | the final answer was generated from the plain conversation and never received the tool transcript |
| simple-a (12:00 UTC) | **S1, S2, S3 PASS** | — |
| simple-b (12:03 UTC) | **S4, S5, S6, S7 PASS** | — |

Both defects are fixed (commits `74f1f96`, `942e1dd`) with regressions in
`apps/backend/tests/test_autonomous_tasks.py`:

- an absolute path the model supplied is authoritative — prompt inference may fill a missing
  target but never replaces a correct one, and an existing path with spaces is recovered from the
  filesystem (`longest_existing_path`); the scope boundary, `..`, junction and `argv` checks are
  unchanged;
- the turn's tool results travel to the final answer as a bounded, labelled user note, so a
  completed `read_file` cannot be denied by the answer that quotes it;
- a process result's text is its **stdout**, not the host's `ok` status.

Evidence is in `artifacts/acceptance-123/runs/live-20261021T120054/` and
`.../live-20261021T120316/`: every scenario has its prompt, SSE timings, `origin=model` tool runs
with arguments, the host result and the final answer.

| Scenario | Evidence | Verdict |
|---|---|---|
| S1 | a Russian answer to the question | PASS |
| S2 | the second fact (`123`) | PASS |
| S3 | model-origin `read_file` of the spaced path; content returned; the answer quotes `S3_CANALLA_READ_OK_20261020` | PASS |
| S4 | model-origin `write_file` + `read_file`, body exactly `S4_EXACT_OK_20261020` | PASS |
| S5 | model-origin `write_file` + `run_python`, stdout `S5_STDOUT_OK` | PASS |
| S6 | model-origin `web_fetch` of `https://example.com`, the answer names `Example Domain` | PASS |
| S7 | the installed-window script ran **inside** the streaming turn (New Chat, chat switching, Settings, Escape, typing) | PASS |

Cost: four Pods in total, all NVIDIA L40S 48 GB at $1.09/h in US-TX-3, each stopped inside the
700 s cap (the last one `within_cap: true`, 353.3 s from create to confirmed delete). GPU cost
**$0.218** (`$0.044` + `$0.042` + `$0.026` + `$0.106`), inside the $0.32 package budget. Every
session ended with `pods: []`, `active_session=null` and no automatic reconnect. Three Pods were
needed instead of the planned two because the first two exposed the defects above.

### The UI test itself was wrong

`helpers/ui-nav-during-gen.ps1` reported PASS while capturing and typing into the **ChatGPT**
desktop window: a topmost window covered the rectangle, and the script trusted “the hash changed”.
It now picks the screen with the fewest overlapping windows, raises Canalla, refuses to continue
unless Canalla is the foreground window, and proves Settings by its panel (≥10 % of the frame)
plus an Escape round trip (≤5 % residue) instead of a changed hash. The live S7 run used the fixed
script.

### S7 stalled planner — measured, then fixed free

S7's verdict is about the interface, and the interface behaved correctly. The same request also
exposed a **stalled planner stream**: `gateway_plan_start id=550af6a0` at 21:05:07, headers in
4.3 s, the first token in 4.6 s, and then **no further chunk for 240 s** — no `gateway_plan_done`,
no error, no retry. The harness gave up on its own read timeout; the product would have ended the
turn at its 300 s planner budget and parked the task as recoverable `llm_unavailable`.

The free fix is implemented. `iter_stream_lines` in `app/providers.py` keeps the first chunk under
the transport read budget (Gateway queue admission plus prefill) and turns a gap longer than
`llm_stream_stall_seconds` (default **60 s**) after the first event into a typed `llm_stalled`
instead of waiting out the whole budget. Both the planner and the final-answer streams use it,
in shared and direct mode, and the log line carries the request id
(`llm_stream_stalled what=gateway_plan:<id>`). Regressions: the two silent-stream tests and the
"slow first chunk is not a stall" test in `tests/test_cloud.py`, plus the local-provider case in
`tests/test_providers.py`; **47 passed**.

The **live re-check remains**: a real Qwen stream that stalls after the first token. It needs a
paid Pod, so it was not part of Package A's authorization; Package B's long turns (H1) are the
first opportunity to observe it live.

## Verification

- Backend: developer gate `pytest.backend` PASS. After the stream-stall fix the full run reports
  `962 passed, 4 skipped, 1 failed`; the only failure is
  `test_the_staged_sidecar_in_this_repository_is_current` (`BACKEND_SIDECAR_STALE`), because the
  backend source changed after the staged sidecar was built. The installer must be rebuilt before
  packaging; the targeted provider/cloud tests are `47 passed`.
- Gateway: `261 passed` (`pytest.gateway` in the gate).
- Frontend: `309 passed`, plus `tsc`, Prettier and the Vite build in the gate.
- GUI: Playwright `e2e/generation-navigation.spec.ts`; the installed-window script passed with
  validated 1280×900 frames on a display no other window covered, and was used live in S7.
- Rust host: `14 passed`.
- Developer gate: **9 steps, 0 FAIL, 0 BLOCKED → PASS** (re-run after the formatting commit).
- Live web (no GPU): TinyFish search returned 5 sources; 3 fetches returned real titles and text.
- Live Qwen (Package A): S1–S7 **PASS**, $0.218, four Pods, `pods: []` after every stop.
- Installed Windows 1.2.3 test build (21 Oct, final):
  - Installer `b4b55325…ab4bf`, 95,463,526 bytes; sidecar `19976051…33f363`,
    `source_digest=fec4bb1d…5084813`. Previous builds stay under `artifacts/` for rollback.
  - `/health`: `version 1.2.3`, protocol 1.
  - 36 chats preserved; preferences unchanged (`computer_mode=ask`, 48 GB, ≤$2/h, idle 10).
  - Computer Ready with a live host heartbeat, Tor Ready, Web Configured, Memory Ready, AI off,
    cloud connected, compute `stopped`.
  - RunPod `pods: []`, balance `$10.07`, watchdog `closed`/`within_cap`.
  - Data backups before installs: verified in-app backups plus raw SQLite copies with
    `integrity_check=ok` (last: `artifacts/install-backup-20261021T221015Z`).

## Package B and C — prepared, not authorized

Package A is green. The remaining packages are **not** authorized: no Pod was created for them.

| Package | Sessions (one Pod each) | Scenarios | Max estimate |
|---|---|---|---:|
| B | medium-a, medium-b, hard-a | M1–M7, H1 | **$0.60** |
| C | hard-b, hard-c, stress | H3–H6, T1–T8 (T3/T4/T8 are free UI checks) | **$0.60** |

Each session is a separate `runner.py --live --confirm-paid --package <name> --budget-usd <n>`
invocation, so exactly one Pod is created and confirmed stopped per session. The free gates are
green now: `catalog.py` (model-origin and deterministic-route self-tests), `runner.py --self-test`
(budget guard, package selection, route gate), and `runner.py --sessions` prints the grouping
above. `preflight.py --with-ui` still has to run against a live local backend before the first
paid session, exactly as it did before Package A.

The product question those packages answer is whether Qwen completes a 5+ step chain without a
transport error (Package A proved single- and double-step tool use only), plus the live re-check
of the new inter-chunk stall deadline.

## Known limitations

- The test installer is unsigned (no Authenticode).
- Gateway production still runs 1.2.2; `docs/replica-us-mo-1.json` now names 1.2.3 for this
  build, but the replica itself must be updated only by an approved deploy.
- Computer Ready is proven by the authenticated host heartbeat, not by a per-poll tool probe.
