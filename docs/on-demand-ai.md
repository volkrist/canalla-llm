# On-demand AI / RunPod lifecycle (0.9.3)

Desktop already owns the local backend. This slice owns **paid GPU** the same way: the user sends a normal message while compute is off, Alex starts one managed Pod, waits until the model is actually ready, continues **the same task**, then stops the Pod. The Network Volume is never deleted.

This is not an installer, not first-run UX, not LoRA, and not a reliability rewrite.

## What the user sees

| Compact AI | Meaning |
|---|---|
| AI Off | No managed GPU. Opening the app does not start one. |
| AI Starting | Capacity search, create/adopt, or model load. Chat copy: «Запускаю AI…» |
| AI Ready | llama.cpp alias is healthy. The original message continues. |
| AI Waiting | Reconcile (`create_unknown`) or a confirmation/external hold. |
| AI Unavailable | No RunPod key, no capacity, or production mock (never shown as Ready). |
| AI Error | `MULTIPLE_COMPUTE`, budget, or structured failure. |

Normal UI does not show CUDA flags, volume IDs, or Pod internals. `/llm/status.diagnostic` keeps advanced fields for later.

Composer send is allowed whenever the **local backend** is healthy. GPU off is not a send blocker.

## Trigger

GPU starts only when execution needs the production llama.cpp provider (`LLM_PROVIDER=llamacpp` and `LLM_CONNECTION_MODE=runpod`).

Not started for: app open, settings, health, navigation, mock/test chat, static llama.cpp.

## State machine (compute)

Existing controller states are reused, not replaced:

`offline` → `searching` / `gpu_found` → `creating` → `starting_pod` → … → `loading_model` → `ready` → `stopping` → `stopped`

Plus:

- `create_unknown` — create timed out or 5xx. List Pods before any retry. At most one retry after the list is empty.
- `multiple_compute` — more than one billed Pod on volume `uwgeaie5b0`. Do not create or destroy.
- `external_compute` — explicit `/compute/start` adopted an unknown-named Pod as unmanaged (historical panel path).
- On-demand path: a **single** compatible volume Pod is adopted as **managed** (`adopted_by_alex=true`).

AI compact mapping lives in `app/compute/runtime.py`.

## Ownership (persisted)

`compute_sessions`: `managed`, `created_by_alex`, `adopted_by_alex`, pod/gpu/dc/price/budget/idle/activity/spend.

`compute_control`: demand idempotency, confirmation digest, create_attempts.

- **Managed**: Alex started or on-demand-adopted this Pod. Idle, budget, model-health timeout, app Quit may stop it. Volume untouched.
- **External**: panel-adopted unknown Pod. Quit does **not** terminate it. Admin `confirm_external` still required for stop.

## Confirmation

Starting paid GPU is a SENSITIVE side effect. One ToolRun `compute.start` per price decision:

«Запустить AI на L40S примерно за $1.09/ч?»

Envelope digest / allow-once / 5 minute TTL unchanged. After allow, the **same** chat/task continues. Ask again after stop, material price change, or budget/cap change.

Tests and the compute panel `StartRequest.confirmed=True` path still use the existing quote confirmation.

## First owner

`ALLOW_USER_COMPUTE_START` stays false. The oldest registered local user (and admins) may request managed compute. Other users cannot start paid GPU. Policy is `can_start_compute` in `app/security.py`.

## Same-task resume

If the model is not ready:

1. Persist the original user message (one `task_id` / one user row).
2. Task `WAITING_LLM` with reason + optional `compute_session_id`.
3. SSE stays open with «Запускаю AI…», or parks on disconnect.
4. When `/v1/models` shows alias `orcarouter-qwen38-27b-q5km`, the **same** assistant message is filled. No resend. No cloned task. Completed tool digests stay on the task.

`recover()` no longer marks WAITING_LLM assistant messages as error.

## Idle and confirmation hold

Default idle: **10 minutes** (`auto_stop_minutes`). Activity includes generation, WORKING tasks, `WAITING_LLM` / `WAITING_DEVICE` / `WAITING_WORKSPACE` / `RECOVERING`.

`WAITING_CONFIRMATION` keeps GPU only for the idle window, then the task is persisted and the managed Pod stops. Later Allow restarts compute and resumes the same task.

Acceptance may override idle (e.g. 60s) without changing the product default.

## App Quit vs window

Tauri `RunEvent::Exit` / `ExitRequested` is full application Quit.

1. Owned Desktop POSTs `/runtime/shutdown` with `ALEX_RUNTIME_TOKEN` (bounded ~12s).
2. Controller stops **managed** Pods only (`terminate` action). Volume remains.
3. Then the owned local backend process is killed.

External developer backends are left running. Internal window navigation is not Quit and does not stop GPU.

If the provider is unreachable, session rows remain for the next start reconciliation.

## Budgets (controller-owned)

Hourly cap **$1.20**. Session **$3**. Weak model never selects GPU or caps.

Over budget: stop managed compute, `COMPUTE_BUDGET_REACHED`, task persisted, volume kept.

## Network Volume

ID `uwgeaie5b0` (`orcarouter-storage`). The client has **no** volume-delete call. Stop/remove Pod ≠ delete volume.

## Packaging

Not in this slice. Sidecar/installer is next.
