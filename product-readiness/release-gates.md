# 1.0 release gates and daily acceptance

Gates are sequential where they depend on each other. Fail = no 1.0 tag.

This is not 0.9.4 and not a feature dump.

---

## GATE A — Core regression green

Backend pytest, ruff, alembic check, desktop unit tests, Playwright e2e
on mock. Windows CI if credentials allow (template already in
`docs/ci-windows.yml`).

User failure: shipping a desktop that cannot login or stream.

## GATE B — 97 evaluation suite target

Branch `eval/real-world-suite` (separate worktree) already holds the 97
cases. 1.0 requires the **agreed target** on that suite against the
production model — not a new suite invented here.

Do not lower the bar because the model is weaker; the controller must
compensate.

**RECHECK AFTER 0.9.3:** reliability work should move this score; re-run
rather than citing 0.9.2 numbers.

## GATE C — Real OrcaRouter real-world trials

A short live script on ONE managed Pod, volume preserved, **RUNNING GPU
FINAL = 0**:

- normal chat
- research (Search/Fetch; Agent/Browser only if needed)
- local file
- coding FAIL→PASS
- pause/resume same task_id
- Tor only if SOCKS present
- idle or quit leaves no GPU

Paid spend is a later execution with explicit approval — not this planning
branch ($0).

## GATE D — Zero-terminal normal setup

`installer.md` MUST rows. A tester without the repo completes first-run
(`first-run.md` acceptance).

## GATE E — RunPod lifecycle reliable

Scenarios A–I in `runpod-lifecycle.md`. Especially: on-demand start,
no flap, adopt after restart, health failure terminates, no second Pod.

## GATE F — No forgotten paid resources

Quit / idle / crash paths: no stray Pod; TinyFish browser/agent sessions
closed (0.9.2 already DELETEs leftovers — keep). Volume never deleted.
Session budget and hourly ceiling still bind.

## GATE G — Crash/restart recovery

Same task resumes; user copy «Задача восстановлена»; no duplicate local
side effects. Backend spawn survives window close while GPU is up.

## GATE H — Installer / upgrade

Preserve SQLite, settings, memory, projects, files, pairing. Pre-migrate
backup. Uninstall can keep data. Unsigned build is a **SHOULD** fail for
public 1.0; private family distribution may ship with a warning.

## GATE I — Security / secret audit

Diagnostics and support bundle contain no secrets. Confirmations human.
CRITICAL still fail-closed. No Tor→Direct. Agent still not a side-effect
bypass. JWT not in localStorage.

## GATE J — LoRA decision

See `lora-gate.md`. Default for 1.0: **do not tune**. If someone insists,
the baseline matrix must pass. A regressing uncensored/refusal profile
**rejects** the LoRA. This gate is a decision, not an implementation.

## GATE K — Final regression

Re-run A + B + a subset of C after any LoRA decision (even “skip”).
Manual checklist below all green.

---

## Manual daily-user checklist (pre-1.0)

Run on a machine that is **not** a developer checkout, using the installer.

| # | Action | Pass if |
|---|---|---|
| 1 | Install | no terminal, no `.env` edit |
| 2 | Launch | chat UI without “start the server” |
| 3 | Ask a normal question | answer; GPU auto-start if needed |
| 4 | Research | sourced answer; compact progress; no tool XML |
| 5 | Read a local file | result; no READ confirmation in default HIGH |
| 6 | Edit a file in trusted scope | file changed; short summary |
| 7 | Code task | tests mentioned only as results; FAIL→PASS or honest fail |
| 8 | Pause / resume | same task; no extra writes |
| 9 | Restart app | session restored; task still listed |
| 10 | Resume task | «восстановлена» / continues |
| 11 | Browser (if Web needed JS) | Alex chooses; cost line if paid |
| 12 | Tor (if available) | onion via Tor only |
| 13 | Stop task | STOPPED; no stray tools |
| 14 | Close app | window gone |
| 15 | Ensure no GPU left running | after idle or Quit: supplier shows 0 running; volume present |

Also: no AUTONOMY/DEEP radios appeared; no forgotten TinyFish session
(Advanced shows none).
