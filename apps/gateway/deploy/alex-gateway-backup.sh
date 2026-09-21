#!/bin/sh
#
# Central Alex Gateway — SQLite snapshot: backup, verify, prune, rollback hint.
#
# Install (as root; the script drops to the service account itself):
#   install -o root -g root -m 0750 alex-gateway-backup.sh /usr/local/sbin/alex-gateway-backup.sh
#   install -d -o alex-gateway -g alex-gateway -m 0700 /var/lib/alex-gateway/backups
#
# Usage:
#   alex-gateway-backup.sh                  snapshot the live Gateway database
#   alex-gateway-backup.sh --verify FILE    verify an existing snapshot (strictly)
#   alex-gateway-backup.sh --prune [N]      keep the newest N snapshots (N <= 5, default 5)
#   alex-gateway-backup.sh --restore-hint   print the rollback procedure, execute nothing
#   alex-gateway-backup.sh --help
#
# Contract:
#   * A file named gateway-*.db only ever appears in the backups directory AFTER it passed
#     PRAGMA integrity_check and PRAGMA foreign_key_check. The snapshot is written to a
#     temporary .part file, verified there, and renamed into place only on success; the
#     temporary file is removed when verification fails.
#   * The snapshot is produced by SQLite itself (VACUUM INTO, falling back to the .backup API
#     of the sqlite3 CLI). A live database is NEVER copied with cp/tar/rsync: the -wal file
#     holds committed transactions, and a byte copy of a database that is being written can
#     capture a torn page state that still "opens" and silently loses rows.
#   * The snapshot is taken as the service user (alex-gateway) so ownership and permissions
#     stay correct for the service account.
#
# SECRETS AND SCOPE — read before editing:
#   The only file this script reads is the Gateway SQLite database. It never opens
#   /etc/alex-gateway/*.env, so the RunPod master key, the JWT signing key and installation
#   secrets cannot reach a snapshot or this script's output. A snapshot holds installations,
#   activation-code DIGESTS (hashes, never the codes), the global compute lease, idempotency
#   records and audit events — by design no chats, prompts, documents or provider responses.
#   Never add environment files, logs or exports to the backups directory.
#   No network access, no provider or Pod action, no restore, and nothing at all is done to
#   the unrelated 12Testers site, its service, its database or its certificates.
#
# Configuration — environment overrides exist for staging/tests only, production uses defaults:
#   ALEX_GATEWAY_DB             live database       (default /var/lib/alex-gateway/gateway.db)
#   ALEX_GATEWAY_BACKUP_DIR     snapshots directory (default <db directory>/backups)
#   ALEX_GATEWAY_SERVICE_USER   service account     (default alex-gateway)
#   ALEX_GATEWAY_KEEP           default retention   (default 5, hard cap 5)

set -eu
umask 077
LC_ALL=C
export LC_ALL

# Deterministic tool lookup: the script may be started from a service account's environment.
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

DB="${ALEX_GATEWAY_DB:-/var/lib/alex-gateway/gateway.db}"
BACKUP_DIR="${ALEX_GATEWAY_BACKUP_DIR:-$(dirname "$DB")/backups}"
SERVICE_USER="${ALEX_GATEWAY_SERVICE_USER:-alex-gateway}"
DEFAULT_KEEP="${ALEX_GATEWAY_KEEP:-5}"

absolutize() {
    case "$1" in
        '' | /*) printf '%s' "$1" ;;
        *) printf '%s/%s' "$(pwd)" "$1" ;;
    esac
}

# Absolute paths: the script drops to the service account, whose session has no access to the
# caller's working directory, so no path may stay relative. SELF also travels into that session
# as ALEX_GATEWAY_BACKUP_SELF, because the script is fed to it on stdin ($0 is /bin/sh there).
SELF="$(absolutize "${ALEX_GATEWAY_BACKUP_SELF:-$0}")"
DB="$(absolutize "$DB")"
BACKUP_DIR="$(absolutize "$BACKUP_DIR")"

MAX_KEEP=5                    # retention policy: at most the last 5 snapshots
BACKUP_BUDGET_KB=8192         # warn above this total size (policy: the directory stays small)
SNAPSHOT_GLOB='gateway-*.db'
PART_GLOB='.gateway-*.db.part'
EXPECTED_TABLES='installations enrollment_codes gateway_compute gateway_sessions audit_events'
EXPECTED_TABLE_COUNT=5
DOC_PATH='apps/gateway/deploy/alex-gateway-backup.md'

CLEANUP=""

note() { printf '%s\n' "$*"; }
ok() { printf 'ok      %s\n' "$*"; }
warn() { printf 'warning %s\n' "$*" >&2; }
err() { printf 'error   %s\n' "$*" >&2; }
die() {
    err "$*"
    exit 1
}

cleanup_add() {
    CLEANUP="$CLEANUP
$1"
}

cleanup_run() {
    set +e
    # The trailing newline is load-bearing: dash skips the final line of a stream that does not
    # end with one, which would silently leave the last registered .part file on disk.
    printf '%s\n' "$CLEANUP" | while IFS= read -r _p; do
        if [ -n "$_p" ]; then rm -f "$_p"; fi
    done
    return 0
}

trap cleanup_run EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

usage() {
    cat <<'EOF'
Central Alex Gateway — SQLite snapshot backup.

Usage:
  alex-gateway-backup.sh                  snapshot the live Gateway database
  alex-gateway-backup.sh --verify FILE    verify an existing snapshot
  alex-gateway-backup.sh --prune [N]      keep the newest N snapshots (N <= 5, default 5)
  alex-gateway-backup.sh --restore-hint   print the rollback procedure (executes nothing)
  alex-gateway-backup.sh --help

Paths (defaults): database /var/lib/alex-gateway/gateway.db,
snapshots /var/lib/alex-gateway/backups/gateway-<UTC>.db, taken as user alex-gateway.

Exit status: 0 on success; non-zero when sqlite3 is missing, the database cannot be read,
verification fails (the unverified snapshot is removed) or a prune could not complete.
See apps/gateway/deploy/alex-gateway-backup.md for retention and the rollback procedure.
EOF
}

file_bytes() {
    wc -c < "$1" | tr -d ' '
}

require_sqlite3() {
    command -v sqlite3 >/dev/null 2>&1 || die "sqlite3 CLI not found in PATH ($PATH). Install it (Debian/Ubuntu: apt-get install -y sqlite3) — without SQLite there is no consistent snapshot, and this script will not fall back to a raw file copy."
}

require_service_user() {
    id "$SERVICE_USER" >/dev/null 2>&1 || die "service user '$SERVICE_USER' does not exist. Fix ALEX_GATEWAY_SERVICE_USER, or create the account the alex-gateway.service unit runs as."
}

# Root creates/hands over the directory, then drops to the service account for every database
# operation, so snapshots are owned by the account that owns the live database.
prepare_backup_dir() {
    _group=$(id -gn "$SERVICE_USER" 2>/dev/null || printf '%s' "$SERVICE_USER")
    if [ -d "$BACKUP_DIR" ]; then
        _owner=$(stat -c '%U:%G' "$BACKUP_DIR" 2>/dev/null || printf 'unknown')
        _mode=$(stat -c '%a' "$BACKUP_DIR" 2>/dev/null || printf 'unknown')
        if [ "$_owner" != "$SERVICE_USER:$_group" ] || [ "$_mode" != "700" ]; then
            warn "backups directory $BACKUP_DIR is $_owner $_mode, expected $SERVICE_USER:$_group 700. Fix it with: install -d -o $SERVICE_USER -g $_group -m 0700 $BACKUP_DIR"
        fi
    else
        install -d -o "$SERVICE_USER" -g "$_group" -m 0700 "$BACKUP_DIR" ||
            die "could not create $BACKUP_DIR as $SERVICE_USER:$_group. Create it once by hand and re-run: install -d -o $SERVICE_USER -g $_group -m 0700 $BACKUP_DIR"
        ok "created backups directory $BACKUP_DIR ($SERVICE_USER:$_group 0700)"
    fi
}

# Drop into the service account before touching the database, so snapshots are owned by the
# account that owns the live database. The script is piped in on stdin because the installed
# file is root:root 0750 and the service account must not need a wider mode than that; both
# shells therefore get the arguments after "--", so they can never be read as shell options.
run_as_service_user() {
    _status=0
    if command -v runuser >/dev/null 2>&1; then
        ALEX_GATEWAY_BACKUP_DROPPED="$SERVICE_USER" \
            ALEX_GATEWAY_BACKUP_SELF="$SELF" \
            runuser -u "$SERVICE_USER" -- /bin/sh -s -- "$@" < "$SELF" || _status=$?
    else
        ALEX_GATEWAY_BACKUP_DROPPED="$SERVICE_USER" \
            ALEX_GATEWAY_BACKUP_SELF="$SELF" \
            su -s /bin/sh "$SERVICE_USER" -c 'exec /bin/sh -s -- "$@"' sh "$@" < "$SELF" || _status=$?
    fi
    exit "$_status"
}

require_snapshot_dir() {
    [ -d "$BACKUP_DIR" ] || die "snapshots directory $BACKUP_DIR does not exist. Create it once as root: install -d -o $SERVICE_USER -g $SERVICE_USER -m 0700 $BACKUP_DIR"
    [ -w "$BACKUP_DIR" ] || die "snapshots directory $BACKUP_DIR is not writable by user '$(id -un)'. Run this script as root (it drops to the service account itself), or fix it: chown $SERVICE_USER:$SERVICE_USER $BACKUP_DIR && chmod 700 $BACKUP_DIR"
}

require_live_db() {
    [ -e "$DB" ] || die "live Gateway database not found: $DB. Check ALEX_GATEWAY_DB and the service datadir /var/lib/alex-gateway; nothing was changed."
    [ -f "$DB" ] || die "live Gateway database is not a regular file: $DB. Nothing was changed."
    [ -r "$DB" ] || die "live Gateway database $DB is not readable by user '$(id -un)'. Run this script as root (it drops to the service account itself)."
}

# sqlite3 is always started with -init /dev/null so no ~/.sqliterc of any account can inject
# commands into a backup run.
sql() {
    sqlite3 -init /dev/null "$@"
}

live_journal_mode() {
    sql "$DB" 'PRAGMA journal_mode;' 2>/dev/null || printf 'unknown'
}

snapshot_vacuum_into() {
    _src=$1
    _dst=$2
    rm -f "$_dst"
    # VACUUM INTO copies committed state through SQLite (including transactions still in the
    # -wal file) and refuses to overwrite an existing destination; on failure it removes the
    # destination itself. Nothing about the live database's content is modified.
    sql "$_src" "VACUUM INTO '$_dst';"
}

snapshot_backup_api() {
    _src=$1
    _dst=$2
    rm -f "$_dst"
    # Fallback for an sqlite3 CLI older than 3.27 (no VACUUM INTO): the CLI's .backup command
    # uses the online backup API with its own busy retries, which is still a consistent read.
    sql "$_src" ".backup '$_dst'"
}

table_present() {
    _file=$1
    _table=$2
    _hit=$(sql "$_file" "SELECT name FROM sqlite_master WHERE type='table' AND name='$_table';")
    [ -n "$_hit" ]
}

# verify_snapshot FILE [strict] [quiet]
#   strict=yes  also fail when an expected Gateway table is missing (used by --verify)
#   strict=no   integrity + foreign keys gate the snapshot; the table census is informational
#   quiet=yes   run silently, let the caller print the summary for the final path
verify_snapshot() {
    _file=$1
    _strict=${2:-no}
    _quiet=${3:-no}
    _rc=0
    _missing=""
    _found=0

    [ -f "$_file" ] || die "not a regular file: $_file"
    _bytes=$(file_bytes "$_file")
    [ "$_bytes" -gt 0 ] || die "snapshot is empty (0 bytes): $_file"

    _integrity=$(sql "$_file" 'PRAGMA integrity_check;' 2>&1) || die "sqlite3 could not read $_file (not a SQLite database, truncated, or unreadable). It was not created by this script's verified path — obtain it again."
    if [ "$(printf '%s' "$_integrity" | tr -d '[:space:]')" != "ok" ]; then
        err "integrity_check failed for $_file:"
        printf '%s\n' "$_integrity" | head -n 10 | sed 's/^/        /'
        _rc=1
    fi

    _fk=$(sql "$_file" 'PRAGMA foreign_key_check;' 2>&1) || die "sqlite3 could not run foreign_key_check on $_file; the file is unreadable and must not be used as a backup."
    if [ -n "$_fk" ]; then
        err "foreign_key_check reported violations in $_file:"
        printf '%s\n' "$_fk" | head -n 10 | sed 's/^/        /'
        _rc=1
    fi

    for _t in $EXPECTED_TABLES; do
        if table_present "$_file" "$_t"; then
            _found=$((_found + 1))
        else
            _missing="$_missing $_t"
        fi
    done
    if [ "$_strict" = "yes" ] && [ -n "$_missing" ]; then
        err "expected Gateway tables are missing from $_file:$_missing"
        err "That is not a Gateway database (or an unsupported pre-migration schema); do not restore it."
        _rc=1
    fi

    if [ "$_rc" -ne 0 ]; then
        return 1
    fi

    if [ "$_quiet" = "yes" ]; then
        return 0
    fi

    ok "valid: $_file"
    note "        size: $(du -h "$_file" | awk '{print $1}') ($_bytes bytes)"
    note "        integrity_check: ok"
    note "        foreign_key_check: no violations"
    note "        gateway tables: $_found of $EXPECTED_TABLE_COUNT expected present"
    return 0
}

do_backup() {
    require_sqlite3
    require_live_db
    require_snapshot_dir

    _ts=$(date -u +%Y%m%dT%H%M%SZ)
    _target="$BACKUP_DIR/gateway-$_ts.db"
    _n=1
    while [ -e "$_target" ]; do
        _target="$BACKUP_DIR/gateway-$_ts-$_n.db"
        _n=$((_n + 1))
    done
    _part="$BACKUP_DIR/.gateway-$_ts-$$.db.part"

    case "$_part" in
        *"'"*) die "path contains a single quote, which this script does not support: $_part" ;;
    esac

    rm -f "$_part"
    cleanup_add "$_part"

    note "Gateway snapshot"
    note "  source:     $DB"
    note "  live mode:  $(live_journal_mode) — read through SQLite itself, never a byte copy"
    note "  target:     $_target"
    note "  taken by:   $(id -un):$(id -gn)"

    _method="VACUUM INTO"
    if ! snapshot_vacuum_into "$DB" "$_part"; then
        warn "VACUUM INTO did not succeed (see the message above); retrying with the SQLite .backup API"
        _method="sqlite3 .backup"
        rm -f "$_part"
        snapshot_backup_api "$DB" "$_part" ||
            die "snapshot failed: neither VACUUM INTO nor .backup produced a readable file at $_part. The live database was not modified. Check free space on $(dirname "$BACKUP_DIR") and that user '$(id -un)' can write to $BACKUP_DIR."
    fi

    # Verified while it is still a .part file, then renamed atomically: no file under a
    # snapshot name ever exists in an unverified state.
    if ! verify_snapshot "$_part" no yes; then
        rm -f "$_part"
        die "the snapshot failed verification and was removed, so no unusable file sits under a snapshot name. $DB was not modified; retention, pruning and the rollback path are unaffected."
    fi

    # chmod then rename: the .part path stays in the cleanup registry, and after a successful
    # rename that rm is a harmless no-op — the verified snapshot is never removed.
    chmod 600 "$_part" 2>/dev/null || true
    mv "$_part" "$_target" || die "could not move the verified snapshot into place: $_part -> $_target"

    ok "snapshot verified (integrity + foreign keys), then renamed into place"
    note "        path: $_target"
    note "        size: $(du -h "$_target" | awk '{print $1}') ($(file_bytes "$_target") bytes)"
    note "        method: $_method"
    note "        integrity_check: ok"
    note "        foreign_key_check: no violations"
    note "        owner: $(stat -c '%U:%G %a' "$_target" 2>/dev/null || printf 'unknown')"

    _count=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name "$SNAPSHOT_GLOB" | wc -l | tr -d ' ')
    note "        snapshots now on disk: $_count (policy cap $MAX_KEEP)"
    if [ "$_count" -gt "$MAX_KEEP" ]; then
        warn "$_count snapshots are older than the retention policy of $MAX_KEEP. Nothing was deleted automatically; enforce retention with: $SELF --prune"
    fi
    _kb=$(du -sk "$BACKUP_DIR" | awk '{print $1}')
    if [ "$_kb" -gt "$BACKUP_BUDGET_KB" ]; then
        warn "snapshots directory is ${_kb} KB, above the ${BACKUP_BUDGET_KB} KB budget. Enforce retention with: $SELF --prune, and delete only files this script created."
    fi
}

do_verify() {
    _file=$1
    [ -n "$_file" ] || die "--verify needs a snapshot path, e.g. $SELF --verify $BACKUP_DIR/gateway-<UTC>.db (list them with: ls -la $BACKUP_DIR)"
    [ -e "$_file" ] || die "snapshot not found: $_file (list the available snapshots with: ls -la $BACKUP_DIR)"
    require_sqlite3
    verify_snapshot "$_file" yes
}

do_prune() {
    _keep=$1
    require_sqlite3

    case "$_keep" in
        '' | *[!0-9]*) die "--prune expects a positive integer, got: '$_keep' (N is the number of snapshots to keep)" ;;
    esac
    [ "$_keep" -ge 1 ] || die "--prune N must be at least 1: N is the number of snapshots to keep, and the newest snapshot is never deleted."
    if [ "$_keep" -gt "$MAX_KEEP" ]; then
        warn "--prune $_keep exceeds the retention policy; the cap of $MAX_KEEP is applied"
        _keep=$MAX_KEEP
    fi

    [ -d "$BACKUP_DIR" ] || die "snapshots directory $BACKUP_DIR does not exist — there is nothing to prune."
    [ -w "$BACKUP_DIR" ] || die "snapshots directory $BACKUP_DIR is not writable by user '$(id -un)'. Run this script as root (it drops to the service account itself)."

    _list=$(mktemp "${TMPDIR:-/tmp}/alex-gateway-prune.XXXXXX") || die "could not create a temporary file to hold the snapshot list."
    cleanup_add "$_list"
    find "$BACKUP_DIR" -maxdepth 1 -type f -name "$SNAPSHOT_GLOB" | LC_ALL=C sort > "$_list"

    _total=$(wc -l < "$_list" | tr -d ' ')
    if [ "$_total" -eq 0 ]; then
        note "no snapshots in $BACKUP_DIR — nothing to prune"
        return 0
    fi

    _newest=$(tail -n 1 "$_list")
    _cutoff=$((_total - _keep + 1))
    note "retention: keep the newest $_keep of $_total snapshot(s) in $BACKUP_DIR (policy cap $MAX_KEEP)"
    note "newest snapshot (never deleted): $_newest"

    _i=0
    _removed=0
    _failed=0
    while IFS= read -r _f; do
        [ -n "$_f" ] || continue
        _i=$((_i + 1))
        if [ "$_i" -ge "$_cutoff" ]; then
            continue
        fi
        case "$_f" in
            "$BACKUP_DIR"/$SNAPSHOT_GLOB) ;;
            *)
                warn "skipping unexpected path (not a snapshot of this script): $_f"
                continue
                ;;
        esac
        if [ "$_f" = "$_newest" ]; then
            warn "refusing to delete the newest snapshot: $_f"
            continue
        fi
        _size=$(du -h "$_f" | awk '{print $1}')
        if rm -f "$_f"; then
            note "removed $_f ($_size)"
            _removed=$((_removed + 1))
        else
            warn "could not remove $_f"
            _failed=$((_failed + 1))
        fi
    done < "$_list"

    # Leftovers from an interrupted run are ours too, and nothing else ever matches this pattern.
    find "$BACKUP_DIR" -maxdepth 1 -type f -name "$PART_GLOB" -mtime +1 | while IFS= read -r _p; do
        if rm -f "$_p"; then note "removed stale partial file $_p"; fi
    done

    note "removed: $_removed   kept: $_keep"
    note "snapshots now on disk:"
    find "$BACKUP_DIR" -maxdepth 1 -type f -name "$SNAPSHOT_GLOB" | LC_ALL=C sort | while IFS= read -r _f; do
        printf '  %s  %s\n' "$(du -h "$_f" | awk '{print $1}')" "$_f"
    done

    _kb=$(du -sk "$BACKUP_DIR" | awk '{print $1}')
    note "directory size: ${_kb} KB ($BACKUP_DIR)"
    if [ "$_kb" -gt "$BACKUP_BUDGET_KB" ]; then
        warn "snapshots directory is ${_kb} KB, above the ${BACKUP_BUDGET_KB} KB budget — remove older snapshots (only files this script created) or copy them off-host."
    fi
    [ "$_failed" -eq 0 ] || die "$_failed snapshot(s) could not be removed; check ownership of $BACKUP_DIR."
    return 0
}

restore_hint() {
    cat <<EOF
Gateway database rollback — PROCEDURE FOR AN OPERATOR. NOTHING BELOW HAS BEEN EXECUTED.

  Database: $DB
  Snapshots: $BACKUP_DIR/gateway-<UTC>.db
  Service:  systemctl {status,stop,start,is-active} alex-gateway   (user $SERVICE_USER, loopback only)
  Full note: $DOC_PATH

  0. Snapshot the current state first, so the state you are replacing stays recoverable:
       sudo $SELF

  1. Pick a snapshot and verify it. Do not continue unless it reports integrity ok,
     no foreign-key violations and the expected Gateway tables:
       sudo ls -la $BACKUP_DIR
       sudo $SELF --verify $BACKUP_DIR/gateway-<UTC>.db

  2. Stop the Gateway (never the 12Testers services):
       sudo systemctl stop alex-gateway
       systemctl is-active alex-gateway     # expect: inactive

  3. Replace the live database AS THE SERVICE USER, and drop the stale WAL sidecars: the
     snapshot already contains every committed transaction, so a -wal/-shm left over from the
     newer database must never be paired with the restored file. The mv is atomic inside the
     same directory:
       sudo install -o $SERVICE_USER -g $SERVICE_USER -m 0600 -- \\
         $BACKUP_DIR/gateway-<UTC>.db $DB.restore
       sudo rm -f "$DB-wal" "$DB-shm"
       sudo mv $DB.restore $DB

  4. Start it again and check health:
       sudo systemctl start alex-gateway
       systemctl is-active alex-gateway
       curl -s http://127.0.0.1:9011/health; echo
       curl -s -o /dev/null -w '%{http_code}\\n' https://gateway.12testers.store/health

  5. Expect exactly the state frozen in the snapshot: installations, the compute lease and
     audit events as of the snapshot time. Installations created after it are gone and must
     enroll again with a fresh one-time activation code (operator CLI). If health does not
     recover, restore the step-0 snapshot and repeat step 4.

  Notes
  - No restore is ever performed by this script; a wrong restore is worse than none.
  - Snapshots contain no RunPod key and no installation secret: those live only in
    /etc/alex-gateway/runpod.env and /etc/alex-gateway/alex-gateway.env and are never copied.
  - 12Testers is untouched: its own vhost, service, PM2 app, database and certificates.
  - Restarting the Gateway interrupts in-flight SSE streams; pick a quiet moment.
EOF
}

# --- argument parsing, then either reach the database as the service account or print text ----

CMD="${1:-}"
FILE=""
KEEP="$DEFAULT_KEEP"

case "$CMD" in
    '' | --backup | -b)
        [ "$#" -le 1 ] || die "unexpected arguments after '$CMD' (see --help)"
        CMD=backup
        ;;
    --verify | -V)
        [ "$#" -le 2 ] || die "unexpected arguments after $CMD (see --help)"
        FILE="${2:-}"
        [ -n "$FILE" ] || die "--verify needs a snapshot path (see --help)"
        CMD=verify
        ;;
    --prune | -p)
        [ "$#" -le 2 ] || die "unexpected arguments after $CMD (see --help)"
        KEEP="${2:-$DEFAULT_KEEP}"
        CMD=prune
        ;;
    --restore-hint | -r)
        [ "$#" -le 1 ] || die "unexpected arguments after $CMD (see --help)"
        CMD=restore-hint
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    *)
        usage >&2
        die "unknown argument: $CMD"
        ;;
esac

# Every path is absolute by now, and the service account cannot enter the caller's working
# directory (GNU find warns when it cannot restore it), so stop depending on it.
FILE="$(absolutize "$FILE")"
cd /

# Pure text, no database access, no privilege handling.
if [ "$CMD" = "restore-hint" ]; then
    restore_hint
    exit 0
fi

if [ "$(id -u)" -eq 0 ]; then
    if [ "${ALEX_GATEWAY_BACKUP_DROPPED:-}" = "$SERVICE_USER" ]; then
        die "refusing to continue as root: dropping into '$SERVICE_USER' did not take effect. Run this script as '$SERVICE_USER' directly, or check that 'runuser'/'su' work for that account."
    fi
    require_service_user
    case "$CMD" in
        backup | prune) prepare_backup_dir ;;
    esac
    run_as_service_user "$@"
fi

if [ "$(id -un)" != "$SERVICE_USER" ]; then
    warn "running as '$(id -un)', not root and not the service account '$SERVICE_USER': a created snapshot would carry the wrong ownership. Prefer: sudo $SELF"
fi

case "$CMD" in
    backup) do_backup ;;
    verify) do_verify "$FILE" ;;
    prune) do_prune "$KEEP" ;;
    *)
        die "internal error: unhandled command '$CMD'"
        ;;
esac
