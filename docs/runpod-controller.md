# RunPod Compute Controller — 0.4.0

`LLM_PROVIDER=mock` keeps independent demo chat; `llamacpp` activates the protected real-model connection described in [real-llm.md](real-llm.md).
No paid resources were created, started, stopped or deleted while developing this release.

## Configuration and roles

Copy the compute entries from root `.env.example` into `apps/backend/.env`. Supply a RunPod API key there only.
An absent key returns `not_configured`; the application and mock chat still work.
`ADMIN_EMAILS=["your-address@example.com"]` defines admins on the backend. Restart the backend after configuration changes.
Ordinary registration cannot grant admin privileges. `ALLOW_USER_COMPUTE_START=false` restricts start/search/stop to admins;
enabling it allows ordinary users to operate the shared compute. Updating active session settings requires its initiator or an admin.

Defaults: existing STANDARD 50 GB volume `uwgeaie5b0`, `US-TX-3`, NVIDIA, 48 GB minimum VRAM,
$1.20/hour ceiling, $3.00 session budget, idle timeout 10 minutes, search interval 30 seconds.
Auto-stop choices are 5/10/15/30 minutes or 0 (Never). Price, session budget and idle timeout are persistent per-user UI preferences, not environment ceilings. Legacy RUNPOD_MAX_HOURLY_PRICE / RUNPOD_MAX_SESSION_BUDGET no longer block UI changes. Input bounds are $100/hour and $1000/session and are exposed by `/compute/status`; the configured minimum VRAM floor, datacenter and Network Volume remain enforced.
Saving preferences applies budget, maximum price and idle timeout to the owned active managed session immediately; completed history stays unchanged. GPU/VRAM selection changes apply to future creation. Saving during a search invalidates the old quote and applies the new conditions on the next tick. A reduced budget/price may stop existing compute.
Exact GPU defaults to `NVIDIA L40S` for new/legacy UI preferences. Empty `gpu_id` permits any suitable NVIDIA; manual autoconnection requires an exact ID. Existing searches lacking `auto_connect` remain search-only until the user initiates the new UI flow.

## API contract

Verified against the official [REST v2 OpenAPI](https://api.runpod.io/v2/openapi.json) on 2026-09-14.
Runtime base is fixed to `https://api.runpod.io/v2`; no MCP, OAuth-token extraction, GraphQL or frontend SDK.

Supplier calls: GET catalog/gpus, catalog/datacenters/{id}, network-volumes/{id}, pods, pods/{id}, pods/{id}/logs,
billing/pods; POST pods and pods/{id}/action. Termination uses `action=terminate` on the exact tracked Pod and keeps Network Volume.
No network-volume deletion endpoint exists in this client. GPU catalogue `memory` is VRAM; Pod `gpu.memory` is host RAM and is never treated as VRAM.

Authenticated application routes:

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/compute/status` | State, permissions, session estimate, server clock, next search |
| GET/PUT | `/compute/preferences` | Persistent user preferences; PUT requires compute permission, updates active session/search |
| GET | `/compute/options` | Compatible catalogue and reasons options are disabled |
| POST | `/compute/search` | Validate volume and quote; auto_connect=true creates when eligible, otherwise waits |
| GET | `/compute/quotes/{id}` | Initiator's saved quote |
| POST | `/compute/start` | Confirmed quote, GPU id and idempotency key |
| POST | `/compute/stop` | Stop now or after active generation; external Pod requires admin confirmation |
| POST | `/compute/search/cancel` | Initiator/admin cancellation |
| GET | `/compute/usage/me`, `/compute/sessions/me` | Personal summaries/history |
| GET | `/admin/compute/current`, `/admin/users` | Admin overview |
| GET | `/admin/compute/sessions`, `/admin/compute/usage`, `/admin/compute/events` | Shared history and per-user usage |
| POST | `/admin/compute/reconcile` | Refresh observed state |
| POST | `/admin/compute/billing/refresh` | Read supplier billing for up to 20 recent completed sessions |

## Selection, confirmation and concurrency

Catalogue filtering requires secure NVIDIA, the configured datacenter, minimum VRAM, allowed hourly price and exact `gpu_id` when specified. Options are ordered by price then GPU ID.
The UI button “Найти GPU и подключиться” sends `auto_connect=true`: finding an eligible GPU starts it without a second confirmation. Background retries run on the existing backend monitor, including while the UI is closed. Cancel search disarms retries. No autostart happens merely by loading the settings screen.
Creation uses the same leased controller and rechecks volume, active Pods, availability and price. It never substitutes another GPU within that create attempt. Price changes require a fresh quote; automatic search obtains it on the next retry within the saved ceiling. Definite placement rejection returns to search; ambiguous creation never retries.
Legacy `auto_connect=false` API calls remain quote-only and retain explicit `/compute/start` confirmation.
An existing active Pod on this volume is adopted read-only as `external_compute` instead of creating a second Pod.
External sessions do not receive automatic budget/idle termination and are excluded from estimated personal compute usage.

The singleton database lease serializes operations across controller instances. A committed session intent precedes POST /pods.
The deterministic Pod name supports reconciliation after a restart. Repeated idempotency keys do not create again.
Timeout, malformed create response or supplier 5xx enters `create_unknown`: no automatic retry, even after restart.
Only definite placement rejection can return to another search. Stop failures retain the active session and retry observation/termination.
Start/stop are limited to six attempted operations per user per minute.

**Supplier limitation:** REST create has no documented atomic max-price reservation. Preflight validation cannot prevent a supplier-side price race.
If the returned/observed rate exceeds the confirmed ceiling, the controller immediately requests termination. A short charge can still occur.
Budget is a polling safeguard, not a prepaid supplier cap. The controller reserves one polling interval plus 15 seconds for termination, and stops at the budget boundary even during generation. Supplier outages/delays can still cause overrun.

## Startup, readiness and stopping

Observed states: offline → searching/no_gpu/gpu_found → creating → starting_pod → starting_environment →
mounting_storage → starting_llm → loading_model → ready (or error/stopping/stopped).
No percentage, TPS, token count or public inference URL is fabricated.
The container wrapper checks existing `/workspace/start-llm.sh` and `/workspace/check-llm.sh`, starts the existing script,
checks localhost health and emits phase markers. The backend reads only known markers through authenticated supplier logs, never exposes raw logs.
Ready requires a fresh marker; unavailable/stale logs produce `health_unknown`. Some fast phases can occur between polling intervals.
In mock mode no Pod ports are published. In llama.cpp mode only `9000/http` is published through RunPod HTTPS, protected by a backend-only key; raw llama.cpp `8080` is not published.

The exact compatibility of the image, existing scripts and model is **not live-validated**. A real paid test requires separate explicit approval.
Startup timeout defaults to 900 seconds and terminates managed compute. Idle timeout applies only after readiness, without active generation.
Budget is evaluated from supplier-confirmed start time. Budget/price termination does not wait for an unbounded response; the terminated upstream stream is closed and the existing chat finalizer preserves received partial text.
Manual stop while generating requires confirmation to stop after the answer. Network Volume is retained in all paths.
The backend must remain running for monitoring/auto-stop; closing the desktop is safe, shutting down the backend suspends enforcement.

## Time, cost and usage

`started_at` comes from the supplier's billable start timestamp; `ready_at` is a separately observed readiness time.
Elapsed billing stops when termination is acknowledged or supplier observation confirms EXITED/TERMINATED.
Estimated cost = elapsed whole seconds / 3600 × captured hourly rate, stored with Decimal precision.
It is an estimate, not an invoice. Actual GPU billing is optional and read separately from supplier billing; absent records remain null.
Storage price is unknown because the current catalogue does not expose a applicable monthly rate. Storage is billed separately even when compute stops.
Summaries split durations across UTC day/week/month boundaries and attribute sessions to the initiator.
GenerationUsage stores provider/user/chat/session/status, with nullable token columns reserved for real provider usage; mock does not invent token counts.

## Recovery and operational limits

Apply `alembic upgrade head` before starting. Migration 0002 preserves existing users/chats/messages.
Back up SQLite with its backup API rather than copying an active WAL database.
Use one backend worker for chat streaming: startup recovery marks unfinished generation interrupted. Compute leases are database-backed,
but multi-worker chat lifecycle recovery is not supported yet. PostgreSQL SQL compatibility does not substitute for a live PostgreSQL integration test.
API errors contain a friendly message, stable code and request id; supplier response bodies and credentials are never returned or logged.

Tests use httpx MockTransport or test-only Playwright route interception. No test reads a live API key or contacts paid RunPod endpoints.
