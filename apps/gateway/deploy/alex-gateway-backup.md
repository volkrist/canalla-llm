# Central Alex Gateway — database backup, retention and rollback (operator note)

`/var/lib/alex-gateway/gateway.db` is a SQLite database (WAL mode) that holds **installations,
activation-code digests, the global compute lease, idempotency records and audit events**. It
never holds chats, prompts, documents or provider responses. `alex-gateway-backup.sh` is the
only supported way to snapshot it; it is installed as `/usr/local/sbin/alex-gateway-backup.sh`.

## What a snapshot is, and what it is not

* **Backed up:** the Gateway database only — the rows listed above.
* **Never backed up, by design:** `/etc/alex-gateway/runpod.env` and
  `/etc/alex-gateway/alex-gateway.env`. The script never opens them, so no RunPod master key,
  JWT signing key or installation secret can reach a snapshot, this script's output or a log
  line. Only activation-code **digests** (hashes) are in the database; a code itself never is.
* **A snapshot contains no RunPod key and no installation secrets.**
* **Self-contained:** `VACUUM INTO` writes a compacted database in `journal_mode = delete`, so a
  snapshot needs no `-wal`/`-shm` sidecar next to it to be readable or restorable.
* **Never touched:** 12Testers (`/var/www/12-testers`, its service, PM2 app, database and
  certificates) — a separate product that only shares the host.
* No logs, no exports and no provider data may ever be added to the backups directory.

## Install (once, as root)

```sh
install -o root -g root -m 0750 alex-gateway-backup.sh /usr/local/sbin/alex-gateway-backup.sh
install -d -o alex-gateway -g alex-gateway -m 0700 /var/lib/alex-gateway/backups
```

Run it with `sudo`. As root it prepares the directory and then drops to the `alex-gateway`
service account, so snapshots are owned by the account that owns the live database.

## Commands

| Command | Effect |
|---|---|
| `alex-gateway-backup.sh` | Snapshot `gateway.db` to `backups/gateway-<UTC>.db` **through SQLite** (`VACUUM INTO`, falling back to the `.backup` API), verify it, then rename it into place. Never `cp`/`tar`/`rsync` of a live database — the `-wal` file holds committed transactions and a byte copy can capture a torn page state. |
| `alex-gateway-backup.sh --verify FILE` | Strict check: `PRAGMA integrity_check` = `ok`, `PRAGMA foreign_key_check` empty, and the expected tables present (`installations`, `enrollment_codes`, `gateway_compute`, `gateway_sessions`, `audit_events`). |
| `alex-gateway-backup.sh --prune [N]` | Keep the newest `N` snapshots (default and hard cap **5**) and delete older ones — only `gateway-*.db` inside the backups directory, never the newest, and it prints each removal. |
| `alex-gateway-backup.sh --restore-hint` | Print the rollback procedure below. It executes nothing. |
| `alex-gateway-backup.sh --help` | Usage. |

A file named `gateway-*.db` only ever appears in the backups directory after it passed
verification: the snapshot is written and verified as a `.part` file and renamed on success; a
failed snapshot is removed and the command exits non-zero. Any failure message is actionable
and secret-free.

## Retention

At most the **last 5** snapshots, each about 90 KB, so the directory stays well under a few MB
(the script warns above 8 MB). Nothing is deleted automatically — run `--prune` after each
backup (or from your own maintenance window); a backup that takes the count past 5 says so and
prints the exact prune command. `--prune` also clears `.part` leftovers from interrupted runs.
Delete only files this script created.

## Verify — and the pre-deploy rule

**Run the backup before any Gateway database migration or release switch, and do not migrate if
the backup cannot be verified.**

```sh
sudo /usr/local/sbin/alex-gateway-backup.sh
sudo /usr/local/sbin/alex-gateway-backup.sh --verify /var/lib/alex-gateway/backups/gateway-<UTC>.db
sudo /usr/local/sbin/alex-gateway-backup.sh --prune

systemctl is-active alex-gateway                                     # active
curl -s http://127.0.0.1:9011/health                                 # loopback readiness
curl -s -o /dev/null -w '%{http_code}\n' https://gateway.12testers.store/health   # 200
```

Expected from `--verify`: `integrity_check: ok`, `foreign_key_check: no violations`, gateway
tables present. If that fails, stop — do not migrate and do not restore an unverified file.

## Rollback (documented, not automated)

`sudo /usr/local/sbin/alex-gateway-backup.sh --restore-hint` prints the full procedure:

1. Snapshot the current state first, so the state you replace stays recoverable.
2. `--verify` the chosen snapshot; stop on any failure.
3. `systemctl stop alex-gateway`.
4. Replace the database **as the service user** (`install -o alex-gateway -g alex-gateway -m 0600`
   into `gateway.db.restore`, then `mv` — atomic in the same directory), and delete the stale
   `gateway.db-wal` / `gateway.db-shm` sidecars: the snapshot already contains every committed
   transaction, and a leftover `-wal` from the newer database must never be paired with it.
5. `systemctl start alex-gateway`, then check `/health` as above.

A restore rewinds installations, the compute lease and audit events to the snapshot time:
installations enrolled afterwards are gone and must re-enroll with a fresh one-time activation
code. Restarting the Gateway interrupts in-flight SSE streams, so pick a quiet moment. No
RunPod key, no secret material and no 12Testers data is involved in any of these steps.
