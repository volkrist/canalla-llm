# Alex LLM — AI handoff

Canonical starting point for a new IDE/agent session. This is not a historical dump; read the linked docs instead of past chat reports.

## 1. Product version

**1.1.0** — release candidate on `release/canalla-1.1.0`. The version is authoritative in:
`apps/desktop/src-tauri/tauri.conf.json`, `apps/desktop/package.json`,
`apps/desktop/src-tauri/Cargo.toml`, `apps/backend/pyproject.toml`,
`apps/gateway/pyproject.toml`, `apps/backend/app/product.py`, the FastAPI app and
`apps/gateway/gateway/config.py`. The deployed Gateway reports `version: 1.1.0` (protocol stays
`1`). Bump only as a deliberate release act.

**1.0.0** — released from `release/canalla-1.0-rc` as the annotated tag `v1.0.0`; frozen, and its
installer stays as published.

User-facing product name: **Canalla LLM** (window title, installer, chat labels, onboarding,
assistant name). Storage, credential and protocol names keep the `Alex LLM` spelling on
purpose — see the Naming section of `AGENTS.md`.

## 2. Main HEAD

Recorded at the 1.0 release: `git rev-parse origin/main` is the live source of truth.

- Release candidate branch: `release/canalla-1.1.0` (NOT merged and NOT tagged: the live GPU sanity
  is externally blocked by provider capacity, see [release-1.1.md](release-1.1.md) §7)
- Previous release: `release/canalla-1.0-rc`, merged into `main` and tagged `v1.0.0` (annotated)
- Deployed Gateway release: `/opt/alex-gateway/releases/adb568734430` (1.1.0, `current`, deployed
  22 Sep 2026 12:35 UTC, service `alex-gateway.service`); rollback targets, newest first:
  `/opt/alex-gateway/releases/9bd75486c34b` (the 1.0.0 release), then
  `/opt/alex-gateway/releases/999e00b19ebd`, `/opt/alex-gateway/releases/5539b623e592`

Release artifacts (rebuilt 22 Sep 2026 23:16, the build the installed acceptances ran against):
`Canalla LLM_1.1.0_x64-setup.exe` (87 199 279 B, SHA-256
`4d3441acc6f555287f68c5fecb87abe352f9107e506771843952d28a126fad3a`), Desktop `alex-llm.exe`
(SHA-256 `fbd339552160454c204a32ce7703c33616c2a9dd99234e54f5920e54c1596ebe`; the installed copy is
the NSIS-patched build, `6d216a69763ba31476dbe6bb10b3ffe2951a2e0299a290cdfd9512cf1b089986`), sidecar
`alex-backend.exe` (`4cc390217d491a52f1e94a63cee9c8d8679adc4b2207ead7bcbb50c88c92db8e`, identical in
the bundle and in `%LOCALAPPDATA%\Programs\Canalla LLM\sidecar\alex-backend\`), native host
`alex-host-loop.exe` (`defc68b1bb62e614af07b95a1799a4ce9a314c7f05c046020dc5d19d4e7c93ea`).

The 1.0.0 artifacts remain as published: `Canalla LLM_1.0.0_x64-setup.exe` (87 180 763 B, SHA-256
`3465e6405cd5817be584b75eb11238a86e36b958f44776f9583d6008d1d22362`), sidecar
`c80560ebdfac55ea46f0fe0ebd56336ba1da5c61a3ccff3c08e494b1aa7a8f01`.

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
| Canalla LLM user-facing brand + composer context meter | [context-usage.md](context-usage.md) |
| Computer + Tor **always ready** (health vs. usage policy, managed Tor with a proven circuit, device-credential isolation in the harnesses) | [tor.md](tor.md), [local-computer.md](local-computer.md) |
| RC / 1.0 final gates (**released as `v1.0.0`**, merged into `main`; live-certified on the production path) | [release-1.0.md](release-1.0.md), [compute-preferences.md](compute-preferences.md), [../CHANGELOG.md](../CHANGELOG.md) |

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
| Binaries | `%LOCALAPPDATA%\Programs\Canalla LLM\` (until this rename: `Programs\Alex LLM`; the installer follows the product name, an older folder is left to Apps & features) |
| Data root | `%LOCALAPPDATA%\Alex LLM\` (unchanged on purpose) |
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
- **No autostart at Windows login** (1.1.0): the installer registers no `Run` entry and there is no
  «start with Windows» toggle, so the Computer host comes back when Canalla is launched — the
  pairing, `device_id` and credential survive, so that launch needs no button. The shipped
  `alex-host-loop.exe` is the headless E2E variant and is never started by the product
  ([local-computer.md](local-computer.md))
- **Web chips report `configured`, not `ready`** (no TinyFish health probe). Tor **does** prove
  `ready` since 1.1.0 through a persisted SOCKS5h round trip ([tor.md](tor.md))
- **A machine-wide device credential is not scoped by `ALEX_DEVICE_DIR`**, only `device.json` is.
  Every installed smoke must set `ALEX_DEVICE_CREDENTIAL_TARGET` or it overwrites
  `Alex LLM/device-credential`, i.e. the credential the operator's own installation pairs with
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
| `feat/canalla-llm-context-meter` | (same) | merged into `main` (brand + context meter); keep |
| `feat/upgrade-backup-data` | (same) | **merged** (upgrade / backup / data preservation slice); keep |
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

Recorded 22 Sep 2026 at the 1.0.0 release cut (no GPU, no TinyFish), one suite at a time:

| Suite | Result |
|---|---|
| gateway pytest | **112 passed** |
| gateway Ruff check / format | PASS (26 files formatted) |
| gateway Alembic (fresh temp SQLite → `0001_gateway_core`, `alembic check` clean) | PASS |
| backend pytest | **579 passed**, 1 skipped |
| Ruff check / format | PASS (176 files formatted) |
| Alembic check (fresh temp SQLite → 0015) | PASS |
| Vitest | **135 passed** |
| TypeScript / Prettier / Vite | PASS |
| Playwright | **20 passed** |
| cargo check / test | host **17**, desktop **50** |
| npm audit --omit=dev | **0** |
| pip-audit | **0** (local package skipped) |
| **Consolidated live RC gate** (one Pod: capacity, basic, context 70/86, overflow, long stream, Stop, after-stop, cleanup) | **PASS** (`scripts/acceptance-live-rc-final.py --max-hourly 0.52 --budget-usd 3.00 --capacity-timeout 300 --spend-ceiling 0.10`) |
| Harness self-test — 28-case lifecycle matrix, fake Gateway, virtual clock | **PASS** (`--self-test`) |
| Harness dry run against the deployed Gateway (no Pod, no spend) | **PASS** (`--dry-run`) |
| **Installer acceptance** (the real `Alex LLM_0.9.3_x64-setup.exe` → the final `Canalla LLM_1.0.0_x64-setup.exe`) | **PASS** (one product; the Alex programme dir, Start Menu/desktop shortcuts, autostart value and uninstall entry removed; data root hash, 31 credentials and the enrollment preserved) |
| Installed GUI smoke of the final 1.0.0 (launch → owned sidecar up → clean quit) | **PASS** |

The installed e2e harnesses below were accepted earlier in the same RC cycle on the 0.9.3
candidate; the version bump does not touch them, and the final installer was re-accepted on the
real machine (installer acceptance and GUI smoke rows above).
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

### Post-release certification of the released 1.0.0 (22 Sep 2026)

Recorded after the repo-wide type-checking cleanup; the release artifact itself is untouched.

| Gate | Result |
|---|---|
| Type checking (BasedPyright, `pyrightconfig.json`, mode `standard`) | **0 errors, 0 warnings** (226 files) |
| Backend pytest / Ruff / Alembic / pip-audit | **579 passed, 1 skipped** / PASS / head `0015`, no new operations / no known vulnerabilities |
| Gateway pytest / Ruff / Alembic | **112 passed** / PASS / `0001_gateway_core`, no new operations |
| Vitest / TypeScript / Prettier / Vite / Playwright / npm audit | **135 passed** / clean / PASS / PASS / **20 passed** / 0 vulnerabilities |
| cargo check / cargo test | clean (re-verified from a fresh target dir) / **67 passed** (host 17, desktop 50) |
| Post-release harness `--self-test` (fake lifecycle matrix) | **16/16** — assertion, transport reset, 5xx, 4xx and KeyboardInterrupt all still stop compute |
| Post-release harness free phase (installed 1.0.0, isolated root) | **PASS** (clean first run, persistence, cloud, meter, restart) |
| Installed GUI / backup / production-default / cloud smokes | **PASS (39)** / **PASS (27)** / **13/13** / **PASS** (50, one skip without `ALEX_SMOKE_SETUP`) |
| Paid live sanity on the exact 1.0.0 (Basic → Stop → After Stop) | **INCOMPLETE — external blocker**: `ensure` answers `searching`/`gpu_unavailable` (compatible GPUs exist, none in stock in US-TX-3). One L40S did start the same day and reached ready in 90 s, so the product path is proven |

### Acceptance of the 1.1.0 release candidate (22 Sep 2026)

Everything below ran on the exact 1.1.0 artifact (`Canalla LLM_1.1.0_x64-setup.exe`
`4d3441ac…`, sidecar `4cc39021…`). The candidate is **READY**; only the paid GPU sanity is
externally blocked.

| Gate | Result |
|---|---|
| Type checking (BasedPyright, `pyrightconfig.json`, mode `standard`) | **0 errors, 0 warnings** (226 files) |
| Backend pytest / Ruff / Alembic / pip-audit | **581 passed, 1 skipped** / PASS / head `0015`, no new operations / no known vulnerabilities |
| Gateway pytest / Ruff / Alembic | **130 passed** (112 + 18 new D-9 cases) / PASS / `0001_gateway_core` |
| Vitest / TypeScript / Prettier / Vite / Playwright / npm audit | **150 passed** / clean / PASS / PASS / **20 passed** / 0 vulnerabilities |
| cargo check / cargo test | clean / **67 passed** (host 17, desktop 50) |
| Post-release harness `--self-test` (1.1.0 harness) | **16/16** — assertion, transport reset, 5xx, 4xx and KeyboardInterrupt all still stop compute |
| Consolidated RC harness `--self-test` (+ new `--core-only`) | **28/28** |
| Post-release harness free phase (installed 1.1.0, isolated root) | **PASS** (clean first run, persistence, cloud, meter, restart, logout keeps the enrollment) |
| Installed GUI / backup / production-default / cloud smokes | **PASS** / **PASS** / **13/13** / **PASS** (Scenario J skipped without `ALEX_SMOKE_SETUP`) |
| Upgrade 1.0.0 → 1.1.0 (real installer over the installed build, migration `0014` → `0015`) | **8/8 PASS** — data, session, enrollment, identity, verified pre-upgrade backup and balance preserved |
| Gateway deployment 1.1.0 (verified snapshot → atomic `current` switch → service restart) | **PASS** — `/health` version 1.1.0, protocol 1, ready, database ok, provider configured; 12Testers site/API 200, nginx and its backend active, PM2 online |
| Paid live sanity on the exact 1.1.0 (Basic → Stop → After Stop, 60 s capacity window) | **INCOMPLETE — external blocker**: two 60-second windows answered `searching`/`gpu_unavailable` (compatible GPUs exist, no usable stock). No Pod was created, nothing was spent, `active_pods=[]`, session `null`, Volume untouched |

### Computer + Tor always-ready, accepted on the installed 1.1.0 (23 Sep 2026)

The requirement was that Computer and Tor be ready after a *normal launch*, with no button pressed,
and that usage policy never masquerade as health. The product code, the tests and the installed
acceptance all landed on `release/canalla-1.1.0` in this cycle.

| Gate | Result |
|---|---|
| `apps/backend/tests/test_tor_service.py` (deterministic: fake listener, fake binary, fake spawn, fake proof) | **16 passed** |
| `apps/backend/tests/test_status_recovery.py` (the new computer/tor contract) | **+7 cases** (3 computer, 4 Tor) |
| Backend pytest / Ruff / BasedPyright | **603 passed, 1 skipped** / PASS / **0 errors, 0 warnings** |
| Vitest / TypeScript / Prettier / Vite / Playwright | **161 passed** / clean / PASS / PASS / **20 passed** |
| cargo check / cargo test | clean / **69 passed** (host 18, desktop 51, incl. the device-credential redirection test) |
| Installed `e2e/always-ready.mjs` (real build, isolated root, no clicks) | **21/21 PASS** — Computer «Готово», Tor «Готово» with a proven circuit, popovers separating health from mode, and after a full Quit + relaunch the session and the **same `device_id`** come back with one device and no second pairing |
| Installed GUI smoke / backup smoke / cloud-default-check / cloud smoke | **PASS** / **PASS** / **15/15** / **PASS** |
| Operator's own installed 1.1.0 (real data root, normal launch) | Computer `ready` on a fresh heartbeat, Tor `ready` from a persisted `runtime/tor.json` proof (`source: managed`, full bootstrap in ~30 s), no orphan `tor.exe` or 9050 listener after Quit |

## 12. Next slice

**CANALLA LLM 1.1.0 — LIVE SANITY ONLY**

Status on the branch `release/canalla-1.1.0` (22 Sep 2026):

- **Done and verified**: the D-9 startup deadline in the Gateway (with 18 deterministic cases), the
  compact workspace and the Settings shell, the POST context preview with an honest failed-snapshot
  state, strict project type checking (0/0 over 226 files), the release/acceptance tooling, the
  1.1.0 build and its real upgrade over the installed 1.0.0, the deployment of Gateway 1.1.0, and
  the **always-ready Computer + Tor service** (backend `TorService` with a proven circuit, the
  health/policy split on both chips, the device-credential isolation in every installed smoke, and
  `e2e/always-ready.mjs` proving readiness on a normal launch *and* after a restart).
- **Open**: only the **paid live sanity on the exact installed 1.1.0** — twice attempted, twice
  blocked externally by RunPod capacity (`gpu_unavailable`, no Pod, no spend). The release branch is
  ready; the next attempt is manual and must stay bounded:

  ```
  apps/backend/.venv/Scripts/python.exe scripts/acceptance-post-release-1.1.0.py --live --ceiling 1.20 --capacity-timeout 60
  ```

  Exit `3` = the typed external blocker (no Pod, no spend), exit `0` = the pass. Never widen
  `--capacity-timeout` beyond 60 s: a capacity drought is reported, not waited out. The two 1.0.1
  candidates from the 1.0.0 certification (draft preview in a POST body, a failed meter snapshot
  instead of a stale ring) are **fixed in the 1.1.0 candidate**; the third finding
  (`client_version` written only at enrollment) stays a documented limitation because fixing it
  would add a field to the token request.

The older 1.0.0 record, kept for history: the paid live sanity for the exact installed 1.0.0 was also
blocked externally (18 attempts over 10 minutes on 22 Sep, 09:12-09:22 UTC — capacity was still
being waited out then, which the 1.1.0 rule no longer allows). An L40S did start at 08:47 UTC that
day and became `ready` 90 s later, so create → ready is proven on the production path.

The upgrade / backup / data preservation slice is **complete and merged into `main`**; do not reopen
it. The Central RunPod Gateway is **deployed** at `https://gateway.12testers.store` and now runs the
1.1.0 candidate; on 22 Sep 2026 it created a real L40S Pod, the model became ready and the
pre-release live gates passed through it.

## 13. Normal test / build commands

From repo root, PowerShell. No paid GPU/TinyFish unless a REAL task says so.

```powershell
# Type checking (BasedPyright). The config is `pyrightconfig.json` in the repo root: mode "standard",
# venv apps/backend/.venv, the project import roots, generated/build dirs excluded. Zed's language
# server reads the same file, so the editor and this command always agree. Baseline: 0 errors, 0 warnings.
npx basedpyright --outputjson

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

# Acceptance harnesses (free first, then the paid sanity). A capacity window is never longer
# than 60 seconds: a drought is reported (`exit 3`, no Pod, no spend), not waited out.
cd scripts
..\apps\backend\.venv\Scripts\python.exe acceptance-post-release-1.1.0.py --self-test   # 16/16, no GPU
..\apps\backend\.venv\Scripts\python.exe acceptance-live-rc-final.py --self-test       # 28/28, no GPU
..\apps\backend\.venv\Scripts\python.exe acceptance-live-rc-final.py --dry-run          # real Gateway, no Pod
cd ..
# Installed-sidecar certification + one paid Pod (Basic → Stop → After Stop):
apps\backend\.venv\Scripts\python.exe scripts\acceptance-post-release-1.1.0.py --live --ceiling 1.20 --capacity-timeout 60
# A release sanity that skips the context/overflow/long-stream cases (already proven in 1.0.0):
apps\backend\.venv\Scripts\python.exe scripts\acceptance-live-rc-final.py --max-hourly 1.20 --budget-usd 3.00 --capacity-timeout 60 --spend-ceiling 0.15 --core-only
# The installed cloud smoke without a hand-made Gateway (temporary Gateway + database + code):
apps\backend\.venv\Scripts\python.exe scripts\cloud-smoke-local.py

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
- Compute Preferences are the **user's own money policy, not product caps**: defaults $0.52/hour and
  $3.00/session, editable by every authenticated local user in both provider modes (no admin role for
  *their own* policy, start/stop stays owner-gated), technical bounds only ($100/hour, $1000/session),
  `compute_policy_invalid` (422) instead of a silent replacement, cheapest compatible GPU inside the
  user's own maximum. Contract: [compute-preferences.md](compute-preferences.md)
- The RunPod master key lives only on the Gateway: never in a Desktop, installer, local backend,
  frontend, client Credential Manager, client SQLite, `localStorage`, client `.env`, client log or
  API response (`Alex LLM/gateway/installation` is an installation credential, not the master key)
- No `/runpod/*` or `/provider/raw` passthrough on the Gateway; typed operations only
- A client can never widen a technical bound, set another installation's policy or claim compute
  ownership
- Local logout never deletes the installation credential; only an explicit Disconnect does
