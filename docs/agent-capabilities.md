# Agent capabilities — inventory and acceptance matrix

What Canalla can actually do today, what is only implemented, and what is missing. Every
"VERIFIED" row names the evidence; a row without evidence is not a PASS, whatever the code says.

The target loop is `REQUEST → PLAN → RESEARCH → TOOLS → EXECUTION → VERIFICATION → REPAIR →
VERIFIED RESULT`, and the recovery loop is `RESTART → LOAD CHECKPOINT → RECONCILE REAL STATE →
RESUME`. The controller, not the model's prose, owns completion.

## 1. Tool surface

84 typed tools are registered (`app.tools.registry.make_registry`). They fall into groups:

| Group | Tools |
|---|---|
| Files | `list_directory`, `read_file`, `write_file`, `patch_file`, `copy_file`, `move_file`, `delete_file`, `delete_directory`, `mass_delete`, `create_directory`, `hash_file`, `search_code`, `search_files` |
| Processes | `run_python`, `run_process`, `run_powershell`, `process_status`, `stop_process`, `list_processes`, `inspect_process` |
| Git | `git_status`, `git_diff`, `git_log`, `git_show`, `git_add`, `git_commit`, `git_branch`, `git_restore`, `git_reset`, `git_push` |
| Windows | `window_inspect`, `window_act`, `registry_read/write`, `windows_service_*`, `scheduled_task`, `firewall_rule`, `install_software`, `uninstall_software`, `list_installed_software`, `list_volumes`, `get_system_info`, `get_known_folders` |
| Web | `web_search`, `web_fetch`, `web_agent`, `web_agent_read`, `web_browser`, `tor_search`, `tor_fetch`, `tor_browser`, `browser_start`, `browser_read`, `browser_write`, `browser_gui`, `inspect_form`, `fill_form_field`, `submit_form` |
| Agent | `deep_research`, `verify_result`, `agent_queue`, `security_active` |
| Money/communication | `checkout_purchase`, `email_send`, `message_send` (all policy-gated) |

## 2. Capability status

### 2.1 Research — **VERIFIED (mechanics), PARTIAL (live web)**

- `deep_research` runs search → read → gap → requery with a bounded budget and keeps sources.
- Code: `app/agent/research.py`, `app/tools/agent_tools.py`.
- Evidence: `apps/backend/tests/test_web_research_gap.py` (4), `test_tor_research.py`,
  `test_127_autonomous_e2e.py`; the tool refuses invented sources and reports gaps.
- Not proven here: a live TinyFish search/fetch this session (needs the owner's key and the
  `live_web` marker). Tor research runs only when Tor is the required route.

### 2.2 Browser automation — **VERIFIED (local browser), PARTIAL (paid provider)**

- `browser_start/read/write/gui`, `inspect_form/fill_form_field/submit_form` drive a real browser
  through Playwright; the desktop E2E drives the real UI.
- Evidence: `test_browser_lifecycle.py` (7), `test_tor_browser.py`,
  `apps/desktop/e2e/*.spec.ts` (25 passed), installed always-ready (Computer + Tor).
- Known limit: WM-07 — the TinyFish Browser live lifecycle is still not certified end to end.

### 2.3 Computer use — **VERIFIED on Windows**

- UI Automation (`window_inspect`, `window_act`: focus/invoke/set_value/select/expand/scroll/
  close/send_keys), plus process, registry, service and scheduled-task tools.
- Evidence: `test_127_windows_uia.py`, `apps/desktop/e2e/always-ready.mjs` (real launch, relaunch,
  and recovery after a killed sidecar), `test_127_autonomous_e2e.py`.
- Status chips are health, not policy, and turn green only with a real proof (Tor: SOCKS5h
  round trip; Computer: live heartbeat).

### 2.4 Autonomous coding — **VERIFIED (free) / PASS (one real run)**

- Build loop: plan → write/patch → run tests → read failures → patch → re-test → verify.
- Evidence: `test_coding_agent.py` (13), `test_autonomous_tasks.py` (35), `test_long_autonomy.py`
  (9), and the real TaskDesk run (`e1845955`): 18 model responses, 17 executed tool calls, a
  Flask/SQLAlchemy project, 14 tests green, HTTP 200, SQLite row, independent browser check.
- The controller now owes an independent `verify_result` after the tests pass, and runs a
  retest from the directory the project's tests actually live in.

### 2.5 Autonomous repair — **VERIFIED**

- Controller-owned retest after the last write; the plan's verification step cannot be skipped;
  a failed migration/backup fails closed.
- Evidence: `test_127_agent_core.py`, `test_long_autonomy.py`
  (`a_write_after_the_test_run_forces_verification`, `the_forced_verification_is_executed_and_recorded`),
  and the real repair inside `29b149b8` (failing checks → patches → `verify_result` 8/8).

### 2.6 Memory and projects — **IMPLEMENTED, PARTIAL verification**

- Tables and API exist (`Memory` model, memories per user, project links, document indexing with
  FastEmbed, context builder budgets, `docs/memory.md`, `docs/context-builder.md`).
- Evidence: memory API tests inside the backend suite; the context builder has budget tests.
- Not proven this session: a real model resuming a long project across restarts with memory — that
  needs a LIVE (LEVEL 7).

### 2.7 Security — **VERIFIED (offline)**

- `security_active` covers SAST-style review, dependency/config checks, TLS/HTTPS probes, port
  inspection on explicitly allowed targets, secret scanning; scope is enforced by the policy.
- Evidence: `test_127_security_active.py`, `test_security_assessment.py`, `docs/local-risk-policy.md`.
- Not run this session: active checks against a real target (needs an authorised scope).

### 2.8 Orchestration, durability, verification — **VERIFIED (mechanics)**

- `ToolOrchestrator` + `LocalTaskController` own the loop, the budget, the plan, retries, the
  interlock against parallel writes, and the terminal verdict; `agent_queue` is durable.
- Evidence: `test_127_recovery_e2e.py` — a child process performs an irreversible side effect and
  dies with `os._exit(17)`; a second process resumes the *same* task id, verifies the side effect
  instead of repeating it, and completes with `evidence_ok`.
- `test_127_provenance.py` proves the journal/ledger chain; the LIVE replay reproduces
  `INCOMPLETE_TRACE` and shows the one missing event.

## 3. Scenario acceptance matrix

| # | Scenario | Status | Evidence / reason |
|---|---|---|---|
| A | Research with verifiable sources | **PARTIAL** | pipeline + offline tests; live web needs the owner's TinyFish key (not exercised) |
| B | Build an application end to end | **PASS** | real LIVE `e1845955` + free CI (`test_autonomous_tasks`, `test_long_autonomy`) |
| C | Drive an installed GUI application | **PARTIAL** | UIA + installed always-ready verified; not repeated with a real model this session |
| D | Repair a broken project | **PASS** | scripted tests + real repair inside `29b149b8` |
| E | Recovery after an interruption | **PASS (mechanics)** / LEVEL 7 NOT_TESTED | `test_127_recovery_e2e.py`; a real-model resume needs a paid LIVE |
| F | Security review of an allowed target | **PARTIAL** | offline SAST/TLS/port/secret checks pass; no authorised live target was scanned |
| G | Memory restores a project after restart | **PARTIAL** | memory + context builder implemented and tested; live cross-restart resume not proven |
| H | No capacity / inference unavailable | **PASS** | typed `WAITING_LLM`/`gpu_unavailable` paths, backoff window, no second Pod, budget rules (`test_on_demand_ai`, `test_cloud_demand`, monitor) |

## 4. Known gaps, in the order they matter

1. **LEVEL 6 and 7 need one real LIVE each.** Everything free is in place; the paid batch is
   exhausted (`$0.358 < $0.413`). No workaround is honest here.
2. **Live web research and the paid browser lifecycle** need the owner's provider key; the code
   path is verified offline, the live path is not.
3. **Long-horizon project memory** (a project worked on across days) has the storage and the
   budgets, but no end-to-end proof with a real model.
4. **A clean Windows desktop for install/uninstall** is not available for free in this
   environment: VirtualBox + Hyper-V on an 8 GB host cannot finish the Windows install, and the CI
   token cannot add a workflow (`workflow` scope missing). The install/uninstall matrix is
   therefore proven on the host with a verified snapshot and full restoration, and the Windows
   build/test gate runs in GitHub Actions (`CI`, 7 jobs).

## 5. What would close each gap

| Gap | Closure |
|---|---|
| LEVEL 6 | one LIVE run with the TaskDesk prompt at HEAD → provenance `REAL_MODEL_CONFIRMED` inside that run |
| LEVEL 7 | same, with a controlled kill mid-task and a resume in a second process |
| Live web | set the TinyFish key and run `pytest -m live_web` |
| Long project memory | a LIVE that returns to a project after a restart and continues from memory |
| Clean Windows install | a Windows VM with ≥8 GB RAM, or a self-hosted runner with an interactive desktop |
