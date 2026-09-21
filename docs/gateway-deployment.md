# Central Alex Gateway — deployment runbook

The **Central Alex Gateway** is the server-side authority for shared-runpod production mode. It
owns the RunPod master credential, the global single-managed-compute rule, the shared balance
snapshot and the model proxy, and it authenticates **installations** (PCs), not local users.

This document is the operator runbook: prerequisites, database, secrets, install, reverse proxy,
day-2 operations, upgrade/rollback and the honest list of what is still missing before real
public use. The contract and the threat model are in
[central-runpod-gateway-audit.md](central-runpod-gateway-audit.md); the local provider behaviour it
reuses is in [runpod-controller.md](runpod-controller.md) and [on-demand-ai.md](on-demand-ai.md).

**The Gateway is deployed.** This runbook was written before the deployment and its generic paths
(Docker, PostgreSQL, other hosts) remain valid, but there is now a **live instance** — see the next
section and [gateway-12testers-deploy-audit.md](gateway-12testers-deploy-audit.md) for the exact
facts, acceptance results, rollback and resource impact.

## Deployed instance (12Testers VPS)

| | |
|---|---|
| Public URL | **`https://gateway.12testers.store`** (Cloudflare proxied, Let's Encrypt via Certbot) |
| Host | the existing 12Testers production VPS (`162.0.216.58`, Ubuntu 24.04, 1 vCPU / 961 MB) — the Gateway runs **beside** the site, in its own directories, user, database and service |
| Release | `/opt/alex-gateway/releases/<sha>` + `current` symlink, venv `/opt/alex-gateway/venv` |
| Service | `alex-gateway.service` (user `alex-gateway`, uid 988), `127.0.0.1:9011`, one worker, `Restart=on-failure` |
| Database | SQLite `/var/lib/alex-gateway/gateway.db` (revision `0001_gateway_core`); `DATABASE_URL` moves it to PostgreSQL unchanged |
| Secrets | `/etc/alex-gateway/alex-gateway.env` (JWT + pod key) and `/etc/alex-gateway/runpod.env` (master RunPod key), both `root:alex-gateway 0640`, loaded with two `EnvironmentFile=` lines |
| Nginx | `/etc/nginx/sites-available/alex-gateway.conf`: `:80` ACME + redirect, `:443` proxy to loopback with SSE-friendly settings; `nginx -t` before every reload |
| Post-DNS script | `/usr/local/sbin/alex-gateway-https.sh` (root, 0750) — idempotent certificate + public vhost |
| Resource guards | `CPUQuota=50%`, `MemoryHigh=280M`, `MemoryMax=384M`; measured RSS 60–76 MB |

Operator commands on the server (run as the service user; the release directory is `root:alex-gateway 0750`,
so the CLI needs the unit's environment):

```bash
sudo -u alex-gateway bash -c 'set -a; . /etc/alex-gateway/alex-gateway.env; set +a; \
  export HOME=/var/lib/alex-gateway PYTHONPATH=/opt/alex-gateway/current/gateway:/opt/alex-gateway/current/backend; \
  export ALEX_BACKEND_LIB_DIR=/opt/alex-gateway/current/backend; cd /opt/alex-gateway/current; \
  /opt/alex-gateway/venv/bin/python -m gateway.cli installations'
# the same prefix runs: create-code --label "PC A", revoke --installation-id <id>, audit, health
```

Day-2 notes specific to this host: certificate renewal is the existing Certbot timer (the ACME path
stays on `:80` behind the proxy); the 12Testers site, service, PM2 app and database are never touched;
the Gateway is the only process allowed to read `/etc/alex-gateway/runpod.env`; a rollback is
`systemctl disable --now alex-gateway` plus removing `/etc/nginx/sites-enabled/alex-gateway.conf`
(`nginx -t` first), and it needs no 12Testers change at all.

## What the Gateway owns

| Concern | Owner | Notes |
|---|---|---|
| RunPod master credential | Gateway environment only | Server-side; never in the client, the image, the DB or a log |
| Global managed compute (at most one Pod) | Gateway (DB lease + committed intent) | `gateway_compute` holds the lease; clients poll it |
| Money ceilings `$1.20/h`, `$3.00/session` | Gateway | A client can only ask for something *stricter* |
| Shared account balance snapshot | Gateway (read-only) | One upstream query serves all installations |
| Production inference endpoint | Gateway proxy | The Pod's llama.cpp port is not reachable directly |
| Installation identity and revocation | Gateway | One-time activation code → installation secret → short-lived JWT |
| Chat history, users, local files, Computer/Web/Tor | **Client installation** | The Gateway stores none of it |

## Trust boundary

```text
client installations (PC A, PC B, ...)         operator (you)
        │  installation secret + short-lived JWT (iss=alex-gateway)
        ▼
Central Alex Gateway ── owns: RunPod master key, global lease, caps, balance, proxy
        │  RUNPOD_API_KEY (environment only)                │  python -m gateway.cli ...
        ▼                                                   ▼
RunPod master account                                Gateway PostgreSQL 16
(Pods, Network Volume)                               installations / lease / audit
```

Properties that must hold after any change:

- A client installation **never** holds the RunPod master key, and no API call lets it reach one.
- Money authority is server-side. The local `Alex LLM/provider/runpod` credential stays a
  dev/private-direct credential and is not migrated, uploaded or used by shared mode.
- The Gateway exposes typed operations only (`/enroll`, `/auth/token`, `/compute/*`, `/balance`,
  `/v1/*`, `/health`). There is no `/runpod/*` or raw provider passthrough.
- Status and balance reads never start, adopt or stop compute and never spend money.
- The Network Volume `uwgeaie5b0` is never deleted by any code path, including this deployment.

## Artifacts in this repository

| File | Purpose |
|---|---|
| `apps/gateway/requirements.txt` | Runtime dependencies of the standalone service (Python 3.12) |
| `apps/gateway/Dockerfile` | `python:3.12-slim` image: gateway package + reused backend provider core |
| `apps/gateway/docker-entrypoint.sh` | Migrate, then `exec` the ASGI server |
| `apps/gateway/docker-compose.example.yml` | **EXAMPLE** gateway + `postgres:16-alpine` stack |
| `apps/gateway/.env.example` | Every configuration variable with placeholder values |
| `apps/gateway/deploy/nginx.example.conf` | **EXAMPLE** TLS termination, SSE-friendly proxying, `/enroll` rate limiting |
| `apps/gateway/deploy/logrotate.example.conf` | **EXAMPLE** rotation (or the journald/Docker alternative) |
| `docs/gateway-deployment.md` | This runbook |

## Prerequisites

### Host sizing

The Gateway is a **control plane**: it never loads the model. The ~20 GB GGUF stays on the RunPod
Network Volume, and inference runs on the Pod.

| Resource | Minimum | Recommended | Why |
|---|---|---|---|
| vCPU | 1 | 2 | Serialized control-plane work; one inference stream is proxied, not computed |
| RAM | 1 GB | 2 GB | PostgreSQL (~256–512 MB) + uvicorn (~150 MB) + proxy |
| Disk | 20 GB | 40 GB | OS + Postgres + logs + dumps; images are small (`python:3.12-slim`) |
| Network in | TCP 443 | TCP 443 | HTTPS only; no other inbound port |
| Network out | TCP 443 | TCP 443 | `api.runpod.io` (provider API) and `*.proxy.runpod.net` (the Pod it proxies to) |

Sizing is a control-plane estimate for the v1 workload (a handful of installations, one serialized
generation at a time). No load test has been run; a public rollout with many installations should
re-measure before scaling the host.

### Software

- PostgreSQL 16 (own role, own database — **never** the local Alex SQLite database).
- Python 3.12 for the bare-metal path, or Docker/compose for the container path.
- A reverse proxy with a TLS certificate (Nginx or Caddy). TLS terminates there.
- A DNS record for the hostname your users will configure, for example
  `gateway.example.com  A  203.0.113.10` (the address is the documentation range; replace it).

The desktop client **refuses plaintext HTTP** for any host that is not loopback. A Gateway reached
over `http://` from another machine will simply not be used, and publishing port 9000 to the
internet would additionally expose enrollment and the model proxy in the clear. TLS is a
requirement, not a hardening step.

## Container / filesystem layout

The Gateway **reuses the local backend's provider core** instead of copying it (`RunPodAPI`, the
Pod/volume schemas and the Pod bootstrap script). The image therefore contains two packages:

| Path in the image | Contents |
|---|---|
| `/srv/alex/gateway` | This service: `gateway/` package, `alembic.ini`, `alembic/`, the entrypoint |
| `/srv/alex/backend/app` | The reused backend package (`app.compute.*`) |
| `PYTHONPATH=/srv/alex/gateway:/srv/alex/backend` | Makes both importable |
| `ALEX_BACKEND_LIB_DIR=/srv/alex/backend` | Directory the Gateway adds to `sys.path` itself |

A bare-metal install mirrors the same layout (`/srv/alex/gateway`, `/srv/alex/backend/app`) so the
two paths stay comparable. The Gateway's database schema and Alembic history are its **own**
(`apps/gateway/alembic`), never the local backend's.

Build from the repository root (the build context is the whole repo, because the image needs both
packages):

```bash
docker build -f apps/gateway/Dockerfile -t alex-gateway:0.9.3 .
```

The Dockerfile contains no secret and refuses to build if `apps/gateway/.env` is inside the build
context. Add a repository-root `.dockerignore` (`**/.env`, `**/.venv`, `**/__pycache__`,
`**/.pytest_cache`, `**/*.db`, `apps/desktop/target`) so local state never reaches a layer.

## Database setup

PostgreSQL 16, one role, one database, one password that exists only in the Gateway environment.

```sql
-- as a superuser, e.g.  sudo -u postgres psql
CREATE ROLE alex_gateway LOGIN PASSWORD 'change-me-strong-db-password';
CREATE DATABASE alex_gateway OWNER alex_gateway;
REVOKE ALL ON DATABASE alex_gateway FROM PUBLIC;
\c alex_gateway
REVOKE ALL ON SCHEMA public FROM PUBLIC;
```

Then restrict access in `pg_hba.conf` to the hosts that need it (loopback for bare metal, the
compose network for containers) with `scram-sha-256`, for example:

```text
host    alex_gateway    alex_gateway    127.0.0.1/32    scram-sha-256
```

Configuration (see `apps/gateway/.env.example` for every variable and its default):

```dotenv
DATABASE_URL=postgresql+psycopg://alex_gateway:change-me-strong-db-password@localhost:5432/alex_gateway
```

The `+psycopg` suffix selects the psycopg 3 driver in `requirements.txt`; a bare
`postgresql://` URL would try to load psycopg2 and fail at startup. Development and tests may use
`sqlite:///./gateway.db`.

### Migrations

The Gateway keeps its own history and must be migrated **before** the service starts:

```bash
alembic -c /srv/alex/gateway/alembic.ini upgrade head
```

Both provided paths do this for you: the container entrypoint runs it before `exec uvicorn`, and
the systemd example runs it in `ExecStartPre`. The container entrypoint skips it when
`ALEMBIC_SKIP=1` is set (local runs) or when the command is not the ASGI server — for example
`docker run ... python -m gateway.cli installations` — so an operator inspection cannot be
misreported as a migration failure.

v1 migrations are **additive**. They add tables and nullable columns and do not rewrite existing
rows, which is what makes the rollback path in this runbook safe.

### What the database holds — and what it must never hold

| Holds | Never holds |
|---|---|
| `installations` (id, hashed installation secret, label, platform, `last_seen_at`, revocation) | Installation secrets in plaintext (digest only) |
| `enrollment_codes` (digest, label, TTL, redemption) | Activation codes in plaintext (digest only) |
| `gateway_compute` (global lease, state, revision) | Prompts, completions or chat history |
| `gateway_sessions` (Pod ownership, caps, timings, cost estimate) | The RunPod master key or any provider credential |
| `gateway_operations` (idempotency records) | Local Alex users, sessions or documents |
| `audit_events` (operation, result, request id, cost, error code) | File contents, RAG data or memory |

Losing this database does **not** lose user chats, files or memory — those live on the client
installations. It does lose installation enrollments (every PC must be re-enrolled with a new
activation code) and the record of which Pod the Gateway owns, so after such a loss reconcile with
RunPod and stop any Pod that is still running before re-enrolling clients.

### Backup and restore

`pg_dump` is the backup; a Docker named volume is not. Roles live in the cluster, not in a database
dump, so capture globals separately.

```bash
# daily, from cron or a systemd timer, off-host
pg_dump -Fc -U alex_gateway -h 127.0.0.1 alex_gateway > /var/backups/alex-gateway/gateway-2026-09-21.dump
pg_dumpall --globals-only -U postgres -h 127.0.0.1 > /var/backups/alex-gateway/globals-2026-09-21.sql
```

Retention: keep 7 daily, 4 weekly and 3 monthly dumps, and store them on a different host or
object store than the Gateway itself. Compose deployments use the same commands, executed in the
`postgres` container and written to a mounted directory.

A restore drill is part of the acceptance, not an optional extra:

```bash
createdb -U postgres alex_gateway_restore
pg_restore -U postgres -d alex_gateway_restore gateway-2026-09-21.dump
psql -U postgres -d alex_gateway_restore -c 'select count(*) from installations;'
psql -U postgres -d alex_gateway_restore -c 'select max(created_at) from audit_events;'
dropdb -U postgres alex_gateway_restore
```

Run the dump on a schedule, verify it is non-empty, and repeat the drill after any schema change.
The Gateway must be able to start against the restored database; the audit table's newest row is a
useful proxy for "the restore is recent enough".

## Secret management

Two secrets matter. Neither belongs in the image, in Git, in the database, in a client or in a log.

| Secret | Where it comes from | Where it lives | Rotation |
|---|---|---|---|
| `JWT_SECRET` | `python -c "import secrets;print(secrets.token_urlsafe(48))"` | Environment of the service; ≥32 chars in dev, ≥48 in `APP_ENV=production` | Cheap — see below |
| `RUNPOD_API_KEY` | The RunPod account (master key) | Environment of the service; a secret manager if available | Provider-side rotation; brief outage while both sides change |
| `POSTGRES_PASSWORD` | Your password manager | PostgreSQL role + compose interpolation | `ALTER ROLE ... PASSWORD` then update the environment |

Delivery options, strongest first:

1. A secret manager that injects environment variables at unit/container start.
2. An environment file readable only by root and the service account:
   `install -m 600 -o root -g root apps/gateway/.env.example /etc/alex-gateway/gateway.env`.
   systemd reads `EnvironmentFile=` as the service manager before dropping privileges, so
   `root:root 0600` works together with a non-root `User=`.
3. For the compose example: `apps/gateway/.env` with `chmod 600`, referenced by `env_file:`.

Rules that do not bend:

- `RUNPOD_API_KEY` is the **master account credential**. Distribution is server-side only; it must
  never be shown in the UI, returned by an endpoint, written to a log, committed, baked into an
  image layer (the Dockerfile refuses a stray `.env`) or copied to a client PC.
- Never put a real value in `apps/gateway/.env.example`. The placeholder `JWT_SECRET` is 40
  characters on purpose: `APP_ENV=production` refuses to start until you generate a real one.
- `.env` files are for development and compose. On a real host, prefer the service manager or a
  secret store, and never a shell history line like `RUNPOD_API_KEY=... uvicorn ...`.

## Install and run

### Path A — Docker / compose (local production-like acceptance)

```bash
cd apps/gateway
cp .env.example .env
chmod 600 .env
# edit .env: JWT_SECRET, RUNPOD_API_KEY, POSTGRES_PASSWORD (keep it equal to DATABASE_URL's password)

cd ../..
docker build -f apps/gateway/Dockerfile -t alex-gateway:0.9.3 .

cd apps/gateway
docker compose -f docker-compose.example.yml up -d
docker compose -f docker-compose.example.yml ps
```

What the example stack does: `postgres:16-alpine` with a named volume and `pg_isready`
healthcheck; the gateway waits for `condition: service_healthy`; the gateway publishes
`127.0.0.1:9000` **only**; both services restart `unless-stopped`; container logs rotate through
the `json-file` driver. The reverse-proxy service is deliberately commented out in that file —
read the note in it before enabling.

Verify:

```bash
curl -sS http://127.0.0.1:9000/health
docker compose -f docker-compose.example.yml logs --tail=50 gateway
```

### Path B — bare metal with systemd

Layout used by the example unit: code in `/srv/alex`, virtualenv in `/srv/alex/venv`, environment
file in `/etc/alex-gateway/gateway.env`, logs through journald.

```bash
sudo install -d -o alex -g alex /srv/alex /srv/alex/gateway /srv/alex/backend
sudo -u alex python3.12 -m venv /srv/alex/venv
sudo -u alex /srv/alex/venv/bin/pip install -r /srv/src/alex-llm/apps/gateway/requirements.txt

# deploy the two packages (same layout as the image)
sudo rsync -a --delete --exclude '.venv' --exclude '__pycache__' --exclude '.env' \
    /srv/src/alex-llm/apps/gateway/  /srv/alex/gateway/
sudo rsync -a --delete --exclude '__pycache__' \
    /srv/src/alex-llm/apps/backend/app/  /srv/alex/backend/app/

sudo install -d -m 750 /etc/alex-gateway
sudo install -m 600 -o root -g root /srv/src/alex-llm/apps/gateway/.env.example \
    /etc/alex-gateway/gateway.env
sudoedit /etc/alex-gateway/gateway.env    # JWT_SECRET, RUNPOD_API_KEY, DATABASE_URL
```

`/etc/systemd/system/alex-gateway.service`:

```ini
[Unit]
Description=Alex LLM Central Gateway
Documentation=file:/srv/src/alex-llm/docs/gateway-deployment.md
After=network-online.target postgresql.service
# `After=postgresql.service` assumes a local PostgreSQL. When the database is on another host,
# drop that dependency (and start the unit after network-online only).
Wants=network-online.target

[Service]
Type=exec
User=alex
Group=alex
WorkingDirectory=/srv/alex/gateway
EnvironmentFile=/etc/alex-gateway/gateway.env
Environment=PYTHONPATH=/srv/alex/gateway:/srv/alex/backend
Environment=ALEX_BACKEND_LIB_DIR=/srv/alex/backend
Environment=PYTHONDONTWRITEBYTECODE=1

# Migrate before serving. `alembic upgrade head` is a no-op when the database is current, so it
# is safe on every restart, and a migration failure stops the unit instead of running old code
# against a newer schema.
ExecStartPre=/srv/alex/venv/bin/alembic -c /srv/alex/gateway/alembic.ini upgrade head

# Loopback only: the reverse proxy terminates TLS and nothing else may reach this port.
# Exactly one worker: the rate limiter is in-process and the database lease is the real authority.
ExecStart=/srv/alex/venv/bin/uvicorn gateway.main:app --host 127.0.0.1 --port 9000 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1

Restart=on-failure
RestartSec=3
TimeoutStopSec=30

NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UMask=0077

# Add `ReadWritePaths=/var/log/alex-gateway` only if you switch to file logging (see
# deploy/logrotate.example.conf); with journald the service needs no write access at all, and a
# ReadWritePaths entry pointing at a directory that does not exist makes the unit fail to start.

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now alex-gateway
systemctl status alex-gateway
journalctl -u alex-gateway -n 50 --no-pager
```

The proxy headers are trusted **only** because the proxy is on loopback. A container deployment
reaches the service across the compose network instead, and v1 makes no authorization decision
from a client IP, so the compose CMD does not enable `--proxy-headers`.

### Configuration notes

The complete variable list, defaults and comments are in `apps/gateway/.env.example`; it mirrors
`apps/gateway/gateway/config.py` field for field. Operationally important ones:

| Variable | Effect |
|---|---|
| `APP_ENV` | `production` enables the stricter validation (48+ char `JWT_SECRET`) |
| `DATABASE_URL` | Must include the `+psycopg` driver suffix |
| `JWT_SECRET`, `JWT_EXPIRE_MINUTES` | Installation token signing key and lifetime (default 15 minutes) |
| `ACTIVATION_TTL_MINUTES` | Validity of an activation code (default 60) |
| `ENROLLMENT_RATE_LIMIT` / `TOKEN_RATE_LIMIT` / `COMPUTE_RATE_LIMIT` / `INFERENCE_RATE_LIMIT` | Per-key sliding-window limits inside `RATE_WINDOW_SECONDS` |
| `RUNPOD_API_KEY` | Master credential (server-side only); empty means `not_configured` |
| `RUNPOD_NETWORK_VOLUME_ID`, `RUNPOD_DATACENTER` | Where the model lives; the volume is never deleted |
| `LLM_PROVIDER`, `LLM_MODEL` | `llamacpp` + the product alias in production; `mock` is a dev path and must never be presented as real AI |
| `MAX_HOURLY_PRICE`, `MAX_SESSION_BUDGET` | Product ceilings `1.20` / `3.00`; anything higher makes startup fail |
| `COMPUTE_IDLE_MINUTES` | Idle timeout applied to the global managed compute |
| `INFERENCE_QUEUE_SIZE`, `INFERENCE_TIMEOUT_SECONDS` | Serialization (`--parallel 1`) and the total request budget |

A knob that does not exist is not silently invented by this stack: the Gateway runs **one**
process. If you need more throughput, the fix is a shared limiter and a shared compute lease
design — not `--workers N`, which would make the in-process rate limiter count each worker
separately.

## Reverse proxy and TLS

The Gateway speaks plaintext HTTP on loopback/private addresses; the proxy terminates TLS and is the
only thing exposed to the internet.

1. Obtain a certificate for your hostname (ACME/`certbot`), and confirm **automatic renewal** runs
   and is monitored — nothing in this repository renews certificates.
2. Install `apps/gateway/deploy/nginx.example.conf` as a starting point. It contains: an HTTP server
   that only serves the ACME challenge and redirects to HTTPS; a TLS server; `proxy_pass
   http://127.0.0.1:9000`; per-IP `limit_req` zones; and SSE-friendly settings
   (`proxy_http_version 1.1`, empty `Connection`, `proxy_buffering off`, `gzip off`,
   `proxy_read_timeout 900s` matching `INFERENCE_TIMEOUT_SECONDS`).
3. Rate limit `/enroll` at the proxy **in addition** to the Gateway's own limit. `/enroll` and
   `/auth/token` are the only routes reachable without a token, and an activation code is the one
   value an attacker can guess; proxy-side limiting also survives a Gateway restart. Nginx does
   this natively; Caddy needs an extra module.
4. Keep request bodies small (the example uses `client_max_body_size 2m`) and never log headers or
   bodies: `Authorization: Bearer …` and prompts must not end up in an access log, and the model
   proxy streams SSE so a buffering proxy would break token-by-token delivery.

Why not expose `9000` directly: enrollment transmits a one-time code and then an installation
secret, inference carries user prompts, and the client refuses non-loopback plaintext HTTP. There
is no configuration in which the Gateway itself should be publicly reachable over HTTP.

## Operations

### Enroll a new installation

```bash
# bare metal — same environment as the service, without editing the database by hand
sudo systemd-run --wait --pipe \
    -p User=alex \
    -p EnvironmentFile=/etc/alex-gateway/gateway.env \
    -p WorkingDirectory=/srv/alex/gateway \
    -p Environment=PYTHONPATH=/srv/alex/gateway:/srv/alex/backend \
    -p Environment=ALEX_BACKEND_LIB_DIR=/srv/alex/backend \
    /srv/alex/venv/bin/python -m gateway.cli create-code --label "PC A"

# docker / compose
docker compose -f docker-compose.example.yml exec gateway \
    python -m gateway.cli create-code --label "PC A"
```

The CLI prints the activation code **once**; the server stores only its digest, so a lost code is
replaced by generating a new one. Deliver it to the user through a channel you trust, tell them it
expires after `ACTIVATION_TTL_MINUTES` and works once, and never send it together with anything
else sensitive. The user enters it on the client; the client exchanges it at `/enroll` for an
installation id and secret, stores the secret in the OS credential store of that PC, and uses it
to obtain short-lived tokens afterwards. The `--label` is free text used to identify the PC in
`installations`.

### List and revoke installations

```bash
# replace the path/prefix per the section above
… python -m gateway.cli installations
… python -m gateway.cli revoke --installation-id 00000000-0000-0000-0000-000000000000
```

- `installations` shows id, label, platform, client version, `last_seen_at` and revocation state.
  An installation you do not recognize is a security event: revoke it and investigate.
- `revoke` stops that installation from obtaining new tokens (`installation_revoked`). Access
  tokens already issued remain valid until they expire — at most `JWT_EXPIRE_MINUTES` (15 by
  default), which is why the lifetime is deliberately short.
- Revocation does not by itself change the global compute state. Check `/compute/status`, and stop
  the managed compute explicitly if the revoked installation started the Pod you no longer want
  running.

### Rotate `JWT_SECRET`

```bash
python -c "import secrets;print(secrets.token_urlsafe(48))"
# write it into the environment file (never into the database), then:
docker compose -f docker-compose.example.yml up -d --force-recreate gateway   # or systemctl restart alex-gateway
```

Because installation tokens are short-lived, rotation is cheap: every outstanding token is
rejected within `JWT_EXPIRE_MINUTES`, and clients re-authenticate with the installation secret they
already hold. No client re-enrollment is required, and no user action is required unless the
client was offline for longer than the token lifetime. `RUNPOD_API_KEY` rotation is a provider-side
operation and costs a short outage; never distribute the new key to installations.

### Health

```bash
curl -sS https://gateway.example.com/health
```

| Field | Meaning |
|---|---|
| `product` | `alex-llm-gateway` |
| `version` | Service version, e.g. `0.9.3` |
| `gateway_protocol_version` | Client/server contract version; a mismatch is reported to the client as `gateway_protocol_mismatch` |
| `ready` | Whether the service can serve installation traffic |
| `database` | Database reachability state |

`/health` is public, cheap and secret-free, never starts or stops compute, and is the correct probe
for both Docker and an external uptime monitor. "Configured" is not "healthy": an empty
`RUNPOD_API_KEY` still yields a running Gateway, so treat provider readiness as a separate signal.

### Logs and rotation

| Path | Where |
|---|---|
| Bare metal | `journalctl -u alex-gateway` (stdout/stderr through journald) |
| Docker | `docker compose -f docker-compose.example.yml logs -f gateway` (rotated by the `json-file` driver) |
| Reverse proxy | `/var/log/nginx/alex-gateway.access.log`, `…error.log` |
| Audit trail | `audit_events` in PostgreSQL (operation, result, request id, cost, error code) |

The service redacts credentials and never logs prompts or completions. `deploy/logrotate.example.conf`
covers the file-based case (daily, compressed, 30 files, **no `copytruncate`** because truncating a
file that a live process holds open can drop the tail of the line being written) and notes that
journald or the Docker log driver should own rotation instead. Logs must never contain credentials,
tokens, connection strings or prompt text; if one leaks, treat it as a leak and rotate.

### What to alert on

| Signal | Threshold | Response |
|---|---|---|
| `/health` non-200 or `ready=false` | 2 consecutive probes | Service down or not ready; check unit/container status |
| `/health` with `database` unhealthy | 1 probe | PostgreSQL down, credentials changed or disk full |
| Proxy returns 502/504 for `/v1/*` | Any occurrence in a window | Gateway down or inference timing out; check `INFERENCE_*` values |
| `gateway_queue_full` in audit events | Sustained | More demand than one serialized model; not a crash |
| `compute_unknown` or `multiple_compute` | Any occurrence | Money risk: inspect RunPod immediately, do not auto-retry |
| Unexpected Pods on the account | Daily check | Provider-side drift; reconcile and stop what the Gateway does not own |
| Unit/container restart count | >3 per hour | Crash loop; read the log tail before restarting again |
| Postgres disk usage / dump age | >80% used, dump older than 26 h | Backup path is broken |
| Certificate expiry | <14 days | Renewal path is broken; renew manually |
| Balance low | Operator's threshold | Shared account funding, not a service fault |
| New `installations` row you did not create | Any occurrence | Revoke and investigate |

## Upgrade and rollback

Upgrade:

1. Build the new tag from a reviewed commit: `docker build -f apps/gateway/Dockerfile -t alex-gateway:0.9.4 .` (or `rsync` the new packages for bare metal).
2. Take a database dump (`pg_dump -Fc`) and confirm it is non-empty.
3. Apply migrations: the container entrypoint does it, or run
   `alembic -c /srv/alex/gateway/alembic.ini upgrade head` explicitly.
4. Restart the service, then verify `/health` and one real inference request end to end.
5. Keep the previous image (or the previous release directory) until the new one has served
   traffic successfully, and record the tag in your change log.

Rollback:

- v1 migrations are additive, so the previous image keeps working against the upgraded schema:
  start `alex-gateway:0.9.3` again (compose: change the tag and `up -d`; bare metal: re-`rsync`
  the previous release).
- Do **not** run `alembic downgrade` unless the migration in question documents it. Downgrade
  paths are not exercised in v1; if a real schema rollback is ever needed, rebuild a fresh
  database and re-enroll installations with new activation codes.
- Roll back immediately if `/health` is not ready, if enrollment fails for a new client, or if
  inference returns provider errors that did not occur before the upgrade.

## What is still missing for a real public deployment

- **No central Alex account or licensing system.** Identity is a one-time activation code that a
  human operator generates and delivers; there is no self-service signup, no billing relationship
  and no organization/tenant model.
- **No multi-worker or multi-node deployment.** The rate limiter is in-process, so the supported
  topology is one process; the global compute lease is database-backed and is the only authority
  that keeps the "at most one managed Pod" rule true. There is no shared cache, no queue broker and
  no leader election beyond that lease.
- **No automated certificate renewal** is configured by these artifacts. Obtain a certificate,
  verify the renewal timer and monitor expiry yourself.
- **No mTLS.** An installation authenticates with a bearer secret plus a short-lived JWT over TLS;
  the installation secret is not bound to a device key or client certificate.
- **No per-installation spend report** beyond the `audit_events` table (raw rows; no dashboards,
  quotas per installation or monthly statements). Budgets remain polling safeguards rather than
  prepaid provider caps, exactly as described in [runpod-controller.md](runpod-controller.md).
- **No metrics endpoint, tracing or log aggregation** is shipped; alerting is built from `/health`,
  the audit table, host metrics and the proxy log.
- **No scheduled backup of the Gateway database.** A snapshot tool now exists
  (`apps/gateway/deploy/alex-gateway-backup.sh`, installed at
  `/usr/local/sbin/alex-gateway-backup.sh`, snapshots under
  `/var/lib/alex-gateway/backups/`, retention 5, verified with `PRAGMA integrity_check` and a
  table check) but it is **run by the operator before a migration or release switch**, not by
  a timer; see [alex-gateway-backup.md](../apps/gateway/deploy/alex-gateway-backup.md). The
  Gateway stores no chats, prompts or documents, so its backup is small and recovery is
  cheap — the trade-off is a manual step, documented as such.
- **No public deployment was performed when this runbook was written.** It has since been
deployed (see «Deployed instance» above and the deploy audit): the reachable instance is
the 12Testers VPS one, and the remaining gaps are listed in that audit (no backup automation,
no metrics, the compute path unproven against a real Pod).
- **No production code signing and no installer integration** for the client side of enrollment;
  the enclosing 0.9.3 limitations (WM-07, CD-08, restart with a live Pod) are unchanged.

## Verification checklist before announcing the Gateway

State on the deployed 12Testers instance (21 Sep 2026, per the deploy audit):

- [x] Public `https://gateway.12testers.store/health` returns 200 with `ready=true`,
      `database=ok`, `provider_configured=true` and no secret in the payload.
- [ ] Port 9000 is not reachable from outside the host; PostgreSQL is not reachable from outside.
      (On this host the Gateway port is loopback `127.0.0.1:9011` and the checks below apply to the
      documented PostgreSQL path instead; UFW is unchanged.)
- [x] TLS certificate is valid for the hostname (Let's Encrypt, 89 days) and the existing Certbot
      timer handles renewal; no external monitoring is wired up yet.
- [x] `JWT_SECRET` is server-generated; no placeholder value remains in the environment file.
- [x] `RUNPOD_API_KEY` exists only in the service environment
      (`/etc/alex-gateway/runpod.env`); verified absent from the repository, the release archive,
      the database, the journal and the proxy logs (positive controls included).
- [ ] Environment file is mode `0600`, owned root … — on this host the files are `0640
      root:alex-gateway`, which is the minimum the service needs and is readable only by the
      service user.
- [~] First real enrollment completed end to end on a real PC: activation code → `/enroll` → token
      → balance. The final step of the chain — **one chat completion through the deployed proxy** —
      has not been exercised, because it requires starting a real Pod (deliberately out of scope).
- [x] Revocation tested (both throwaway installations and the operator's own disconnect): the client
      is refused and re-enrollable only with a new code.
- [ ] Money rules verified against a real Pod: the `$1.20/h` and `$3.00/session` ceilings are proven
      by the FakeRunPod suite only; no Pod has been created through the deployed Gateway.
- [x] `installations` contains exactly the expected PC (one live installation; every throwaway one
      is revoked).
- [ ] Backup ran, the dump is non-empty, and a restore into a scratch database succeeded.
- [x] Log inspection: no `Authorization` header value, connection string or provider key appears in
      journald or the proxy log.
- [ ] Rotation is in place for whichever log path you use; disk usage alarms are configured.
- [ ] Alerting configured for the signals in the table above and tested by stopping the service
      once.
- [x] RunPod account shows no unexpected Pods; the Network Volume `uwgeaie5b0` is intact.
- [~] Rollback rehearsed: the release, database and environment files stay in place and the
      documented `systemctl disable --now` path was never needed; it has not been executed on this
      host.
- [ ] Users were told what to expect: the Gateway is required for AI features, local
      Computer/Web/Tor/Memory/data keep working while it is down, and their chats never leave their PC.

## Related documents

- [central-runpod-gateway-audit.md](central-runpod-gateway-audit.md) — trust boundary, contract, threats
- [on-demand-ai.md](on-demand-ai.md) — Pod lifecycle, budgets, Network Volume rules
- [runpod-controller.md](runpod-controller.md) — provider behaviour, readiness markers, limits
- [security.md](security.md) — repository-wide security rules
- [AI-HANDOFF.md](AI-HANDOFF.md) — build, test and packaging commands
