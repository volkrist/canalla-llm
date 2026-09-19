# RunPod user experience and auto-stop

Design for a household GPU: Alex spends money when work needs the model,
and stops spending when work is done. The user does not operate RunPod.

**Do not implement in this branch.**

**CURRENT VERIFIED FROM REPO (0.9.2)** is marked on each scenario.

---

## Product rules

1. One managed Pod per Network Volume. Never create a second because the
   UI was clicked twice or a task restarted.
2. Volume is never deleted automatically.
3. Cost ceilings (hourly + session) are always enforced while the backend
   monitor is alive. 1.0 must keep that monitor alive if a Pod exists.
4. The chat UI shows a compact cost chip, not a GPU control panel.
5. Exact GPU SKU, CUDA, image, ports, gateway keys stay in Advanced.

---

## Compact cost (normal view)

When a managed session exists or is starting:

```text
AI · L40S · $1.09/ч · 12 мин · ≈ $0.22
```

If idle-stop is armed:

```text
остановится через 8 мин без задач
```

Not in the main chat: Pod ID, datacenter enum, VRAM floor, search interval,
catalogue table, `auto_connect` checkbox.

Persistent volume monthly cost: show in Advanced **if** a real supplier
figure exists. Today the catalogue does not expose it — **do not invent**.
Copy if unknown: «Хранилище оплачивается отдельно, даже когда GPU выключен.»

---

## Scenario A — on-demand start

```text
Alex launched (no GPU)
→ user sends a task that needs the model
→ Alex decides model required
→ starts appropriate GPU (search + auto_connect under saved ceilings)
→ waits for llama.cpp health + exact model alias
→ same user message continues
```

**CURRENT:** user must open Compute and click «Найти GPU и подключиться».
Chat with `llamacpp` is blocked until Ready. Tasks go `WAITING_LLM`.

**1.0 acceptance:** no Compute click for the happy path. Confirmation of
spend: **once per session start**, in human language:

> «Alex сейчас запустит GPU NVIDIA L40S примерно за $1.09 в час.
> Сессия остановится сама после простоя. Лимит этой сессии: $3.00.»

If the user already approved auto-start for this machine (owner setting
in Advanced, default ON for local single-user 1.0), do not re-ask every
message. Re-ask if hourly price rose above the last confirmed ceiling
(`price_changed` already exists).

---

## Scenario B — GPU unavailable (none in catalogue)

States: AI: Waiting → eventually AI: Unavailable.

Copy:

- happened: no compatible GPU in the allowed datacenter/VRAM/price
- tried: searching every N seconds (existing `search_interval`)
- safe: no Pod created; volume intact
- retry: automatic while the task is live or the user is waiting
- user: wait, cancel task, or raise ceiling in Advanced

**CURRENT:** `no_gpu` / `no_compatible_gpu` / `price_limit` with search retries
if armed.

---

## Scenario C — selected GPU unavailable, fallback

Preferred SKU (L40S) missing → next cheapest NVIDIA that still meets
VRAM/DC/price, **without** creating two Pods.

Tell the user once: «L40S нет в наличии, запускаем [other] за $X/ч».

**CURRENT:** empty `gpu_id` allows any suitable NVIDIA; UI default pins
`NVIDIA L40S`. Search retries on definite placement reject; does not
substitute GPU inside a single create attempt (correct). 1.0 should unpin
the happy path to “automatic cheapest meeting floors” and keep exact ID
in Advanced.

---

## Scenario D — Pod starts, model fails health

Terminate managed compute (existing `startup_failed` / `startup_timeout` /
`model_mismatch`). Volume kept. Task: `WAITING_LLM` then auto-retry start
**bounded** (e.g. 2 retries / session) to avoid spend loops.

Copy must say the model did not become ready, GPU was released, volume safe.

**CURRENT:** terminate on startup fail/timeout; **no** automatic re-create
loop (good against spend; 1.0 may retry with a cap and user-visible count).

---

## Scenario E — network interruption

Desktop ↔ backend local: Computer/Web may still work; GPU monitor is local
HTTP to RunPod API.

Backend ↔ RunPod API loss: keep session row; do not create another Pod;
`health_unknown` / connection failed; resume when API returns; **never**
treat unknown as “safe to create”.

**CURRENT:** `create_unknown` never auto-retries create (keep).
`connection_failed` continues checking.

---

## Scenario F — desktop/backend restart while Pod still running

Adopt the tracked Pod (or the single volume-matching Pod). Do not create.
If it is healthy, AI: Ready and the **same** task resumes.
If it is the billed Pod but unmanaged (`external_compute`), 1.0 should
**take it under management** when it matches Alex’s name/volume, so idle
stop applies. Today external skips idle/budget — that is a money leak.

**CURRENT:** recover() + adopt; external = no auto-stop.

---

## Scenario G — Pod disappears

Supplier `not_found` / EXITED: finish session, AI: Unavailable.
Live task → `WAITING_LLM` + auto Scenario A if the user/task still wants
work. No duplicate side effects on local tools (existing digest skip).

---

## Scenario H — task finished, idle timer, auto stop

After Ready and **no keep-alive reason** (below), stop GPU after N minutes.
Default N = 10. Never = Advanced only, not the 1.0 default.

**CURRENT:** idle applies only `status==ready`, no active **generation**,
`last_activity_at` age. Autonomous task that is paused or waiting may
still look idle if no generation is open — **gap**.

---

## Scenario I — multiple tasks, no GPU flapping

New user message or queued task while Pod is up: reset idle timer, do not
stop/start. Queue WRITE tasks as today. One Pod serves all local users
(today: shared singleton compute). 1.0 stays single-owner local.

---

## Auto-stop policy (recommended)

### Keep GPU

Reset / hold idle timer when any of:

- active generation (token stream)
- non-terminal autonomous task (`PLANNING`…`VERIFYING`, `RETRYING`,
  `RECOVERING`)
- `WAITING_CONFIRMATION` (user may be reading the dialog)
- `WAITING_DEVICE` / `WAITING_WORKSPACE` (work is not done)
- `WAITING_LLM` is the opposite — GPU is missing; do not hold a dead session
- user activity in the last N minutes (composer send, explicit Resume)
- TinyFish Agent/Browser **run in flight** for a live task (planning still
  needs the model before/after)

### Stop GPU

When **all** of:

- Ready
- no keep-alive reason
- idle N minutes
- not mid-create/start

### TinyFish-only / Browser-only / Tor-only tasks

They still need the local planner model unless a future design runs tools
without LLM (not 1.0). GPU stays for the whole task.

### Desktop closes

If a managed Pod is running, backend **must** keep running to enforce this
policy. User-facing: closing the window is fine; «AI остановится сам».

If the user **Quit Alex** (full exit): stop GPU first (graceful, like
idle), then exit backend. Confirm only if a live CRITICAL wait exists.

### Confirmation wait longer than N

Do **not** kill GPU under a pending SENSITIVE/CRITICAL card. Optionally
notify: «GPU ждёт вашего подтверждения — это стоит деньги». After a long
cap (e.g. 30 min) pause the task, stop GPU, keep the card; Resume restarts
GPU (Scenario A) without duplicating local actions.

---

## Acceptance rules (money)

| Rule | Pass |
|---|---|
| No forgotten running Pod after Quit + idle policy | monitor or terminate-on-quit |
| No accidental multiple Pods | existing lease + adopt + `multiple_compute` visible recovery |
| Volume never auto-deleted | keep current client (no delete API) |
| Cost ceilings obeyed | keep budget/price terminate; 1.0 must not disable them |
| No GPU flap across tasks | Scenario I |
| External adopted Pod gets the same idle/budget | new vs today |

**RECHECK AFTER 0.9.3:** whether tasks hold generation leases for the whole
plan (affects idle). Do not assume.
