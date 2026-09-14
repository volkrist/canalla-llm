# RunPod Compute Controller — 0.3.0

`LLM_PROVIDER=mock` keeps independent demo chat; `llamacpp` activates the protected real-model connection described in [real-llm.md](real-llm.md).
No paid resources were created, started, stopped or deleted while developing this release.

## Configuration and roles

Copy the compute entries from root `.env.example` into `apps/backend/.env`. Supply a RunPod API key there only.
An absent key returns `not_configured`; the application and mock chat still work.
`ADMIN_EMAILS=["your-address@example.com"]` defines admins on the backend. Restart the backend after configuration changes.
Ordinary registration cannot grant admin privileges. `ALLOW_USER_COMPUTE_START=false` restricts start/search/stop to admins;
enabling it allows ordinary users to operate the shared compute within the same server caps.

Defaults: existing STANDARD 50 GB volume `uwgeaie5b0`, `US-TX-3`, NVIDIA, 48 GB minimum VRAM,
$1.20/hour ceiling, $3.00 session budget, idle timeout 10 minutes, search interval 30 seconds.
Auto-stop choices are 5/10/15/30 minutes or 0 (Never). Server settings are ceilings/floors, not suggestions the frontend can bypass.
Compute preferences are stored per user. Session settings are captured at creation and retained historically.

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
| GET/PUT | `/compute/preferences` | User preferences bounded by server settings |
| GET | `/compute/options` | Compatible catalogue and reasons options are disabled |
| POST | `/compute/search` | Validate volume and save a 90-second quote; never creates compute |
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

Catalogue filtering requires secure NVIDIA, selected data center stock, minimum VRAM and allowed hourly price.
Options are ordered by price then GPU id. Automatic mode confirms the cheapest selectable quoted option; manual mode confirms one explicit GPU.
Creation rechecks volume, existing Pods and live catalogue. Bounded automatic fallback tries at most three available GPUs at or below the confirmed price;
manual mode never substitutes another GPU. A changed price requires a new quote and confirmation.
An existing active Pod on this volume is adopted read-only as `external_compute` instead of creating a second Pod.
External sessions do not receive automatic budget/idle termination and are excluded from estimated personal compute usage.

The singleton database lease serializes operations across controller instances. A committed session intent precedes POST /pods.
The deterministic Pod name supports reconciliation after a restart. Repeated idempotency keys do not create again.
Timeout, malformed create response or supplier 5xx enters `create_unknown`: no automatic retry, even after restart.
Only definite placement rejection can advance to a bounded fallback. Stop failures retain the active session and retry observation/termination.
Start/stop are limited to six attempted operations per user per minute.

**Supplier limitation:** REST create has no documented atomic max-price reservation. Preflight validation cannot prevent a supplier-side price race.
If the returned/observed rate exceeds the confirmed ceiling, the controller immediately requests termination. A short charge can still occur.
Budget is also a polling safeguard rather than an exact prepaid cap: startup, active generation and supplier delays can produce overrun.

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
Budget is evaluated from supplier-confirmed start time. It blocks new generation and schedules termination after active work ends.
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
