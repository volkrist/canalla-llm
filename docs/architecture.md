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
    └── LlamaCppProvider  (inactive; backend env only)
```

The desktop has no infrastructure SDK and no native shell/filesystem plugin.
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

event: done
data: {"message_id":"..."}

```

On upstream failure: `event: error` with `{detail}` instead of `done`.
Provider internals and credentials are never included in error payloads.
The stream is read with authenticated `fetch`, not EventSource, because it is a POST with an Authorization header.
SSE decoding handles arbitrarily split UTF-8 bytes and LF/CRLF frames.

Stop aborts fetch. The backend closes its provider iterator and persists the generated partial response in a `finally` block.
If no tokens were generated, the empty assistant placeholder is removed; the user message remains.
Concurrent generation, deleting or adding messages in that chat returns 409 while the stream is active.
Other chats remain available. A hard process crash cannot save an in-flight partial response; only completed/cleanly cancelled
generation is guaranteed durable in this MVP. A future generation-job model should represent crash recovery explicitly.

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

## Existing inference infrastructure — reference only

User-provided reference for the future stage: model `orcarouter/Qwen3.8-27B-Uncensored`, Q5_K_M, llama.cpp,
network volume `uwgeaie5b0`, datacenter `US-TX-3`, scripts `/workspace/start-llm.sh` and `/workspace/check-llm.sh`.
These values are documentation, not a controller or active connection. No pod was created, started, stopped or deleted.
