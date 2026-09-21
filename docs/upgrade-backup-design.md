# Upgrade, backup and recovery — design

This slice makes an Alex upgrade survivable: a newer build installed over an older one must
keep every piece of user work, and a failure must be recoverable. It adds no network updater.

## 1. Data classes and what each one deserves

| Class | Examples | Upgrade | Backup | Restore |
|---|---|---|---|---|
| A. critical user data | users, chats, messages, projects, memories, documents, settings | migrated, never dropped | **copied** (DB snapshot + document bytes) | replaced from the backup |
| B. security identity | `runtime/jwt.secret`, `runtime/install.id`, `runtime/session.id`, `device.json` | untouched | **excluded** | **never** replaced |
| C. OS credentials | `Alex LLM/session/<id>`, `Alex LLM/provider/runpod`, `Alex LLM/gateway/installation`, `Alex LLM/device-credential` | untouched (outside the data root) | **excluded** | untouched: the OS keeps them |
| D. recreatable cache | `models/embeddings/**`, `logs/**`, `-wal`/`-shm` | untouched | **excluded** | rebuilt on demand |
| E. application binaries | Desktop exe, sidecar, native host | replaced by the installer | not applicable | not applicable |

The distinction is the whole point: A can be copied anywhere, B decides *who this machine is*
and must stay machine-local, C must stay inside Windows, D can be recreated, E is disposable.

## 2. Backup

`app/backup/` implements one format (see `backup-format.md`):

* `format.py` — paths, manifest, hashing, path validation, read-only SQLite helpers;
* `archive.py` — `BackupService` with create / verify / list / prune and an operation lock;
* `restore.py` — the transactional restore, used only while the backend is stopped;
* `routes.py` — the authenticated HTTP surface (create, list, verify, diagnose).

A backup directory holds `manifest.json`, `state.json`, `verification.json`,
`data/alex.db` (SQLite snapshot) and `documents/`. It is created in a staging directory and
renamed into place; the manifest is written last, so an interrupted backup can never be
mistaken for a complete one.

## 3. Where backups live

`%LOCALAPPDATA%\Alex LLM\backups\` — inside the data root (so it survives a reinstall the
same way user data does) but never inside the install directory. `data/` is where the live
database is; nothing in the backup path is ever confused with it.

## 4. Retention

Automatic backups (`pre_upgrade`, `pre_restore`) are kept 3 deep; manual backups 10. The
newest backup overall and the last verified one are never pruned, and pruning only ever
touches directories directly inside `backups/`. The numbers are settings
(`BACKUP_KEEP_AUTOMATIC`, `BACKUP_KEEP_MANUAL`).

## 5. Pre-upgrade backup (fail closed)

`runtime_entry.main()` compares the database's alembic revision with the packaged head
before it migrates:

```
fresh database        → migrate, no backup (nothing to lose)
revision == head      → migrate (a no-op), no backup
revision != head      → create + verify a `pre_upgrade` backup, then migrate
backup fails          → exit 15 (`pre_upgrade_backup_failed`), database untouched,
                        runtime/migration.json = blocked_no_backup
migration fails       → exit 12, database untouched, the backup stays,
                        runtime/migration.json = failed + the backup id
migration succeeds    → runtime/migration.json = ok + from/to + the backup id
```

The Desktop maps exit 15 to `PRE_UPGRADE_BACKUP_FAILED`, so an upgrade that could not be
protected is refused instead of being attempted "hopefully". There is no code path that
creates a fresh empty database after a failure.

## 6. Restore

Restore is deliberately **not** an HTTP route: the running server holds the database.
`apps/desktop/src-tauri/src/backup.rs` owns it:

1. the Desktop validates the id and requires the directory to live inside
   `<data root>/backups` with a `manifest.json` (no traversal, no symlink, no absolute path);
2. it refuses when the backend is not its own (`backend_external`) — an external developer
   backend is never stopped;
3. it stops the owned backend through the same path a restart uses (managed-compute shutdown
   included);
4. it runs `alex-backend(.exe) --restore-backup <dir>` — the identical helper code, one shot,
   no server, no stdout dependency: the typed result goes to `runtime/backup-restore.json`;
5. the helper verifies the backup, takes and verifies the `pre_restore` safety backup, stages,
   validates, swaps, validates again, and rolls back from the safety copy if the second
   validation fails;
6. the Desktop starts the backend again and returns the report to the panel.

Machine identity and credentials are outside all of this: a restore cannot clone them.

## 7. Different PC

A backup directory copied to another machine restores **user data only**. The importing
installation keeps its own `jwt.secret`, `install.id`, `session.id`, `device.json` and its
own Credential Manager entries; it must enroll in Alex Cloud with its own one-time activation
code. Nothing in the format can export a credential, so "make migration easy by exporting
secrets" is impossible by construction.

## 8. Concurrency

| Situation | Behaviour |
|---|---|
| create + create | serialized by an asyncio lock; both succeed and both are valid, or the second gets `backup_busy` |
| verify while creating | `backup_busy` (HTTP 409) |
| restore while creating | impossible: restore only runs after the Desktop stopped the backend |
| migration during a restore | impossible: the restore helper never starts a server, the server never starts during a restore |
| backup during a running task | allowed: the snapshot is consistent. Shared compute keeps running — a local backup has no business stopping a paid Pod |

## 9. User-facing states

| Where | States |
|---|---|
| Settings → Резервные копии | count, «Создать копию», per-row «Проверить» / «Восстановить» with an in-panel confirmation, status «Проверена» / «Не проверена» / «Повреждена», the backup folder path, the last restore result |
| Startup | the existing bootstrap states; a refused upgrade surfaces `PRE_UPGRADE_BACKUP_FAILED` with an actionable message, not a stack trace |
| Recovery | `runtime/migration.json` records what failed and which backup to use; the panel lists that backup as restorable, so recovery is a click away |

No screen shows a raw stack trace, an alembic revision dump or a file path outside the data
root.

## 10. Diagnostics

`GET /backup/diagnostic` (and the panel's folder line) expose exactly what support needs:
app version, backup format version, current and head schema revision, whether a migration is
pending, the last migration result, backup counts, the last verified timestamp and free disk
space. No secret, no credential value, no prompt or chat content.

## 11. Logging

`backup_created`, `backup_pruned`, `backup_unverified`, `backup_restored`,
`backup_restore_failed`, `backup_restore_rolled_back`, `backup_restore_leftovers_removed`,
`pre_upgrade_backup_ok`, `pre_upgrade_backup_failed`, `migration_ok`, `migration_failed` —
each with an operation id (the backup id), the kind, counts, bytes, revisions and stable
error codes. Never a password, a JWT, a refresh secret, an installation secret or a RunPod
key.

## 12. Gateway side

The deployed Gateway keeps its own database and its own recovery story (see
`apps/gateway/deploy/alex-gateway-backup.md`): a SQLite snapshot tool with verification and a
retention cap of 5, run **before** any migration or release switch, and a documented
symlink-switch rollback. It never touches 12Testers: only the VPS itself is shared.
