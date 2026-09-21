# Alex Cloud Gateway — production deployment audit (12Testers VPS)

Date: 21 September 2026. Slicе: `feat/central-runpod-gateway`, release HEAD
`e0987a9d3ff0df07dd6aa9828a15ed05ec59b0e3` (branch HEAD after the production-default fix).

This document records the read-only baseline, the production identification evidence, the
deployment layout and the acceptance results. It contains **no secrets**: the RunPod master
credential is described only by location, ownership and mode, never by value.

## 1. Production identification (evidence, not notes)

Old notes mention `162.0.216.58` for more than one context, so production was confirmed from
the live environment before any change:

| Evidence | Result |
|---|---|
| `~/.ssh/config` | `Host distance` → `162.0.216.58`, user `alex`, key `id_12testers` |
| Ansible inventory on the VPS (`/home/alex/ansible-12testers/inventory.ini`) | `[production] 12testers ansible_host=162.0.216.58`, `ansible_user=alex` |
| Live filesystem | `/var/www/12-testers`, `/var/www/12-testers/backend`, `/var/www/12-testers/frontend` |
| Live nginx | site `12-testers` enabled, `server_name 12testers.store www.12testers.store`, TLS via Let's Encrypt (`/etc/letsencrypt/live/12testers.store/`), Certbot-managed |
| Live services | `12-testers-backend.service` active, PM2 `12-testers-frontend` online |
| Public endpoints | `https://12testers.store/` → 200, `https://12testers.store/api/v1/health` → 200 |
| DNS | `12testers.store` resolves to Cloudflare edge IPs; NS `nelly.ns.cloudflare.com`, `houston.ns.cloudflare.com` → the zone is on Cloudflare |
| Unrelated contexts | the local VM `ubuntu-homo` (127.0.0.1:2222) was down; `194.68.245.26:22165` / `89.167.112.246` from `known_hosts` are not this deployment |

Conclusion: **production = `distance` = `162.0.216.58`, user `alex` (sudo NOPASSWD available)**.

## 2. Read-only baseline before any mutation

| Item | Value |
|---|---|
| Host | `distance`, KVM VM, Ubuntu 24.04.5 LTS, Python 3.12.3 |
| CPU / RAM | **1 vCPU**, 961 MB RAM (≈420 MB available), 2 GB swap (446 MB used) |
| Disk | 20 GB, 13 GB used, **6.2 GB free** (67 %) |
| Load (before) | 0.27 / 0.29 / 0.25 |
| Listening | 22, 80, 443, 8443 (public); loopback: 3000 (Next), 8000 (FastAPI), 5432/6380/6432 (containers/tunnels) |
| UFW | active: OpenSSH, Nginx Full, 172.18.0.0/16 tunnel rules |
| Fail2Ban | active, jails `sshd`, `nginx-critical-paths` |
| Nginx | 1.24.0 active |
| Backend | `12-testers-backend.service` active |
| Frontend | PM2 `12-testers-frontend` online (10 days uptime) |
| Database engine | **SQLite** (`/var/www/12-testers/backend/dev.db`); `psql` is not installed on the host, the 5432 listener belongs to another project's container |
| Public | site 200, API 200 |
| Fail2Ban Cloudflare action | file exists but `cftoken`/`cfuser` are **empty** → no Cloudflare credential in it |

Baseline verdict: production was healthy before the change, so the deployment proceeded.

## 3. Deployment layout

Chosen layout (isolated from `/var/www/12-testers`):

```
/opt/alex-gateway/releases/e0987a9d3ff0df07dd6aa9828a15ed05ec59b0e3   release (root:alex-gateway 0750)
/opt/alex-gateway/current -> releases/<sha>                           atomic symlink
/opt/alex-gateway/venv                                               root:alex-gateway 0750, 107 MB
/etc/alex-gateway/alex-gateway.env                                   config, 0640 root:alex-gateway
/etc/alex-gateway/runpod.env                                         master provider secret, 0640 root:alex-gateway
/var/lib/alex-gateway/gateway.db                                     SQLite, owner alex-gateway
/var/log/alex-gateway/                                               reserved for file logs (journald is used today)
/usr/local/sbin/alex-gateway-https.sh                                post-DNS HTTPS enablement (0750 root)
```

Service user `alex-gateway` (uid 999, system, `nologin`, no home). The Gateway never lives
under `/var/www/12-testers` and never shares the 12Testers database.

**Database choice:** SQLite (`/var/lib/alex-gateway/gateway.db`) with a single worker. Reason:
the actual server has 1 vCPU / ≈400 MB headroom, PostgreSQL is not installed on the host, and
the Gateway DB holds installations, the compute lease, idempotency records and audit events —
by design no chats, prompts or documents. This is the lower-risk option of the two allowed by
the task; the service is PostgreSQL-ready (`DATABASE_URL`) if the deployment ever grows.

## 4. Release artifact

* Built from branch HEAD `e0987a9…`: service package, its own Alembic history, and the exact
  reused provider core from `apps/backend/app` (`runpod_api.py`, `schemas.py`, `runtime.py`,
  `remote_runtime.py`, `config.py`, `packaging.py`) — 26 files, 43 002 bytes.
* Inspected before upload: **no `.env`, no key material, no JWT secret, no SSH key** in the archive.
* `sha256 = d1ffd88dd38e7e102f8b1c0c5b3cbabb7019138bcd1c56d770f53c16d6045e7f`, verified again on
  the server after `scp` (match).
* Installed into a versioned directory; `current` switched atomically (`ln -sfn` + `mv -T`).

## 5. Secrets

| Secret | Where | Owner / mode | How it arrived |
|---|---|---|---|
| RunPod master key | `/etc/alex-gateway/runpod.env` | `root:alex-gateway` 0640 | read on Windows through the product's own credential (`Alex LLM/provider/runpod`) and piped over the authenticated SSH channel into an atomic write; never in argv, history, chat or a plaintext temp file |
| Gateway JWT secret | `/etc/alex-gateway/alex-gateway.env` | `root:alex-gateway` 0640 | generated on the server (`secrets.token_urlsafe(48)`) |
| Pod gateway key (`LLM_API_KEY`) | `/etc/alex-gateway/alex-gateway.env` | `root:alex-gateway` 0640 | generated on the server (`secrets.token_urlsafe(40)`) |

Verification (with a **positive control**, so a "not found" cannot be a false negative):

* `runpod.env`: key found (control PASS) — the value is never printed.
* Key **absent** from: `alex-gateway.env`, `gateway.db`, the release code, the 12Testers SQLite
  database, 12Testers nginx access/error logs, the Gateway's systemd journal (0 matches).
* `systemd` loads both files via `EnvironmentFile=`; the provider secret is separated from
  ordinary configuration exactly as the task required.

## 6. Service

`/etc/systemd/system/alex-gateway.service`:

* `ExecStart=…/uvicorn gateway.main:app --host 127.0.0.1 --port 9011 --workers 1` → **loopback
  only** (verified: `LISTEN 127.0.0.1:9011`), never `0.0.0.0`.
* `Restart=on-failure`, `RestartSec=3`, enabled at boot.
* Resource guards for the shared VPS: `CPUQuota=50%`, `MemoryHigh=280M`, `MemoryMax=384M`.
* Hardening: `NoNewPrivileges`, `PrivateTmp`, `PrivateDevices`, `ProtectSystem=strict`,
  `ProtectHome`, `ProtectKernel*`, `ProtectControlGroups`, `RestrictAddressFamilies=AF_INET
  AF_INET6 AF_UNIX`, `RestrictNamespaces`, `LockPersonality`, `MemoryDenyWriteExecute`,
  `SystemCallFilter=@system-service`, `ReadWritePaths=/var/lib/alex-gateway /var/log/alex-gateway`.
* Separate environment files, separate journal (`journalctl -u alex-gateway.service`).

Database migrated with the Gateway's own Alembic history (`0001_gateway_core`) as the service
user before the first start.

## 7. Loopback acceptance (before any public routing)

```
GET http://127.0.0.1:9011/health
{"product":"alex-llm-gateway","version":"0.9.3","gateway_protocol_version":1,
 "ready":true,"database":"ok","provider_configured":true,"time":"2026-09-21T09:25:04Z"}
```

* HTTP 200, `ready=true`, `database=ok`, protocol 1, `provider_configured=true`.
* No secret, path or credential material in the payload.
* Service `active (running)`, **RSS ≈ 79 MB** (MemoryCurrent ≈ 59 MB), well inside the cap.
* Journal contains only normal uvicorn lines.

## 8. 12Testers regression checkpoints

| Checkpoint | site | API | backend | nginx | PM2 | host |
|---|---|---|---|---|---|---|
| 1. before any change | 200 | 200 | active | active | online | load 0.27, 420 MB avail, 6.2 GB free |
| 2. after the Gateway process | 200 | 200 | active | active | online | load 1.02 (pip/build), 389 MB avail |
| 3. after nginx vhost (inert, no DNS) | 200 | 200 | active | active | online | unchanged |
| 4. final | 200 | 200 | active | active | online | load 0.55, 403 MB avail, 6.1 GB free |

Regressions caused by the Gateway: **none**. The loopback port stays closed to the public
(UFW unchanged: 22/80/443/8443 only).

## 9. Client-side production default (verified on the installed app)

`apps/desktop/e2e/cloud-default-check.mjs` on the freshly built and installed package with
`https://gateway.12testers.store` baked in (installer 87 115 411 bytes), isolated data root,
no enrollment: **13/13 PASS**

* `mode = shared` without an enrollment, backend log `mode=packaged`;
* cloud state `not_connected`, `enrolled=false`, `balance_source=gateway`;
* `balance.configured=false` → **no silent fallback** to the local RunPod credential, even
  though `Alex LLM/provider/runpod` exists on this machine;
* AI chip `not_configured` with the message «Alex Cloud не подключён…», never Ready;
* five chips render, RunPod API key field absent in shared mode, AI / Compute note present.

## 10. Remaining blocker: DNS

`gateway.12testers.store` does not exist. The zone is on Cloudflare, and no authorized
programmatic mechanism was found anywhere:

* Windows: no `CLOUDFLARE_*` env vars, no `cloudflared`, no `wrangler`, no Cloudflare entry in
  the Credential Manager, no token in the 12Testers project copy or its `env/` files;
* VPS: `/etc/fail2ban/action.d/cloudflare*.conf` exist but their `cftoken`/`cfuser` values are
  empty; no `cloudflared`/`flarectl`/`cf-terraforming`, no `~/.cloudflared`, no CF variables in
  `ansible-12testers`;
* GitHub: `gh` is authenticated, but the 12Testers repository's Actions secrets contain no
  Cloudflare credential (`SERVER_NEXT_PUBLIC_API_URL`, `SSH_HOST`, `SSH_PRIVATE_KEY` only) and
  its workflows reference neither Cloudflare nor DNS.

### DNS_ACTION_REQUIRED — the one human action

In the Cloudflare dashboard for `12testers.store` add:

| Type | Name | Content | Proxy status | TTL |
|---|---|---|---|---|
| A | `gateway` | `162.0.216.58` | Proxied (orange cloud) | Auto |

Do not modify the existing `12testers.store` or `www` records. Nothing else is needed: after the
record exists, one command finishes the deployment.

## 11. Post-DNS automation (pre-staged, idempotent)

`/usr/local/sbin/alex-gateway-https.sh` (root, 0750) — already installed:

1. refuses to run while `gateway.12testers.store` does not resolve (`DNS_ACTION_REQUIRED`);
2. issues a Let's Encrypt certificate (`certbot certonly --nginx`, HTTP-01 through the
   Cloudflare proxy, using the inert `:80` ACME vhost that is already enabled and validated by
   `nginx -t`);
3. writes the public vhost: `:80` → ACME + redirect, `:443` → `proxy_pass http://127.0.0.1:9011`
   with `proxy_http_version 1.1`, `proxy_buffering off`, `proxy_cache off`,
   `proxy_request_buffering off`, `proxy_read_timeout 900s`, HSTS and `X-Content-Type-Options`
   matching the site's conventions;
4. runs `nginx -t` and only then reloads nginx;
5. then the public acceptance (`/health` 200, `/balance|/compute/status|/v1/chat/completions`
   401 unauthenticated, `/runpod/*` and `/provider/raw` 404), the activation code, the client
   enrollment and the shared-balance check follow.

## 12. Rollback

* Gateway only: `systemctl disable --now alex-gateway.service` (and, if needed,
  `rm -f /etc/nginx/sites-enabled/alex-gateway.conf && nginx -t && systemctl reload nginx`).
* The 12Testers deployment is untouched by any of this: its vhost, service, PM2 app, database
  and certificates are unchanged, and the Gateway never writes outside its own directories.
* Nothing is deleted automatically: `releases/`, the database and the two environment files
  stay in place for inspection.

## 13. Not done in this step (deliberately)

* Public HTTPS routing, the public security acceptance, the activation code, the client
  enrollment, the second (throwaway) installation and the shared-balance verification all
  depend on the DNS record above.
* No merge to `main`: the task requires public HTTPS, enrollment and a real shared balance to
  pass first.
* No compute: `/compute/ensure`, `/compute/stop`, Pod creation, GPU — never called. Provider
  traffic so far is read-only.
