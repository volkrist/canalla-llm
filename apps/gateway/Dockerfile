# Central Alex Gateway — production image (Alex LLM 0.9.3).
#
# Build from the REPOSITORY ROOT, because this image intentionally contains two packages:
#
#   /srv/alex/gateway        the Gateway service (this directory)
#   /srv/alex/backend/app    the REUSED local-backend provider core (RunPodAPI, pod/volume
#                            schemas, the Pod bootstrap script). The Gateway imports it
#                            instead of forking it, so both stay in one implementation.
#
#   docker build -f apps/gateway/Dockerfile -t alex-gateway:0.9.3 .
#
# .dockerignore — this repository has no committed .dockerignore yet, and the build context is
# the whole repo. Before building on a developer machine, make sure the context excludes local
# state so it can never reach a layer:
#
#   **/.env
#   **/.venv
#   **/__pycache__
#   **/.pytest_cache
#   **/*.db
#   **/*.db-wal
#   **/*.db-shm
#   apps/desktop/target
#
# The build additionally REFUSES to continue if `apps/gateway/.env` is present, and removes
# caches/local database files that still made it into the context. The image must never carry
# the RunPod master key, the JWT signing key or a database password: those arrive as runtime
# environment variables from the service manager (see docs/gateway-deployment.md).

FROM python:3.12-slim

# Deterministic, quiet, and no runtime bytecode: the application user cannot write to
# /srv/alex anyway. The base image is not digest-pinned here because the repo does not track
# upstream digests; pin `python:3.12-slim@sha256:...` in a fork if your policy requires it.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# Unprivileged runtime account. No login shell, no home directory, no write access to the
# code: the container only ever needs to read its own files and open a database connection.
RUN groupadd --system --gid 10001 alex \
 && useradd --system --uid 10001 --gid alex --no-create-home --home-dir /srv/alex \
            --shell /usr/sbin/nologin alex

# Container layout, mirrored from a bare-metal install so the two paths stay comparable:
#   gateway package   /srv/alex/gateway          -> `import gateway.*`
#   backend package   /srv/alex/backend/app      -> `import app.compute.*`
# PYTHONPATH makes both importable; ALEX_BACKEND_LIB_DIR is the same directory the Gateway
# adds to sys.path itself, so the two mechanisms agree instead of fighting.
ENV PYTHONPATH=/srv/alex/gateway:/srv/alex/backend \
    ALEX_BACKEND_LIB_DIR=/srv/alex/backend

WORKDIR /srv/alex/gateway

# Dependencies first: this layer stays cached while the Gateway code changes.
# No compiler is needed or left behind — psycopg[binary] ships manylinux wheels and every
# other dependency is pure Python. If a future dependency needs a toolchain, add a multi-stage
# build (builder stage with build-essential) rather than installing gcc into this image.
COPY apps/gateway/requirements.txt /srv/alex/gateway/requirements.txt
RUN pip install --no-cache-dir -r /srv/alex/gateway/requirements.txt

# Application code.
COPY apps/gateway/ /srv/alex/gateway/
COPY apps/backend/app/ /srv/alex/backend/app/

# Line-ending guard. The repository standardizes on LF (.gitattributes: `* text=auto eol=lf`).
# A CRLF checkout turns the shebang into `#!/bin/sh\r`, and the container then dies at startup with
# "exec /srv/alex/gateway/docker-entrypoint.sh: no such file or directory" instead of failing here.
RUN python -c "import pathlib,sys; d=pathlib.Path('/srv/alex/gateway/docker-entrypoint.sh').read_bytes(); sys.exit(0 if d.startswith(b'#!/bin/sh\n') and b'\r\n' not in d else 1)" \
 || { echo "ERROR: docker-entrypoint.sh has CRLF line endings or a non-POSIX shebang." >&2; \
      echo "ERROR: this repository keeps LF for all text files (.gitattributes)." >&2; \
      echo "ERROR: fix the checkout and rebuild, e.g. dos2unix apps/gateway/docker-entrypoint.sh" >&2; \
      exit 1; }

# Secret/state guard. The build context is the repository root, so a developer's local
# `apps/gateway/.env` (JWT_SECRET, RUNPOD_API_KEY) or a stray SQLite file would otherwise be
# baked into a layer that is impossible to un-publish. Fail loudly instead of shipping it.
RUN if [ -f /srv/alex/gateway/.env ]; then \
        echo "REFUSING TO BUILD: apps/gateway/.env is inside the build context." >&2; \
        echo "Add '**/.env' to a repository-root .dockerignore and rebuild." >&2; \
        exit 1; \
    fi \
 && rm -rf /srv/alex/gateway/.env \
           /srv/alex/gateway/gateway.db \
           /srv/alex/gateway/gateway.db-wal \
           /srv/alex/gateway/gateway.db-shm \
           /srv/alex/gateway/.pytest_cache \
           /srv/alex/gateway/.venv \
 && find /srv/alex/gateway /srv/alex/backend -type d -name __pycache__ -prune -exec rm -rf {} + \
 && chmod 0755 /srv/alex/gateway/docker-entrypoint.sh

# The copy above brings the Gateway's OWN Alembic history (apps/gateway/alembic.ini +
# apps/gateway/alembic/) — never the local backend's. The entrypoint applies it before uvicorn
# starts. New revisions are created in a developer checkout: inside the image these files are
# root-owned and read-only for the application user.

# The Gateway listens on plaintext HTTP inside the container network; TLS terminates at the
# reverse proxy in front of it. Publishing this port beyond loopback would expose enrollment and
# the model proxy in the clear — see the compose example and docs/gateway-deployment.md.
EXPOSE 9000

# Public, secret-free probe. It reports product/version/protocol/ready/database and never
# starts, adopts or stops compute. Implemented with urllib because slim images have no curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9000/health', timeout=3).status == 200 else 1)"]

USER 10001:10001

ENTRYPOINT ["/srv/alex/gateway/docker-entrypoint.sh"]

# Exactly ONE process. The sliding-window rate limiter and the compute lease are in-process
# assumptions; the database lease is the real authority that keeps a global single-managed-
# compute rule true, and `--workers 1` keeps counters accurate without a shared store.
# Multi-worker/multi-node deployment is explicitly out of scope for v1.
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "1"]
