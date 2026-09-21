# Session restore / first-run — current auth flow audit (0.9.3)

Audited on branch `feat/session-first-run` from `main` `cc8ddb9`. Findings are from
code inspection, not from historical chat reports. Verified against
`apps/backend/app/auth.py`, `security.py`, `models.py`, `config.py`, `main.py`,
`runtime_entry.py`, `data_paths.py`, `apps/desktop/src-tauri/src/{main,backend,host,credential}.rs`,
`apps/desktop/src/App.tsx`, `lib/api.ts`, `components/AuthScreen.tsx`.

## Access token

- Single JWT, HS256, issued by `security.create_token`.
- Claims: `sub=user.id`, `iat`, `exp`, `iss=alex-llm`, `aud=alex-desktop`.
- Lifetime: `JWT_EXPIRE_MINUTES` default **60** (bounded 1..1440) in `config.py`.
- Verification: fixed algorithm allowlist `["HS256"]`, issuer/audience check,
  required `sub/exp/iat` (`current_user`). Signed with `JWT_SECRET`.
- `JWT_SECRET` is generated once into `%LOCALAPPDATA%\Alex LLM\runtime\jwt.secret`
  (`load_or_create_jwt`, ≥48 chars) — so tokens remain valid across backend
  restarts; no invalidation on restart.

## Where the frontend stores the token

- **React memory only.** `App.tsx` keeps `session = { token, user }` in `useState`.
- `Api` (lib/api.ts) holds the token in a constructor field; every request adds
  `Authorization: Bearer`.
- `localStorage` stores only `alex-settings` (backend URL, theme, font…). No token.
- **No refresh token exists anywhere.** Desktop quit → token lost → login again.
  This confirms the historical finding: auth state is memory-oriented and a full
  app restart always lands on the login screen.

## Backend session storage

- No session/refresh tables. `users` only.
- `presence_sessions` is heartbeat presence, not authentication.
- `paired_devices` (tools) stores a hashed device credential; separate concern.

## Logout semantics

- **No backend logout endpoint.** Frontend logout = `setSession(null)`.
- Token remains valid until natural expiry; a copied token keeps working.

## Restart semantics

- Backend restart: JWT secret persists on disk; the frontend would keep working
  while the app is open, but the UI has no reconnect-retry for 401s.
- Desktop restart: backend is respawned (same data root), but the frontend has
  no stored token → AuthScreen → password again. **No session restore.**

## First-user / owner semantics

- Implicit: `security.is_first_owner` = oldest user by `created_at` (then `id`).
- Used by `can_start_compute` (owner or admin may start paid compute).
- Admin role: `sync_role` derives admin from `ADMIN_EMAILS` env only.
- Registration (`POST /auth/register`) is open to any loopback client; no
  bootstrap, no closure after first owner, **no race protection**.
- Backend binds `127.0.0.1` (owned spawn sets `ALEX_BACKEND_HOST=127.0.0.1`).

## Password hashing

- Argon2id via `pwdlib.PasswordHash.recommended()`. Dummy hash verification for
  nonexistent users (timing equalization). No password-change endpoint exists.

## Credential Manager usage (Rust)

`apps/desktop/src-tauri/src/credential.rs` already provides:
- Device credential target `Alex LLM/device-credential` (Credential Manager
  `CRED_TYPE_GENERIC`, `CRED_PERSIST_LOCAL_MACHINE`), with DPAPI file fallback
  `device.cred.dpapi` in the data root.
- Named user credentials `Alex LLM/user/{name}` (`store_named/load_named/…`,
  name charset `[a-zA-Z0-9._-]`, ≤80) used by coding tools for user-supplied
  secrets; the names list is stored in `user-credential-names.json`.
- `storage_kind()` reports `windows_credential_manager | dpapi_file_fallback | none`.

## device.json usage

- Data root `device.json` = paired-device record (`device_id`, `display_name`),
  **non-secret**; the device *secret* lives in Credential Manager.
- Pairing (`POST /tools/devices/pair`) is authenticated by the user token and is
  tools-scoped, not auth-scoped. Reinstall preserves data root → record survives.

## Runtime trust primitives available

- `runtime/shutdown.token` → env `ALEX_RUNTIME_TOKEN` (backend) and read by
  Desktop; used today for `/runtime/shutdown` (managed compute stop on Quit).
  Desktop is the only party that knows it — usable as a local-installation proof
  for first-owner bootstrap.
- Per-spawn `ALEX_BACKEND_INSTANCE` (non-persistent uuid) for health identity.

## RunPod key source

- `settings.runpod_api_key` ← env `RUNPOD_API_KEY` / `.env` only.
- Desktop `spawn_owned` passes no provider key env today.
- An installed user must edit `.env` to configure RunPod. No product storage
  path, no status API (`/compute/status.configured` derives from the env key).

## CORS

- Allowed headers: `Authorization`, `Content-Type`, `X-Alex-Device-Id`,
  `X-Alex-Device-Credential`. Origins explicit (tauri.localhost, dev ports).

## Migration head

- `0013` (`0013_compute_ownership.py`). Batch-mode SQLite migrations,
  `alembic check` used in tests; `test_migrations.py` pins `version_num=0013`.

## Conclusions for the session/first-run slice

1. Access JWT is short-lived and stateless — keep, it is correct.
2. Missing: persistent device session (hashed refresh secret in DB), secure
   client-side credential storage (reuse/extend `credential.rs`), refresh +
   rotation + revocation endpoints, and first-run state/owner bootstrap.
3. Desktop restart must restore via a refresh stored in Credential Manager;
   backend restart already keeps JWTs valid.
4. First owner must become a deterministic, race-safe, local-proofed bootstrap
   that closes permanently; legacy `is_first_owner` stays as a compute fallback
   so migrated DBs keep working.
5. RunPod key needs a secure product path (Credential Manager) passed to the
   owned backend via env, with set/delete/status operations that never return
   the secret.
