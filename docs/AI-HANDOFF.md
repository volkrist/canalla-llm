# Alex LLM — AI handoff

Canonical starting point for a new IDE/agent session. This is not a historical dump; read the linked docs instead of past chat reports.

## 1. Product version

**0.9.3 development.** Do not bump for installer or handoff work. Not a 1.0 release cut.

## 2. Main HEAD

Recorded at handoff (fast-forward of sidecar installer, then this document):

- `origin/main` before sidecar merge: `c6070b5460948dc865b29dd639d8b5297ac41f77`
- `feat/backend-sidecar-installer`: `b52be3d3ea13463e0adf0ecd19e2d75ea51f6667`

After this file lands, `git rev-parse origin/main` is the live source of truth.

Session restore / first-run slice was merged into `main` as a fast-forward of
`feat/session-first-run` (`e1f90d0`); the branch is kept.

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
RunPod key: `Alex LLM/provider/runpod` (Credential Manager), passed to the
owned backend via env; set/delete restarts the owned backend. Details:
[session-first-run-design.md](session-first-run-design.md).

**Developer (`tauri dev` / debug):** venv Python `python -m app.runtime_entry` is allowed when the sidecar artifact is absent. An already-healthy Alex API on 8000–8019 is reused as `DEV_EXTERNAL` and is not owned/killed.

Health identity: `product=alex-llm`, `version=0.9.3`, `runtime_protocol_version=1`.

## 6. RunPod lifecycle

GPU does **not** start on app open, settings, or health. A real model-needed task may start **one** managed Pod, wait until the llama.cpp alias is healthy, then continue **the same** task. No duplicate Pods. Full application Quit stops managed compute. Network Volume `uwgeaie5b0` is **never** auto-deleted.

Details: [on-demand-ai.md](on-demand-ai.md), [runpod-controller.md](runpod-controller.md).

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

## 10. Useful branches / worktrees

Primary checkout: `C:\Users\Volkr\Documents\Codex\2026-09-13\x20\outputs\alex-llm`

| Branch | Worktree | Role |
|---|---|---|
| `main` | this repo | product |
| `feat/backend-sidecar-installer` | (same) | merged; keep |
| `feat/on-demand-ai-runtime` | — | merged; keep |
| `feat/product-runtime-foundation` | — | merged; keep |
| `planning/1.0-product-readiness` | `...\alex-llm-product` | planning only |
| `eval/real-world-suite` | `...\alex-llm-eval` | eval harness |
| `eval/093-*` | `...\alex-llm-*-verify` | historical eval |

Never edit another worktree by accident. Do not delete these worktrees.

Local leftover: `docs/screenshots/0.4/*.png` — do not commit or delete.

## 11. Current local test counts

Recorded 21 Sep 2026, no GPU, no TinyFish (session-first-run merge gate):

| Suite | Result |
|---|---|
| backend pytest | **424 passed**, 1 skipped |
| Ruff check / format | PASS |
| Alembic check (fresh temp SQLite → 0014) | PASS |
| Vitest | **31 passed** |
| TypeScript / Prettier / Vite | PASS |
| Playwright | **15 passed** |
| cargo test | host **17**, desktop **30** |
| npm audit --omit=dev | **0** |
| pip-audit | **0** (local package skipped) |
| Headless acceptance (packaged sidecar, isolated root) | **29/29 PASS** (`scripts/acceptance-session-first-run.py`) |
| Installed GUI smoke (real Tauri app, CDP-driven) | **25/25 PASS** (`apps/desktop/e2e/gui-smoke.mjs`) |

A leftover `apps/backend/alex.db` at an old revision is **not** the product data root. Prefer a fresh `DATABASE_URL` for `alembic check`.

## 12. Next slice

**FIVE-CHIP STATUS + ERROR / RECOVERY UX**

Do not start this unless explicitly tasked. After that: upgrade/backup → RC / 1.0.

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

# pip-audit (from apps\backend)
.\.venv\Scripts\python.exe -m pip_audit

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
9. `apps/desktop/src-tauri/src/backend.rs`
10. `apps/backend/app/runtime_entry.py`, `app/packaging.py`

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
