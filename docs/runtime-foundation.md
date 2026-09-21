# Runtime foundation (0.9.3 product slice)

Desktop owns the local FastAPI process. This is not a version bump and not 1.0.

## Before

`scripts/start-backend.ps1` → Alembic → uvicorn `127.0.0.1:8000`.  
Desktop did not start, stop, or supervise that process. Persistent SQLite lived at `apps/backend/alex.db` (cwd-relative). JWT came from `.env`.

## After this slice

1. Tauri `ensure_backend` on launch.
2. Probe `127.0.0.1:8000`…`8019`.
   - Healthy Alex (`product=alex-llm` or existing `/health`) → reconnect.
   - Unrelated occupant → skip that port; never kill by name.
3. If no Alex: spawn `python -m app.runtime_entry` (Job Object when assign succeeds; Child kill on quit always). Never kill the child just because Job assign failed.
4. `runtime_entry` ensures `%LOCALAPPDATA%\Alex LLM\` layout, JWT file, Alembic, then uvicorn.
5. Close Desktop → owned child stops. External developer backend is left running.
6. UI states: Starting / Ready / Error only.

Data root:

```text
%LOCALAPPDATA%\Alex LLM\
  data\alex.db
  documents\
  models\embeddings\
  logs\backend.log
  runtime\jwt.secret
  runtime\backend.lock
```

Developers can still run `scripts/start-backend.ps1`. Desktop then connects as `external` and does not claim ownership.

## Config / secrets

| Setting | Class |
|---|---|
| `JWT_SECRET` | generated into `runtime/jwt.secret` (never logged, never sent to the UI) |
| `DATABASE_URL` | derived from data root when Desktop launches |
| `ALEX_LLM_DATA_DIR` | Desktop / tests overlay |
| RunPod / TinyFish keys | still `.env` / OS env (not this slice) |
| Backend URL | automatic; Advanced settings remain |

## Packaging

PyInstaller **onedir** sidecar `alex-backend.exe` + `_internal/`, bundled as a Tauri resource. Desktop supervisor still owns the PID/Job Object. Production packaged builds never search `python.exe`. `tauri dev` may still use `apps/backend/.venv`. See [backend-sidecar-decision.md](backend-sidecar-decision.md) and [installer-data-layout.md](installer-data-layout.md).

GPU weights stay on Network Volume `uwgeaie5b0`. Embedding weights stay in `%LOCALAPPDATA%\Alex LLM\models\embeddings\`.

## Quit vs window

Full application Quit (`RunEvent::Exit` / `ExitRequested`) stops the owned local backend.
On-demand GPU adds a managed-Pod shutdown on that same Quit path.
Ordinary in-app navigation does not stop the backend or GPU.

## Not in this slice

Installer UX, session restore, five-chip status, LoRA, WM-07, CD-08 harness.
