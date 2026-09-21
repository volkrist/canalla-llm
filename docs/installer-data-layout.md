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
| `backups/<id>/` | Verified local backups (manifest + DB snapshot + documents); see [backup-format.md](backup-format.md) |
| `runtime/jwt.secret` | Local JWT, generated once |
| `runtime/shutdown.token` | Quit hook for managed compute |
| `runtime/backend.lock` | Owned PID/port (ephemeral) |
| `runtime/migration.json` | Last migration result (from → to, backup id) |
| `runtime/backup-restore.json` | Result of the last one-shot restore |
| `documents/` | User files |
| `models/embeddings/` | FastEmbed cache after prepare |
| `logs/backend.log` | Rotating sidecar log |
| `device.json` / Credential Manager | Device pairing |

Uninstall of the application folder must not delete this tree. NSIS still has no uninstall
hook that touches it, `deleteAppDataOnUninstall` is not set, and no Credential Manager entry
is removed by uninstalling: **uninstalling removes binaries only**. A future "remove my data"
option would be a separate, explicit user choice.

## Backup on upgrade

Before a migration that has work to protect, the sidecar creates and verifies a `pre_upgrade`
backup under `backups/`; if that fails the upgrade is refused (exit 15) and the database is
left untouched. Restoring is a Desktop operation (it stops its owned backend first) and never
runs against a live server. Details: [upgrade-backup-design.md](upgrade-backup-design.md).

## Future sidecar replace

1. Installer writes new `alex-backend/` next to Desktop.
2. Next launch: Desktop starts the new sidecar.
3. Sidecar creates a verified pre-upgrade backup when a migration is pending, then runs
   `alembic upgrade head` against `data/alex.db`.
4. Failed migration → `MIGRATION_FAILED`; DB file is left in place. Refused backup →
   `PRE_UPGRADE_BACKUP_FAILED`; DB file is left in place and nothing is migrated.

Do not copy the DB into the install directory. Do not store JWT under the install directory.
