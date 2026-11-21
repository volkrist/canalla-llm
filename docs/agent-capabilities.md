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

### 2.1. Research — **VERIFIED (live search and fetch), PARTIAL (model-driven run)**

- `deep_research` runs search → read → gap → requery with a bounded budget and keeps sources.
- Code: `app/agent/research.py`, `app/tools/agent_tools.py`, search chain in `app/tools/search/`.
- The search surface is provider-agnostic (`app/tools/search/chain.py`): the paid TinyFish provider
  is used when a key exists, and four official keyless APIs answer otherwise — Wikipedia
  (MediaWiki), arXiv (Atom), Stack Exchange and GitHub. `web_fetch` has the same fallback: the
  product's own bounded, robots-aware reader (`app/agent/webclient.py`) reads the page when no key
  is configured. Exact-domain filters, per-provider typed errors and URL dedupe live in the chain.
- Evidence: `apps/backend/tests/test_free_search.py` (17, offline with `httpx.MockTransport`),
  `test_web_research_gap.py`, `test_tor_research.py`, `test_127_autonomous_e2e.py`.
- Live proof (22 Nov 2026): `tests/test_live_web_smoke.py -m live_web` → **5 passed** against real
  public APIs with no key configured — a technical query answered by Stack Exchange + GitHub +
  Wikipedia, a research query answered by arXiv + Wikipedia, `web_fetch` reading `https://example.com`.
  Raw result: `artifacts/acceptance-search/free-search-live.json`. The packaged backend also needed
  a CA-bundle fix for HTTPS to work at all (see the release notes).
- Not proven: a *model-driven* research run (choice of queries, gap handling in the answer) — that
  needs a LIVE; and the paid TinyFish path, which needs the owner's key.

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

### 2.6. Memory and projects — **VERIFIED (restart and isolation), PARTIAL (real model)**

- Tables and API exist (`Memory` model, memories per user, project links, document indexing with
  FastEmbed, context builder budgets, `docs/memory.md`, `docs/context-builder.md`).
- Evidence: `apps/backend/tests/test_127_memory_resume.py` — three **separate processes** share only
  the SQLite file: one writes a project, a chat history and four memories (two project, one pinned
  general, one for another project, one for another account), a second builds the model context
  after a restart, a third deletes a row and a fourth restart proves it is gone. The test asserts
  that the project decisions reach the assembled messages without the user repeating them, that
  neither the other project's nor the other account's memory leaks in, that `use_count` increments
  durably, and that `use_memory = false` disables retrieval.
- Defect found by that test and fixed: `_drop_superseded` in `app/context_builder.py` treated two
  shared words as "the same fact", so two decisions of one project ("Alpha uses FastAPI…" /
  "Alpha tests run with pytest…") silently dropped the older one. Supersession now requires the
  shared vocabulary to be at least half of the shorter memory, which still replaces a correction
  (Kazan → Moscow) and keeps a different fact.
- Not proven: a real model resuming a long project across days — that needs a LIVE (LEVEL 7).

### 2.7. Security — **VERIFIED (offline engine, find → fix → re-check)**

- `security_active` covers SAST-style review, dependency/config checks, TLS/HTTPS probes, port
  inspection on explicitly allowed targets, secret scanning; scope is enforced by the policy.
- Evidence: `test_127_security_active.py`, `test_security_assessment.py`,
  `test_127_security_repair.py` (5), `docs/local-risk-policy.md`.
- `test_127_security_repair.py` scans a deliberately vulnerable local project with the product's
  own rule engine and asserts every planted defect with its CWE — SQL built by string (CWE-89),
  a hardcoded secret (CWE-798, redacted in the evidence), disabled TLS verification (CWE-295),
  `shell=True` (CWE-78), dynamic `eval` (CWE-95), debug mode (CWE-489). It then applies a fix and
  re-runs the *same* engine: the four high-risk rules disappear and the fixed tree scans clean;
  the report refuses a verdict of clean while findings exist. The fix itself is scripted in the
  test — a model-driven repair conversation is a LIVE claim and is not made.
- Not run: active checks against a real authorised target (needs an owner-approved scope).

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
| A | Research with verifiable sources | **PARTIAL (search + fetch VERIFIED live)** | the free chain answers from real public APIs and `web_fetch` reads pages without a key; a model-driven research run still needs a LIVE |
| B | Build an application end to end | **PASS** | real LIVE `e1845955` + free CI (`test_autonomous_tasks`, `test_long_autonomy`) |
| C | Drive an installed GUI application | **PARTIAL** | UIA + installed always-ready verified; not repeated with a real model this session |
| D | Repair a broken project | **PASS** | scripted tests + real repair inside `29b149b8` |
| E | Recovery after an interruption | **PASS (mechanics)** / LEVEL 7 NOT_TESTED | `test_127_recovery_e2e.py`; a real-model resume needs a paid LIVE |
| F | Security review of an allowed target | **PARTIAL (engine VERIFIED)** | the planted-defect matrix and the fix→re-check loop run against the product's own scanner on a local fixture; the fix is scripted, and no owner-approved live target was scanned |
| G | Memory restores a project after restart | **PASS (context)** / LEVEL 7 NOT_TESTED | `test_127_memory_resume.py`: cross-process restore, project and account isolation, durable usage counters, deletion; a live model continuation still needs a paid LIVE |
| H | No capacity / inference unavailable | **PASS** | typed `WAITING_LLM`/`gpu_unavailable` paths, backoff window, no second Pod, budget rules (`test_on_demand_ai`, `test_cloud_demand`, monitor) |

## 4. Known gaps, in the order they matter

1. **LEVEL 6 and 7 need one real LIVE each.** Everything free is in place; the paid batch is
   exhausted (`$0.358 < $0.413`). No workaround is honest here.
2. **A model-driven research run** and the paid browser lifecycle need a LIVE / the owner's key;
   the free search and fetch paths themselves are verified live.
3. **Long-horizon project memory** (a project worked on across days) has the storage, the budgets
   and a cross-process restart proof; only the real-model continuation is unproven.
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
