# Alex LLM — AI handoff

Canonical starting point for a new IDE/agent session. This is not a historical dump; read the linked docs instead of past chat reports.

## 1. Product version

**0.9.3 development.** Do not bump for installer or handoff work. Not a 1.0 release cut.

## 2. Main HEAD

Recorded at handoff (sidecar installer, session restore, then this document):

- `origin/main` before the sidecar merge: `c6070b5460948dc865b29dd639d8b5297ac41f77`
- `feat/backend-sidecar-installer`: `b52be3d3ea13463e0adf0ecd19e2d75ea51f6667`

After this document lands, `git rev-parse origin/main` is the live source of truth.

Merge history kept as branches:

- Session restore / first-run slice — fast-forward of `feat/session-first-run` (`e1f90d0`)
- Status / recovery / balance slice — `feat/status-recovery-ux`, merged into `main` as a fast-forward after the Web/Tor truthfulness and live-balance gate

(When the two hashes above no longer match `origin/main`, trust `git rev-parse origin/main`.)

## 3. Architecture

Thin model + thick deterministic controller.

- Model: OrcaRouter / Qwen3.8-27B-Uncensored Q5_K_M (`orcarouter-qwen38-27b-q5km`), `enable_thinking=false`
- AUTONOMY = HIGH (fixed)
- RESEARCH_DEPTH = DEEP (fixed)
- No selectors. Do not change the model.

Overview (partially stale on mock vs packaged llama.cpp defaults): [architecture.md](architecture.md). Prefer runtime docs below for current process ownership.

## 4. Completed milestones

| Slice | Canonical doc |
|---|---|
| 0.9.3 reliability foundation (CLOSED) | [0.9.3-reliability-closeout.md](0.9.3-reliability-closeout.md) |
| Desktop-owned local backend | [runtime-foundation.md](runtime-foundation.md) |
| On-demand RunPod lifecycle | [on-demand-ai.md](on-demand-ai.md), [runpod-controller.md](runpod-controller.md) |
| Backend sidecar / installer foundation | [backend-sidecar-audit.md](backend-sidecar-audit.md), [backend-sidecar-decision.md](backend-sidecar-decision.md), [installer-data-layout.md](installer-data-layout.md) |
| Session restore / first-run foundation | [session-first-run-audit.md](session-first-run-audit.md), [session-first-run-design.md](session-first-run-design.md) |
| Five-chip status + error/recovery UX + shared RunPod balance | [status-recovery-audit.md](status-recovery-audit.md), [status-recovery-design.md](status-recovery-design.md) |
| Central RunPod Gateway / Cloud Control Plane v2 (**deployed** at `https://gateway.12testers.store`, merged) | [central-runpod-gateway-audit.md](central-runpod-gateway-audit.md), [central-runpod-gateway-design.md](central-runpod-gateway-design.md), [gateway-deployment.md](gateway-deployment.md), [gateway-12testers-deploy-audit.md](gateway-12testers-deploy-audit.md) |
| Upgrade / backup / data preservation | [upgrade-backup-audit.md](upgrade-backup-audit.md), [upgrade-backup-design.md](upgrade-backup-design.md), [backup-format.md](backup-format.md) |

Security invariants: [security.md](security.md).

## 5. Current installed / runtime architecture

**Packaged (normal Windows user):**

```text
%LOCALAPPDATA%\Programs\Alex LLM\alex-llm.exe
  → sidecar\alex-backend\alex-backend.exe   (PyInstaller onedir)
  → alex-host-loop.exe
  → %LOCALAPPDATA%\Alex LLM\                (mutable data)
```

Desktop `ensure_backend` prefers the packaged sidecar. Production packaged builds **must not** search `python.exe`. Missing sidecar → `BACKEND_SIDECAR_MISSING`.

**Auth / session (0.9.3):** access JWT (60 min, `jti`) + persistent device session
(hashed refresh secret in `auth_sessions`, rotated per refresh, sliding
`AUTH_SESSION_DAYS=30` bounded by absolute `AUTH_SESSION_MAX_DAYS=90`).
Raw refresh secret lives only in Windows Credential Manager
(`Alex LLM/session/{id}`, DPAPI fallback in the data root). Endpoints:
`/auth/bootstrap` (first owner, runtime-token proof, singleton `bootstrap_claim`,
closes permanently), `/auth/login|register|refresh|revoke|state`. First-run UI
states: `first_run | auth_required | restoring | authenticated | error`.
RunPod key: **one installation-global credential** — `Alex LLM/provider/runpod` (Credential Manager,
`CRED_PERSIST_LOCAL_MACHINE`, DPAPI fallback in the data root), saved through the installed
Settings → AI / Compute → RunPod API key. It is never per-user, never returned to the UI
(`configured: true/false` only), never deleted by logout, is passed to the owned backend via env (a stored
credential wins over a stale developer `.env`), and set/delete restarts the owned backend.
Details: [session-first-run-design.md](session-first-run-design.md).

**Developer (`tauri dev` / debug):** venv Python `python -m app.runtime_entry` is allowed when the sidecar artifact is absent. An already-healthy Alex API on 8000–8019 is reused as `DEV_EXTERNAL` and is not owned/killed.

Health identity: `product=alex-llm`, `version=0.9.3`, `runtime_protocol_version=1`.

**Status / recovery / balance (0.9.3):** `GET /status` (authenticated, read-only) returns the five
user-facing chips `ai | computer | web | tor | memory` plus the **shared** RunPod account balance.
Vocabulary: `ready | starting | configured | off | not_configured | unavailable | error | degraded`. Each
chip carries `message`, `detail_code`, `recoverable`, `action`, `details`. Chips read the subsystem that
already owns the state (compute `compact_ai`, device heartbeat, TinyFish config, Tor SOCKS5 endpoint,
`use_memory`). Two semantics are permanent and must not be weakened:

- **Web configured ≠ verified healthy** — a configured provider reports `configured` ("Настроено"), never
  `ready`; no TinyFish request is made for a chip.
- **Tor socket reachable ≠ verified Tor route** — an open SOCKS5 port reports `configured`, and Tor
  **never** reports `ready` because no authoritative verified-chain proof is stored
  (`details.verified_chain = false`, `details.proof_store = none`, `details.fallback = none`); routing
  stays fail-closed with no clearnet fallback.

RunPod has **no REST balance**: `RunPodAPI.account_balance()` is a read-only GraphQL call to
`RUNPOD_GRAPHQL_URL` `myself.clientBalance`, cached process-wide (5 s while compute is billable, 15 s
otherwise) with single-flight refresh; money stays `Decimal` and is serialized as a string. A failed read
keeps the last value and marks it stale — never a fake `$0`. `BALANCE_BACKGROUND_ENABLED` (default true)
starts the optional background refresher. Recovery actions reuse existing paths (settings, device loop,
compute panel with its confirmation). UI polls through one owner (`useStatus`). Live read-only acceptance:
`scripts/acceptance-runpod-balance.py`. Details: [status-recovery-design.md](status-recovery-design.md).

## 6. RunPod lifecycle

GPU does **not** start on app open, settings, or health. A real model-needed task may start **one** managed Pod, wait until the llama.cpp alias is healthy, then continue **the same** task. No duplicate Pods. Full application Quit stops managed compute. Network Volume `uwgeaie5b0` is **never** auto-deleted.

Details: [on-demand-ai.md](on-demand-ai.md), [runpod-controller.md](runpod-controller.md).

### Two provider modes (Central RunPod Gateway, deployed)

| | Production shared | Dev / private direct |
|---|---|---|
| Provider credential | Gateway only (`RUNPOD_API_KEY` in server env) | local `Alex LLM/provider/runpod` |
| Compute authority | Gateway (`gateway_compute` singleton + database lease) | local `RunPodController` |
| Inference | client → Gateway `/v1/chat/completions` → Pod | client → Pod directly |
| Gateway | `https://gateway.12testers.store` (12Testers VPS, loopback `127.0.0.1:9011`) | not used |
| Selected by | packaged Desktop default (enrollment or not) + `ALEX_AI_MODE=shared` from the Desktop spawn env | explicit `ALEX_AI_MODE=direct` |

The Desktop decides the mode when it spawns its backend (`apps/desktop/src-tauri/src/gateway.rs`): a
packaged install is **shared by default**, so without an enrollment it reports `gateway_not_connected`
instead of silently falling back to a local provider credential; an explicit `ALEX_AI_MODE=direct` keeps
the dev/private path. In shared mode the local compute lifecycle refuses with `gateway_managed_compute`,
`POST /runtime/shutdown` never stops shared compute, and the balance plus the AI chip come from the
Gateway. Typed Gateway operations only: no `/runpod/*` or `/provider/raw` passthrough. Client-side code:
`apps/backend/app/cloud/*`. Service: `apps/gateway/gateway/*`.

Shared-mode readiness is a **local** answer: `GatewayProvider.health()` is cached (`READY_TTL_SECONDS`,
single-flight background refresh), because `/health` is the Desktop's 400 ms liveness probe — probing the
Gateway per request made an enrolled app fail to start and hammered the shared service (see the deploy
audit §17.1).

## 7. Sidecar / installer architecture

Choice: **PyInstaller onedir** (not Nuitka, not onefile, not `externalBin`). See [backend-sidecar-decision.md](backend-sidecar-decision.md).

- Build sidecar: `scripts/build-backend-sidecar.ps1`
- Stage host + Tauri NSIS: `scripts/build-desktop.ps1`
- NSIS `currentUser` install dir is forced to `%LOCALAPPDATA%\Programs\Alex LLM\` so it does not collide with the data root ([installer-data-layout.md](installer-data-layout.md), `apps/desktop/src-tauri/nsis/hooks.nsh`)
- Alembic scripts ship inside the sidecar; migrate on startup; failure → `MIGRATION_FAILED`, DB not deleted
- FastEmbed libraries ship; embedding **weights** do not. Chromium is not bundled.

## 8. Data / install locations

| | Path |
|---|---|
| Binaries | `%LOCALAPPDATA%\Programs\Alex LLM\` |
| Data root | `%LOCALAPPDATA%\Alex LLM\` |
| DB | `data\alex.db` |
| JWT | `runtime\jwt.secret` |
| Logs | `logs\backend.log` |
| Embeddings cache | `models\embeddings\` |
| Documents | `documents\` |

Reinstall must not wipe the data root. Provider keys are not in the installer.

## 9. Known limitations

Do not “fix” these as a reliability rewrite:

- WM-07 TinyFish Browser live execution/lifecycle — [tinyfish.md](tinyfish.md)
- CD-08 REAL stale-SHA coverage debt
- REAL backend restart with a live Pod not live-tested (FakeRunPod covered)
- No production code signing / SmartScreen publisher yet
- Access JWT stays valid until its short expiry after logout (copied-token note, [security.md](security.md))
- Live RunPod **balance** was accepted read-only through the **production credential path**
  (`scripts/acceptance-runpod-balance.py` for the two-user/shared-cache check, and the installed UI for the
  real display): the key was saved in the installed app, stored in Credential Manager
  `Alex LLM/provider/runpod`, survived logout, full restart and reinstall, served two users with the same
  shared balance, and was removed only by an explicit delete in Settings. No `.env` is involved in the
  installed product.

## 10. Useful branches / worktrees

Primary checkout: `C:\Users\Volkr\Documents\Codex\2026-09-13\x20\outputs\alex-llm`

| Branch | Worktree | Role |
|---|---|---|
| `main` | this repo | product |
| `feat/central-runpod-gateway` | (same) | **merged** (production deployment slice); keep |
| `feat/status-recovery-ux` | (same) | merged; keep |
| `feat/session-first-run` | (same) | merged; keep |
| `feat/backend-sidecar-installer` | (same) | merged; keep |
| `feat/on-demand-ai-runtime` | — | merged; keep |
| `feat/product-runtime-foundation` | — | merged; keep |
| `planning/1.0-product-readiness` | `...\alex-llm-product` | planning only |
| `eval/real-world-suite` | `...\alex-llm-eval` | eval harness |
| `eval/093-*` | `...\alex-llm-*-verify` | historical eval |

Never edit another worktree by accident. Do not delete these worktrees.

Local leftover: `docs/screenshots/0.4/*.png` — do not commit or delete.

## 11. Current local test counts

Recorded 21 Sep 2026, no GPU, no TinyFish (Central RunPod Gateway → production deployment):

| Suite | Result |
|---|---|
| gateway pytest | **95 passed** |
| gateway Ruff check / format | PASS |
| gateway Alembic (fresh temp SQLite → `0001_gateway_core`, `alembic check` clean) | PASS |
| backend pytest | **553 passed**, 1 skipped |
| Ruff check / format | PASS |
| Alembic check (fresh temp SQLite → 0015) | PASS |
| Vitest | **98 passed** |
| TypeScript / Prettier / Vite | PASS |
| Playwright | **19 passed** |
| cargo check / test | host **17**, desktop **50** |
| npm audit --omit=dev | **0** |
| pip-audit | **0** (local package skipped) |
| Installed GUI smoke (real Tauri app, CDP-driven) | **PASS** (`apps/desktop/e2e/gui-smoke.mjs`) |
| Installed cloud GUI smoke (enroll → balance → user switch → restart → reinstall) | **PASS** (`apps/desktop/e2e/cloud-smoke.mjs`) |
| Installed production-default check (shared, no silent direct fallback) | **13/13 PASS** (`apps/desktop/e2e/cloud-default-check.mjs`) |
| **Public Gateway acceptance** (deployed HTTPS, two installations, shared balance, revoke) | **PASS** (`scripts/acceptance-public-gateway.py`) |
| **Installed app against the public Gateway** (disconnect → restart → enroll → balance → leaks) | **PASS** (`apps/desktop/e2e/cloud-prod-enroll.mjs`) |
| **Installed backup/restore acceptance** (backup → mutate → restore, tamper rejection, migration failure) | **PASS** (`apps/desktop/e2e/backup-smoke.mjs`) |
| **Installer over-install acceptance** (older schema + data → new build → data, session, enrollment) | **PASS** (`apps/desktop/e2e/upgrade-over-install.mjs prepare-legacy` then `verify`) |
| Local production-like Gateway acceptance (real read-only shared balance) | **PASS** (`scripts/acceptance-central-gateway.py`) |
| Live read-only RunPod balance acceptance (direct mode) | **PASS** (`scripts/acceptance-runpod-balance.py`) |

A leftover `apps/backend/alex.db` at an old revision is **not** the product data root. Prefer a fresh `DATABASE_URL` for `alembic check`.

## 12. Next slice

**RC / 1.0 FINAL GATES**

Do not start this unless explicitly tasked. (Code signing, WM-07 and CD-08 stay closed until
separately tasked; the Central RunPod Gateway is **deployed** at
`https://gateway.12testers.store` and merged into `main` — its compute path
(`/compute/ensure`, the inference proxy) is still unproven against a real Pod, which is the
next verification step when a Pod is first started deliberately.)

## 13. Normal test / build commands

From repo root, PowerShell. No paid GPU/TinyFish unless a REAL task says so.

```powershell
# Backend
cd apps\backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests alembic
.\.venv\Scripts\python.exe -m ruff format --check app tests alembic
# Alembic against a fresh file, not a dirty leftover ./alex.db:
$env:DATABASE_URL = "sqlite:///$env:TEMP/alex-alembic-check.db"
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic check
Remove-Item Env:DATABASE_URL
cd ..\..

# Desktop
cd apps\desktop
npm test
npm run format:check
npx tsc -b --pretty false
npx vite build
npm run test:e2e
npm audit --omit=dev
cd src-tauri
cargo check
cargo test
cd ..\..\..

# Gateway (its own service, own database, own Alembic history).
# Deployed instance: https://gateway.12testers.store on the 12Testers VPS
# (see gateway-12testers-deploy-audit.md; operator CLI runs on the server, not here).
cd apps\gateway
..\backend\.venv\Scripts\python.exe -m pytest -q
..\backend\.venv\Scripts\python.exe -m ruff check .
..\backend\.venv\Scripts\python.exe -m ruff format --check .
# Run the gateway locally (production-like): set APP_ENV/DATABASE_URL/JWT_SECRET/RUNPOD_API_KEY
# and ALEX_BACKEND_LIB_DIR=<repo>\apps\backend, then:
..\backend\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
..\backend\.venv\Scripts\python.exe -m uvicorn gateway.main:app --host 127.0.0.1 --port 9011
# One-time installation activation code (operator CLI, no admin panel):
..\backend\.venv\Scripts\python.exe -m gateway.cli create-code --label "PC A"
cd ..\..

# REAL read-only Gateway acceptance (two installations, shared balance, no compute action).
apps\backend\.venv\Scripts\python.exe scripts\acceptance-central-gateway.py

# REAL PUBLIC acceptance against the deployed Gateway (creates codes on the VPS over SSH,
# read-only balance, revokes its own throwaway installations; no secret is printed).
apps\backend\.venv\Scripts\python.exe scripts\acceptance-public-gateway.py

# Installed app against the deployed Gateway (real data root + real credentials):
# disconnect -> restart -> enroll -> shared balance -> leak checks. No Pod, no GPU.
cd apps\desktop
node e2e/cloud-prod-enroll.mjs
cd ..\..

# Upgrade / backup acceptance (installed app, isolated data roots, no GPU):
#   backup -> mutate -> restore, corrupt/tampered rejection, pre-upgrade backup,
#   installer over-install with data + credentials preserved.
cd apps\desktop
node e2e/backup-smoke.mjs
node e2e/upgrade-over-install.mjs
cd ..\..

# Gateway snapshot tool (on the VPS, before any Gateway migration or release switch):
sudo /usr/local/sbin/alex-gateway-backup.sh && sudo /usr/local/sbin/alex-gateway-backup.sh --prune

# pip-audit (from apps\backend)
.\venv\Scripts\python.exe -m pip_audit

# Live read-only RunPod shared-balance acceptance (2 read-only GraphQL queries; $0, no GPU).
# Run it from apps\backend so the product's settings/.env credential is resolved.
cd apps\backend
..\..\apps\backend\.venv\Scripts\python.exe ..\..\scripts\acceptance-runpod-balance.py
cd ..\..

# Packaged artifacts (slow; needs .venv + Rust)
.\scripts\build-backend-sidecar.ps1
.\scripts\build-desktop.ps1
```

Dev loop: `.\scripts\setup-backend.ps1`, `.\scripts\start-desktop.ps1` (`tauri dev` may use venv Python).

## 14. Read before changing runtime

1. `AGENTS.md` (this repo root)
2. This file
3. [runtime-foundation.md](runtime-foundation.md)
4. [installer-data-layout.md](installer-data-layout.md)
5. [backend-sidecar-decision.md](backend-sidecar-decision.md)
6. [on-demand-ai.md](on-demand-ai.md)
7. [runpod-controller.md](runpod-controller.md)
8. [security.md](security.md)
9. [status-recovery-design.md](status-recovery-design.md)
10. [central-runpod-gateway-design.md](central-runpod-gateway-design.md), [gateway-deployment.md](gateway-deployment.md)
11. `apps/desktop/src-tauri/src/backend.rs`
12. `apps/backend/app/runtime_entry.py`, `app/packaging.py`

## 15. Permanent prohibitions / invariants

- Do not start GPU or spend TinyFish unless the task requires REAL acceptance
- Do not automatically delete Network Volume `uwgeaie5b0`
- Do not bundle the production GGUF or provider secrets
- Do not require system Python in the production package
- Do not silently fall back to arbitrary `python.exe` on user machines
- Do not store mutable user DB under Program Files / INSTDIR
- Do not wipe data on reinstall
- Do not kill unrelated processes or kill-by-name
- Do not redesign RunPod lifecycle or reopen reliability without a proven regression
- Do not fix WM-07 as a drive-by
- Do not start LoRA; do not change OrcaRouter; do not add autonomy/depth selectors
- Do not bump version because docs or installer changed
- Confirmations remain bound to an immutable payload
- TinyFish Agent stays READ_ONLY; Tor intent is fail-closed to Tor
- Production mock must not masquerade as Ready/real AI
- Status and balance endpoints stay READ ONLY: they must never start, adopt or stop compute or spend
- Money stays `Decimal` server-side; a failed provider read keeps the last value and is marked stale
- The RunPod master key lives only on the Gateway: never in a Desktop, installer, local backend,
  frontend, client Credential Manager, client SQLite, `localStorage`, client `.env`, client log or
  API response (`Alex LLM/gateway/installation` is an installation credential, not the master key)
- No `/runpod/*` or `/provider/raw` passthrough on the Gateway; typed operations only
- A client can never raise the $1.20/h or $3/session caps, and no client can claim compute ownership
- Local logout never deletes the installation credential; only an explicit Disconnect does
