#!/bin/sh
# Central Alex Gateway container entrypoint.
#
# Order of work:
#   1. apply the Gateway's OWN Alembic history (apps/gateway/alembic, never the local backend's)
#   2. exec the ASGI server as PID 1 so Docker signals (SIGTERM on stop) reach uvicorn directly
#
# Environment knobs (documented in docs/gateway-deployment.md):
#   ALEMBIC_SKIP=1   skip step 1 (local runs, or a setup where migrations are rolled out
#                    separately). Never set it in production.
#
# This script never prints environment values: `gateway.env` contains JWT_SECRET and the
# RunPod master key, and DATABASE_URL contains the database password. Only the ini path and
# the phase of the startup are logged.

set -eu

log() {
    printf '%s %s\n' '[gateway-entrypoint]' "$*" >&2
}

# Directory of this script. In the production image the script lives at /srv/alex/gateway,
# which is also the working directory the Gateway expects (its own `alembic.ini`, the gateway
# package, and the optional development `.env` are all resolved relative to it).
GATEWAY_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ALEMBIC_INI="${GATEWAY_DIR}/alembic.ini"

# Default command when the image is started without one (`docker run --entrypoint ...`).
if [ "$#" -eq 0 ]; then
    set -- uvicorn gateway.main:app --host 0.0.0.0 --port 9000 --workers 1
fi

run_migrations=true
case "${ALEMBIC_SKIP:-}" in
    ''|0|false|no|NO|False) run_migrations=true ;;
    *) run_migrations=false ;;
esac

# Operator CLI invocations (`python -m gateway.cli installations`) must not migrate: they are
# meant to inspect or repair an existing database, and a failed migration would hide the
# command's real error behind an Alembic traceback.
case "${1}" in
    uvicorn|*/uvicorn) ;;
    *)
        log "first argument is '${1}', not the ASGI server: skipping migrations"
        run_migrations=false
        ;;
esac

if [ "${run_migrations}" = true ]; then
    if [ ! -f "${ALEMBIC_INI}" ]; then
        log "ERROR: ${ALEMBIC_INI} is missing."
        log "The Gateway keeps its own Alembic history (apps/gateway/alembic + alembic.ini);"
        log "without it the schema cannot be upgraded. Set ALEMBIC_SKIP=1 to start anyway."
        exit 1
    fi
    cd "${GATEWAY_DIR}"
    log "applying migrations: alembic -c ${ALEMBIC_INI} upgrade head"
    alembic -c "${ALEMBIC_INI}" upgrade head
    log 'migrations applied'
else
    log 'migrations skipped'
fi

# Only the program name is logged: an operator's own command line is not echoed back, so a value
# passed on the command line can never be copied into the container log by this script.
log "starting: ${1}"
exec "$@"
