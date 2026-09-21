# Installer and data layout (0.9.3)

This is upgrade *preparation*, not an updater.

## Immutable binaries (replaced by installer)

Typical current-user install:

`%LOCALAPPDATA%\Programs\Alex LLM\`

NSIS current-user default would have been `%LOCALAPPDATA%\Alex LLM\`, which is the data root. Installer hooks force `$LOCALAPPDATA\Programs\${PRODUCTNAME}` so binaries never overwrite user DB/JWT.

| Artifact | Role |
|---|---|
| `Alex LLM.exe` | Tauri Desktop + native host IPC |
| `alex-backend/alex-backend.exe` + `_internal/` | Packaged FastAPI sidecar |
| `alex-host-loop.exe` | Headless native host (same modules as Desktop) |
| bundled WebView frontend | UI |

Reinstall/upgrade replaces these files. They must not hold user state.

## Persistent user data (never in Program Files)

`%LOCALAPPDATA%\Alex LLM\`

| Path | Contents |
|---|---|
| `data/alex.db` (+ WAL) | SQLite; Alembic on sidecar start |
| `runtime/jwt.secret` | Local JWT, generated once |
| `runtime/shutdown.token` | Quit hook for managed compute |
| `runtime/backend.lock` | Owned PID/port (ephemeral) |
| `documents/` | User files |
| `models/embeddings/` | FastEmbed cache after prepare |
| `logs/backend.log` | Rotating sidecar log |
| `device.json` / Credential Manager | Device pairing |

Uninstall of the application folder must not delete this tree. There is no backup-on-upgrade yet.

## Future sidecar replace

1. Installer writes new `alex-backend/` next to Desktop.
2. Next launch: Desktop starts the new sidecar.
3. Sidecar runs `alembic upgrade head` against `data/alex.db`.
4. Failed migration → `MIGRATION_FAILED`; DB file is left in place.

Do not copy the DB into the install directory. Do not store JWT under the install directory.
