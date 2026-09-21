# Session restore + first-run foundation — design (0.9.3)

Product property: **identity continuity.** Install once, create/login once,
close the app, open it tomorrow — Alex securely knows the user without a
terminal, without `.env`, without weakening auth, without a password prompt.

Audit of the previous state: [session-first-run-audit.md](session-first-run-audit.md).

## Model

Access session (short-lived) vs persistent device session (longer-lived):

| | Access token | Persistent session |
|---|---|---|
| Form | HS256 JWT (`jti`, `sub`, `iat`, `exp`, `iss=alex-llm`, `aud=alex-desktop`) | Random 256-bit secret, rotated on every use |
| Lifetime | `JWT_EXPIRE_MINUTES` (default 60) | sliding `AUTH_SESSION_DAYS` (default 30), bounded by absolute `AUTH_SESSION_MAX_DAYS` (default 90) from `created_at` — refresh never extends a session forever |
| Stored | Frontend memory only | Device: Windows Credential Manager `Alex LLM/session/{id}` (DPAPI fallback file in data root). Backend: SHA-256 digest only |
| Issued by | `/auth/*`, `/auth/refresh` | `/auth/login`, `/auth/register`, `/auth/bootstrap`, rotated by `/auth/refresh` |

The raw refresh secret never enters SQLite, logs, settings, localStorage or
Git. It crosses the webview only in browser-dev fallback mode (no native
storage there); in Tauri the login/refresh HTTP calls happen in Rust, so the
secret never reaches the JS layer at all.

## Backend session representation (`auth_sessions`)

`id` (uuid), `user_id` (FK cascade), `device_id` (optional, non-secret install
id), `token_hash` (sha256), `rotated_from` (previous hash — replay protection),
`created_at`, `expires_at`, `last_used_at`, `revoked_at`, `replaced_at`.

- Rotation: every successful refresh consumes the presented secret
  (`rotated_from = old hash`) and issues a new one. A replayed rotated secret
  is rejected.
- Expiry: `expires_at = min(now + sliding, created_at + absolute_max)`;
  after the absolute cap the session is rejected even while active.
- Revocation: `POST /auth/revoke` by possession of the secret, idempotent.
  Per-session only; `revoke_all_for_user` exists for future account-wide
  actions (password change/block). Logout = revoke + delete local credential.
- Expiry: rejected at refresh; stale rows are purged opportunistically.
- The access JWT remains valid until its short expiry after logout (same
  posture as security.md's copied-token note).

## First owner

`POST /auth/bootstrap` (migration 0014):

1. Requires local-installation proof: header `X-Alex-Runtime-Token` must match
   `ALEX_RUNTIME_TOKEN` held only by the Desktop-owned backend process
   (hmac.compare_digest). Generic web content cannot bootstrap.
2. Closed permanently once a user exists (409) or the singleton
   `bootstrap_claim` row (id=1) is taken.
3. Race safety is a DB constraint, not timing: two concurrent bootstraps both
   pass the emptiness pre-check, but only one commit wins the primary-key
   claim; the loser rolls back its user row too. Covered by a threaded test.
4. The winner gets `users.is_owner = true`. Migration backfills the oldest
   user of pre-0014 DBs (the historical heuristic). `security.is_first_owner`
   keeps the oldest-user fallback so legacy compute permissions never regress.
5. No default credentials, no hidden master password. Registration after the
   owner exists follows normal product rules (open loopback registration,
   role `user`).

## Desktop device identity

- `runtime/install.id` — stable per-installation uuid (non-secret), sent as
  `X-Alex-Device-Id`. Survives reinstall because the data root is preserved.
- `runtime/session.id` — pointer to the active session credential (non-secret).
- Credential namespace: `Alex LLM/session/{id}` and `Alex LLM/provider/{name}`
  (Credential Manager `CRED_TYPE_GENERIC`; DPAPI fallback
  `runtime/cred-{scope}-{name}.dpapi`). Separate from the tools' user
  credentials (`Alex LLM/user/{name}`) so session secrets never appear in
  tool credential listings.

## First-run state machine

`GET /auth/state` → `{users_exist, state: first_run | auth_required}`.
Desktop never infers first-run from random HTTP errors.

Startup order: Desktop → backend Starting → Ready → state:

```
credential exists ── SESSION_RESTORING → /auth/refresh ── ok → authenticated
                                                        └─ rejected → clear → anonymous
no credential ── first_run → owner bootstrap UI
              └─ auth_required → login UI
refresh network failure → error screen with retry (credential kept)
```

No login screen is flashed during restore. AI/RunPod is never started by
session restore; GPU stays 0.

## 401 recovery (frontend)

`Api` holds the access token; on 401 outside `/auth/*` it performs one
single-flight refresh (parallel 401s share one attempt), updates the token and
retries once. The original request was rejected at auth, so the retry cannot
double-apply it. Refresh failure → auth screen, no loop. SSE/tasks are not
restarted by rotation: the server-side task lifecycle (WAITING_LLM etc.) is
untouched.

## RunPod secret (smallest secure path)

- `POST /auth/…`-style secrets API is not used; the Desktop owns the key:
  Tauri commands `provider_secret_configured` / `set_provider_secret` /
  `delete_provider_secret` (whitelist: `runpod`), stored in
  `Alex LLM/provider/runpod`. The key is **never returned**; status is
  `configured: true/false`.
- The owned backend receives `RUNPOD_API_KEY` in its spawn environment; an
  absent credential leaves developer `.env` untouched, a present one wins
  (it was explicitly configured through the product).
- After set/delete the Desktop restarts the owned backend
  (`restart_backend`): managed Pods are stopped first (same path as Quit),
  the port is awaited to free, then a fresh sidecar picks up the new env.
  Setting a key is not itself a paid action; GPU start confirmation is
  unchanged and separate.

## Desktop quit/restart hardening

The PyInstaller onedir sidecar tree is stopped on Quit/restart via the Job
Object (`KILL_ON_JOB_CLOSE`) and, when Job assignment failed, an explicit
`taskkill /T /F` on our own PID only. Never kill-by-name.

## What this slice is NOT

No polished wizard, no account switcher, no multi-device UI, no enterprise
IAM, no updater/backup, no compute redesign, no WM-07/CD-08 repairs, no LoRA.
