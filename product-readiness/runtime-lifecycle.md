# Runtime lifecycle

How the local product should live: process, session, data, update, degrade.
RunPod-specific lifecycle is `runpod-lifecycle.md`.

---

## CURRENT VERIFIED FROM REPO (0.9.3)

```text
Developer still: scripts/start-backend.ps1 → Alembic → uvicorn :8000
Desktop on feat/product-runtime-foundation: ensure_backend Job Object sidecar
JWT for API: .env today; generated file in data root when Desktop owns the process
```

There is no Windows service, no sidecar spawn from Tauri, no updater,
no uninstall data policy.

---

## Target process model

One product, two supervised processes (implementation later; spec now):

| Process | Role | Owner |
|---|---|---|
| `alex-llm.exe` | UI, host jobs, pairing | user session |
| `alex-backend` | API, SQLite, compute monitor, tools | spawned by desktop **or** local service |

Rules:

1. Launching the app starts backend if it is not healthy on the expected port.
2. Backend stays up when the window closes **until** GPU idle policy says
   stop **and** no live task exists — then backend may exit too.
   Alternative acceptable design: backend is a per-user service that always
   runs; GPU still idle-stops. Pick one in implementation; both beat today.
3. Killing the window must not leave a **billable Pod** without a monitor.
   If the desktop is the only supervisor, closing it must keep backend alive
   while a managed Pod exists.
4. One worker remains until a later architecture stage. 1.0 is single-user
   local, single worker.

User failure this solves: “I closed the chat and the GPU billed all night”
and “I opened Alex and nothing answers because uvicorn is not running”.

---

## Session lifecycle

| Event | Today | 1.0 |
|---|---|---|
| First register | works | keep |
| App restart | login again | restore session from OS credential store (not localStorage plaintext) |
| Backend URL change | logout | Advanced-only; still logout |
| Token expiry (60 min) | surprise login | refresh or long-lived device session for local owner |
| Logout | drop memory token | revoke local session + drop token |

Do not store JWT in `localStorage`. Windows Credential Manager is already
used for the device secret — reuse that class of storage.

---

## What resets / survives

| Event | Chat history | Memory | Projects | Files | Task | GPU | JWT |
|---|---|---|---|---|---|---|---|
| Window close | SQLite | SQLite | SQLite | disk | SQLite | **stays if backend stays** | lost today |
| Backend restart | yes | yes | yes | yes | recover same task_id | recover/adopt | n/a |
| App upgrade | **must** | **must** | **must** | **must** | **must** | reconnect | restore |
| Uninstall | ask | ask | ask | ask | ask | stop GPU first | wipe |

Volume: never deleted on any of these events.

---

## Data locations (1.0 target)

Move cwd-relative SQLite/documents out of the git checkout into the
application data root already used for embeddings:

```text
%LOCALAPPDATA%\Alex LLM\
  models\embeddings\...
  data\alex.db          (target; today often apps/backend/alex.db)
  documents\            (target)
  logs\                 (target; today stdout only)
  device.json           (already, DPAPI fallback)
```

User failure this solves: “I moved the repo / rebuilt / uninstalled and
lost chats” and “support asked me where the database is”.

Exact paths belong in Advanced diagnostics, not the main chat.

---

## Update strategy (acceptance, not implementation)

See also installer.md.

1. In-place NSIS upgrade is acceptable for 1.0.
2. Upgrade **must** run Alembic before serving.
3. Upgrade **must not** overwrite `JWT_SECRET`, RunPod/TinyFish keys, or user DB.
4. Failed migrate → do not boot chat; offer support bundle + keep previous
   files on disk (no silent delete).
5. Rollback: restore previous app binaries + keep data if schema is
   backward-compatible; if migrate already ran, document “no automatic
   down-migrate in 1.0” and require backup. **MUST:** backup reminder or
   automatic pre-migrate copy of SQLite.
6. No mandatory account re-register.

---

## Offline / degraded

Honest chips. No fake success. No mock answers in a production `llamacpp`
install.

| Down | Still works | User sees |
|---|---|---|
| RunPod / GPU | local UI, history, files browse, pairing | AI: Unavailable / Starting / Waiting |
| TinyFish | local computer, Tor, RAG, coding | Web: Unavailable; Search fallback **only if a real other provider exists** (today: none — do not invent) |
| Internet | local files, coding, history, memory | Web+AI may fail if GPU is remote |
| Tor | Direct web if asked without Tor | Tor: Unavailable; **never** fetch onion over TinyFish |
| Computer | chat, web, Tor | Computer: Unavailable |
| Backend | nothing useful | launch recovery |

**CURRENT VERIFIED FROM REPO:** TinyFish down → `provider_unavailable`;
Tor down → `tor_unavailable` with no Direct fallback; embeddings missing →
chat OK. Production mock-vs-real confusion remains a 1.0 gate.

**RECHECK AFTER 0.9.3:** WAITING_LLM / continue_task copy and whether
reliability layer retries provider errors automatically.
