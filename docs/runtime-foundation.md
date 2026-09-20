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

## Packaging (not implemented here)

How FastAPI will ship with Tauri is still open. Options vs this repo:

| Approach | Fit | Risk |
|---|---|---|
| Keep venv + discover `apps/backend/.venv` | Current developer path | Not an installer |
| PyInstaller/Nuitka on `app.runtime_entry` | One exe sidecar, Tauri `externalBin` | Size (FastEmbed/native), Playwright not bundled here |
| Embedded CPython | Updateable runtime | Complex on Windows |
| Full embed in `alex-llm.exe` | Single file | Rebuild cost, antivirus |

Recommendation: **sidecar from `runtime_entry`** after this slice is proven. Do not migrate packaging before Desktop ownership works on a developer machine. FastEmbed ONNX and future Browser deps dominate size; GPU weights stay on the Network Volume.

Next slice after this PASSed: on-demand RunPod lifecycle — see [on-demand-ai.md](on-demand-ai.md).

## Quit vs window

Full application Quit (`RunEvent::Exit` / `ExitRequested`) stops the owned local backend.
On-demand GPU adds a managed-Pod shutdown on that same Quit path.
Ordinary in-app navigation does not stop the backend or GPU.

## Not in this slice

Installer UX, session restore, five-chip status, LoRA, WM-07, CD-08 harness.
