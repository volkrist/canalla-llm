# Alex Cloud Gateway — production deployment audit (12Testers VPS)

Date: 21 September 2026. Slice: `feat/central-runpod-gateway`, releases
`e0987a9d3ff0df07dd6aa9828a15ed05ec59b0e3` (first deployment) and later branch HEAD §17.1
(client fix). **This deployment is live at `https://gateway.12testers.store`** — see Part 2.

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

Service user `alex-gateway` (system, `nologin`, no home, **uid/gid 988/987** — deliberately not 999,
which an unrelated long-running `redis-server` on this host already uses). The Gateway never lives
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
* Service `active (running)`, **RSS ≈ 77 MB** (memory peak 62 MB), well inside the cap.
* Journal contains only normal uvicorn lines.

### Nginx → Gateway path, proven before DNS exists

The public hostname does not resolve yet, so the proxy path was proven on a **temporary**
loopback vhost (`127.0.0.1:9477`, throwaway self-signed certificate, same `proxy_pass` and
streaming settings as the final block), then removed and nginx reloaded:

| Request through nginx (HTTPS) | Result |
|---|---|
| `GET /health` | **200** with `ready=true`, `provider_configured=true` |
| `GET /balance` unauthenticated | **401** |
| `GET /compute/status` unauthenticated | **401** |
| `POST /v1/chat/completions` unauthenticated | **401** |
| `POST /runpod/graphql` | **404** |
| `POST /runpod/request` | **404** |
| `POST /provider/raw` | **404** |

After cleanup only `12-testers` and the inert `alex-gateway.conf` (ACME `:80` stub) remain
enabled, no temporary port is listening, and the site/API are still 200/200.

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

## 10. Production Gateway core acceptance (two installations, real account)

Run against the deployed Gateway over loopback (the public hostname does not exist yet),
with two server-generated one-time codes. No value of any code, token or secret is printed.

| Check | Result |
|---|---|
| two enrollment codes issued (32 chars each, values hidden) | PASS |
| different installation ids | **PASS** |
| different installation secrets, length ≥ 43 (256-bit) | **PASS** |
| both installations mint a short-lived token | **PASS** |
| the same shared account balance for both | **PASS** |
| same cached snapshot (`fetched_at` equal → one upstream read for two installations) | **PASS** |
| balance (UI rounded) from the **real** RunPod account | **$0.85** |
| `shared_account` / `read_only` flags | PASS |
| revoke installation B | 200; B's token → **403**; B cannot mint a new token → **403** |
| installation A keeps working after B is revoked | **200** |
| compute state / session | `offline` / none (no Pod, no GPU) |
| installations stored / rows with a non-digest secret | 2 / **0** |

This is the multi-installation and shared-balance evidence; the *client* hop over public
HTTPS (and therefore the real enrollment of this desktop) still waits for the DNS record below.

## 11. DNS (resolved in Part 2)

`gateway.12testers.store` did not exist when the Gateway was first deployed. The zone is on
Cloudflare, and no authorized programmatic mechanism was found anywhere:

* Windows: no `CLOUDFLARE_*` env vars, no `cloudflared`, no `wrangler`, no Cloudflare entry in
  the Credential Manager, no token in the 12Testers project copy or its `env/` files;
* VPS: `/etc/fail2ban/action.d/cloudflare*.conf` exist but their `cftoken`/`cfuser` values are
  empty; no `cloudflared`/`flarectl`/`cf-terraforming`, no `~/.cloudflared`, no CF variables in
  `ansible-12testers`;
* GitHub: `gh` is authenticated, but the 12Testers repository's Actions secrets contain no
  Cloudflare credential (`SERVER_NEXT_PUBLIC_API_URL`, `SSH_HOST`, `SSH_PRIVATE_KEY` only) and
  its workflows reference neither Cloudflare nor DNS.

### DNS_ACTION_REQUIRED — the one human action (done)

In the Cloudflare dashboard for `12testers.store` add:

| Type | Name | Content | Proxy status | TTL |
|---|---|---|---|---|
| A | `gateway` | `162.0.216.58` | Proxied (orange cloud) | Auto |

Do not modify the existing `12testers.store` or `www` records. Nothing else is needed: after the
record exists, one command finishes the deployment.

## 12. Post-DNS automation (pre-staged, idempotent)

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

## 13. Rollback

* Gateway only: `systemctl disable --now alex-gateway.service` (and, if needed,
  `rm -f /etc/nginx/sites-enabled/alex-gateway.conf && nginx -t && systemctl reload nginx`).
* The 12Testers deployment is untouched by any of this: its vhost, service, PM2 app, database
  and certificates are unchanged, and the Gateway never writes outside its own directories.
* Nothing is deleted automatically: `releases/`, the database and the two environment files
  stay in place for inspection.

## 14. Not done in this step (deliberately)

* Public HTTPS routing, the public security acceptance, the activation code, the client
  enrollment, the second (throwaway) installation and the shared-balance verification all
  depend on the DNS record above.
* No merge to `main`: the task requires public HTTPS, enrollment and a real shared balance to
  pass first.
* No compute: `/compute/ensure`, `/compute/stop`, Pod creation, GPU — never called. Provider
  traffic so far is read-only.

---

# Part 2 — public HTTPS, client enrollment and the shared balance (same day)

The Cloudflare record was added by the operator (`A gateway → 162.0.216.58`, proxied). Every
step below was then executed by the agent; the only human action in the whole deployment was
that DNS record.

## 15. DNS and TLS

| Check | Result |
|---|---|
| `gateway.12testers.store` from 1.1.1.1 / 8.8.8.8 | resolves to Cloudflare edge (`104.21.72.181`, `172.67.153.215`, both IPv6 addresses) — proxied, as intended |
| ACME challenge through the proxy | `http://gateway.12testers.store/.well-known/acme-challenge/<probe>` → **200** with the probe body (checked *before* requesting the certificate) |
| Certificate | Let's Encrypt via the pre-staged `/usr/local/sbin/alex-gateway-https.sh`; `gateway.12testers.store`, valid 89 days, `fullchain.pem` at `/etc/letsencrypt/live/gateway.12testers.store/` |
| Public vhost | `/etc/nginx/sites-available/alex-gateway.conf`: `:80` ACME + redirect, `:443` → `proxy_pass http://127.0.0.1:9011`, `TLSv1.2 TLSv1.3`, HSTS, `proxy_http_version 1.1`, `proxy_buffering off`, `proxy_cache off`, `proxy_request_buffering off`, `proxy_read_timeout 900s` |
| Validation | `nginx -t` before every reload; the 12Testers server block was not touched |
| TLS verification | `curl` reports `ssl_verify_result=0` over HTTP/1.1; HTTP → **301** to HTTPS (ACME path stays on `:80`) |

## 16. Public acceptance

`GET https://gateway.12testers.store/health` → **200**: `product=alex-llm-gateway`,
`version=0.9.3`, `gateway_protocol_version=1`, `ready=true`, `database=ok`,
`provider_configured=true` — no secret, path or credential field in the payload.

Unauthenticated surface: `/balance` **401**, `/compute/status` **401**,
`POST /v1/chat/completions` **401**; `/runpod/graphql`, `/runpod/request`, `/provider/raw`
**404**. The Gateway is a public hostname, so internet scanners do reach it (a few `404`
probes for `/`, `/favicon.ico`, `/functions/.env` were observed and refused).

`scripts/acceptance-public-gateway.py` (new) then ran the full public acceptance against the
deployed service — **PASS on every check**:

* two one-time activation codes created on the server with the operator CLI (values never
  printed), both single-use (a second redemption → **409**);
* different installation ids, different secrets ≥ 256 bits, digest-only rows, no raw secret,
  code or master key anywhere in the database;
* short-lived tokens for both installations, a wrong secret → 401/403, an unknown
  installation id → 401/403;
* the **same shared account balance for both** (`$0.85`, UI rounded) from **one cached
  upstream snapshot** (identical `fetched_at`), `shared_account`/`read_only` flags set;
* a controlled refresh after the cache TTL replaced the snapshot (read-only provider call);
* protocol mismatch → **409**;
* revoking B → B refused (**403**, no new token) while A keeps working;
* the master key, the activation codes and the installation secrets are absent from the
  journal, the nginx logs and a database dump, with a non-trivial scan corpus as control;
* both throwaway installations were revoked at the end (2/2).

## 17. Client: the real installed Alex

`apps/desktop/e2e/cloud-prod-enroll.mjs` (new) drives the real installation — real data root,
real credential entries, no isolated fixtures — through the real UI. Every check passed:

| Phase | Evidence |
|---|---|
| explicit Disconnect | panel → «Не подключено», `Alex LLM/gateway/installation` removed, the separate `Alex LLM/provider/runpod` untouched, server-side revoke (1 live → 0 live) |
| full restart | the app starts again (ready in 6–15 s) and stays honestly disconnected: five chips, AI chip `Не настроено`, no balance, RunPod key field absent, shared-mode note present |
| enrollment | one-time code from the server, filled in Settings → Alex Cloud → «Подключить»; the panel settles on «Alex Cloud · Подключено», the owned backend is restarted with the shared environment, the installation id and the Gateway URL are shown, and the activation code never appears in the DOM |
| shared balance | the app shows the **real** account balance `$0.84` seconds after the enrollment; the public access log records the `/balance 200` that served it |
| secrets | master key absent from the DOM, browser storage, the local SQLite database, the backend log, the installer, the installed binaries and the sidecar (positive controls included); the installation credential is a different secret |
| no direct provider traffic | the backend log of the run never mentions `api.runpod.io`; no passthrough route was used |

### 17.1 Production defect found and fixed by this acceptance

The first enrollment exposed a real defect that only a live Gateway could reveal:

* in shared mode `GET /health` awaited `provider.health()`, which probed the Gateway
  (`/v1/models`); the Desktop's runtime readiness probe allows **400 ms**, so the local server
  never looked ready — after enrolling, a relaunch of the installed app sat on «Запуск Alex…»
  and then failed with «Локальный сервер не ответил вовремя»;
* the same probe ran on every `/health`/`/llm/status` call, which turned a healthy client into
  a hot loop (2–5 requests/second) against the shared Gateway and tripped its rate limiter
  (**429**).

Fix (minimal, no redesign): shared-mode readiness is now **cached** in `GatewayProvider`
(`READY_TTL_SECONDS = 8`, single-flight background refresh; the first caller still waits for
the real answer, a stale answer is served instead of a slow `/health`). After the fix the
installed app starts in 6–15 s, exercises **2 model probes per minute**, and the Gateway never
rate-limits it. Two deterministic tests pin this (`test_provider_health_is_cached_instead_of_hot_looping`,
`test_provider_health_never_raises_into_the_health_endpoint`), and a third covers the client
UI settling: the Alex Cloud panel used to be able to keep the transient «Подключаемся…» after
an enrollment because it reads the cloud state once on mount (`refreshCloud` now waits, bounded,
for the first settled answer).

## 18. Resource impact

| Metric | Baseline (before the Gateway) | Final |
|---|---|---|
| Gateway RSS | — | **60–76 MB** (peak 80 MB, caps 280 MB high / 384 MB max) |
| Host MemAvailable | ≈ 420 MB | **365 MB** |
| Load average | 0.27 | 0.25 |
| Disk free | 6.2 GB | 6.1 GB |
| Gateway restarts | — | **0** (`NRestarts=0`, active since 09:46:56 UTC) |
| Gateway CPU share | — | `CPUQuota=50%` of 1 vCPU |

## 19. 12Testers regression checkpoints (continued)

| Checkpoint | site | API | backend | nginx | PM2 | host |
|---|---|---|---|---|---|---|
| 5. after TLS/public routing | 200 | 200 | active | active | online | 379 MB avail |
| 6. after client enrollment | 200 | 200 | active | active | online | 375–406 MB avail |
| 7. final | 200 | 200 | active | active | online | load 0.25, 365 MB avail, 6.1 GB free |

Regressions caused by the Gateway: **none**. UFW unchanged (22/80/443/8443 public only; the
Gateway port stays loopback), Fail2Ban jails unchanged, the 12Testers vhost/service/PM2/database
untouched.

## 20. Server state after the acceptance

The Gateway database holds **8 installations (1 live — the operator's installed Alex)**, 9
enrollment codes (all redeemed or expired) and 15 audit events; compute is `offline`, sessions
**0**. Every throwaway installation created by the two acceptance harnesses was revoked. The
master key is still only in `/etc/alex-gateway/runpod.env` (`root:alex-gateway 0640`) and in the
service environment at runtime.

## 21. Rollback and backup

* Gateway only: `systemctl disable --now alex-gateway`; to also remove the public entry point,
  `rm -f /etc/nginx/sites-enabled/alex-gateway.conf && nginx -t && systemctl reload nginx`.
* The 12Testers deployment is not involved in either operation.
* Backup: `/var/lib/alex-gateway/gateway.db` (installations, compute lease, audit events — no
  chats, prompts or documents) plus `/etc/alex-gateway/*.env`. A certificate renewal is handled
  by the existing Certbot timer.
* Nothing is deleted automatically: releases, database and environment files stay in place.
