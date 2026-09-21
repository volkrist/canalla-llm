# Central RunPod Gateway — audit (BEFORE)

Written **before** any implementation of the `feat/central-runpod-gateway` slice, on
`main` = `a1dcd1366183146dce1e51478f9ff6714caa9513` (Alex LLM 0.9.3 development).

It records what exists today, what the trust boundary is, and why the current
installation-global local RunPod credential cannot carry production shared multi-PC usage.
The implemented architecture is in [central-runpod-gateway-design.md](central-runpod-gateway-design.md).

## 1. Local RunPod architecture today (verified in code)

| Concern | Where it lives now |
|---|---|
| Provider HTTP client | `apps/backend/app/compute/runpod_api.py` (`RunPodAPI`: REST v2 + read-only GraphQL balance) |
| Compute state machine, quotes, ownership, idle, budgets | `apps/backend/app/compute/controller.py` (`RunPodController`, DB lease `compute_control`) |
| Owned state tables | `app/compute/models.py` (`compute_control`, `compute_sessions`, `compute_quotes`, `compute_events`, `generation_usage`) |
| Compact AI mapping | `app/compute/runtime.py` (`compact_ai`, `LABELS`) |
| Balance snapshot + cache | `app/status/balance.py` (`RunPodBalanceService`, single-flight, 5 s / 15 s) |
| Credential | Windows Credential Manager `Alex LLM/provider/runpod` (installation-global, saved through Settings → AI / Compute) |
| Secret → backend | Desktop spawn env `RUNPOD_API_KEY` (`backend.rs`, `auth::provider_env`) |
| Readiness | llama.cpp `/v1/models` alias check (`LlamaCppProvider.status`) |
| Inference | `LlamaCppProvider` → the managed Pod's proxy URL (`RunPodController.connection_target`) |

Facts that matter for this slice:

- The per-installation controller already serializes operations with a **database lease**
  (`compute_control.lease_owner/lease_until`) and a committed create intent, so one installation
  never races itself into two Pods.
- Money rules are `$1.20/h` (`runpod_max_hourly_price`) and `$3` per session
  (`runpod_max_session_budget`) — controller-owned, evaluated from supplier-confirmed start time.
- The balance service is a read-only shared snapshot per backend process; it never starts compute.
- The local RunPod credential is **one credential per installation**, shared by all local users of that PC
  (proved and merged in `fix/global-runpod-credential`).

## 2. Trust boundary today

```text
local user (any, incl. the first owner)
        │  local JWT (iss=alex-llm, aud=alex-desktop)
        ▼
local backend  ── owns everything: users, chats, tasks, tools, compute, budgets, credential
        │  RUNPOD_API_KEY from the Desktop spawn env (Credential Manager)
        ▼
RunPod (master account)
```

Properties:

- **Money authority is the local process.** Anyone who can edit the local installation (its `.env`,
  its DB, or its binaries) can raise the caps, and the master key is readable by that installation.
- **Provider authority is the local installation.** Each installation can create/stop Pods on the
  shared account with no cross-installation coordination: N PCs → up to N Pods and N× spend.
- The balance is read per installation, so N PCs polling produce N upstream account queries.
- The local user identity (`user_id`, `email`, `role`) has meaning **only** inside that installation.

## 3. Why an installation-global local credential cannot solve production multi-PC use

1. **Key distribution.** Sharing one master RunPod key across installations means copying it onto every
   PC — into Credential Manager, into spawn env, into the reach of every local admin. A leaked client
   leaks the money.
2. **No global ceiling.** Nothing coordinates two PCs: two installs can each hold a `$3` session and
   two Pods, so the account-level exposure is unbounded by design.
3. **No global single-compute guarantee.** `--parallel 1` and the single-managed-compute rule are
   enforced per installation, not per account.
4. **Revocation.** There is no way to cut one PC off without touching the other PCs or rotating the key
   everywhere.
5. **Audit.** Pod ownership, spend and idle decisions would live in N local SQLite files.
6. **Client is not trustworthy for spending.** Local users are not cloud identities: a modified
   Desktop can send any limit it likes, and the local process is the only thing checking it.

Conclusion: in production shared mode the authority for the provider key, global compute ownership,
financial limits, shared balance and production inference must sit **server-side**, and installations
must authenticate as *installations* — not as local users.

## 4. What must not change

- Local users, first-run owner, persistent sessions, Credential Manager session restore, logout — stay local.
- `Alex LLM/provider/runpod` stays as the **dev / private direct** credential; it is not migrated,
  not uploaded, and is ignored by production shared mode.
- Model, alias, Volume, llama.cpp flags, AUTONOMY/RESEARCH_DEPTH, five status chips, Web/Tor semantics —
  untouched. The local RunPodController keeps working for direct mode.

## 5. Client/server contract for this slice (fixed here, implemented after this document)

```text
GET  /health                     public  → product, version, gateway_protocol_version, ready, database
POST /enroll                     public* → activation_code [+ metadata] → installation_id + installation_secret
POST /auth/token                 public* → installation_id + secret → short-lived JWT (installation_id claim)
POST /auth/revoke                auth    → self-revoke (or operator CLI)
GET  /compute/status             auth    → global compute state + queue + shared balance
POST /compute/ensure             auth    → operation_id + task_id + optional caps → authoritative state (idempotent)
POST /compute/stop               auth    → operation_id → authoritative state
GET  /balance                    auth    → shared account balance snapshot
GET  /v1/models                  auth    → readiness probe (proxied alias list)
POST /v1/chat/completions        auth    → OpenAI-compatible inference (stream + non-stream), cancel on disconnect
```

*public = reachable without a token, but rate limited and requiring a single-use activation code
(`/enroll`) or a valid installation credential (`/auth/token`).

Error envelope: `{"detail", "code", "request_id"}` with stable codes
(`gateway_protocol_mismatch`, `gateway_auth_failed`, `installation_revoked`, `gateway_busy`,
`gateway_queue_full`, `gateway_budget_denied`, `compute_unknown`, `multiple_compute`, `not_configured`).

There is deliberately **no** `/runpod/*` or `/provider/raw` passthrough: the Gateway exposes typed
operations only.

## 6. Threats considered in this slice

| Threat | Mitigation in the design |
|---|---|
| Stolen activation code | single use, short TTL, hash-only server storage, race-safe redemption |
| Client sends `max_hourly=999` | server caps $1.20 / $3 always win; client can only ask for stricter |
| Two installations ensure compute simultaneously | DB-backed global lease + committed intent → exactly one provider create |
| Client bypasses the Gateway for inference | production shared mode has no direct llama.cpp endpoint on the client; the Pod port is only reachable through the Gateway proxy |
| Client contains the master key | master key exists only in Gateway env; artifacts are scanned |
| Remote plaintext Gateway URL | only loopback HTTP allowed; anything else requires HTTPS (fail-closed, tested) |
| Gateway restart loses the credential | installation credential lives in client Credential Manager; token is renewed automatically |
| Gateway outage breaks local product | AI degrades; Computer/Web/Tor/Memory/local data stay local; the same task stays recoverable |
