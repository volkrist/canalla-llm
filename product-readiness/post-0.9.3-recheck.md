# Recheck after 0.9.3 merge

0.9.3 (Weak-Model Reliability & Grounded Execution) is **in progress on
another worktree**. This planning tree is frozen to **0.9.2 / origin/main**.

After 0.9.3 merges to `main`, re-read the repo and tick each row.
Do **not** assume today’s limitations still exist. Do **not** assume they
are gone.

Cheap audit: walk this file, update `current-state.md` stamps from
`CURRENT VERIFIED FROM REPO` where needed, adjust MUST items that 0.9.3
already satisfied.

---

## Product statements that may go stale

| # | 0.9.2 statement | Why it may change |
|---|---|---|
| 1 | Planner/orchestrator may skip inspect/test; weak tool XML leaks | reliability loop, compact, plan |
| 2 | Assistant may claim “no filesystem access” after successful tools | grounding / host observation labels |
| 3 | No `continue_task` module | new continue_task path |
| 4 | Progress is mostly tool names + TaskPanel enums | `progress` events / copy |
| 5 | Intent is regex/`needs_research` heuristics | `intent` classifier |
| 6 | Facts are not a first-class bounded store | `facts` |
| 7 | Scope of edits may drift | `scope` |
| 8 | TinyFish classify / Agent inject rules | `classify.py` |
| 9 | Coding path quality on Qwen | `coding.py` + tests `test_093_reliability.py` |
| 10 | Chat stream error/task events | `chat_stream.py` |
| 11 | ContextBuilder layout | `context_builder.py` |
| 12 | Policy/orchestrator confirmation frequency | may reduce Ask-like nags **or** add more gates |
| 13 | `tools_max_*` / task ceilings | pyproject/tests may retune |
| 14 | Desktop host/process/git (dirty in the other tree) | only if those land; **this tree’s desktop is 0.9.2** |
| 15 | README version narrative | will become 0.9.3 |
| 16 | OpenAPI still 0.8.2 | maybe bumped |
| 17 | Eval 97-case scores vs 0.9.2 | must re-run; do not reuse |
| 18 | WAITING_LLM / resume behavior | continue_task may auto-resume |
| 19 | “New task created” vs same id | must re-verify copy and API |
| 20 | Over-questioning (“should I read?”) | primary 0.9.3 goal — recheck HIGH autonomy |
| 21 | Deep research looping | grounding may stop useless fetches |
| 22 | Mock vs real provider tests | new fixtures |
| 23 | Computer default still **Ask** | 0.9.3 may not touch UX defaults |
| 24 | No backend sidecar | 0.9.3 should **not** need to; still a 1.0 MUST |
| 25 | No on-demand GPU start | likely **unchanged** — still MUST |
| 26 | JWT in memory only | likely **unchanged** |
| 27 | NSIS without backend | likely **unchanged** |
| 28 | Idle stop ignores non-generation tasks | likely **unchanged** |
| 29 | `ALLOW_USER_COMPUTE_START=false` | likely **unchanged** |
| 30 | Auto memory capture off | likely **unchanged** |

---

## Recheck procedure (after merge)

1. New worktree from `origin/main` (do not reuse the dirty 0.9.3 tree).
2. `git log 0.9.2..HEAD --oneline`.
3. Diff this folder’s “CURRENT VERIFIED” claims against new code.
4. Run Gate A tests locally (no paid GPU required for the audit).
5. Update `roadmap-to-1.0.md` MUST list: move done items to “satisfied in 0.9.3”.
6. Do not start LoRA because 0.9.3 landed.

---

## Still true even if 0.9.3 is perfect

- This is not a feature release and not production fixes in
  `planning/1.0-product-readiness`.
- Zero-terminal, installer sidecar, status chips, forgotten-GPU policy,
  and LoRA gate remain product work **after** reliability.
- `AUTONOMY=HIGH` and `RESEARCH_DEPTH=DEEP` stay non-selectable.
