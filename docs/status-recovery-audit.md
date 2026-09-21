# Status + recovery — subsystem audit (BEFORE)

Audit performed at the start of the **FIVE-CHIP STATUS + ERROR / RECOVERY UX + SHARED RUNPOD BALANCE**
slice, on `feat/status-recovery-ux` based on `main` `ad41009c60057f1c052dd8becef6d53156551d88`
(Alex LLM 0.9.3 development).

This document records **what actually exists in code**, not what the names suggest. It is the input for
[status-recovery-design.md](status-recovery-design.md). Nothing here was changed by the audit.

The five user-facing chips are `AI`, `Computer`, `Web`, `Tor`, `Memory`. The shared RunPod account
balance is a separate read-only value, not a chip.

## 0. Shared vocabulary

The slice needs one small vocabulary. Before the slice the codebase had **no** shared status
vocabulary at all:

| Concept | Where it existed before | Notes |
|---|---|---|
| `ai` compact state | `app/compute/runtime.py` `LABELS` / `COMPUTE_TO_AI` | only for AI: `off/starting/ready/waiting/unavailable/error` |
| `tor_status` | `app/tools/routes.py` `_tor_connected` | only two values: `"Connected"` / `"Unavailable"` |
| embedding model state | `app/documents/model_manager.py` | `NOT_INSTALLED/CHECKING/DOWNLOADING/VERIFYING/CANCELLED/READY/FAILED` |
| device online | `app/tools/local/devices.py` `public_device` | boolean, 45 s freshness window |

There is no existing aggregate snapshot, no unified vocabulary, and no per-subsystem message/detail
contract. `web` and `memory` have **no** state representation at all.

## 1. AI

**Real sources of truth**

- Compute state machine: `app/compute/controller.py` `RunPodController.get_compute_status()`
  (`state`, `error_code`, `configured`, `session`).
- Compact mapping: `app/compute/runtime.py` `compact_ai()` — already used by the UI.
- Endpoints: `GET /compute/status` (any authenticated user), `GET /llm/status`
  (`app/main.py`, any authenticated user), `GET /admin/compute/current` (admin).

**Observed compute states** (`COMPUTE_TO_AI` keys, verified in code):
`offline, stopped, not_configured, searching, gpu_found, creating, starting_pod, starting_environment,
mounting_storage, starting_llm, connecting, loading_model, generating, ready, stopping, create_unknown,
external_compute, multiple_compute, error`.

**What the states actually mean**

- `not_configured` is forced by `get_compute_status` when `RunPodAPI.configured` is false
  (no `RUNPOD_API_KEY`).
- `ready` means the container emitted the `ALEX_LLM_PHASE=ready` marker **and** the backend observed the
  configured alias in `/v1/models`. It is not implied by "a Pod exists".
- `create_unknown` means create timed out / 5xx; reconciliation must run before any retry.
- `multiple_compute` means more than one billed Pod on Volume `uwgeaie5b0` — a safety condition.
- `external_compute` = adopted unknown-named Pod; **not** Alex-owned (`managed=false`).

**Readiness honesty gap found**: `compact_ai()` maps `generating` and `ready` to `AI Ready`, but when
`LLM_PROVIDER=mock` in a non-production environment it returns `ready` too (intentional demo), and in
`APP_ENV=production` mock returns `unavailable` (never masquerades as real AI).

**Recoverable**: everything transient (`runpod_unavailable`, `runpod_timeout`, `runpod_rate_limit`,
`connection_failed`). **Requires a decision**: `multiple_compute`, `create_unknown` (reconciliation only,
never auto-retry), budget/price failures, `not_configured` (needs a provider key).

## 2. Computer (local computer-use)

**Real sources of truth**

- `app/tools/local/devices.py` `active_device(db, user_id)` → `None` when there is no paired device or the
  newest one is stale; `public_device(row)` computes `online = last_seen >= now()-45s and not revoked`.
- `GET /tools/devices` (`app/tools/routes.py`) — per-user list, revoked rows filtered out.
- Frontend heartbeat loop: `apps/desktop/src/components/Workspace.tsx` (8 s) →
  `POST /tools/devices/heartbeat` (device-header authenticated).
- `computer_mode` ∈ `off | ask | trusted` — `app/tools/policy.py` `WebSettings`, stored per user in
  `tool_preferences.values`.

**What "Ready" may mean**: a non-revoked paired device whose `last_seen` is inside the 45 s window.
**Trap found**: the Tauri command `device_status` reports `online = credential::load().is_some()`
(`apps/desktop/src-tauri/src/host.rs`), i.e. "a credential file exists" — that is **not** liveness and
must not be used for the chip.

**Missing**: there is no `starting` / `pairing` state, and no backend "host process is running" signal.
Tool errors carry `host_offline`; tasks park in `WAITING_DEVICE`.

## 3. Web

**Real sources of truth**

- `GET /tools/status` (`app/tools/routes.py`) already aggregates a web block:
  `configured`, `capabilities.{tinyfish_search,tinyfish_fetch,tinyfish_agent,tinyfish_browser}` each with
  `{configured, enabled, available = configured and enabled, paid, risk}`.
- `configured` = `bool(settings.tinyfish_api_key.get_secret_value())`.
- `enabled` = per-user `WebSettings` (`search_enabled`, `fetch_enabled`, `agent_mode != off`,
  `browser_mode != off`).
- SSE `web_status` events and `/tools/preferences` (this is what the UI edits).

**Honesty gap found**: `available` is purely configurational. It does **not** mean the provider is
reachable right now. There is no provider health probe and no `starting` state; a provider outage only
appears as a tool error code (`provider_unavailable`, `provider_timeout`, `rate_limited`,
`provider_auth`, `provider_forbidden`, `billing_required`).

**Not in scope**: WM-07 TinyFish Browser live execution/lifecycle remains a known limitation and is not
touched by this slice.

## 4. Tor

**Real sources of truth**

- `app/tools/routes.py` `_tor_connected()`: a single TCP connect to `tor_socks_host:tor_socks_port`
  (default `127.0.0.1:9050`) with a 0.25 s timeout → `"Connected"` / `"Unavailable"`.
- `tor_mode` ∈ `off | auto | on` per user (`WebSettings`), `tor_search_providers` in settings.
- Real chain proof exists as `app/tools/tor/browser.py` `prove_socks5()` but is only invoked from
  `TorBrowserController.start_session` — deliberately **not** part of any status path.
- Fail-closed enforcement: `app/tools/executor.py` blocks clearnet tools under `network_route=TOR_ONLY`
  and emits `TOR_ROUTE_VIOLATION_BLOCKED`; the transport raises `tor_not_configured` /
  `tor_unavailable`.

**Honesty gap found**: "Connected" means *a socket accepted a connection*, not *a Tor circuit was
verified*. It must not be rendered as a verified-Tor Ready state, and a Tor intent must never silently
fall back to clearnet. **Missing**: no `starting`.

## 5. Memory

**Real sources of truth**

- Per-user preference `User.use_memory` (+ `relevant_memory`, `max_memories`) in `app/models.py`,
  edited through `PATCH /profile` and the memory endpoints in `app/personal.py`.
- `MemoryRetriever.retrieve()` (`app/context_builder.py`) builds the memory block for a prompt.

**Key fact found**: memory does **not** use embeddings. Retrieval is lexical, so a memory chip does not
depend on the embedding subsystem at all. Nothing to download, nothing to warm up.

**Closely related but separate**: the document/RAG embedding model
(`app/documents/model_manager.py`, `GET /rag/model`) has real states
(`NOT_INSTALLED/CHECKING/DOWNLOADING/VERIFYING/READY/FAILED`) and can trigger a multi-hundred-MB
download. **The status chip must not trigger it.**

**Missing**: there is no memory health endpoint and no memory state field. The honest signals are
"the user enabled memory" and "the memory subsystem answered".

## 6. Shared RunPod balance (billing)

**Real source of truth found in the repository**: `app/compute/runpod_api.py` `RunPodAPI`.
Base `https://api.runpod.io/v2`, bearer `runpod_api_key`, `transport` injectable (`httpx.MockTransport`
in tests). Existing read methods: `volume()`, `gpu_options()`, `list_pods()`, `get_pod()`,
`phases()`, and `actual_cost()` → `GET /billing/pods` reading `records[].gpuAmount` as `Decimal`.

**Critical finding (external verification)**: **neither REST v2 nor REST v1 exposes an account balance.**
`https://api.runpod.io/v2/openapi.json` covers Account (SSH keys, secrets), Pods, Serverless, Templates,
Network Volumes, Registries, Catalog and Billing **spend** endpoints only; `https://rest.runpod.io/v1/openapi.json`
is the same minus v2-only routes. There is no `/v2/billing/account`, no `/credits`.

The account balance exists only on the GraphQL endpoint:

- `POST https://api.runpod.io/graphql`, header `Authorization: Bearer <api_key>`,
  body `{"query": ..., "variables": {...}}`.
- `runpodctl` documents `RUNPOD_GRAPHQL_URL` (default `https://api.runpod.io/graphql`) and its
  `user` output example contains `clientBalance`, `currentSpendPerHr`, `spendLimit`, `email`, `id`.
- The account view is reachable through the `myself { ... }` root field (the documented pattern used by
  other read-only tooling).

So a balance read is a **new read-only method on the existing client**, not a new client.

**Per-user vs shared**: the RunPod account is a single deployment-wide account. Balance is therefore
identical for every user; it is not a per-user value. `refresh_billing()` (admin-only, per session) is a
different thing and stays admin-only.

**Money representation**: the repository already uses `Decimal` for money
(`ComputeSession.hourly_rate`, `estimated_cost`, `actual_cost`, `Numeric(16,6)`). Balance must stay
`Decimal` end-to-end. A previous live RunPod flow already had a Decimal/datetime JSON serialization bug,
so responses need an explicit serialization test.

**Local per-session accounting that exists** (for an honest billing UI):

- `estimate()` — whole seconds ÷ 3600 × captured `hourly_rate`, `Decimal`, quantized to 6 places.
- `session_out()` exposes `hourly_rate`, `estimated_cost`, `actual_cost` (nullable), `session_budget`,
  `started_at`, `gpu_type`.
- `usage_for()` — split by day/week/month, explicitly labelled *estimated compute cost, not a RunPod
  invoice*, and explicit that mock requests are counted separately.

## 7. Error surface (before)

- Backend error shape today: `{"detail": <friendly message>, "code": <stable code>, "request_id": ...}`
  for `RunPodError`/`LLMError`; `HTTPException` elsewhere; supplier bodies and credentials are never
  returned.
- `ERROR_MESSAGES` (`app/compute/runpod_api.py`) is the closest thing to a presentation layer, and
  already contains Russian user-facing text per code.
- Frontend: `ApiError(status, message)` with the backend `detail`, plus `src/lib/tools.ts` textual
  mapping for tool events. There is **no** categorization (configuration / network / provider /
  compute / …), no `recoverable` flag, and no action model. Components each decide what to show.
- Recovery affordances that already work and can be reused: `POST /auth/refresh` (session slice),
  `Api`'s single-flight 401 refresh, `POST /compute/search/cancel`, `POST /compute/stop`,
  `POST /tools/devices/pair`, `Retry` = re-request, and `/rag/model/retry`.

**Same-task rule found in code**: a parked task keeps one `task_id` / one user message
(`WAITING_LLM`), the SSE stream stays open or parks, and the same assistant message is filled later.
Nothing in this slice may create a second user message, a second task, or a second Pod.

## 8. Startup / refresh (before)

- Desktop startup order: `ensureBackend()` → `restoreSession()` → `authenticated | first_run | anonymous`.
- The only periodic frontend pollers are `/llm/status` (3 s, `App.tsx`), `/health` (10 s), the device
  heartbeat (8 s) and presence. Each owns its own timer inside a component; there is no shared status
  service and no visibility handling.
- There is no backend-side cache for any status value; every UI poll becomes a backend call. For RunPod
  that matters: an uncached balance endpoint would mean one supplier call per user per interval.

## 9. Gaps this slice must close

1. No normalized, honest, single status snapshot for the five user-facing subsystems.
2. `Web` and `Memory` have no state at all; `Tor` has only Connected/Unavailable; `Computer` has only a
   boolean with a 45 s window.
3. Configurational "available" must not be presented as healthy/running.
4. No shared RunPod account balance read path, and no cache — a naive implementation would fan out one
   supplier call per user per interval.
5. No error categorization / recovery action model on the frontend.
6. No server-side rate discipline for supplier reads from the UI.
