# Alex LLM — 1.0 product / UX / runtime readiness

This directory is an **acceptance spec and roadmap**, not a release.

It is **not** 0.9.4. It does **not** patch production code.

Goal: take a technically capable agent and specify what must be true
before it is a dependable daily product called **1.0**.

Architecture that the product must protect:

```text
thin model (Qwen3.8-27B-Uncensored Q5_K_M)
+ thick deterministic controller
AUTONOMY = HIGH          (no selector)
RESEARCH_DEPTH = DEEP    (no selector)
```

The weaker model is not an excuse for a weaker outcome. Routing,
verification, tools, recovery, and UX hide the weakness.

---

## Identity

| | |
|---|---|
| BRANCH | `planning/1.0-product-readiness` |
| WORKTREE | `C:\Users\Volkr\Documents\Codex\2026-09-13\x20\outputs\alex-llm-product` |
| BASE HEAD | `b23debdd96a8c009a50e865d7aec65578dfc64f0` (origin/main, **0.9.2**) |
| COMMITS | this tree, `product-readiness/**` only |
| PUSH | `origin/planning/1.0-product-readiness` only |
| Merge | **no** |
| Push main | **no** |
| Production code modified | **NO** |
| Paid resources | **$0** |

Parallel work not touched:

- `alex-llm` — 0.9.3 reliability
- `alex-llm-eval` — `eval/real-world-suite` (97 cases)

---

## Documents

| File | Contents |
|---|---|
| [current-state.md](current-state.md) | factual 0.9.2 map |
| [first-run.md](first-run.md) | install → chat |
| [runtime-lifecycle.md](runtime-lifecycle.md) | processes, session, data, update, degrade |
| [runpod-lifecycle.md](runpod-lifecycle.md) | scenarios A–I, idle policy, cost UX |
| [ux-states.md](ux-states.md) | five chips, progress, autonomy, deep research |
| [error-recovery.md](error-recovery.md) | taxonomy + «задача восстановлена» |
| [daily-use.md](daily-use.md) | 12 scenarios, memory/web/tor/coding |
| [security-confirmations.md](security-confirmations.md) | human allow-once |
| [installer.md](installer.md) | packaging gaps, zero-terminal, settings classes |
| [observability.md](observability.md) | diagnostics + support bundle |
| [release-gates.md](release-gates.md) | gates A–K + manual checklist |
| [roadmap-to-1.0.md](roadmap-to-1.0.md) | minimal sequence, MUST/SHOULD/LATER |
| [post-0.9.3-recheck.md](post-0.9.3-recheck.md) | stale-claim list |
| [lora-gate.md](lora-gate.md) | **do not tune without baseline** |

Every proposal answers: **what real user failure does this solve?**
If none, it is not in 1.0.

---

## Current product state

**CURRENT VERIFIED FROM REPO (0.9.2).** Recheck after 0.9.3.

Already exists and is real:

- Desktop chat (Tauri), auth, history, drafts, Markdown
- Memory, projects, files/RAG (CPU embeddings, explicit prepare)
- Web Off/Auto/On + TinyFish Search/Fetch (free) + Agent/Browser (paid, gated)
- Tor Search/Fetch/Browser, no Direct fallback for onion
- Local Computer host, Job Objects, risk levels, coding tools, git
- Autonomous tasks: plan, pause/resume/stop, checkpoints, workspace queue
- RunPod controller: search, start, idle/budget stop, volume retain, usage
- Structured tool/compute error codes (partial UI mapping)

What exists as **operator software**, not a household product:

- Two PowerShell scripts to start anything
- Backend not in the installer
- JWT forgotten on every launch
- GPU started from ComputePanel, not from “I asked a question”
- Computer default **Ask** (confirms READ)
- Statuses are technical (`starting_llm`, `MOCK MODE`, tool names)
- No diagnostics export, no updater, unsigned NSIS

Default `LLM_PROVIDER=mock`. Production alias is configured but the daily
path does not present Qwen until an admin starts a Pod.

---

## Top 1.0 gaps (dependency order)

1. **Reliability of the weak model** — owned by 0.9.3 + 97-case eval; this
   tree does not implement it.
2. **Backend as part of the app** — without this, zero-terminal and GPU
   monitor-while-window-closed cannot be true.
3. **Installer + data dir** — without this, (2) never leaves developers.
4. **Session restore + owner can start AI** — otherwise first-run still
   feels like a lab.
5. **On-demand GPU + idle policy that understands tasks** — money and
   “just works.”
6. **Product status + progress + human errors/confirmations** — hide
   internals; keep HIGH/DEEP without knobs.
7. **Diagnostics / support bundle / upgrade preservation** — operable 1.0.
8. **Gates A–I, then LoRA decision (default skip), then K.**

---

## First run

**Now:** clone, Python, Node, `setup-backend`, two terminals, register,
maybe mock, maybe Compute click.

**Target:** install → launch → backend starts → host pairs → session ready
→ chat → GPU only if the message needs the model.

Acceptance: `first-run.md`.

---

## RunPod lifecycle

**Now:** capable controller, user-driven start, idle after **no generation**,
external pods skip idle/budget, backend must be up to enforce stop.

**Gaps:** Scenario A (on-demand), F (manage adopted pods), H (task/confirm
holds idle), I (no flap — mostly true if nobody stops between tasks),
Quit-with-monitor.

Volume delete: already never. Keep.

---

## Zero terminal

Still requires a terminal today: venv, start backend, start desktop,
`.env`, Alembic (if not using the script), embedding CLI, often Git/Tor
install, always developer build toolchain.

Must vanish for standard use: all of the above except Git/Tor **install
on a bare OS** (SHOULD, guided in UI). Developer pytest/playwright stay
developer-only.

---

## UX states

Main: **AI · Computer · Web · Tor · Memory**
each `Ready | Starting | Waiting | Needs confirmation | Unavailable | Error`.

Progress: Planning / Working / Researching / Editing / Testing /
Verifying / Waiting for confirmation / Completed.

Advanced: Pod ID, tool names, costs, codes. No secrets.

---

## Recovery

User sees **«Задача восстановлена»**, same `task_id`, no duplicate
side effects. Not «создана новая задача».

---

## Installer

Now: unsigned NSIS, UI only.

1.0: UI + backend runtime + migrate on start + data under
`%LOCALAPPDATA%\Alex LLM` + upgrade keeps DB/memory/projects/files.

---

## Settings

**AUTONOMY = HIGH** fixed. **RESEARCH_DEPTH = DEEP** fixed.
Selectors must not exist (they do not exist today — do not add them).

Stays user-facing: theme, font, memory/projects/files, export.
Becomes automatic: backend, pairing, GPU start/stop, routing of
Search/Fetch/Browser/Agent, Computer Trusted default.
Advanced: URLs, ceilings, Off switches, SKUs.
Removed from the composer: Ask-as-default, tool-name headlines.

---

## LoRA / fine-tuning

**NOT STARTED.**

Baseline requirement: `lora-gate.md`.
Uncensored behavior preservation: **critical reject criterion**.

---

## Roadmap to 1.0

```text
0.9.3 reliability
→ real-world eval (97)
→ small 0.9.x fixes
→ product/runtime polish
→ 0.99 RC
→ LoRA decision (default: skip)
→ final regression
→ 1.0
```

Parallel: this spec; eval harness; installer spike (not on `main`).

---

## MUST / SHOULD / LATER

| Class | Count | Where listed |
|---|---|---|
| MUST before 1.0 | **28** | `roadmap-to-1.0.md` |
| SHOULD before 1.0 | **12** | same |
| AFTER 1.0 | **14** | same |

After 1.0 unless critical: extra providers, mobile, plugin marketplace,
SSO/SaaS, encryption-at-rest, English UI, LoRA.

---

## 0.9.3 recheck

See `post-0.9.3-recheck.md`. Planner, grounding, progress, continue_task,
and over-questioning claims are the hottest. Sidecar/GPU/installer claims
likely stay valid.

---

## Cost of this audit

| | |
|---|---|
| GPU | 0 |
| TinyFish paid | 0 |
| Total | **$0** |
