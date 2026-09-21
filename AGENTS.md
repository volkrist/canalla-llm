# Alex LLM — agent instructions

PROJECT: **Alex LLM**  
VERSION: **0.9.3 development** (do not bump unless asked)

Read `docs/AI-HANDOFF.md` before changing runtime, packaging, or compute.

## Architecture

Thin model + thick deterministic controller.

- MODEL: `orcarouter/Qwen3.8-27B-Uncensored` Q5_K_M (alias `orcarouter-qwen38-27b-q5km`)
- AUTONOMY: **HIGH** (fixed)
- RESEARCH_DEPTH: **DEEP** (fixed)
- Do not add autonomy/depth selectors.
- Do not change OrcaRouter.

Reliability stage: **CLOSED**. Do not reopen a broad reliability rewrite unless a demonstrated regression requires it.

## Completed product slices

- 0.9.3 reliability foundation
- Desktop-owned local backend
- On-demand RunPod lifecycle
- Backend Sidecar / Installer Foundation
- Session Restore / First-Run Foundation

## Production package

- Tauri Windows Desktop
- packaged PyInstaller onedir `alex-backend.exe`
- `alex-host-loop.exe`
- binaries: `%LOCALAPPDATA%\Programs\Alex LLM\`
- mutable data: `%LOCALAPPDATA%\Alex LLM\`
- production package does **not** require Python, a git checkout, or `.venv`
- production packaged Desktop must **not** search arbitrary `python.exe`
- heavy GGUF stays on RunPod Network Volume — never bundle ~20GB weights

Developer `tauri dev` may still use `apps/backend/.venv\Scripts\python.exe`.

## RunPod

- Network Volume: `uwgeaie5b0`
- **Never** automatically delete the Network Volume
- GPU does **not** start on app launch
- A model-needed task may start **one** managed Pod
- The same task resumes automatically after the model is ready
- No duplicate Pods
- Full application Quit stops a **managed** Pod (not external; not the volume)

## Permanent security

- No provider secrets in code, UI, or logs
- No kill-by-name; do not kill unrelated processes
- Confirmations stay bound to an immutable payload
- TinyFish Agent is READ_ONLY
- Tor intent is fail-closed to Tor
- Production mock must not masquerade as real AI

## Known focused limitations

- WM-07 TinyFish Browser live execution/lifecycle
- CD-08 REAL stale-SHA coverage debt
- REAL backend restart with a live Pod is not yet live-tested
- No production code signing yet

Do not start LoRA yet. Before future LoRA: baseline + censorship/refusal + coding/tools/security + catastrophic-forgetting regression.

## Next product slice (not this checkout)

FIVE-CHIP STATUS + ERROR / RECOVERY UX — do not implement unless explicitly tasked.

Then: upgrade/backup → RC / 1.0.

## Git safety

Primary repo: this directory. Historical helper worktrees exist (`alex-llm-eval`, `alex-llm-product`, …). Never edit another worktree by accident. Verify `git branch --show-current`, `git status`, and `git rev-parse HEAD` before substantial changes.

Do not commit `docs/screenshots/0.4/*.png` leftover noise.

## Tests

No paid GPU or TinyFish unless the task explicitly requires REAL acceptance. Commands: `docs/AI-HANDOFF.md`.
