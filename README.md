# Alex LLM

Windows desktop **0.4.0**: **Tauri 2 + React + TypeScript** and **FastAPI + SQLAlchemy**.
The desktop connects only to the backend. The default LLM is a deterministic **mock**, so no GPU, RunPod account or inference server is needed.

## Quick start on Windows

Prerequisites: Node.js 22.12+ (tested with 24), Python 3.12, Rust stable MSVC,
Visual Studio C++ Build Tools with Windows SDK, and WebView2.
See [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/).

Run these commands from the repository root in PowerShell:

```powershell
# Once: install Python dependencies, generate backend-only JWT secret, migrate SQLite.
.\scripts\setup-backend.ps1

# Once: install locked frontend dependencies.
cd apps\desktop
npm ci
cd ..\..

# Terminal 1: backend at http://127.0.0.1:8000
.\scripts\start-backend.ps1

# Terminal 2: native Tauri development window
.\scripts\start-desktop.ps1
```

The scripts add the default Rust binary directory to the current process PATH when needed.
If PowerShell blocks local scripts, run them with `powershell -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1`
(only affects that process).

For a browser preview instead of a native window:

```powershell
cd apps\desktop
npm run dev
# Open http://127.0.0.1:1420
```

Register through **Регистрация** using an email such as `tester@example.com` and your own password of 10–128 characters.
There are no built-in accounts or default passwords. Registration signs you in automatically.
Send a message; the mock streams a response with Markdown and a Python code block.
Use **Stop generation** to cancel; the partial answer remains in history.
Create another account to verify its history is separate.

## Native Windows build

```powershell
.\scripts\build-desktop.ps1
```

Outputs:

- `apps/desktop/src-tauri/target/release/alex-llm.exe` — native portable executable, displayed as Alex LLM.
- `apps/desktop/src-tauri/target/release/bundle/nsis/` — Windows installer.

The backend is a separate service; it is not bundled into the desktop installer.
Start it locally for this MVP or configure an HTTPS backend in **Settings**.
The development build is not code signed.

## Structure

```text
alex-llm/
├── apps/
│   ├── backend/
│   │   ├── app/          # config, auth, database, models, routes, LLM providers
│   │   ├── alembic/      # explicit versioned database migrations
│   │   ├── tests/        # authentication, ownership, SSE and failure tests
│   │   ├── pyproject.toml
│   │   └── requirements.lock
│   └── desktop/
│       ├── src/
│       │   ├── components/
│       │   ├── hooks/    # chat state and cancellation
│       │   └── lib/      # backend transport, SSE decoder, settings
│       ├── src-tauri/    # Rust host, CSP, capabilities, Windows packaging
│       └── e2e/          # real UI + isolated backend tests
├── docs/
├── scripts/
├── .env.example
└── .gitignore
```

## Implemented

- 0.3.0: real llama.cpp transport, authenticated Pod gateway, dynamic endpoint recovery, model-alias readiness, supplier token usage, and visible Start/Stop AI controls. [Real LLM setup and preflight](docs/real-llm.md). Paid integration requires explicit approval and has not yet been executed.

- Chat rename/search/pin, Markdown/JSON export (one or all), user-message editing, edit-and-resend and regeneration with linear-history truncation.
- Per-account/backend/chat local drafts, keyboard shortcuts, configurable Enter, timestamps, syntax highlighting, safe system-browser links and native save dialog.
- Separate backend/mock/compute status, RunPod REST v2 controller, GPU discovery with automatic connection under saved UI limits, persistent sessions, budget/idle stop and usage/admin views.
- RunPod is optional: an empty backend API key shows **Not configured** and mock chat remains usable. Read [the controller guide](docs/runpod-controller.md) before enabling paid compute.

- Login, registration, authenticated user endpoint; Argon2id password hashes and expiring JWTs.
- Private chats and messages with ownership checks on every chat endpoint; deletion cascades to messages.
- Persisted history, new chat, first-message titles, pagination, clear loading/error/offline states.
- Incremental SSE rendering, Stop, partial-answer persistence, protection against simultaneous generation in one chat.
- Dark responsive UI, Markdown/GFM tables, fenced code blocks and Copy, adjustable text size and backend address.
- `LLMProvider.chat`, `stream_chat`, `health`; default `MockLLMProvider` and inactive `LlamaCppProvider`.
- Backend-only settings, explicit CORS origins, minimal Tauri permissions and CSP.
- SQLite migrations plus PostgreSQL-compatible models and the psycopg driver.

## Configuration

The root `.env.example` is the backend template. The setup script creates `apps/backend/.env` with a fresh secret.
It does not overwrite an existing `.env`. Run backend commands from `apps/backend`, or use the provided scripts.

| Variable | Purpose / default |
|---|---|
| `APP_ENV` | `development`; `production` validates stricter CORS and secret length |
| `DATABASE_URL` | `sqlite:///./alex.db`; later `postgresql+psycopg://user:password@host/database` |
| `JWT_SECRET` | Required random server secret; generated by setup, never sent to desktop |
| `JWT_EXPIRE_MINUTES` | `60`, range 1–1440 |
| `CORS_ORIGINS` | Explicit JSON array of allowed desktop/dev origins |
| `LLM_PROVIDER` | **`mock`**; `llamacpp` exists for a later explicitly configured stage |
| `LLM_BASE_URL` | Backend-only inference server root or `/v1` URL |
| `LLM_MODEL` | `orcarouter-qwen38-27b-q5km` |
| `LLM_API_KEY` | Optional backend-only inference key; leave empty for the mock |
| `MOCK_DELAY` | Optional seconds per 7-character mock chunk, default `0.035` |

Desktop has just one optional public build variable: `VITE_BACKEND_URL` (default `http://127.0.0.1:8000`).
`apps/desktop/.env.example` documents it. Settings can override the address at runtime.
HTTP is allowed only on loopback; remote backend addresses require HTTPS. Changing the backend logs the user out.
UI preferences and per-account drafts are persisted in localStorage. JWTs live in memory; app restart requires login.

**Do not put any RunPod keys, JWT secrets, database credentials or inference credentials in desktop env variables.**

## Tests

```powershell
cd apps\backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests alembic
.\.venv\Scripts\python.exe -m alembic check
cd ..\desktop
npm test
npm run build
npx playwright install chromium
npm run test:e2e
npm audit
```

Playwright starts its own backend on `8001`, UI on `1421`, and a temporary SQLite database.
It never uses the developer's chat database. Keep those test ports free.
Unit tests also use a temporary database. `requirements.lock`, `package-lock.json`, and `Cargo.lock` record resolved dependencies.

A Windows GitHub Actions template is included at `docs/ci-windows.yml`.
It is not activated: the current GitHub credential lacks the `workflow` scope required to push workflow files.
Once that permission is available, place the template at `.github/workflows/ci.yml`.

## Scope and next stage

Run the backend with **one worker**: chat recovery assumes a single streaming worker. Compute operations use a database lease plus committed intents;
multiple users share one tracked compute session. Before horizontal scaling, add distributed streaming-job ownership, production deployment,
account recovery, token revocation, observability, backups and code signing.
PostgreSQL is supported by design and migrations; live PostgreSQL execution is a separate validation step.

RunPod provisioning is implemented through official REST v2; the runtime has no MCP dependency. Set backend `LLM_PROVIDER=llamacpp` to use real inference; `mock` remains available for development.
Memory, RAG, web search, browser agents, terminal tools, coding agent, LoRA and fine-tuning are outside this stage.

See [architecture and API](docs/architecture.md), [security and deployment](docs/security.md), and [verification report](docs/verification.md).

## Version 0.4.0 — local Presence and personal context

Adds WebSocket Presence, profile/custom instructions, projects, curated long-term memory and backend ContextBuilder. This stage uses MockLLMProvider only; no paid RunPod E2E has been performed.

- [Presence architecture and authentication](docs/presence.md)
- [Memory and projects](docs/memory.md)
- [Context selection and budgets](docs/context-builder.md)
- [0.4.0 verification report](docs/verification-0.4.md)

Run `alembic upgrade head` before starting this version. Use a single backend worker. Keep `LLM_PROVIDER=mock` for this stage and `COMPUTE_BACKGROUND_ENABLED=false`; local offline launches also override `RUNPOD_API_KEY` to an empty process value so compute recovery cannot contact RunPod. Stored credentials need not be displayed or removed. The existing real provider/controller remains available for a future explicitly authorized E2E.
