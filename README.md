# Canalla LLM

Windows desktop **0.9.3**: **Tauri 2 + React + TypeScript** and **FastAPI + SQLAlchemy**.
The desktop connects only to the backend. The default LLM is a deterministic **mock**, so no GPU, RunPod account or inference server is needed.

## Naming

**Canalla LLM** is the user-facing product name: window title, installer, Start menu,
About, chat labels, onboarding and the assistant's own name in the system prompt.

The following keep the older `Alex LLM` spelling on purpose, because they are storage,
identity or protocol names and renaming them would strand user data or lose an
enrollment:

| Kept | Why |
|---|---|
| `%LOCALAPPDATA%\Alex LLM` (data root) | user data, documents and the database live there |
| Windows Credential Manager targets `Alex LLM/session/{id}`, `Alex LLM/provider/runpod`, `Alex LLM/gateway/installation`, `Alex LLM/device-credential` | session, provider key and Gateway enrollment would be orphaned |
| bundle identifier `com.alexllm.desktop`, product id `alex-llm`, `alex-llm-desktop` | installer identity, gateway protocol and packaging |
| `alex-host-loop.exe`, `alex-backend.exe` | paired-host and sidecar binaries |
| the shared Gateway's internal names: product id `alex-llm-gateway`, unit `alex-gateway.service`, `/opt/alex-gateway`, endpoint `gateway.12testers.store` | the cloud service is **Canalla Cloud** in user-facing text; renaming the internals would break the deployment |
| `alex-chats.md` export filename, `ALEX_*` environment variables | file/CLI contract |

The installer still installs per user into `%LOCALAPPDATA%\Programs\<product name>`; after
the rename that is `Programs\Canalla LLM`. An older `Programs\Alex LLM` folder from a
previous build is not removed automatically — uninstall it from Apps & features once, or
ignore it; it only holds binaries (never user data).

The assistant has no separate name: chat labels, confirmations, status messages and the
system prompt all say Canalla LLM. The one remaining bare `Alex` in the UI is the
display-name placeholder in the owner form, which is an example of the user's own name.

## Context meter

The composer shows a small ring with the tokens the next message will use
(`Context 12 480 / 32 768 · 38%`) and a breakdown of system, memory, documents, history
and draft. The window comes from the backend setting `LLM_CONTEXT_WINDOW` (default
32768, i.e. llama.cpp `--ctx-size`), never from the frontend:
[Context usage meter](docs/context-usage.md).

## Compute Preferences

Compute limits are each user's own money policy, not product caps. A new user starts at
**$0.52/hour** and **$3.00/session** (48 GB VRAM floor, 10-minute idle stop, automatic
cheapest-compatible selection, retry search) and may raise or lower both for themselves in
either provider mode — direct or shared; only the technical bounds ($100/hour,
$1000/session) are enforced, and a malformed policy is rejected instead of silently
replaced. Automatic mode always prefers the cheapest compatible GPU inside the user's own
maximum, so raising the limit never buys a more expensive card. The policy is stored per
account in the local database and survives logout, restart and backup/restore; starting and
stopping compute stays the machine owner's action:
[Compute Preferences](docs/compute-preferences.md).

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

- `apps/desktop/src-tauri/target/release/alex-llm.exe` — native portable executable, displayed as Canalla LLM.
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
- 0.9 autonomous tasks: persistent plan, Pause/Resume/Stop, checkpoints, budgets, workspace lock, and verification before Completed. Built on the existing tool stack. [Autonomous tasks](docs/autonomous-tasks.md). Real OrcaRouter proof: disposable two-bug Python project, native host, natural prompt, Pause/Resume, tests FAIL→PASS.

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
| `LLM_CONTEXT_WINDOW` | `32768`; the served context window the composer's context meter reports against (must match llama.cpp `--ctx-size`) |
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
Local Computer is a paired host with confirmation and Job Objects, not an unrestricted shell. LoRA and fine-tuning remain outside this stage.

See [architecture and API](docs/architecture.md), [security and deployment](docs/security.md), and [verification report](docs/verification.md).

## Version 0.4.0 — local Presence and personal context

Adds WebSocket Presence, profile/custom instructions, projects, curated long-term memory and backend ContextBuilder. This stage uses MockLLMProvider only; no paid RunPod E2E has been performed.

- [Presence architecture and authentication](docs/presence.md)
- [Memory and projects](docs/memory.md)
- [Context selection and budgets](docs/context-builder.md)
- [0.4.0 verification report](docs/verification-0.4.md)

Run `alembic upgrade head` before starting this version. Use a single backend worker. Keep `LLM_PROVIDER=mock` for this stage and `COMPUTE_BACKGROUND_ENABLED=false`; local offline launches also override `RUNPOD_API_KEY` to an empty process value so compute recovery cannot contact RunPod. Stored credentials need not be displayed or removed. The existing real provider/controller remains available for a future explicitly authorized E2E.

## Version 0.5.0 — local Files and RAG

Upload PDF/DOCX/TXT/MD, index with real multilingual CPU embeddings, retrieve owner/project-scoped chunks and inspect saved sources under answers. No GPU is required for indexing. [Files](docs/files.md) · [RAG setup and limits](docs/rag.md).

After installing backend dependencies, run `python -m alembic upgrade head` and explicitly prepare the embedding model with `python -m app.documents.embedding`. Runtime does not download models. `.data/` stores local documents and is gitignored; 0.6 moves embedding installation to the application data directory. Missing embeddings do not prevent ordinary chats.

Generation metadata now retains its actual context snapshot and nullable TTFT/cancellation telemetry. Compute preferences remain persisted and editable through the UI; the existing session's snapshot is shown separately. This release was tested locally with MockLLMProvider, not a paid OrcaRouter RAG session.

## Version 0.6.0 — managed embeddings and Web & Tools

Files and Settings now prepare the pinned CPU embedding model with real byte progress, cancellation, resumable downloads and verified atomic activation. Chat remains usable without embeddings. Windows model data lives in `%LOCALAPPDATA%/Alex LLM/models/embeddings`; `ALEX_LLM_DATA_DIR` overrides the common data root. No model is bundled in Git or the installer.

Provider-independent Tools add Web Off/Auto/On, Search/Fetch, persisted W sources, separate D document sources, bounded model tool loops, ownership checks and per-action approval. Set `TINYFISH_API_KEY` only in backend configuration. Production uses REST/httpx; no TinyFish CLI subprocess. TinyFish Search/Fetch are free. TinyFish Browser is Alex-controlled cloud Chromium (typed Playwright/CDP). TinyFish Agent is paid read-only multi-page research; side-effect goals are blocked before the provider call because the current API has no pre-action approval. Agent/Browser modes default to Auto. Vault/Profiles stay off.

**Agent limitation:** the current TinyFish Agent API has no enforceable pre-action intercept. Alex therefore allows Agent only for classified READ_ONLY goals and blocks submit/buy/login/upload goals before the paid call. Prompt instructions are not a security boundary. Search/Fetch stay free. Browser stays Alex-controlled with typed actions and confirmation for side effects.

Run `alembic upgrade head` (0007), then one backend worker. [Embedding lifecycle](docs/embedding-model-manager.md) · [Tools](docs/tools.md) · [Web](docs/web.md) · [TinyFish contracts and limitations](docs/tinyfish.md).

## Version 0.7.0 — WebRouter, Tor, Local Computer

Web Off/Auto/On stay independent of Tor and Local Computer. **Найти в интернете** is Auto-only. When Web is On and the planner skips `web_search`, the server injects one Search (then Fetch 1–3 canonical URLs, `ttl=0` when fresh) and audits `origin=server_policy`. TinyFish Agent/Browser stay Auto by default: the server injects them only for JS/browser or complex read-only multi-page tasks. Tor never uses TinyFish.

Tor SOCKS5h is a separate transport: real `.onion` fetch, configured Tor-search providers, curated official mapping only for provenance. Local Computer is a paired Tauri host with a random device credential in Windows Credential Manager (DPAPI file fallback), Job Objects, sanitized child environments, atomic writes and a secret-path denylist.

Run `alembic upgrade head` (0008). Keep `LLM_PROVIDER=mock`. [Web](docs/web.md) · [Tor](docs/tor.md) · [Local Computer](docs/local-computer.md) · [Tools](docs/tools.md).

## Version 0.7.1 — Local Computer risk policy

Trusted Workspace is a confirmation reduction for safe NORMAL_CHANGE inside roots, not a filesystem jail. Risk levels are READ / NORMAL_CHANGE / SENSITIVE / CRITICAL. SENSITIVE/CRITICAL show an explanation and bind Allow-once to an immutable digest. Delete, registry, services and install stay as typed tools. Device pairing stays pseudonymous (`Windows device`). Forget this device revokes the OS credential. Network-sensitive runs show Direct or Tor with no silent fallback.

## Version 0.8.0 — Coding Agent foundation

Local Computer is usable for daily coding: typed file/process/git/service/registry tools, `patch_file` with conflict hashes, LocalTaskController ceilings, CodingWorkspace on the existing tool stack, CredentialBroker, rotate device credential, and risk-split confirmation UX. Git push is SENSITIVE; force-push and `git reset --hard` are CRITICAL and fail-closed on the host.

Run `alembic upgrade head` (0009). Keep `LLM_PROVIDER=mock`. Do not start GPU/RunPod or TinyFish Agent/Browser.

[Coding Agent](docs/coding-agent.md) · [Device security](docs/device-security.md) · [Local risk policy](docs/local-risk-policy.md) · [Local Computer](docs/local-computer.md)

## Version 0.8.1 — 0.8.x verification patch

Closes local 0.8.x gaps that did not need a second GPU pod: CodingWorkspace prefers a git project among workspace roots, native host digest mismatch returns 409 with live device headers, RAG `multilingual-e5-small` reaches `model_ready=true` and indexes, and the 0.8 harness waits for catalog stock without treating an old session cost as the current budget.

**Not fully verified:** model-driven Coding Agent, model-driven Tor, Stop generation, RAG-with-OrcaRouter, and Combined Web+Coding were not retested on GPU in this patch. ONE managed Pod was consumed (`qx9xehgintpwyf`, ~60s, ~$0.018) after US-TX-3 L40S stock sat at NONE, then the harness aborted during `starting_pod`. A second pod was not started.

Keep `LLM_PROVIDER=mock` unless you intentionally start compute. TinyFish Agent/Browser stay unused.

## Version 0.8.2 — 0.8 line, not fully verified

Closes four of the five remaining REAL E2E items on ONE L40S pod (`plbskald4189bw`, US-TX-3, $1.09/h, ~1967s, ~$0.596, volume `uwgeaie5b0` preserved):

- Coding Agent: Alex fixed a disposable pytest-failing project on native Windows (`divide` `*` → `/`). Cursor did not touch the source.
- RAG → OrcaRouter: D1 excerpt, answer `silver-lantern-otter`.
- Stop generation: client abort 3s after first delta, `status=stopped`, partial persisted, no ReadTimeout.
- Combined Web + Coding: one task, TinyFish Search/Fetch only, W sources, local pytest FAIL→PASS.

**Limitation:** model-driven Tor Search/Fetch did **not** REAL PASS. Direct tor_search/fetch remain REAL PASS from 0.8.1. This run's Tor planner burned `max_tokens=1200` on Qwen3 thinking and emitted no `tool_calls`; the follow-up stream stored 0 visible tokens. `enable_thinking=false` is now sent to llama.cpp. GPU retest of model-driven Tor is still required. Do not call 0.8.2 fully verified.

Keep `LLM_PROVIDER=mock` unless you intentionally start compute. TinyFish Agent/Browser stay unused.

## Version 0.8.3 — Automatic Tor research REAL VERIFIED

A normal user prompt such as «Через Tor найди …» now runs automatic Tor research on the existing tool stack: Tor Off/Auto/On, intent router, model-first `tor_search`/`tor_fetch`, honest `origin=server_policy` fallback, link extraction, follow with loop protection, T sources, and no Tor→Direct fallback.

GPU retest on ONE L40S (`o2ossjuc01e6jx`, US-TX-3, $1.09/h, 203s, ~$0.061, volume `uwgeaie5b0` preserved): OrcaRouter itself called `tor_search` (`origin=model`), completed `tor_fetch` including a real `.onion`, followed further onion pages on continue, wrote T sources, and returned a visible sourced answer without treating reachable as official. TinyFish Agent/Browser stayed 0.

Run `alembic upgrade head` (0010). Keep `LLM_PROVIDER=mock` unless you intentionally start compute.

[Tor](docs/tor.md) · [Verification](docs/verification.md)

## Version 0.9.3 — Weak-Model Reliability & Grounded Execution

Production is still OrcaRouter/Qwen 27B Q5_K_M. Autonomy stays **HIGH** and research stays **DEEP** with no Low/Normal/High or Fast/Normal/Deep selectors. Reliability moves into the controller: verified facts from successful tools, final answers grounded (or repaired once, then a deterministic fallback), obvious Local Computer intents routed with `origin=server_policy`, no-progress / duplicate suppression, TaskScope + Alex-owned scratch, and WRITE queue auto-resume without a new user message.

GPU: create/read/hash/search answers **REAL PASS** on L40S `j2qzj29p7d8zlq`. TinyFish Browser inject **REAL PASS** on a sequential later L40S after GPU=0 (`aoy0ocnnk6okur`, `web_browser` `origin=server_policy`). Native host process start/stop and queue auto-resume **REAL PASS**. **RUNNING GPU FINAL = 0**. Volume `uwgeaie5b0` preserved.

Run `alembic upgrade head` (0012). Keep `LLM_PROVIDER=mock` unless you intentionally start compute.

[Autonomous tasks](docs/autonomous-tasks.md) · [TinyFish](docs/tinyfish.md) · [Verification](docs/verification.md)

## Version 0.9.2 — TinyFish Agent & Browser Integration

Existing TinyFish Search/Fetch stay free. TinyFish Browser is Alex-controlled cloud Chromium (Playwright over CDP, typed actions, no model JS/raw CDP). TinyFish Agent is a paid read-only multi-page workflow owned by TinyFish. The current Agent API has no pre-action approval, so side-effect goals are blocked before the run. Prompt text is not a security boundary. Agent and Browser are Off/Auto/On (default Auto). Auto hides paid tools; the server injects them before the planner when the deterministic router selects them.

Paid budgets are app-side: default $1.00/task (hard $2.00), Agent 2 runs / 20 steps, Browser 2 sessions / 10 minutes. Rates are configurable (`$0.016`/step, `$0.002`/minute) because provider pricing can change. Vault/profiles are off. `.onion` never goes to TinyFish.

Direct live proof: Search/Fetch REAL PASS; Browser REAL PASS (python.org, session closed); Agent REAL READ_ONLY PASS (3 steps). GPU on ONE later L40S after Local Computer GPU=0 (`31bv7sbvpfnkwi`, US-TX-3, $1.09/h, 553s, ~$0.167, volume `uwgeaie5b0` preserved): OrcaRouter selected `web_agent` `origin=server_policy` **REAL PASS**. Explicit Browser on that pod used Search/Fetch only (**FAIL**); 0.9.2 injects Browser before the planner (local tests, not a second GPU pod). **RUNNING GPU FINAL = 0**. Auto-reload left OFF.

Keep `LLM_PROVIDER=mock` unless you intentionally start compute.

[TinyFish](docs/tinyfish.md) · [Web](docs/web.md) · [Tools](docs/tools.md) · [Autonomous tasks](docs/autonomous-tasks.md) · [Verification](docs/verification.md)

## Version 0.9.1 — Local Computer & Task Reliability

Workspace WRITE tasks queue as `WAITING_WORKSPACE` (FIFO, persisted, stale-lock cleanup) instead of failing `workspace_busy`. Desktop/backend restart continues the same `task_id` from checkpoint (task continuation, not token-stream continuation). Pause drops a partial planner buffer and shows `Paused while preparing next action.` Git commit/push stay off unless the user asks or enables the matching setting; push is SENSITIVE and never `--force`. Loopback form submit is SENSITIVE; fake checkout is CRITICAL; email/message contracts exist without a configured provider.

GPU proof used ONE L40S after a stopped first attempt (`a7ea8t33um7wh8`, US-TX-3, $1.09/h, 1170s, ~$0.354, volume `uwgeaie5b0` preserved). Real OrcaRouter (`llamacpp`, Mock=false) chose local tools (`origin=model`, 100 tool actions): Desktop via Known Folder API, created `Desktop\Alex-LLM-E2E\hello.txt`, edited/copied/moved, installed `jqlang.jq` `jq-1.8.2` after SENSITIVE confirmation (user-scope winget, no UAC bypass). Pause UI had no raw tool fragment. **RUNNING GPU FINAL = 0**.

Limitation: the final chat turn still often claimed “no filesystem access” even after successful tools. Local notes are now labeled as host observations; that prompt fix was not GPU-retested. Git/form/fake-purchase GPU cases stayed queued behind an unfinished WRITE task in the harness. Email provider is not configured. No real purchase or real email.

Run `alembic upgrade head` (0012). Keep `LLM_PROVIDER=mock` unless you intentionally start compute. TinyFish adapters were already in this tree; paid Agent/Browser verification is 0.9.2.

[Autonomous tasks](docs/autonomous-tasks.md) · [Verification](docs/verification.md) · [Device security](docs/device-security.md)

## Version 0.9.0 — Autonomous Task Agent REAL VERIFIED

A large natural request becomes one persistent task: plan, inspect, optional research, execute, verify, diagnose, fix, re-verify, then complete. Pause / Resume / Stop, checkpoints, budgets, exclusive workspace WRITE locks, and Sensitive/Critical confirmations stay on the existing tool stack. GPU proof on ONE L40S (`2sgnu3ljhkefpz`, US-TX-3, $1.09/h, 198s, ~$0.060, volume `uwgeaie5b0` preserved): real OrcaRouter, native host, disposable two-bug Python project, natural prompt (no tool/file/bug names), Pause with no new tools then Resume, `patch_file` of `app.py`, tests FAIL→PASS, `COMPLETED`. TinyFish Search/Fetch only; Agent/Browser 0.

Run `alembic upgrade head` (0011). Keep `LLM_PROVIDER=mock` unless you intentionally start compute.

[Autonomous tasks](docs/autonomous-tasks.md) · [Verification](docs/verification.md)

## Version 0.8.6 — automatic Tor Browser fallback REAL VERIFIED

A natural Tor request with a `.onion` URL now fetches that URL first. If HTTP `tor_fetch` only sees a JS shell, production heuristic `needs_browser` selects `TorBrowserProvider` automatically (`origin=server_policy`) without the user saying “use Tor Browser”. GPU proof: real OrcaRouter fetched a temporary Tor v3 JS onion (raw `Loading...`), launched isolated Tor Browser, rendered `ALEX_ONION_JS_RENDERED_OK`, followed L1 to the second onion page, and answered with the rendered markers. A live official Tor Project onion was opened independently. TinyFish Agent/Browser stayed 0.

## Version 0.8.5 — model-driven Tor Browser REAL PASS

Real OrcaRouter (`llamacpp`, alias `orcarouter-qwen38-27b-q5km`) called `tor_browser` itself (`origin=model`): isolated Tor Browser opened `check.torproject.org`, rendered the Congratulations page, followed `L1` to torproject.org, wrote T sources with `transport=tor` / `retrieval=browser` / `rendered=true`, and returned a visible answer. `tor_fetch` stays primary. Automatic HTTP-fetch → browser fallback on a public JS-shell was **not** claimed: official Tor Project pages already had enough HTTP text. Follow-up reused prior T sources and fetched another safe page. TinyFish Agent/Browser stayed 0.

## Version 0.8.4 — Tor Browser automation REAL LOCAL PASS

HTTP `tor_search`/`tor_fetch` remains the fast path. When a page is a JS shell or the user asks for Tor Browser, Alex can start an isolated Tor Browser session (Marionette, Job Object, no personal profile), render the DOM, follow L-ids, and store T sources with `retrieval=browser`. Live Windows proof: Tor Browser 15.0.22 / Firefox 140.15.0, `check.torproject.org` Congratulations via SOCKS 9050, local JS marker `TOR_BROWSER_JS_OK`, fail-closed on a dead SOCKS port, user `tor.exe` survived close. `TorRoutedBrowserProvider` is not implemented and is not this feature. Model-driven GPU fallback is not retested in this patch.

Keep `LLM_PROVIDER=mock` unless you intentionally start compute. TinyFish Agent/Browser stay unused.
