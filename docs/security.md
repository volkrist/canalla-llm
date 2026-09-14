# Security and production boundary

## Current protections

- Secrets exist only in `apps/backend/.env`, which is ignored by Git. The setup script creates a random 48-byte JWT secret.
- Passwords use Argon2id through pwdlib. Authentication of nonexistent users still verifies a dummy hash.
- JWTs use HS256 with a fixed algorithm allowlist, issuer, audience, issue time and required expiration.
- Tokens remain in desktop memory. No token/password is stored in localStorage; logout drops the token.
- The server checks ownership for reading, deleting, adding messages and generation. Foreign IDs return 404.
- The client cannot assign assistant/system roles.
- CORS origins are explicit. Wildcards are rejected in every environment; production rejects HTTP origins.
- Remote backend addresses require HTTPS. Loopback HTTP is the development exception.
- No third-party fonts, raw HTML rendering, remote images or executable Markdown are used.
- Markdown links open only after a click, through the system browser; HTTP(S) only, no credentials in URLs.
- Tauri has no shell, RunPod or arbitrary remote IPC permissions. File writes are limited to paths explicitly selected in the save dialog; no read/delete permission is granted.
- Compute writes require an authenticated admin, or an authenticated user when the backend explicitly enables user compute control.
- RunPod errors are sanitized; API keys and supplier response bodies are not logged or returned. Paid creates have persistent intent, idempotency and price checks.
- Backend and upstream error details are not exposed to users. The frontend handles expired JWTs by returning to login.

## Deployment requirements for the next stage

This is a local functional foundation, not an already deployed public service.

1. Put FastAPI behind HTTPS with bounded request bodies, auth/registration rate limits and SSE-friendly timeouts.
   Disable reverse-proxy response buffering for `/chats/*/stream`.
2. Use `APP_ENV=production`, a strong unique secret, a backed-up PostgreSQL database, and explicit origins.
   The Windows window uses `useHttpsScheme: true`; allow `https://tauri.localhost` for the native client.
   Development additionally allows the explicit Vite and alternate Tauri origins listed in `.env.example`.
3. Narrow the desktop `connect-src https:` CSP to the exact backend host once its production address is chosen.
   The current HTTPS scope supports the runtime backend URL setting.
4. Run one Uvicorn worker: chat startup recovery assumes one streaming owner. Compute uses a database lease.
5. Add registration policy/invitations, email verification, password reset, JWT revocation/refresh and audit/abuse handling.
   Logout currently removes the client token; a previously copied token remains valid until expiration.
6. Add limits for concurrent streams per user, quotas, context token budgeting, retention rules and monitored failure handling.
7. Sign installers and verify the update/distribution process before public distribution.

Database files contain chat contents as plaintext at rest; production storage/access/encryption policies remain deployment work.
SQLite is a local database for the backend, never shared directly with desktop users.
Do not add RunPod management to the desktop. Any later infrastructure controller must remain behind authenticated backend services.
