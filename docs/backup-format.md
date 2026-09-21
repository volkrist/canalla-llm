# Local backup format (Alex 0.9.3)

One backup is a **directory**, not an opaque archive: every artifact inside it is a plain
file a support engineer can inspect, and every rule here exists so that a restore cannot
touch anything outside the data root.

```
<data root>/backups/<backup id>/
  manifest.json        inventory + SHA-256 + format version   (written last)
  state.json           non-secret plan a restore follows
  verification.json    written by the verifier that accepted this backup
  data/alex.db         consistent SQLite snapshot
  documents/<key>      user documents exactly as the storage layer keeps them
```

`<backup id>` is `<UTC stamp>-<kind>`, e.g. `20260921T130339Z-pre_upgrade`, with a `-2`
suffix if that second already exists. Kinds: `manual` (the user asked), `pre_upgrade` (taken
automatically before a migration), `pre_restore` (the safety copy a restore takes first).

## backup_format_version

`manifest.json` carries `backup_format_version` (currently **1**). A reader that does not
know the version refuses the backup with `backup_unsupported_format` instead of guessing;
a restore refuses a snapshot whose `schema_revision` is not in this build's alembic history,
so a database is never silently downgraded. The version covers the manifest's structure;
adding fields inside it is allowed, changing their meaning is not.

## Manifest

```json
{
  "backup_format_version": 1,
  "product": "alex-llm",
  "app_version": "0.9.3",
  "schema_revision": "0015",
  "backup_id": "20260921T130339Z-manual",
  "kind": "manual",
  "label": "перед обновлением",
  "created_at": "2026-09-21T13:03:39.512+00:00",
  "source_machine": "sha256:1f0c…",         // digest of the source install id, advisory
  "database": { "path": "data/alex.db", "size": 528384, "sha256": "…" },
  "documents": { "path": "documents", "count": 2,
                 "files": [ { "path": "documents/<key>", "size": 15, "sha256": "…" } ] },
  "state": { "path": "state.json", "size": 141, "sha256": "…" },
  "excluded": [ "runtime/jwt.secret", "runtime/install.id", "…" ],
  "bytes_total": 530000
}
```

* `source_machine` is a **digest**, never the identity itself, and is informational only: a
  restore never writes it anywhere.
* The manifest lists `excluded` so the honest answer to "does my key live in this backup?"
  is written down inside the backup.

## Included

| Item | Why |
|---|---|
| `data/alex.db` | users, chats, messages, projects, memories, tool runs, tasks, preferences, sessions |
| `documents/<key>` | the document bytes; the DB rows carry names, ownership and metadata |
| `state.json` | the non-secret restore plan (which trees a restore may apply) |

## Excluded (deliberately)

| Item | Why |
|---|---|
| `runtime/jwt.secret` | a secret; a fresh machine generates its own, restoring one would weaken the installation |
| `runtime/install.id`, `runtime/session.id`, `device.json` | machine identity — a backup must not be able to clone it |
| `runtime/shutdown.token`, `runtime/backend.lock`, `runtime/migration.json`, `runtime/backup-restore.json` | ephemeral runtime state |
| `models/embeddings/**` | recreatable cache (the model manager re-downloads/rebuilds it) |
| `logs/**` | diagnostics, not user work |
| Windows Credential Manager entries | OS-managed: `Alex LLM/session/<id>`, `Alex LLM/provider/runpod`, `Alex LLM/gateway/installation`, `Alex LLM/device-credential` |

## Hashing and atomicity

* SHA-256 for every file in the inventory, computed while copying and re-checked on every
  verification.
* The manifest is written **last** and through `*.tmp` + `os.replace`, so a directory either
  has a complete manifest or is reported as incomplete.
* A backup is created in `<backups>/.staging-<id>` and renamed into place
  (`os.replace`), so a reader never sees a half-written backup.
* The database is snapshotted with the SQLite backup API (never a byte copy): the snapshot
  contains committed WAL content and passes `PRAGMA integrity_check`.
* Verification opens snapshots read-only **and** immutable, so verifying a backup can never
  create `-wal`/`-shm` side files inside it.

## Verification

`verify_backup` fails closed with a stable code on any of:

| Situation | Code |
|---|---|
| manifest missing/unreadable | `backup_missing` / `backup_corrupt` |
| unknown `backup_format_version` | `backup_unsupported_format` |
| file missing, or a directory entry that is not a regular file | `backup_incomplete` |
| size or hash mismatch, or an undeclared extra file | `backup_tampered` |
| `PRAGMA integrity_check` not `ok`, or missing `users`/`chats`/`messages` | `backup_corrupt` |
| path outside the backup directory, absolute path, `..`, symlink | `restore_unsafe_path` |

A successful verification writes `verification.json` (its timestamp, the format version, the
file count and the schema revision). The list endpoint reports `verified`, `complete` and a
`problem` code, so the UI can show «Проверена» / «Не проверена» / «Повреждена» without
pretending a corrupt directory is usable.

## Retention

| Class | Rule |
|---|---|
| automatic (`pre_upgrade`, `pre_restore`) | keep the newest **3** |
| manual | keep the newest **10** |
| always | the newest backup overall and the last verified one are never deleted |
| never | pruning never removes anything outside `<data root>/backups` |

Pruning happens after a successful create and before a pre-upgrade backup. Every removal is
logged with the id and kind. The numbers come from `BACKUP_KEEP_AUTOMATIC` /
`BACKUP_KEEP_MANUAL`.

## Disk space

Before creating or restoring, the estimate is `(database + documents + current state) × 1.3
+ 64 MB` and it is compared with the free space of the target volume. If it does not fit, the
operation is refused with `insufficient_disk_space` **before** anything is written.

## Restore semantics (same PC)

1. the backup is verified completely (it is untrusted input);
2. a `pre_restore` safety backup of the current state is created and verified — a restore is
   always reversible;
3. the database and documents are staged inside the data root and validated;
4. the live database and `documents/` are moved aside, the staged copies are moved in
   (rename, with a short retry for Windows handle timing);
5. the result is validated again; if that fails, the previous state is put back
   (`restore_rolled_back`) and the failure is reported;
6. everything in `runtime/` is untouched, so the local JWT, installation id, device identity
   and every Credential Manager entry survive. A session row that exists in the backup keeps
   working because the machine credential that proves it is still there.

Moving the backup directory to another PC restores user data only: the importing machine
keeps **its own** identity and credentials, and it must enroll in Alex Cloud with its own
activation code. There is no secret export path, by design.
