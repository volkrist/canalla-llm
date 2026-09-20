# Current product map

**Source of truth:** this worktree, branch `planning/1.0-product-readiness`,
base `origin/main` = **0.9.3** (`80c53ade3756ca8fd10471295f9b29bbe89d17f0`).

Legend:

- **CURRENT VERIFIED FROM REPO** — observed in this tree.
- **RECHECK AFTER 0.9.3** — may change when Weak-Model Reliability lands.
- **CLAIM / NOT VERIFIED HERE** — README or docs claim; this audit did not re-run live GPU/TinyFish.

This file is a factual inventory, not a wish list. Target behavior lives in
the sibling specs.

Production model (configured, not bundled):
`orcarouter/Qwen3.8-27B-Uncensored` via alias `orcarouter-qwen38-27b-q5km`,
Q5_K_M, llama.cpp on a managed RunPod volume.

Architectural intent already in the repo, incomplete as a daily product:

```text
thin model  +  thick deterministic controller
(tools, policy, router, task machine, compute lease, host)
```

`AUTONOMY` and `RESEARCH_DEPTH` **do not exist** as settings, env vars, or
UI. Keep it that way. Effective autonomy today is still weaker than the 1.0
rule `HIGH` because Computer defaults to **Ask**.

---

## Desktop app

**CURRENT VERIFIED FROM REPO**

- Tauri 2 + React + TypeScript, product name `Alex LLM`, version `0.9.3`.
- Entry: `apps/desktop/src/main.tsx` → `App.tsx`.
- No onboarding, no first-run flag, no tutorial.
- Settings from `localStorage` key `alex-settings`.
- JWT lives **in React memory only**. Restart → login again.
- Health poll `/health` every 10s (public). LLM poll `/llm/status` every 3s when logged in.
- Workspace auto-pairs the native host every 8s **only in the Tauri window**.
  Browser Vite preview (`npm run dev`) reports Device Offline / unpaired.
- UI language is Russian; language control is disabled.

Shell after login: sidebar chats, message list, composer, compute chip,
personal/memory/projects, files/RAG, tool activity, task panel, usage.

**Not present:** updater, diagnostics page, support-bundle export, MSI,
bundled backend, password reset, SSO, English UI, AUTONOMY selector,
RESEARCH_DEPTH selector.

## Backend

**CURRENT VERIFIED FROM REPO**

- FastAPI + SQLAlchemy + Alembic, `apps/backend/pyproject.toml` version `0.9.3`.
- OpenAPI `version=` **`0.9.3`**.
- Start: `scripts/start-backend.ps1` → `alembic upgrade head` then
  `uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`.
- **One worker is required** for stream ownership / crash recovery.
- Desktop does **not** start, stop, or supervise this process.
- Config: `apps/backend/.env` from root `.env.example` via `setup-backend.ps1`.
- Default `LLM_PROVIDER=mock`. Real inference: `llamacpp` + RunPod gateway.

Lifespan: document/tool reconcile, Presence monitor, `RunPodController.recover()`,
optional compute tick.

## RunPod / llama.cpp

**CURRENT VERIFIED FROM REPO**

Controller: `apps/backend/app/compute/controller.py`.
User guide: `docs/runpod-controller.md`.

| Capability | Status |
|---|---|
| Manual search + auto_connect start | Yes — UI «Найти GPU и подключиться» |
| Start merely because the user sent a chat | **No** |
| Idle stop after ready, no active generation | Yes — default 10 min; 5/10/15/30/Never |
| Session budget stop (even mid-generation) | Yes |
| Price-ceiling terminate | Yes |
| GPU fallback search on placement reject | Yes, if search armed |
| Adopt existing volume-matching pod | Yes, as `external_compute` |
| Auto idle/budget stop on external pod | **No** |
| Multiple pods on volume | `multiple_compute` — admin, no auto-kill |
| Network Volume delete | **Never implemented** (correct) |
| Idle/budget enforcement if backend process is down | **No** |
| Model alias readiness | Yes — exact `LLM_MODEL` on `/v1/models` |

Default constraints: volume `uwgeaie5b0`, DC `US-TX-3`, min 48 GB VRAM,
exact GPU `NVIDIA L40S`, `$1.20/h` ceiling, `$3.00` session budget.

`ALLOW_USER_COMPUTE_START=false` by default: ordinary registered users
**cannot** start GPU. First-run owner is not automatically an admin.

## Auth / session

**CURRENT VERIFIED FROM REPO**

- Register / login, Argon2id, JWT HS256 (`iss=alex-llm`, `aud=alex-desktop`).
- `JWT_EXPIRE_MINUTES` default 60 (1–1440).
- No email verification, reset, refresh, or revocation list.
- Logout drops the in-memory token; a copied token stays valid until expiry.
- `ADMIN_EMAILS` is the only admin grant path.

## Chat / history

**CURRENT VERIFIED FROM REPO**

- Private chats, ownership on every route, pin/rename/search/export.
- SSE `meta` / `delta` / `task` / `task_status` / `done` / `error`.
- Stop persists partial assistant text.
- Edit / resend / regenerate with linear truncation.
- Local drafts in `localStorage` keyed by backend+user+chat.
- Context ≈ last 100 messages / 64k characters (character bound, not tokenizer).

**RECHECK AFTER 0.9.3:** planner loop, grounding, progress events, continue-task.

## Memory / projects / RAG

**CURRENT VERIFIED FROM REPO**

| Concept | What it is today | User can confuse with |
|---|---|---|
| **Memory** | Curated long-lived facts/prefs (`personal.py`). Manual CRUD. Auto-capture **off** («пока недоступен»). | Project notes, RAG snippets |
| **Project** | Workspace-scoped container; chats and memories may attach | Memory, files |
| **RAG / Files** | Uploaded PDF/DOCX/TXT/MD, CPU `multilingual-e5-small`, owner/project chunks | Memory |
| **Task state** | Persistent `LocalTask` plan/journal/checkpoints | Chat history |

Missing embeddings: chat still works; retrieval empty + warning.
Prepare embeddings is an explicit UI/CLI action, not first-run.

## Web / TinyFish

**CURRENT VERIFIED FROM REPO**

- Composer: Web Off / Auto / On. Default Auto.
- «Найти в интернете» forces this turn On (Auto-only control).
- Server router injects Search/Fetch; Agent/Browser default Auto, hidden
  unless the deterministic router selects them.
- Search/Fetch treated as free (`TINYFISH_SEARCH_FETCH_FREE=true`).
- Agent: paid, READ_ONLY only; side-effect goals blocked **before** provider.
- Browser: Alex-controlled cloud Chromium, typed actions, confirm writes.
- Vault/Profiles off.
- `.onion` never goes to TinyFish.

Paid budgets default `$1.00/task` (hard `$2.00`), Agent 2×20 steps,
Browser 2 sessions / 10 minutes.

**RECHECK AFTER 0.9.3:** `classify.py` and when Agent/Browser are injected.

## Tor

**CURRENT VERIFIED FROM REPO**

- Independent Off / Auto / On. Default Auto.
- SOCKS5h loopback (`127.0.0.1:9050`). No Tor→Direct fallback.
- `tor_search` / `tor_fetch` / `tor_browser` (Marionette + Job Object).
- Tor Browser is **user-installed**, discovered on disk or `TOR_BROWSER_EXE`.
- `TorRoutedBrowserProvider` is **not** implemented.

User still sees SOCKS/Marionette only in docs and some error strings
(`tor_browser_not_ready`), not as a setup wizard.

## Local Computer

**CURRENT VERIFIED FROM REPO**

- Pairing: Tauri host, device credential in Windows Credential Manager
  (DPAPI file fallback under `%LOCALAPPDATA%\Alex LLM`).
- Composer Computer: Off / **Ask** / Trusted Workspace. Default **Ask**.
- Ask confirms even READ. That is **not** HIGH autonomy.
- Trusted: READ and in-root NORMAL_CHANGE auto; SENSITIVE/CRITICAL always confirm.
- Job Objects, sanitized child env, secret-path denylist, digest-bound allow-once.
- CRITICAL host actions often `critical_not_armed` unless `ALEX_EXECUTE_CRITICAL=1`.

## Coding / autonomous tasks

**CURRENT VERIFIED FROM REPO**

- Same tool stack (no second agent). `LocalTaskController` + plan/verify.
- Pause / Resume / Stop; same `task_id` after desktop/backend restart.
- WRITE tasks queue `WAITING_WORKSPACE` (FIFO).
- Git commit/push off unless asked or settings `auto_commit` / `allow_push`.
- Push SENSITIVE; force-push / hard reset CRITICAL and host-disarmed.
- Task does **not** start a GPU; uses current managed session or `WAITING_LLM`.

**CLAIM / NOT VERIFIED HERE:** README 0.9.0–0.9.2 GPU proofs on L40S.
**RECHECK AFTER 0.9.3:** grounding, facts, intent, continue_task, progress,
false “no filesystem access” claims from 0.9.1.

## Recovery

**CURRENT VERIFIED FROM REPO**

- Unfinished generation → `interrupted` / message `error`.
- In-flight non-waiting tasks → `INTERRUPTED`; waiting/paused kept.
- Resume skips completed local action digests; no token-stream replay.
- TinyFish leftover sessions cancelled at reconcile.
- Compute recover + tick if API key set.
- Hard crash mid-stream: in-flight tokens not guaranteed durable.

User-facing copy still surfaces raw statuses (`INTERRUPTED`, `WAITING_LLM`)
more than “Задача восстановлена”.

## Budgets / confirmations

See `security-confirmations.md`. Defaults (task): 30 min / 40 tools / 20 files
(hard 120 / 100 / 100). Confirmation window 5 minutes, digest-bound, one-time.

## Installer

See `installer.md`. NSIS current-user only, unsigned, **backend not bundled**.

## Settings surface

See the classification in `README.md` and `runtime-lifecycle.md`.
Critical: **no** autonomy/depth knobs exist today — do not add them.

## Logs / errors

- Uvicorn stdout; no log directory; no support bundle.
- Stable string codes for LLM / RunPod / tools; desktop maps many in `tools.ts`.
- Generic fallback still exists: «Операция не выполнена» / «Something»-class copy
  when a code is unmapped.

## Data locations (no secret values)

| Data | Location |
|---|---|
| SQLite | `DATABASE_URL` default `apps/backend/alex.db` (cwd-relative) |
| Documents | `.data/documents` under backend cwd |
| Embeddings | `%LOCALAPPDATA%\Alex LLM\models\embeddings\...` |
| Device cred | Credential Manager / DPAPI file |
| Settings/drafts | desktop `localStorage` |
| Task state | SQLite `local_tasks`, `task_events` |
| Logs | process stdout |

Restart survives: SQLite, embeddings, device pairing, localStorage settings.
App restart does **not** survive: JWT session.
Uninstall: **undefined** (NSIS current-user; no documented keep/delete policy).

## What already works as product pieces

Alex already has the **capabilities** of a personal agent: chat, memory,
projects, files, web, Tor, local computer, coding, autonomous tasks,
confirmations, RunPod cost controls.

What it does **not** yet have is a single **runtime product**: one install,
one launch, one status model, on-demand GPU, and zero terminal for a normal day.
