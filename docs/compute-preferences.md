# Compute Preferences — per-user money policy

Since 1.0 a user's **maximum $/hour** and **session budget** are *preferences the user owns*,
not product caps. `$0.52/hour` and `$3.00/session` are the values a new user starts with; both
can be raised or lowered inside technical bounds only, and the Gateway enforces the value the
authenticated installation sent instead of replacing it with a server value.

This page is the contract for that policy: ownership, defaults, bounds, what the Gateway
enforces, how a GPU is chosen, and the truthful state when a running Pod costs more than the
current user's own maximum.

## 1. What changed (migration note)

| | 0.9.3 (removed in 1.0) | 1.0 |
|---|---|---|
| Hourly rule | product ceiling `$1.20/h` (`runpod_max_hourly_price`, Gateway `MAX_HOURLY_PRICE`) | default `$0.52/h`, user-owned |
| Session rule | product ceiling `$3.00/session` | default `$3.00/session`, user-owned |
| Gateway behaviour | `_caps()` clamped every request with `min()`, so a user's own higher value was **silently replaced** | honours the caller's value; rejects malformed/out-of-range values with `compute_policy_invalid` |
| Automatic selection | a stray `gpu_id` (legacy default `NVIDIA L40S`) could pin a pricier card | ignored unless the client asks for `selection: "manual"` |
| Saving the policy | a non-initiator could be refused (`403`) while somebody else's managed session ran | always saved; another user's running session is left untouched |

The old Gateway settings `MAX_HOURLY_PRICE` / `MAX_SESSION_BUDGET` still exist, but only as the
**defaults for a request that carries no policy** (`DEFAULT_MAX_HOURLY_PRICE = 0.52`,
`DEFAULT_SESSION_BUDGET = 3.00`). Nothing clamps a legitimate user policy any more.

Some older documents in `docs/` still write the 0.9.3 ceilings and defaults
(`on-demand-ai.md`, `runpod-controller.md`, `central-runpod-gateway-design.md`,
`gateway-deployment.md`). This page is the current contract; the audit/design documents are
historical and are kept as they were written.

## 2. Ownership

| Action | Who may do it | Notes |
|---|---|---|
| Read `/compute/preferences` | any authenticated local user | own row only; there is no endpoint that returns another user's policy |
| `PUT /compute/preferences` | **any authenticated local user** | deliberately **not** gated by the compute-owner role or by `local_compute_required`: a money policy is not a compute lifecycle action. Works in **both** provider modes (direct and shared) |
| Start / stop / search compute | the machine's compute owner (`compute_user`: first owner or admin, plus `ALLOW_USER_COMPUTE_START`) | unchanged by this feature; in shared mode the local lifecycle refuses anyway (`gateway_managed_compute`) and start/stop goes through Canalla Cloud |
| Raise someone else's limit | nobody | an admin has no write path into another user's preferences |

Rationale: the person who lowers their own ceiling must be able to raise it again without an
admin, and the person who pays attention to cost must not need the machine owner's permission
to express it. Compute *activation* stays gated because it spends the installation's money.

## 3. Defaults for a new user

| Field | Default | Source |
|---|---|---|
| `selection` | `automatic` (cheapest compatible) | `ComputePreferences` schema |
| `min_vram_gb` | **48** | `RUNPOD_MIN_VRAM_GB` (also the server floor) |
| `max_hourly_price` | **$0.52** | `RUNPOD_DEFAULT_HOURLY_PRICE` |
| `session_budget` | **$3.00** | `RUNPOD_DEFAULT_SESSION_BUDGET` |
| `auto_stop_minutes` | 10 | `RUNPOD_AUTO_STOP_MINUTES`; allowed values 0/5/10/15/30 |
| `auto_search` | enabled (retry GPU search) | `ComputePreferences.defaults()` |
| `search_interval` | 30 s | `RUNPOD_SEARCH_INTERVAL` (15–300) |
| `auto_connect` | off | a manual exact GPU is required to turn it on |
| `gpu_id` | none | no exact GPU is injected for a new or legacy profile |

The policy is stored per user in the local database (`compute_preferences`, one row per
`user_id`, JSON values) and is returned by `GET /compute/status` → `preferences` with the
technical bounds in `limits`. Because it lives in the database, it survives logout, restart,
install-over-install and backup/restore (see [backup-format.md](backup-format.md)).

## 4. Technical bounds (the only ones)

| Field | Shape accepted | Bound | Enforced by |
|---|---|---|---|
| `max_hourly_price` | decimal, > 0, finite, ≤ 4 dp (local schema) | **≤ $100/hour** | local schema `le=100`; Gateway money layer `ABSOLUTE_MAX_HOURLY_PRICE` |
| `session_budget` | decimal, > 0, finite, ≤ 4 dp (local schema) | **≤ $1000/session** | local schema `le=1000`; Gateway `ABSOLUTE_MAX_SESSION_BUDGET` |
| `min_vram_gb` | int | 1–1024, then floored at the server minimum (`max(value, RUNPOD_MIN_VRAM_GB)`) | schema + Gateway `_caps()` |
| `auto_stop_minutes` | int | one of `0, 5, 10, 15, 30`; anything else falls back to the server idle default | Gateway `ALLOWED_AUTO_STOP` |
| `selection` | `automatic` \| `manual` | unknown values become `automatic` | Gateway `_caps()` |
| `gpu_id` | string, 1–160 chars | honoured **only** with `selection: "manual"` | Gateway `_caps()` |

These bounds exist to reject nonsense (negative, zero, `NaN`, infinities, parser overflow) and
absurd abuse — not to cap a legitimate policy. The local schema uses `extra="forbid"` and
`allow_inf_nan=False`, so an unknown field or a non-finite number is a validation error rather
than a silently ignored value.

## 5. What the Gateway does with the policy

`POST /compute/ensure` carries the policy of the *authenticated local user* whose request
caused the model to be needed. In sequence:

1. **Schema validation** — wrong shape, unknown fields, zero or negative numbers → the
   framework's `422` (the schema is `extra="forbid"` with `gt=0`).
2. **Money layer (`_caps()`)** — the caller's values are used as sent, and its guard is purely
   technical: a non-finite value, `≤ 0` or a value above `$100/h` / `$1000/session` raises
   `compute_policy_invalid` (422) with a Russian detail. Nothing is replaced silently — the case
   that used to be clamped is now an explicit error (`max_hourly_price: 999` passes the request
   schema and is rejected here).
3. **Normalisation** — `min_vram_gb` is floored at the server minimum, `auto_stop_minutes` is
   restricted to the allowed set, `selection` defaults to `automatic`, and `gpu_id` is dropped
   unless `selection` is `manual`.
4. **Cheapest-compatible selection** — among the GPUs that are `selectable` **and** cost at most
   `max_hourly_price`, the cheapest wins (ties: lower id). A higher maximum therefore never
   causes a more expensive GPU to be created.
5. **No candidate** — no Pod is created: state `searching` with `error_code: price_limit` when
   compatible GPUs exist but none fits the price, or `no_compatible_gpu` when nothing is
   compatible.
6. **Session record** — the session stores the policy budget and
   `max_hourly_price = min(policy maximum, actual rate)`, so the record never claims a limit
   larger than what the chosen GPU actually costs.
7. **Runtime guards (unchanged)** — if the created Pod's rate exceeds the policy maximum it is
   terminated with `price_violation`; the periodic `tick()` stops a session whose estimated
   cost has reached the budget, whose rate exceeds the recorded maximum, or whose idle timeout
   expired.
8. **Idempotency** — the same `operation_id` from the same installation returns the recorded
   answer and never creates a second Pod. The policy is part of the recorded request digest.

Balance protection still applies: if the shared balance is known and lower than the requested
session budget, nothing is created — the answer is state `offline` with
`error_code: runpod_balance` (covered by `test_insufficient_balance_denies_creation`).

## 6. Automatic vs manual selection

| `selection` | Behaviour |
|---|---|
| `automatic` (default) | cheapest selectable GPU inside the user's own maximum; `gpu_id` is ignored, whatever the client sends |
| `manual` | the exact GPU named by `gpu_id` is used when it is selectable and inside the maximum; otherwise the request finds no candidate (no silent substitution with a different card) |

The local panel shows the stored policy as the user's own: «Сохранённые лимиты: максимум
$0.52/ч · бюджет $3.00» with the same wording for a running session, and it states in shared
mode that compute is managed by Canalla Cloud while «лимиты ниже — ваши собственные, их можно
повышать и понижать».

## 7. The truthful over-limit state

Shared compute is installation-global, so a running Pod can cost more than the current user's
own maximum: the user lowered the value while the Pod was already up, or another installation
started it. The panel states the fact and changes nothing:

> Сейчас работает общий GPU за $X/час — это выше вашего предела $Y/час. Можно повысить предел
> в настройках ниже.

The line comes from the last Gateway compute answer (`ensure`/`stop` — `GET /cloud/status`
carries no rate), so an unknown rate or an unread policy says nothing instead of guessing, and
rendering the panel never asks the Gateway for compute. Raising the limit stays the user's own
decision; nothing repairs the situation automatically.

## 8. What the Gateway still enforces (and never delegates)

| Rule | Value |
|---|---|
| Network Volume | `RUNPOD_NETWORK_VOLUME_ID` (`uwgeaie5b0`) — the model lives there and is never deleted |
| Datacenter / image | `RUNPOD_DATACENTER` (`US-TX-3`), `RUNPOD_IMAGE` |
| VRAM floor | `RUNPOD_MIN_VRAM_GB` (48 GB) — a policy can ask for more, never less |
| Single Pod | one managed Pod for the whole account (`gateway_compute` lease + committed intent); no duplicate Pods |
| Provider credential | exists only in the Gateway environment; no response, log or client ever sees it |
| Technical bounds | `$100/hour`, `$1000/session`; no client can widen them |
| Client isolation | the policy of one installation cannot be set by another; cross-installation replay of an operation id is refused |
| Passthrough | none: no `/runpod/*`, no `/provider/raw`, no direct llama.cpp endpoint |

## 9. HTTP contract

**Local backend (the user's own policy).** Authenticated as a local user; ownership is by
session, never by a parameter.

```http
GET /compute/preferences        # → the caller's stored policy (defaults when no row exists)
PUT /compute/preferences        # body: ComputePreferences, extra fields forbidden
```

```json
{
  "selection": "automatic",
  "min_vram_gb": 48,
  "max_hourly_price": "0.52",
  "session_budget": "3.00",
  "auto_stop_minutes": 10,
  "gpu_id": null,
  "auto_connect": false,
  "auto_search": true,
  "search_interval": 30
}
```

`PUT` returns the saved policy; a value that violates a bound or leaves a manual
auto-connection without a GPU is answered `422` with a Russian message
(`ComputePreferences.enforce`). The authenticated user is the only one affected — no other
user's policy and no other user's *running* session is rewritten.

**Local backend → Gateway (shared compute).** `POST /cloud/compute/ensure` (authenticated as a
local user; not owner-gated) reads that user's stored policy and sends it as caps. The client
never sends more than the user set:

```http
POST /cloud/compute/ensure   { "task_id": "...", "auto_stop_minutes": 10 }   # both optional
POST /cloud/compute/stop     {}
```

```json
{
  "operation_id": "local-<uuid>",
  "task_id": "…",
  "max_hourly_price": 0.52,
  "session_budget": 3.0,
  "min_vram_gb": 48,
  "selection": "automatic",
  "gpu_id": null
}
```

The `gpu_id` is only used when `selection` is `manual`; otherwise the Gateway drops it.

**Gateway (installation-authenticated).**

```http
POST /compute/ensure
Authorization: Bearer <short-lived installation JWT>
X-Alex-Protocol-Version: 1
```

| Field | Type and bounds (schema) | Meaning |
|---|---|---|
| `operation_id` | string, 8–64 | idempotency key; replayed per installation |
| `task_id` | string ≤ 64, optional | the task that needs the model |
| `max_hourly_price` | float `> 0`, `≤ 1000` in the schema | effective bound is `$100/hour` (`compute_policy_invalid` above it) |
| `session_budget` | float `> 0`, `≤ 10000` in the schema | effective bound is `$1000/session` |
| `auto_stop_minutes` | int 0–240 | restricted to `0/5/10/15/30`; otherwise the server idle default |
| `min_vram_gb` | int 1–1024 | floored at 48 |
| `selection` | `automatic` \| `manual`, optional | default `automatic` |
| `gpu_id` | string ≤ 160, optional | used only with `selection: "manual"` |

Omitting the money fields is allowed: the Gateway then applies its own defaults (`$0.52`,
`$3.00`). Typed errors: `compute_policy_invalid` (422, invalid or out-of-range policy),
`compute_offline` / `gateway_busy` / `gateway_unavailable` (shared-service state),
`gateway_request_in_flight` / `gateway_request_completed` / `gateway_invalid_request`
(operation-id reuse, including an operation id that belongs to another installation).
Money conditions that are not request errors come back as a state plus `error_code` instead:
`searching` + `price_limit` / `no_compatible_gpu` (nothing fits the policy) and `offline` +
`runpod_balance` (known balance below the requested session budget).

## 10. Files

| File | Role |
|---|---|
| `apps/backend/app/compute/schemas.py` | `ComputePreferences` — shape, defaults, technical bounds, `enforce()` |
| `apps/backend/app/compute/controller.py` | per-user read/save, session application, search |
| `apps/backend/app/compute/routes.py` | `GET`/`PUT /compute/preferences`, start/stop/search gating |
| `apps/backend/app/cloud/routes.py` | forwards the user's policy as caps to the Gateway |
| `apps/gateway/gateway/config.py` | `DEFAULT_*` (defaults) and `ABSOLUTE_*` (technical bounds) |
| `apps/gateway/gateway/compute.py` | `_caps()` enforcement, cheapest-compatible selection, session guards |
| `apps/gateway/gateway/routes.py` | `POST /compute/ensure` request schema |
| `apps/desktop/src/lib/compute.ts` | policy formatting and the truthful over-limit line |
| `apps/desktop/src/components/ComputePanel.tsx` | the settings panel and its wording |
| `apps/backend/tests/test_compute.py`, `apps/backend/tests/test_on_demand_ai.py`, `apps/gateway/tests/test_compute.py` | policy, bounds, selection, rejection and no-create coverage |

## 11. Cloud tiers and the allocation candidate set (1.1.0)

Two things are *policy the user can see*, not hidden behaviour:

**Cloud tier.** A RunPod Community Cloud Pod cannot mount a Network Volume, and the model lives on
one, so the product policy is Secure Cloud only by default (`RUNPOD_ALLOW_COMMUNITY_CLOUD=false`).
A user's own `allow_community` preference exists, is sent as `allow_community` in the caps, and is
decided by two gates: the deployment must permit the tier *and* the user must opt in — and even
then only on a placement the operator declared Community-capable
(`RUNPOD_DATACENTERS="US-TX-3:uwgeaie5b0:community"`). `GET /compute/status` reports the effective
policy read-only (`policy.cloud_tiers`, `policy.community_allowed`, `policy.community_blocked_by`),
and Settings → AI / Compute shows the choice with the reason it is unavailable, instead of a switch
that silently does nothing.

**Allocation candidates.** A model-required request no longer evaluates one physical placement: it
walks an ordered candidate set (cheapest compatible GPU, Secure before Community, the model's own
datacenter first) inside one **total** 60-second budget. A candidate that the provider refuses is
left behind immediately and the next one is tried. The full contract — ordering, budget
arithmetic, deduplication, the storage-topology audit and the deterministic test matrix — is
[gpu-allocation.md](gpu-allocation.md).
