# Central Alex Gateway — design (Cloud Control Plane v2)

Alex LLM 0.9.3. This document describes what was implemented on
`feat/central-runpod-gateway` (branch base `main` = `a1dcd1366183146dce1e51478f9ff6714caa9513`).
The audit that came first is in [central-runpod-gateway-audit.md](central-runpod-gateway-audit.md);
the operator runbook is in [gateway-deployment.md](gateway-deployment.md).

> **Not deployed.** This slice ships the service, the client integration, the deployment
> artifacts and a local production-like acceptance. No public Gateway exists yet, and the
> repository never touches 12Testers infrastructure.

## 1. What changed in the product

Before: one installation held the RunPod master key and was the money and compute authority
for itself. Many installations meant many keys, N Pods and uncoordinated spend.

After: RunPod is **infrastructure of Alex**, not of a user or a PC.

```text
PC A  Desktop + local backend ─┐
PC B  Desktop + local backend ─┼─►  Central Alex Gateway  ─►  RunPod (one account,
PC C  Desktop + local backend ─┘    (server-side master key)   one managed compute)
```

* The master RunPod key exists **only** on the Gateway. It is never in a Desktop, an
  installer, a local backend, the frontend, client Credential Manager, client SQLite,
  `localStorage`, `settings.json`, a client `.env`, a client log or an API response.
* Installations authenticate **as installations** (not as local users) with a credential that
  is issued once at enrollment.
* The Gateway is the authority for the provider key, global compute ownership, financial
  limits, the shared account balance and production model inference.

## 2. Product modes

| Mode | Provider credential | Who starts compute | Inference path | Selected by |
|---|---|---|---|---|
| **Production shared** | Gateway only (server-side) | Gateway (`/compute/ensure`) | client → Gateway → Pod | enrollment + build/runtime config |
| **Dev / private direct** | local `Alex LLM/provider/runpod` | local `RunPodController` | client → Pod directly | default when no enrollment exists |

The mode is **not** a user-facing autonomy setting. The desktop decides it at spawn time:

* an installation credential exists → `ALEX_AI_MODE=shared` plus the gateway URL, installation
  id and installation secret;
* otherwise → `ALEX_AI_MODE` is passed through only if the environment sets it explicitly,
  so a developer can point a checkout at a local Gateway;
* the local backend defaults to `direct`.

The existing `Alex LLM/provider/runpod` credential is untouched: it is not migrated, not
uploaded, not deleted, and it is ignored while shared mode is active.

## 3. Trust boundary

```text
local user (any role)      local identity, stays local       ─┐
                                                              │  local JWT (iss=alex-llm)
local backend (per PC)     local task state, Computer,        │
                           Memory, Web, Tor, confirmations    │
        │  installation credential (Credential Manager)       │
        │  → POST /auth/token → short-lived JWT (memory only) │
        ▼                                                     │
Central Alex Gateway       master RunPod key, shared account, │
                           global compute lease, budgets,     │
                           balance cache, model proxy         │
        │  provider REST v2 + read-only GraphQL               │
        ▼
RunPod (one account, one Network Volume)                      ◄┘
```

Not trusted by the Gateway: local `user_id`, `email`, `role`, `User-Agent`, a client-supplied
`installation_id`, any client-supplied price or budget, and any claim about compute ownership.
The only trusted principal is the installation behind a valid gateway token, and the only
trusted compute view is the one the Gateway reads from the provider itself.

## 4. Enrollment and installation identity

One-time enrollment keeps v1 honest: without a central Alex account system, "anyone who
downloaded the EXE" must not be able to spend a shared account.

* The operator creates an **activation code** with the CLI. It is cryptographically random
  (`secrets.token_urlsafe(24)`), short-lived (`ACTIVATION_TTL_MINUTES`, default 60 minutes),
  single-use, stored server-side as a **SHA-256 digest only**, and redeemed with one
  conditional `UPDATE` so concurrent enrollments cannot both succeed.
* `POST /enroll` returns `installation_id` + `installation_secret`
  (`secrets.token_urlsafe(32)` = 256 bits). That is the only time the secret is emitted; the
  server stores a digest.
* The client stores the enrollment in the OS credential store under
  `Alex LLM/gateway/installation` — a different scope from `Alex LLM/provider/runpod`.
* `POST /auth/token` exchanges the credential for a short-lived JWT (`iss`, `aud`, `sub`,
  `jti`, `iat`, `exp`, default 15 minutes). The secret is never a bearer token. The local
  backend keeps the token in memory and renews it automatically.
* Revocation is per installation (`POST /auth/revoke` or `gateway.cli revoke`): tokens stop
  working immediately, new tokens are refused, and other installations are unaffected. Local
  user data is untouched.
* Local logout, restart and reinstall never remove the installation credential: it is not a
  user session. Only an explicit **Disconnect Alex Cloud** in Settings revokes and deletes it.

`GET /health` reports `product`, `version`, `gateway_protocol_version`, `ready`, `database`
and a boolean `provider_configured` — never a value, a path or a secret. Clients check
`gateway_protocol_version`; a mismatch is an explicit `gateway_protocol_mismatch`, not
undefined behaviour.

## 5. Global compute authority

The provider is shared, so ownership is server-side and database-backed.

* **One managed compute.** A singleton `gateway_compute` row holds the state, the active
  session, the lease and the revision. Operations run inside a CAS lease
  (`lease_owner`/`lease_until`), so two installations (or two gateway workers) can never both
  create. The in-process `asyncio.Lock` is a fast path; the database lease is the authority.
* **Create intent before effects.** The session row is committed before `POST /pods`, so an
  ambiguous answer is always reconcilable.
* **No blind retry.** An ambiguous create (`timeout`, `unavailable`, `malformed`, 5xx) becomes
  `create_unknown`. Reconciliation confirms with the provider whether a Pod exists for the
  Volume; only then may a create be retried, and the attempt budget on the control row
  (2 attempts, server-side, cleared only by an operator) makes sure the loop is bounded.
  A Pod that answers slowly and actually succeeded can therefore never be followed by a
  second Pod.
* **No destructive guessing.** Several Pods on the Volume are reported as `multiple_compute`
  and left alone. A Pod Alex did not create is adopted as `external_compute`, reported, offered
  for inference only after it demonstrably answers with this deployment's alias, and **never**
  terminated by the Gateway.
* **Idle is global.** Compute stops when *every* installation is quiet
  (`auto_stop_minutes` × global activity) and no generation is in flight — installation A
  finishing never stops compute that B is using.
* **State vocabulary.** Shared mode reuses the product's existing compute states
  (`offline`, `searching`, `creating`, `starting_pod`, `loading_model`, `ready`, `generating`,
  `stopping`, `stopped`, `create_unknown`, `multiple_compute`, `external_compute`, `error`) and
  the existing `compact_ai` mapping by importing it — there is no second state machine.
* **Idempotency.** `POST /compute/ensure|stop` take an `operation_id`; a replay returns the
  recorded result and never creates a second Pod. Ambiguous states are deliberately *not*
  cached so a retry can learn the reconciled answer.

## 6. Money

Server-side, authoritative, and not weakenable by a client:

* hourly ceiling **$1.20**, session budget **$3.00** — the existing product rules, enforced in
  `GatewaySettings` and clamped again per request in `_caps()`. A client may only ask for
  something *stricter*; `max_hourly_price=999` becomes `1.20`.
* The supplier's confirmed price is re-checked after create; a Pod above the approved ceiling
  is released (`price_violation`).
* The session cost is estimated from the supplier's start time (same formula as direct mode);
  reaching the budget releases compute with `COMPUTE_BUDGET_REACHED`.
* A known balance below the requested session budget refuses to create (`runpod_balance`)
  instead of starting a Pod that cannot be paid for.
* No new blocking limits (daily, monthly, per-user quota) were invented.

## 7. Inference through the Gateway

Production shared mode never gives a client a llama.cpp endpoint.

* `POST /v1/chat/completions` is authenticated, OpenAI-compatible, and proxied to the Pod's
  authenticated port. The Pod id, the provider proxy URL and the Pod bearer key never appear
  in a response.
* Streaming stays incremental: chunks are forwarded as they arrive, with no full-response
  buffering. A client disconnect (Stop button, closed window) cancels the upstream generation
  and releases the single generation slot — the release is synchronous, so a cancelled stream
  can never leave the slot occupied.
* `--parallel 1` is enforced by a **bounded** queue (default 8 waiters, 120 s): queue full →
  `gateway_queue_full`, wait timeout → `gateway_busy`. Requests are never silently dropped.
* Cancelling a generation is not stopping compute: a stop during a generation sets
  `pending_stop` and completes when the generation ends.
* A replayed `X-Alex-Request-Id` never generates twice: in-flight → `gateway_request_in_flight`,
  completed non-stream → the recorded completion, completed stream → `gateway_request_completed`.
* `X-Alex-Task-Id` carries the local task identity, so a retry cannot create a new user message.

## 8. Shared balance

The account belongs to the deployment, so `GET /balance` is one cached snapshot per Gateway:
single-flight refresh, 5 s while compute is active / 15 s while idle, `Decimal` end to end and
serialized as a string. A failed read keeps the last successful value and marks it stale — it
never becomes a fake `$0`. The local backend consumes the Gateway with the same
`RunPodBalanceService` used in direct mode (it only needs `configured` + `account_balance()`),
so the client keeps its cache, staleness and low-balance semantics unchanged, while 100 clients
polling produce one upstream read.

## 9. Status integration

The five chips are unchanged. In shared mode the AI chip reads the Gateway's compute state
through the same `compact_ai` mapping and the same `ai_status()` code path, so both modes can
never disagree about what "ready" means. New stable codes have honest chip mappings:
`gateway_not_connected` → `not_configured` (action: configure), `gateway_unavailable` / `gateway_busy`
→ `unavailable` (retry), `gateway_protocol_mismatch` / `gateway_auth_failed` /
`installation_revoked` / `gateway_budget_denied` → `error` (not recoverable by retry).

Computer, Web, Tor and Memory stay purely local, and no regression is possible: they never
contact the Gateway.

While shared mode is active, the local RunPod compute lifecycle routes
(`/compute/search|start|stop|preferences`, the on-demand demand path) refuse with
`gateway_managed_compute`, and `POST /runtime/shutdown` does not stop shared compute: one
installation quitting must not end a Pod that others use. Starting and stopping shared compute
is done through the typed Gateway operations exposed at `/cloud/compute/ensure|stop` and in the
Settings → Alex Cloud panel.

### 9.1 Readiness is local, and the panel settles (production fix)

The Desktop's runtime readiness probe gives `GET /health` **400 ms** and the UI polls
`/health` and `/llm/status` while it runs. In shared mode readiness must therefore never be a
Gateway round trip: `GatewayProvider.health()` answers from a cached value
(`READY_TTL_SECONDS = 8`, single-flight background refresh). The first caller still waits for
the real answer, a stale answer is served instead of a slow `/health`, and a failed probe is
cached too. Probing per request is what made an enrolled install fail to become ready and turned
a healthy client into a hot loop against the shared service (the deployed limiter answered
**429**), so this is a permanent invariant rather than a tuning detail — see AGENTS.md.

On the client UI side the panel reads the cloud snapshot when it mounts and after
enroll/disconnect. A backend that was just restarted still answers `connecting` for a moment, so
the read is **settled**: `refreshCloud()` and the enroll/disconnect paths wait (bounded,
12 × 750 ms) for the first answer that is not the transient `connecting`, and publish nothing
else. The activation code, the installation secret and the access token are never part of that
snapshot.

## 10. Gateway database

Its own database and its **own Alembic history** (`apps/gateway/alembic`, revision
`0001_gateway_core`), never the local backend's `0001`–`0014`. Tables: `installations`,
`enrollment_codes`, `gateway_compute`, `gateway_sessions`, `gateway_operations`,
`audit_events`. No chats, messages, memories, documents or prompts. Production target is
PostgreSQL; SQLite is used for development and tests. Tests assert that the migration matches
the models (`alembic check`).

Audit events record `installation_id`, operation, result, error code, task id and coarse
detail — never a credential, never a prompt or a completion.

## 11. Code reuse (no diverging copies)

The Gateway does not fork the provider core. `apps/gateway/gateway/provider.py` imports the
existing `app.compute.runpod_api` (`RunPodAPI`, error table), `app.compute.schemas` (pod/GPU/
volume models, `ComputePreferences`) and `app.compute.runtime.compact_ai` from the client
backend package (path from `ALEX_BACKEND_LIB_DIR`), and the Pod bootstrap
(`app/compute/remote_runtime.py`) is read from the same place — so direct mode and shared mode
deploy byte-identical Pod runtimes with the same image, mounts, model and flags.

## 12. Tests and acceptance

* `apps/gateway/tests` — 95 deterministic tests: enrollment (single-use, expiry, revocation,
  digest-only storage, a 4-thread race with exactly one winner), auth (claims, renewal,
  revocation, protocol header, rate limit), compute (a two-worker race that creates exactly one
  Pod, cap tampering, `create_unknown` bounded retry, attempt budget, adoption, no destructive
  action, budget/idle/price stops, global idle with two installations), balance (single-flight,
  TTL, stale-never-zero), inference (readiness gate, incremental streaming, disconnect cancel,
  queue full/busy, replay safety, no upstream detail leakage), security (no secret in DB,
  payloads, logs or audit), migrations and the operator CLI. Every provider call goes through
  `httpx.MockTransport`: no test can create paid compute.
* `apps/backend/tests/test_cloud.py` — 30 client-side tests: config fail-closed policy, token
  behaviour, balance through the Gateway, provider streaming/failure mapping, chip mapping for
  shared states, and the local-compute refusal.
* `apps/desktop` — 74 Vitest tests (21 for Alex Cloud), `tsc`, Prettier; Rust `cargo test`
  42 + 17, including the credential scope, URL policy and the "no secret in status" contract.
* `scripts/acceptance-central-gateway.py` — **PASS**: the production Gateway process with
  production settings, two enrolled installations with different secrets (stored as digests),
  one shared balance seen identically by both from a single cached snapshot, a second snapshot
  after the TTL, per-installation revocation, the protocol guard, 404 on every passthrough route,
  no Pod on the Volume, no GPU spend (only the ~$0.005/h Network Volume storage fee) and no
  credential in the log, the database or any API payload. It never calls `ensure`/`stop`.
* `apps/desktop/e2e/cloud-smoke.mjs` — **PASS** on the REAL installed app (fresh NSIS install of
  this slice): first-run owner → Settings → Alex Cloud → one-time code → «Подключено» → shared
  balance visible → AI chip honest (never Ready while nothing runs) → RunPod key input gone in
  shared mode → logout → different local user → still connected → full Quit + relaunch → still
  connected with the balance restored → reinstall over the installation → still connected and the
  data root unchanged. No «Запустить AI», no `ensure`/`stop` call, no Pod, GPU 0.
* `scripts/acceptance-public-gateway.py` — **PASS** against the **deployed** Gateway over public
  HTTPS: health, 401/404 surface, two installations with server-created codes, digest-only
  storage, short-lived tokens, one shared balance from one cached snapshot (`$0.85`), a controlled
  cache refresh, protocol mismatch 409, revocation isolation, and the master key absent from the
  journal, the nginx logs and a database dump. Both throwaway installations were revoked.
* `apps/desktop/e2e/cloud-prod-enroll.mjs` — **PASS** on the real installed app against the public
  Gateway: explicit disconnect (local credential removed, server revoked, dev credential kept) →
  restart → enroll through the real UI → «Alex Cloud · Подключено» → real balance `$0.84` → master
  key absent from the DOM, browser storage, the local database, the logs, the installer and the
  installed binaries → no direct provider traffic → no hot loop (2 model probes/min, no 429).

## 13. Known limitations

* **The Gateway is deployed** at `https://gateway.12testers.store` (12Testers VPS, separate
  service/database/user, Let's Encrypt) and merged into `main`. Its **compute path is still
  unproven against a real Pod**: `POST /compute/ensure` and the inference proxy have never been
  exercised end to end against RunPod — money caps, the single-Pod lease and the create-unknown
  path are covered by the FakeRunPod suite only.
* **No central Alex account.** Enrollment uses one-time activation codes delivered by an
  operator. A future account service can replace the code without moving the RunPod trust
  boundary.
* **Single process.** Rate limiting is in-process; the compute lease is the real authority.
  Multi-worker or multi-node deployment is out of scope for v1.
* **Direct-mode UI still starts local compute.** In shared mode the direct lifecycle routes
  refuse with `gateway_managed_compute`; the Compute panel shows a note instead of controls.
  A richer shared compute UI (quotes, GPU choice) is future work: the Gateway picks the GPU
  server-side.
* **WM-07 and CD-08 stay closed**, the model, alias, Volume and llama.cpp flags are unchanged,
  AUTONOMY/RESEARCH_DEPTH are fixed, and the version stays 0.9.3.

## 14. Roadmap

1. Central RunPod Gateway / Cloud Control Plane — **this slice**.
2. Upgrade / Backup / Data Preservation — not started.
3. RC / 1.0 gates — not started.
