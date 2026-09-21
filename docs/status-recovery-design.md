# Status + recovery + shared RunPod balance — design (AFTER)

Implemented in the **FIVE-CHIP STATUS + ERROR / RECOVERY UX + SHARED RUNPOD BALANCE** slice,
branch `feat/status-recovery-ux`, Alex LLM **0.9.3 development**.

Input: [status-recovery-audit.md](status-recovery-audit.md) (state BEFORE). Permanent rules that were
not touched: [security.md](security.md), [on-demand-ai.md](on-demand-ai.md), [runpod-controller.md](runpod-controller.md).

## 1. Goal

The user should see, in one glance and without a terminal: whether Alex can think, work with the local
computer, use the web, route through Tor and use memory — plus the shared RunPod balance, what it costs
per hour, and what to do when something fails. A status read must never start compute or spend money.

## 2. One vocabulary

`ready | starting | off | not_configured | unavailable | error | degraded`

`app/status/snapshot.py` owns it. Every chip is a small object:

```json
{
  "state": "ready",
  "message": "Веб доступен.",
  "detail_code": null,
  "recoverable": false,
  "action": null,
  "details": { "provider": "TinyFish", "probe": "configuration" }
}
```

- `state` — the vocabulary above; the UI always renders text for it, never colour alone.
- `message` — product copy, Russian, no stack traces, no internals, no secrets.
- `detail_code` — the stable code (compute/RunPod/tool code) for diagnosis.
- `recoverable` — whether a plain retry can plausibly help.
- `action` — `retry | configure | reconnect | stop | cancel_search`, only when a safe path exists.
- `details` — small, non-secret facts used by the chip detail panel.

`details` never contains a provider key, a token, or a raw upstream body.

## 3. Chips and their single source of truth

| Chip | Source of truth | `ready` means |
|---|---|---|
| **AI** | `RunPodController.llm_public_status()` → existing `compact_ai()` mapping | the production alias is healthy (`ready`/`generating`) |
| **Computer** | `app/tools/local/devices.py` `active_device()` (heartbeat inside 45 s) | a non-revoked paired device answered recently |
| **Web** | TinyFish configuration + per-user `WebSettings` | configured **and** enabled by the user |
| **Tor** | `socks_listening()` — the same SOCKS5 endpoint the executor requires | the SOCKS5 endpoint accepts a connection |
| **Memory** | `User.use_memory` + a live count query on `memories` | enabled and the subsystem answered |

No subsystem state machine was duplicated: AI is refined from `compact_ai()` output (three explicit
branches), and every other chip reads the module that already owns it.

Honesty rules that are enforced in code and covered by tests:

- GPU running ≠ AI ready. Only the compact mapping decides, and `create_unknown` never becomes Ready.
- Web `ready` is explicitly a **configuration** verdict (`details.probe = "configuration"`); provider
  health is confirmed per request, not by this chip.
- Tor is **fail-closed**: `ready` requires the SOCKS endpoint, `details.verified_chain` stays `false`
  (the chip does not claim a proven circuit), `details.fallback` is `none`, and an unconfirmed Tor with
  `tor_mode=on` reports `unavailable` with `required: true`. The chip never performs the expensive
  `prove_socks5()` handshake.
- Memory never triggers an embedding download. Retrieval is lexical (`details.retrieval = "lexical"`),
  so the chip stays free.
- `multiple_compute` never gets a destructive action; `create_unknown` offers a retry that only re-reads
  state and lets the existing controller reconciliation continue.

## 4. Endpoint

`GET /status` — authenticated, **read-only**, available to any authenticated user (not admin-only):

```json
{
  "generated_at": "…",
  "subsystems": { "ai": {…}, "computer": {…}, "web": {…}, "tor": {…}, "memory": {…} },
  "balance": { … }
}
```

`app/status/routes.py` only assembles. It cannot start, adopt or stop compute: there is no write path in
the module, and a test monkeypatches `start_compute`/`search_gpu`/`create_pod`/`stop_compute` to raise and
still asserts a 200 response.

Existing endpoints (`/llm/status`, `/compute/status`, `/tools/status`) are unchanged so nothing that
already works can regress.

## 5. Shared RunPod balance

**Upstream reality** (verified against both OpenAPI documents): neither REST v2 nor REST v1 exposes an
account balance. The balance exists only on `POST https://api.runpod.io/graphql` as
`myself { clientBalance currentSpendPerHr }`.

- Implemented as a **read-only method on the existing client**: `RunPodAPI.account_balance()` +
  `RunPodAPI.graphql()`. No second RunPod client, no second compute stack, no mutation of any kind.
  The query only selects `id`, `clientBalance`, `currentSpendPerHr`, and a test asserts that no
  mutation verb appears in the query body.
- Endpoint/host comes from `RUNPOD_GRAPHQL_URL` (default `https://api.runpod.io/graphql`), the same
  default `runpodctl` uses. Timeout 6 s, bounded so a hung supplier cannot hold a request open.
- The key is read from the existing secure source (`settings.runpod_api_key`, populated for the owned
  backend from Windows Credential Manager by the session slice). It is never returned, never logged and
  never sent to the frontend.

**One snapshot for the whole installation** — `app/status/balance.py`:

| Property | Behaviour |
|---|---|
| Scope | one process-local snapshot per backend, shared by every user |
| Single-flight | one `asyncio.Lock`; concurrent callers wait and then reuse the fresh value |
| Cache TTL | **5 s** while tracked compute is billable, **15 s** otherwise (`RunPodController.gpu_active()`, read-only) |
| On failure | the timestamp still moves, so a broken supplier is polled at the interval — never once per user request — and the previous balance is kept with `stale: true` |
| Persistence | none. No balance in SQLite; a backend restart simply reads again |
| Money | `Decimal` end to end, serialized as a **string** (`"8.73"`), so precision is preserved and the historical Decimal/datetime JSON bug cannot return |
| Cost | read-only. No compute is created, resumed or stopped, and no Network Volume is touched |

Failure semantics (fail closed, never a fake zero):

| Situation | `available` | `balance_usd` | `error_code` |
|---|---|---|---|
| No key | false | null | `not_configured` (no upstream call at all) |
| First read fails | false | null | the RunPod code (`runpod_timeout`, `runpod_auth`, …) |
| Later read fails | true | last value | the RunPod code, `stale: true` |
| Malformed / missing field | unchanged | unchanged | `malformed_response` |

`low` / `low_threshold_usd` are presentation-only (`LOW_BALANCE_USD = $5.00`, a module constant). They
do **not** gate compute: the `$1.20/h` cap and `$3` session budget stay the only compute rules.

## 6. Billing UI

From the same payload, so nothing is invented and nothing is hardcoded:

- `RunPod balance: $X.XX` — shared account, identical for every user. Stale reads are labelled
  "Устарело: последнее удачное обновление …"; no successful read ever → "Баланс недоступен";
  no key → "RunPod не настроен". Never `$0.00`.
- `GPU: <gpu_type> · $<hourly_rate>/ч` — from the tracked session row, only when a session exists.
- `Сессия: ≈$<estimated_cost>` — the controller's own `estimate()` over the same session row, labelled
  as an estimate. It is deliberately **not** presented as an invoice; `compute/usage/me` already
  separates estimated compute cost from actual supplier billing.
- `Низкий баланс` — presentation warning only.

## 7. Error UX and recovery

`apps/desktop/src/lib/errors.ts` maps a stable code to one of
`configuration | network | provider | compute | model_startup | computer | web | tor | memory | authentication | unknown`
and produces one card: category title, product message, diagnostic code and an optional action.
`apps/desktop/src/lib/status.ts` only exposes a whitelist of detail rows, so an unexpected field can
never reach the DOM.

Every action reuses machinery that already exists:

| Action | What it does |
|---|---|
| `retry` | re-reads the authoritative snapshot (the shared cache decides whether upstream is asked again — no stampede) |
| `configure` | opens the existing settings dialog (provider secret panel) |
| `reconnect` | re-triggers the existing device loop (`alex-host-jobs`) |
| `stop`, `cancel_search` | opens the existing compute panel, which keeps its own confirmation and lifecycle |

No button is rendered when the backend cannot safely perform the action (for example `multiple_compute`).

**Same-task recovery** is unchanged: this slice adds no task, no message and no retry of a user request.
`WAITING_LLM`, the parked SSE stream and the existing task identity are untouched; `create_unknown` only
displays state and lets the existing reconciliation run.

## 8. Frontend lifecycle

- One hook owns polling: `useStatus` in the Workspace. Self-scheduling timeout (never overlapping),
  interval taken from the backend's `refresh_seconds` (5 s GPU-active / 15 s idle), at least 30 s while
  the window is hidden, one immediate re-read when it becomes visible again, and a full cleanup of the
  timer and listener on unmount.
- Before the first snapshot every chip renders `starting` ("Проверяем…"), so startup never flickers
  `Error → Ready`.
- The five chips live in a status bar under the topbar and keep the existing Alex visual language; the
  old one-off `ai-chip` was replaced by the AI chip in that row (no duplicate indicator).
- 401 handling is unchanged: `Api` keeps its single-flight refresh and short access token.
  `/status` is a `GET`, so a refresh-and-retry cannot double-apply anything.

## 9. Deliberately not done

- WM-07 TinyFish Browser live execution/lifecycle, CD-08 stale-SHA debt.
- No RunPod controller redesign, no new compute stack, no new client.
- No balance persistence, no WebSocket/SSE for status (polling + shared cache is simpler and sufficient).
- No `Session spend` invention beyond the controller's own estimate; no billing rewrite.
- No new budget/threshold system that could block compute.
- No session-management UI for multiple devices (the session model already supports it).

## 10. Cost

Status, balance reads and polls never start or resume compute. RunPod GPU spend `$0`, TinyFish `$0`,
GPU `0`, Network Volume `uwgeaie5b0` untouched.
