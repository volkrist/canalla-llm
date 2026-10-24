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

- Backend: developer gate `pytest.backend` PASS. After the sidecar rebuild the full run reports
  **963 passed, 0 failed, 4 skipped**; the staged sidecar stamp is current
  (`source_digest=f1893258…9fbc`). The targeted provider/cloud tests are `47 passed`.
- CI: **7/7 PASS** on `ce7a93c` (developer, frontend-e2e, native, tor, llama runtime,
  llama runtime image, installer build).
- Gateway: `261 passed` (`pytest.gateway` in the gate).
- Frontend: `309 passed`, plus `tsc`, Prettier and the Vite build in the gate.
- GUI: Playwright `e2e/generation-navigation.spec.ts`; the installed-window script passed with
  validated 1280×900 frames on a display no other window covered, and was used live in S7.
- Rust host: `14 passed`.
- Developer gate: **9 steps, 0 FAIL, 0 BLOCKED → PASS** (re-run after the formatting commit).
- Live web (no GPU): TinyFish search returned 5 sources; 3 fetches returned real titles and text.
- Live Qwen (Package A): S1–S7 **PASS**, $0.218, four Pods, `pods: []` after every stop.
- Installed Windows 1.2.3 test build (24 Oct, Package B):
  - Installer `86536fcd…21cd`, 95,459,567 bytes; sidecar `6b19756a…64ed`,
    `source_digest=f1893258…9fbc`. Previous builds stay under `artifacts/` for rollback.
  - Over-install preserved 36 chats and preferences; SQLite `integrity_check=ok`
    (backup `artifacts/install-backup-20261024-pre-package-b/alex.db`).
  - `/health`: `version 1.2.3`, protocol 1.
  - 36 chats preserved; preferences unchanged (`computer_mode=ask`, 48 GB, ≤$2/h, idle 10).
  - Computer Ready with a live host heartbeat, Tor Ready, Web Configured, Memory Ready, AI off,
    cloud connected, compute `stopped`.
  - RunPod `pods: []`, balance `$10.07`, watchdog `closed`/`within_cap`.
  - Data backups before installs: verified in-app backups plus raw SQLite copies with
    `integrity_check=ok` (last: `artifacts/install-backup-20261021T221015Z`).

## Package B live acceptance (24 Oct 2026)

Owner-approved envelope: **3 Pods, $0.60, ≤$2/h, ≥48 GB, one at a time, 700 s per Pod**.
Result: **M1, M2, M4, M5, M6, M7 PASS; M3 FAIL (test-harness policy); H1/H2 BLOCKED by
`startup_timeout`; the third Pod was the last allowed one.**

| Scenario | Verdict | What the model actually did |
|---|---|---|
| M1 | PASS | model-origin `write_file` → `run_python` → `write_file` → `run_python`; stdout **4** then **6**; final file `print(2 * 3)` |
| M2 | PASS | `read_file` → `write_file` (calc script) → `run_python` → `read_file`; independent check: revenue 200, units 9, min 20, max 120; report file matches |
| M3 | FAIL | the model correctly chose `create_directory` for `m3_app`, but the harness allowlist denied it; `write_file` then had no parent directory. No Qwen failure: the product asked for the normal NORMAL_CHANGE confirmation, the test policy refused it. Fixed free; not re-run because the 3-Pod cap was spent. |
| M4 | PASS | model-origin `web_fetch` of `example.com` and `python.org`; exact titles and both links |
| M5 | PASS | model-origin `web_fetch` → `write_file` (`m5_title.txt`) → `run_python`; answer `EXAMPLEDOMAIN` |
| M6 | PASS | `write_file` → `run_python`; a second chat was created/read mid-turn (`chat_switched=true`); `M6_STATE_OK`; file was not recreated |
| M7 | PASS | `write_file` confirmation **denied**; no file was created; answer names `confirmation_denied`; no bypass |
| H1/H2 | BLOCKED | no model turn: Pod 3 never became ready, the Gateway's D-9 stopped it as `startup_timeout` (309.8 s) |

Model-origin evidence is in `artifacts/acceptance-123/runs/live-20261024T101659/` (M1–M3) and
`.../live-20261024T102626/` (M4–M7). A `server_policy` web step is never counted as a Qwen PASS;
the M2/M4/M5 extra `web_search` calls were deterministic and are ignored for the verdict.

### Cost, Pods and time

| Session | Pod | DC / Volume | Confirmed lifetime | Gateway cost |
|---|---|---|---|---:|
| medium-a | `oq27ufvafpmspq` | US-TX-3 / `uwgeaie5b0` | 421.5 s | $0.124139 |
| medium-b | `rl766abs1rnjfo` | US-MO-1 / `y92bt25ru8` | 232.0 s | $0.065703 |
| hard-a | `rkybxq7g8m239b` | US-MO-1 / `y92bt25ru8` | 309.8 s (`startup_timeout`) | $0.091742 |
| **Total** | **3 Pods** | | all `within_cap=true` | **$0.281584** |

Every Pod was an NVIDIA L40S 48 GB at **$1.09/h**; one at a time; no capacity wait, no second
Pod. RunPod balance moved `$9.392691 → $9.202275` (the Gateway session estimate is $0.281584;
the account is shared, so the balance delta is not a single-package meter).

### Timing

- Planner turns: first token median **2.6 s**, max 7.2 s; completed planner turn median **4.8 s**,
  max 25.9 s (20 turns in medium-a, 11 in medium-b).
- Final answer stream: first token 2.5–5.6 s; done 3.3–8.5 s.
- First streamed content delta for the six PASS scenarios: **average 48.6 s**, median **29.6 s**
  (tool work happens before the answer). Time to the first model action (first tool call):
  **average 9.0 s**.
- No planner turn exceeded the 60 s inter-chunk deadline without being typed.

### The S7 fix fired live — twice

`llm_stream_stalled` is in the real log, not just unit tests:

```
19:21:28 llm_stream_stalled what=gateway_plan:c4e99dd2 lines=24 elapsed_ms=61094 silent_ms=60015
19:23:46 llm_stream_stalled what=gateway_plan:02cad99d lines=70 elapsed_ms=61937 silent_ms=60000
```

The client ended the turn at 60 s instead of waiting out 240–300 s, kept the completed tool
result, and the final answer still ran. **This is detection and recovery, not a cure for the
underlying silence**: the model/Gateway produced no chunk after the first tokens in two real
turns, and the reason is still unknown. It is recorded as an open product finding, not as a
fixed root cause.

### Harness defects fixed free

- `common.safe_run` allowed `create_directory` inside the sandbox (and `run_process` only for
  `python` with a sandbox `cwd`); this is a test-harness policy, not a product permission change.
- `runner.py` no longer reports an empty `pods []` as “not empty”.

### State after Package B

`/llm/status`: **AI off**, compute `stopped`, `last_error=startup_timeout`, `session=null`;
after 45 s it stayed stopped — no auto-reconnect. RunPod `pods: []`; watchdog `disarmed` /
`confirmed_stopped`, `within_cap=true`; no `WAITING_LLM` tasks in the local DB. Stop AI was
called after every session.

## Package C live acceptance (24 Oct 2026)

Owner-approved envelope: **4 Pods (including failed starts), $1.00, ≤$2/h, ≥48 GB, one at a time,
700 s per Pod**. Four Pods were used and **$0.4106** of GPU time was spent. Result:
**M3 PASS, H2 PASS, H1 FAIL (three independent causes, all outside the model), H3–H6 and T1–T8
not run — the Pod cap was spent on the two open items and on proving the defects found.**

| Scenario | Verdict | What really happened |
|---|---|---|
| M3 | **PASS** | 7 model-origin tool calls (`run_python` ×5, `write_file`, `list_directory`, one refused `write_file`); the package `m3_app/` was created and `python -m m3_app` printed `M3_PROJECT_OK` |
| H1 | **FAIL** | the model wrote correct modules, but three product/harness defects broke the task (below). No final answer: the last planner turn ended `llm_unavailable` |
| H2 | **PASS** | 9 model-origin tool calls (`read_file` ×3, `run_python` ×3, `write_file`, `patch_file`, `list_directory`); `calc.py` repaired, `pytest` green — the independent checker agrees. The final *prose* was the typed recovery message, not a summary |
| H3–H6, T1–T8 | **NOT RUN** | the 4-Pod cap was exhausted; no budget was spent on them |

The H2 code fix is real even though the answer text is not: `post_check` ran the project's own
`pytest` and the previously failing `test_calc.py` passes, with the changed file on disk.

### H1: three causes, none of them the model

| Attempt | Cause | Evidence | Status |
|---|---|---|---|
| Pod 1 | **transport**: the Gateway completed the request in 4 s with `200`, the client received no headers for 300 s and ended `llm_unavailable` (status 0) | Gateway journal `POST /v1/chat/completions 200` at 12:35:38 for a request started 12:35:34; no client headers | open (network path) |
| Pod 2 | **workspace lock**: a task parked in Pod 1 held `user:<id>:computer`, so H1 and H2 answered `WAITING_WORKSPACE` and never reached the model | `workspace_locks` row for task `73f0dfc7`, two `workspace_waiters` | fixed in the harness (`stop_stale_tasks`) |
| Pod 3 | **`run_process` never returned**: the host delivered `exit_code 9009`; `HostResult` allowed only −1…255, so the POST was `422`, the host retried every 2 s forever and the tool ended `timeout` | host journal `"exit_code":9009`, backend access log `host-result 422` repeated | **fixed** (`HostResult.exit_code` is signed 32-bit), proven free |
| Pod 4 | **`write_file` mangled the body**: the host passed content through `trim_matches('"')`, deleting a leading `"""` docstring; the file differed from the request, `write_verification_failed` fired, and the model retried the same correct write four times | intended `"""h1_cli package."""\n` vs disk `h1_cli package."""\n`; `expected_sha256 != actual_sha256` | **fixed** (`host.rs` writes the body verbatim), proven free |

Free proof of the write fix, with no model and no GPU (`POST /tools/execute`): the body
`"""Module docstring."""\nvalue = 1\n` is written byte-for-byte, `expected_sha256 ==
actual_sha256 == 907f0ebb…`, and the docstring is intact. The `run_process` probe now completes
in about one second with `exit_code 9009` and the real stderr instead of a 30 s `timeout`.

### The two `llm_stream_stalled` turns (diagnosis, not a cure)

Both are real planner turns in M2 (Package B):

```
19:20:25 gateway_plan_start id=c4e99dd2 -> headers 2.3 s -> first token 3.1 s -> 24 lines -> 60.0 s silence
19:22:41 gateway_plan_start id=02cad99d -> headers 3.3 s -> first token 3.5 s -> 70 lines -> 60.0 s silence
```

The client saw SSE lines and then **no byte at all**; the Gateway stayed healthy (health, models
and balance polls answered throughout) and served the next request in 7.5 s. So the silence is
between the Gateway's upstream socket and llama.cpp: either the generation paused after a partial
tool call, or the Gateway's read stalled. It cannot be attributed further from the surviving
evidence, because the Gateway records no per-chunk timing and the Pod (and its llama.cpp log) is
gone. Detection is `app/providers.py` (60 s inter-chunk deadline); the cause remains open.

During Package C the same guard fired **twice more** (22:24:43 `841373fa`, 128 lines;
22:27:01 `2e79372d`, 56 lines), both inside H2, which still passed because the tool work had
completed.

### Startup timeout of Package B's third Pod

Session `70257548…` (US-MO-1 / volume `y92bt25ru8`) never reached `ready_at` and was stopped by
D-9 at 303 s. It was created **50 s after** the previous Pod on the *same* volume stopped; that
Pod had worked normally 4 minutes earlier. The likely cause is the regional volume detaching from
the stopped Pod before the next mount, so the new Pod could not mount the model in time. The
startup deadline stays **300 s**; the harness now waits (`--cooldown-seconds`) between Pods.

### Cost, Pods, ready latency

| Session | DC / volume | created → ready | billable | cost |
|---|---|---|---:|---:|
| medium-a (M3 + H1) | US-TX-3 / `uwgeaie5b0` | 73.0 s | 462 s | $0.139883 |
| H1/H2 (locked) | US-MO-1 / `y92bt25ru8` | 105.6 s | 160 s | $0.048444 |
| H1 | US-TX-3 / `uwgeaie5b0` | 50.8 s | 438 s | $0.132617 |
| H1 + H2 | US-TX-3 / `uwgeaie5b0` | 21.8 s | 296 s | $0.089622 |
| **Total (4 Pods)** | | mean 62.8 s | | **$0.410566** |

Every Pod was an NVIDIA L40S 48 GB at $1.09/h, one at a time, `within_cap=true`, no capacity
wait. The account balance moved `$9.182830 → $8.761883` (the Gateway estimate is the GPU figure;
the delta also carries the volume's hourly charge).

### Latency in the Package C window (installed backend log)

| Phase | n | min | p50 | p95 | max |
|---|---:|---:|---:|---:|---:|
| planner turn, total | 38 | 3.67 s | **5.44 s** | **10.39 s** | 10.67 s |
| planner turn, first token | 38 | 1.91 s | **2.72 s** | **6.19 s** | 6.84 s |
| final answer stream | 1 | 5.63 s | 5.63 s | 5.63 s | 5.63 s |

The single completed final stream is itself a finding: when a planner turn stalls, the turn ends
`llm_unavailable` and `skip_final_stream` suppresses the answer, so the user sees «LLM unavailable.
Task will continue after recovery.» even though the tool work succeeded (H2). The task is parked
and resumable, but the wording does not match what happened.

### Harness changes (test-only, no product permission widened)

- `patch_file` is approvable inside the scenario sandbox (H2 needed it).
- Parked acceptance tasks are stopped before a scenario, so they cannot hold the single computer
  workspace and turn every new scenario into `WAITING_WORKSPACE`.
- Pods are spaced by `--cooldown-seconds` (volume detach) and the runner no longer reports an
  empty `pods []` as “not empty”.

## Known limitations

- The test installer is unsigned (no Authenticode).
- Gateway production still runs 1.2.2; `docs/replica-us-mo-1.json` now names 1.2.3 for this
  build, but the replica itself must be updated only by an approved deploy.
- Computer Ready is proven by the authenticated host heartbeat, not by a per-poll tool probe.
- The planner can stall after its first tokens (four real cases, all detected at 60 s and all
  recovered); the cause is not yet identified at the provider or the Gateway.
- `run_process` runs with the host's minimal PATH, so a bare `python` resolves to the Windows
  Store alias and exits `9009`. The result is now delivered honestly; `run_python` remains the
  route for Python.
