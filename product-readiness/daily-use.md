# Daily use, autonomy, and capability UX

Twelve household scenarios plus coding/web/Tor/computer rules.
`AUTONOMY = HIGH`, `RESEARCH_DEPTH = DEEP`. No mode radios for those.

**CURRENT VERIFIED FROM REPO** notes where 0.9.2 already matches or fights
the target.

---

## Memory vs project vs RAG vs task

| User concept | Lives | Survives restart | Example |
|---|---|---|---|
| Memory | curated facts/prefs | yes | «меня зовут…», «не коммить без просьбы» |
| Project | named workspace context | yes | «репо alex-llm» |
| Files / RAG | uploaded docs as **sources** | yes | PDF contract excerpts labeled D |
| Task state | this job’s plan/checkpoints | yes until terminal | «ещё не прогнал pytest» |

Do not merge them into one «Knowledge» blob. The UI already separates
Память / Проекты / Файлы / Задача — keep names, improve explanations in
empty states.

Auto memory capture is off today. 1.0 can stay manual (**SHOULD**, not MUST)
because a weak model writing memory incorrectly is worse than none.

---

## Web

User does not pick Search vs Fetch vs Browser vs Agent. Alex does.

Main: **Web: Auto**. Advanced: Off (airgap), paid ceilings.
«Найти в интернете» as an explicit **this-turn** intent is allowed — it is
not a depth selector.

Paid actions: one cost line when Agent/Browser actually run.

No TinyFish for `.onion`.

---

## Tor

Capability, not networking homework.

User: «Через Tor найди…» / onion URL → Tor: Starting/Ready → T sources.
Status: Ready / Starting / Unavailable.
No SOCKS port, no Marionette, no “install Firefox profile”.

If Tor Browser is required and missing: Tor: Unavailable,
«Нужен установленный Tor Browser» + link — not a terminal command.

---

## Local computer

Natural language only: «Создай папку», «Найди файл», «Установи jq»,
«Исправь проект».

Report **results** (path, version, test summary), not tool names.
Long work: compact progress. Verified result: actual outcome.

Default Computer policy = HIGH: auto READ + safe NORMAL_CHANGE.
**CURRENT default Ask fights this.**

---

## Coding

Ideal: understand → baseline → plan → edit → test → fix → verify → summary.

User should not need to say: open file, patch, run pytest, git diff —
unless they want to.

| Topic | Rule |
|---|---|
| Diff | show a short summary when files changed; full diff on click / Advanced |
| Tests | run when the project has a known command; mention FAIL→PASS in the summary |
| Commit | only if asked or `auto_commit` (**SHOULD** remain off by default) |
| Push | always confirm (SENSITIVE). Never `--force` in 1.0 happy path |

**CURRENT:** matches commit/push policy. Weak model still needs the
controller to force inspect/test (**RECHECK AFTER 0.9.3**).

---

## Scenarios

### 1. Normal chat question

UX: answer in the bubble. No tools required.
Statuses: AI Ready (after on-demand start if needed). Confirmations: none.
Auto: GPU start if cold; idle-stop later.

### 2. Research task

UX: compact research progress; sourced answer (W).
Statuses: Web Auto/Ready; AI Ready.
Confirmations: none for Search/Fetch; paid Agent/Browser only if router
selects them — show cost.
Auto: Deep investigation within bounds; not endless.

### 3. Work with local files

UX: Computer Ready; result path/list.
Confirmations: none for read/create in trusted scope; SENSITIVE for delete.
Auto: host jobs.

### 4. Fix coding project

UX: Planning → Testing → Editing → Testing → Completed + short summary.
Confirmations: none for in-repo patches; SENSITIVE install/push.
Auto: discover test command, baseline, patch, retest.

### 5. Install software

UX: Needs confirmation (SENSITIVE) with package name, scope (user vs machine),
UAC if elevation.
Statuses: Computer Ready.
Auto: after allow, install and report version. No UAC bypass.

**CURRENT:** jq user-scope winget proof in 0.9.1 notes.

### 6. Multi-hour autonomous project

UX: Task panel with human phases; Pause/Resume/Stop.
Statuses: AI stays Ready (GPU held); Computer as needed.
Confirmations: only SENSITIVE/CRITICAL along the way.
Auto: budgets; queue; no GPU restart between steps.
If runtime cap hits: structured budget error, work kept.

### 7. Tor research

UX: Tor Ready/Starting; T sources; no TinyFish.
Confirmations: none for read.
If SOCKS down: Unavailable, no Direct onion.

### 8. Read uploaded docs

UX: attach → if embeddings missing, **auto-prepare with progress** (today:
manual prepare). Then D sources under the answer.
Chat without files still works if prepare fails.

### 9. Resume interrupted task

UX: «Задача восстановлена» + Resume if user paused.
Same task_id. No duplicate writes.
**CURRENT:** resume API exists; copy is technical.

### 10. Close/reopen Desktop

UX: session restored (1.0); task still there; AI chip shows if GPU was held
by backend.
Confirmations: none extra.
**CURRENT:** login wall; backend may still hold GPU (good) or user killed
both (money risk).

### 11. Internet unavailable

UX: Web/Tor/AI (remote GPU) degrade honestly. Local files/coding if Computer
Ready. No fake citations.

### 12. RunPod unavailable

UX: AI Unavailable/Waiting. Local UI works. Task WAITING_LLM, auto-retry
search if that is the policy. No mock pretending to be Qwen.

---

## Daily acceptance sketch

Used in `release-gates.md` as the manual checklist seed.
Each row: expected chip, confirmation count, automatic work.
