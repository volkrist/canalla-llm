# Upgrade / backup / data preservation — audit before the change

Date: 21 September 2026. Slice: `feat/upgrade-backup-data`, starting from
`main == origin/main == f74625b4b1691ab121ce00f398ee908b20c6de06`.

This is the honest description of how Alex 0.9.3 behaved **before** this slice. Everything
below was read out of the code that ships (the NSIS hooks, the Tauri config, the Rust
supervisor and the backend startup path); nothing is assumed.

## 1. Installer behaviour

| Aspect | Before |
|---|---|
| Installer | Tauri NSIS, `installMode: "currentUser"`, one bundle target |
| Install directory | `%LOCALAPPDATA%\Programs\Alex LLM\` — forced by `nsis/hooks.nsh` (`NSIS_HOOK_PREINSTALL` sets `$INSTDIR`), because the NSIS default would have been `%LOCALAPPDATA%\Alex LLM`, which is the **data root** |
| Files replaced on reinstall | the main binary, `sidecar/README.txt`, `sidecar/alex-backend/**` (sidecar + `_internal/`), `sidecar/alex-host-loop.exe` |
| Uninstall | Tauri's generated `Section Uninstall`: deletes the files it installed under `$INSTDIR`, then removes the directory, shortcuts and registry entries. `NSIS_HOOK_PREUNINSTALL` / `NSIS_HOOK_POSTUNINSTALL` were **empty**, and `deleteAppDataOnUninstall` was not set |
| Uninstall, exact scope | The generated script contains exactly two wholesale removals — `RmDir /r "$APPDATA\${BUNDLEID}"` and `RmDir /r "$LOCALAPPDATA\${BUNDLEID}"` — i.e. Tauri's own `com.alexllm.desktop` folders, which this product does not use for user data. `%LOCALAPPDATA%\Alex LLM` and every Credential Manager entry are outside the uninstaller's reach. `apps/desktop/src/lib/installer-policy.test.ts` pins this |
| Effect on user data | none: the data root lives outside `$INSTDIR` and no hook touched it. Uninstalling did not delete `%LOCALAPPDATA%\Alex LLM\`, and it did not touch Windows Credential Manager |
| Reinstall over an existing install | worked: the same files were overwritten, the data root and the credential entries were left alone |
| Upgrade workflow | there was none beyond "install the new build over the old one"; there is no network updater and none is wanted |

## 2. Data root

`%LOCALAPPDATA%\Alex LLM\` (or `ALEX_LLM_DATA_DIR`), created by
`app.data_paths.ensure_layout`:

| Path | Contents | Class |
|---|---|---|
| `data/alex.db` (+ `-wal`, `-shm`) | the whole product database | critical user data |
| `documents/<32-hex key>` | uploaded documents, **flat** files; the DB row holds the original name | critical user data |
| `models/embeddings/multilingual-e5-small/` | FastEmbed weights | recreatable cache |
| `logs/backend.log` | sidecar log | recreatable |
| `runtime/jwt.secret` | local JWT signing secret, 48 bytes generated on first start | machine identity |
| `runtime/install.id` | installation id, also sent as `X-Alex-Device-Id` | machine identity |
| `runtime/session.id` | pointer to the persistent session row | machine identity |
| `runtime/shutdown.token` | token for the managed-compute quit hook | machine identity |
| `runtime/backend.lock` | owned PID/port, ephemeral | ephemeral |
| `device.json` | `device_id` + display name | machine identity |

Credentials are **not** files: `Alex LLM/session/<id>`, `Alex LLM/provider/runpod`,
`Alex LLM/gateway/installation` and `Alex LLM/device-credential` live in Windows Credential
Manager (the scoped store) and are never written into the data root.

## 3. Database

* SQLite, opened by SQLAlchemy with `PRAGMA foreign_keys=ON` and
  `PRAGMA journal_mode=WAL` on every connection (`app/database.py`), `timeout=30`.
* WAL means the file alone is not the whole state: `alex.db-wal` / `alex.db-shm` carry
  committed data that has not been checkpointed. A plain `copy alex.db` could therefore
  lose or tear recent writes — the reason a snapshot must go through SQLite.
* Alembic history is `0001`…`0014` (this slice adds `0015`), one linear chain, SQLite-safe
  additive migrations only.

## 4. Migration behaviour

`app/runtime_entry.py` was the only path that migrated:

1. `ensure_layout` → `load_or_create_jwt` → `load_or_create_runtime_token`;
2. `alembic upgrade head` against `data/alex.db`;
3. on failure: log `migration_failed <Type>`, write `ALEX_RUNTIME_ERROR migration_failed …`
   to stderr and exit **12**. The Desktop maps exit code 12 to `MIGRATION_FAILED` and shows
   «Не удалось обновить базу. Чат не запущен; данные не удалены.» — the database file was left
   exactly as it was and no empty database was ever created in its place;
4. on success: log `migration_ok` and start uvicorn.

What did **not** exist: any backup before the migration, any record of what the previous
revision was, and any way to reach a backup from the recovery message (`docs/installer-data-
layout.md` said it plainly: "There is no backup-on-upgrade yet").

## 5. Documents

`LocalDocumentStorage` treats the data-root `documents/` directory as a flat keyed store:
keys must match `[a-f0-9]{32}`, and the resolved path's parent must be the root exactly, so
there is no way to address a file outside it. Uploads are written with `open("xb")` and the
original filename lives only in the database row. A backup therefore has to copy the DB (for
names, ownership and metadata) and the flat files (for the bytes) together.

## 6. Existing backup behaviour

* The product had **no** backup feature: no UI, no API, no files.
* The only thing resembling a backup was manual developer practice, and the two
  `data/alex.db.pre-import-*` files that an earlier import left behind in the real data root.
* Gateway side: `/var/lib/alex-gateway/gateway.db` had no snapshot tooling either; the
  deployment audit listed "No backup automation" as an open gap.

## 7. Rollback possibilities that existed

| Failure | What a user could do before this slice |
|---|---|
| Failed migration | the app refused to start; the database was intact but there was no copy to go back to and no UI that said which file to keep |
| Bad build installed over a good one | reinstall an older installer; the data root survived, but a schema that the older build cannot read would break it |
| Restore of anything | impossible without hand-copying files while the app was closed |

## 8. Gaps this slice has to close

1. A verified, deterministic backup of the database **and** documents before any migration.
2. A restore that cannot run against a live server, cannot be tricked into writing outside
   the data root, and always leaves a safety copy of what it replaced.
3. A bounded retention rule that never deletes the only usable backup.
4. An explicit uninstall policy (binaries yes, user data no) and a reinstall path that finds
   existing data without duplicating the owner.
5. A backup that never contains a secret and never clones machine identity to another PC.
6. Gateway-side: a snapshot tool with bounded retention and a documented rollback, without
   touching 12Testers.
