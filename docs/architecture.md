# Architecture and API

```text
Alex LLM / Tauri WebView2
    │ Authorization: Bearer <JWT>
    │ HTTPS (loopback HTTP in local development)
    ▼
FastAPI ─── SQLAlchemy ─── SQLite / later PostgreSQL
    │
    ▼
LLMProvider
    ├── MockLLMProvider   (active)
    └── LlamaCppProvider  (backend selection; authenticated gateway to current Pod)
```

The desktop has no infrastructure SDK or shell plugin. Tauri dialog/fs plugins save exports only to user-selected paths;
the opener plugin permits only HTTP(S) URLs. RunPod credentials exist only in the backend.
React components handle presentation, `useChat` handles chat state, `Api` owns requests, and `consumeSSE` parses streaming data.
The Rust host is deliberately small and exposes no custom commands.

## API

Interactive development docs: `http://127.0.0.1:8000/docs`.
All request and response bodies are JSON except the SSE response. IDs are UUID strings.

| Method | Route | Request / result |
|---|---|---|
| GET | `/health` | `{status, provider, llm_ready}`; public |
| POST | `/auth/register` | `{email, password}` → 201 `{access_token, token_type}` |
| POST | `/auth/login` | `{email, password}` → token |
| GET | `/auth/me` | Current user; never returns password hash |
| GET | `/chats` | Own chats, newest updated first; `offset`, `limit` (max 100) |
| POST | `/chats` | `{title?}` → 201 chat |
| GET | `/chats/{id}` | Own chat or 404 |
| DELETE | `/chats/{id}` | 204, cascades to messages |
| GET | `/chats/{id}/generation` | Own chat's `{active}` state, used to reconcile cancellation |
| GET | `/chats/{id}/messages` | Chronological messages; `offset`, `limit` (max 200) |
| POST | `/chats/{id}/messages` | `{content}` → 201 user message, no generation |
| POST | `/chats/{id}/stream` | `{content}` → persists user message, streams assistant response |

The desktop uses `/stream` directly; it must **not** also POST the same input to `/messages`.
Clients cannot set message role or impersonate the assistant. Blank or oversized messages are rejected.
`401` means missing/expired credentials; `404` also covers foreign chats; `409` covers a duplicate email or busy chat.

## Streaming contract

```text
event: meta
data: {"user":{...},"assistant":{...}}

event: delta
data: {"content":"incremental text"}

event: task
data: {"id":"...","status":"EXECUTING","steps":[...], ...}

event: task_status
data: {"state":"WAITING_DEVICE","code":"host_offline"}

event: done
data: {"message_id":"..."}

```

On upstream failure: `event: error` with `{detail}` instead of `done`.
Provider internals and credentials are never included in error payloads.
The stream is read with authenticated `fetch`, not EventSource, because it is a POST with an Authorization header.
SSE decoding handles arbitrarily split UTF-8 bytes and LF/CRLF frames.

Stop aborts fetch. The backend closes its provider iterator and persists the generated partial response in a `finally` block.
If no tokens were generated, the stopped/error assistant row remains so Retry is available.
Concurrent generation, deleting or adding messages in that chat returns 409 while the stream is active.
Other chats remain available. A hard process crash cannot save an in-flight partial response; only completed/cleanly cancelled
generation is guaranteed durable in this MVP. GenerationUsage records interrupted work; startup marks unfinished responses as errors.

Provider context uses at most the latest 100 messages and approximately 64,000 characters.
This is a conservative character bound, not a model tokenizer-based limit.
The mock deliberately labels its templated output as a mock and includes a code block for UI testing.
The llama.cpp adapter calls `/v1/chat/completions` and `/v1/models` using only backend configuration.

## Data and migrations

Users contain email, Argon2 hash and timestamps. Chats reference their owner. Messages reference their chat.
All IDs are strings containing UUIDs, timestamps use SQLAlchemy `DateTime(timezone=True)`, and no SQLite-specific column types are used.
Foreign keys with `ON DELETE CASCADE` are enabled in SQLite. SQLite uses WAL and a 30-second busy timeout.
Schema creation is through Alembic, not implicit startup `create_all`.

```powershell
# From apps/backend, after configuration:
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic check
```

## Compute and chat-management extensions (0.2.0)

`app/compute/runpod_api.py` validates and sanitizes REST v2 responses. `controller.py` owns state transitions,
database leases and committed create/stop intents. `routes.py` enforces authenticated roles and exposes usage.
The monitor runs in FastAPI lifespan. Browser close/logout does not stop the backend monitor or reset accounting.

Migration `0002` adds chat pinning, message status/edit timestamps, user roles, compute sessions/events/control/preferences/quotes,
and generation usage. Existing chat data remains intact. The `compute_control` singleton stores the active session and expiring CAS lease.
No network operation is made inside an open database write transaction.

| Method | Route | Purpose |
|---|---|---|
| PATCH | `/chats/{id}` | Rename / pin; ownership checked |
| GET | `/chats?q=...` | Literal title search, pinned first |
| GET | `/chats/export`, `/chats/{id}/export` | `format=json` or `markdown`; owned data only |
| PATCH | `/chats/{id}/messages/{message}` | Edit user text, retain later history |
| POST | `/chats/{id}/messages/{message}/resend` | Edit user text, delete all later messages, stream replacement |
| POST | `/chats/{id}/messages/{message}/regenerate` | Return to preceding user message, truncate tail, stream replacement |
| GET | `/llm/status` | Explicit mock availability separate from compute |

SSE meta includes `replace_after_id`: the client replaces that user message and everything after it with the returned user/assistant pair.
Compute endpoints and billing semantics are described in [runpod-controller.md](runpod-controller.md).

## Existing inference infrastructure

Version 0.3.0 adds `remote_runtime.py`, a dependency-free authenticated Pod gateway and idempotent wrapper around the existing scripts.
LlamaCppProvider resolves the current managed Pod from persistent compute control on each request. Readiness requires an exact model alias from `/v1/models`.
Usage is collected per stream, without shared mutable counters, then persisted with the existing GenerationUsage relationship.
Migration 0003 adds nullable `total_tokens`. Missing supplier usage remains null. See [the real-LLM contract](real-llm.md).

User-provided reference for the future stage: model `orcarouter/Qwen3.8-27B-Uncensored`, Q5_K_M, llama.cpp,
network volume `uwgeaie5b0`, datacenter `US-TX-3`, scripts `/workspace/start-llm.sh` and `/workspace/check-llm.sh`.
The controller mounts that existing volume and invokes those existing scripts. No model download or volume deletion is implemented.
No real pod was created, started, stopped or deleted during development; paid integration remains untested.

## 0.4.0 personal context and realtime state

Presence sessions/tickets and personal projects/memories are separate from chats and compute. `ContextBuilder -> LLMProvider` is the only generation context path. GenerationUsage is also the backend source for Using AI. See [presence](presence.md), [memory](memory.md), and [context-builder](context-builder.md) for ownership boundaries, schemas, events, scoring and single-worker limitations. RAG and automatic memory extraction remain future work.

## 0.5.0 documents and generation snapshots

`app/documents` owns LocalDocumentStorage, isolated LocalExtractor, token-aware DocumentChunker, CPU LocalEmbeddingProvider, SQLVectorStore, DocumentIndexJob and authenticated routes. Upload endpoints are byte bounded before multipart spooling; binaries never enter SQL. Document/chunk ownership and project scope are mandatory in database queries. Index replacement is one transaction; jobs are serialized and stale jobs reconcile at startup. Run one backend worker.

ContextBuilder adds a distinct untrusted document section after memory and before history/current prompt. MessageContext.snapshot stores actual generation metadata and source references/excerpts. Deletion redacts old excerpts; it does not rerun historical retrieval. All settings and budgets are validated on the backend. Tables use standard SQLAlchemy types compatible with PostgreSQL; pgvector is a future VectorStore implementation, not included now.

Migrations 0005 add context/TTFT fields; 0006 adds documents, document_chunks and rag_preferences. Existing compute/session/auth/presence behavior is retained. See [rag.md](rag.md) and [files.md](files.md).

## 0.6.0 lifecycle and tools

EmbeddingModelManager owns pinned artifacts, filesystem locking, staging, integrity checks and real CPU smoke. EmbeddingProvider consumes only a READY installation. Model state is shared; documents and vectors remain owner scoped.

ToolRegistry → ToolOrchestrator → model proposal → ToolExecutor → ToolPolicy → adapter → ToolResult → next model proposal/final ContextBuilder. Adapters depend on generic contracts; core has no TinyFish API dependency. Planner receives current request and public tool results, not private memories/documents/history, reducing external disclosure. ContextBuilder places bounded untrusted web references after documents and before history/current request. Migration 0007 adds preferences, audit runs and web source snapshots.

User Stop cancels planner/provider tasks. Failed/denied tool executions emit terminal audit events, removing stale confirmation cards. TinyFish Browser sessions are backend-only, owner scoped, guarded before navigation, timed and supplier-terminated. CDP URLs never reach UI, SQL or model context. TinyFish Agent is READ_ONLY only until the provider exposes pre-action approval.

## 0.7.0 WebRouter, Tor and Local Computer

WebRouter classifies Off/Auto/On independently of `tor_mode` and `computer_mode`. Planner `tool_choice` remains auto. Missing required Search is injected as `origin=server_policy`, then Fetch 1–3 canonical URLs (`ttl=0` when fresh). TinyFish Agent/Browser default to Auto: hidden from the planner unless the server route selects them.

Tor uses a custom SOCKS5h client (ATYP 0x03, loopback proxy). Tor Search is configured providers, not the official onion catalog. Authority is official vs reachable, never collapsed.

Local tools are host jobs. `paired_devices` stores a credential hash only. Tauri keeps the secret in Credential Manager / DPAPI. Host results bind device, digest, owner, pending status, one-time consume and expiry. Trusted Workspace auto-allows seven filesystem tools inside canonical roots; process tools still confirm. Child processes use a sanitized environment and a Job Object with `KILL_ON_JOB_CLOSE`. Migration 0008 adds origin, assigned device, source channel/authority/canonical/kind and `paired_devices`.

## 0.7.1 Local Computer risk policy

Trusted Workspace is no longer a path jail: Alex may use the whole local computer. Trusted roots only reduce confirmations for `READ` and in-root `NORMAL_CHANGE`. `SENSITIVE`/`CRITICAL` always confirm with an explanation and an immutable digest (`action` + arguments). CRITICAL is Allow-once only. Delete/registry/services/install remain registered. Device display name defaults to `Windows device`. Forget this device revokes the credential. ToolRun metadata records `network: direct|tor` with no Tor→Direct fallback.

## 0.8.0 Coding Agent foundation

Coding uses the same registry/policy/orchestrator/host stack. `LocalTask` checkpoints (migration 0009) store hashes and command digests, not file bodies. `patch_file` requires `expected_before_sha256`. Git tools are argv-only with credential helper disabled. Force-push and hard reset stay CRITICAL and host-disarmed. Rotate device credential overwrites Windows Credential Manager. Confirmation copy is split by risk. Hard ceiling: 32 tool calls.

## 0.8.3 Automatic Tor research

Tor Off/Auto/On is independent of Web. A Tor intent router can inject `tor_search` with `origin=server_policy` when the user asked for Tor and the planner returned no Tor tool. `tor_fetch` extracts structured links; follow stays model-selected under visited-URL and budget ceilings. llama.cpp planner/stream payloads send `chat_template_kwargs.enable_thinking=false`. Migration 0010 adds `web_source_snapshots.details`.

## 0.8.4 Tor Browser automation

`TorBrowserProvider` drives the installed Tor Browser executable through Marionette with a temporary profile and a Windows Job Object. `tor_fetch` stays primary; browser is a read-only fallback for JS shells or an explicit Tor Browser request. Click uses L-ids. Downloads, forms, credentials and arbitrary model JS are blocked. `TorRoutedBrowserProvider` remains unimplemented.

## 0.8.6 automatic Tor Browser fallback

Prompt `.onion` URLs are fetched before search. JS-shell HTTP fetches (`#root` /
short `Loading...` + script, not a mere `<script>` on a rich page) trigger
`TorBrowserProvider` automatically. Onion navigations wait long enough for JS.
Temporary hidden-service keys are test-only and are not shipped.

## 0.8.5 model-driven Tor Browser

Planner policy prefers `tor_browser` for an explicit Tor Browser request (official check URL `https://check.torproject.org/`). Server-policy still opens/clicks if the model already produced a snapshot with L-ids. GPU E2E: OrcaRouter called `tor_browser` `origin=model`, rendered DOM, followed L1, T sources `retrieval=browser`. Automatic fetch→browser fallback on a public JS-shell was not claimed.

## 0.9 Autonomous Task Agent

Persistent tasks reuse `LocalTask` (migrations **0011** and **0012**): plan steps, journal, checkpoints, exclusive workspace WRITE locks with a FIFO `WAITING_WORKSPACE` queue, Pause/Resume/Stop, budgets, and a final verification gate. The loop is still `ToolOrchestrator` — not a second agent stack. 0.9.1 adds queue persistence, task continuation after restart, Pause text sanitization, optional verified git commit/push, and confirmed loopback external actions. 0.9.2 adds TinyFish Agent (READ_ONLY) and TinyFish Browser (Alex-controlled CDP) with app-side paid budgets. See [autonomous-tasks.md](autonomous-tasks.md) and [tinyfish.md](tinyfish.md).

| Method | Route | Purpose |
|---|---|---|
| GET | `/tasks` | Own tasks; optional `chat_id` |
| GET | `/tasks/{id}` | Plan, journal, verification; 404 for other users |
| POST | `/tasks/{id}/pause` | `PAUSED`; no new tools |
| POST | `/tasks/{id}/stop` | `STOPPED`; cancel generation |
| POST | `/tasks/{id}/resume` | SSE resume of the same task, no new user message |

