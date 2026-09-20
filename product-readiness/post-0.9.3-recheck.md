# Recheck after 0.9.3 merge

**Audited:** 20 September 2026 against `origin/main` `80c53ade3756ca8fd10471295f9b29bbe89d17f0` (0.9.3 closeout).

Reliability stage is **CLOSED**. Do not reopen a broad repair loop. Known limitations (WM-07 Browser live lifecycle, CD-08 REAL stale-SHA coverage) stay focused backlog.

This file is the cheap post-0.9.3 readiness recheck. Production code was **not** changed on the planning branch.

---

## Product statements

| # | 0.9.2 statement | After 0.9.3 |
|---|---|---|
| 1 | Planner may skip inspect/test; weak tool XML leaks | **Improved.** Thick controller, compact, plan, no-progress. Residual UI copy can still be tightened. |
| 2 | Assistant claims “no filesystem access” after tools | **Closed** for core grounding (WM-01 class). |
| 3 | No `continue_task` module | **Present** (`continue_task.py`, queue monitor). |
| 4 | Progress is mostly tool names | **Improved** (`progress.py`, task events). Five-chip UX still missing. |
| 5 | Intent is regex heuristics | **Present** (`intent.py`) — still deterministic, not a user selector. |
| 6 | Facts not first-class | **Present** (`facts.py` VerifiedFactStore). |
| 7 | Scope of edits may drift | **Present** (`targets.py`, TaskScope). |
| 8 | TinyFish classify / Agent inject | **Agent READ_ONLY.** Browser routing/`server_policy` work; live Browser lifecycle = known limitation. |
| 9 | Coding path on Qwen | **DoD + fresh verification.** CD-08 REAL stale-SHA = coverage debt, not confirmed product FAIL. |
| 10 | Chat stream error/task events | **Present**; remaining work is copy/UX. |
| 11 | ContextBuilder layout | **Memory vs RAG independent; Newhaven supersedes Oldtown.** |
| 12 | Confirmation frequency | **Payload-bound, replay-protected, allow-once.** Headlines still SHOULD. |
| 13 | `tools_max_*` ceilings | **Present**, retuned in 0.9.3. |
| 14 | Desktop host/process/git | **Owned Job Object processes** (0.9.3). Sidecar backend was still missing at closeout; first slice is `feat/product-runtime-foundation`. |
| 15 | README version | **0.9.3** |
| 16 | OpenAPI still 0.8.2 | **Fixed** — FastAPI `version="0.9.3"`. |
| 17 | Eval 97-case scores | Do **not** reuse 0.9.2. Canonical eval rebased; no new 97 REAL this stage. |
| 18 | WAITING_LLM / resume | **continue_task** auto-resumes pending; GPU on-demand still missing. |
| 19 | “New task created” vs same id | **Improved** in controller; UX copy still a MUST polish. |
| 20 | Over-questioning | **HIGH autonomy in controller.** Computer UI default remains **Ask** — still a 1.0 MUST. |
| 21 | Deep research looping | **Grounding + no-progress.** RESEARCH_DEPTH stays DEEP, no selector. |
| 22 | Mock vs real provider tests | **Expanded** (`test_093_*`). |
| 23 | Computer default Ask | **Still Ask.** |
| 24 | No backend sidecar | **First implementation slice started** (not on this planning branch). |
| 25 | No on-demand GPU start | **Unchanged** — next slice after runtime foundation. |
| 26 | JWT in memory only | **Unchanged** (session restore still MUST). Desktop now can persist **backend** JWT in data dir. |
| 27 | NSIS without backend | **Unchanged.** Packaging spike documented, not shipped. |
| 28 | Idle stop ignores non-generation tasks | **Unchanged.** |
| 29 | `ALLOW_USER_COMPUTE_START=false` | **Unchanged.** |
| 30 | Auto memory capture off | **Unchanged** (after 1.0). |

---

## Still true

- 0.9.3 is a development release, not 1.0.
- AUTONOMY=HIGH and RESEARCH_DEPTH=DEEP stay non-selectable.
- Zero-terminal, installer sidecar, status chips, forgotten-GPU policy, LoRA gate remain product work.
- Do not start LoRA.

## First product slice

**RUNTIME FOUNDATION** on `feat/product-runtime-foundation` (not this planning branch): Desktop-owned backend, data root, port policy, JWT file, Starting/Ready/Error.

Next recommended slice after that PASSes: **on-demand AI / RunPod lifecycle**.
