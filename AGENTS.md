# Alex LLM — agent instructions

PROJECT: **Alex LLM** (user-facing name: **Canalla LLM** — see Naming)
VERSION: **0.9.3 development** (do not bump unless asked)

Read `docs/AI-HANDOFF.md` before changing runtime, packaging, or compute.

## Naming

The product is branded **Canalla LLM** for users: window title, installer, Shortcuts, About,
chat labels, onboarding, assistant name in the system prompt, README.

These names deliberately keep the old `Alex LLM` spelling — renaming them would strand user
data or lose an enrollment. Do not "finish the rename" in them:

- data root `%LOCALAPPDATA%\Alex LLM\` and every path below it
- Credential Manager targets `Alex LLM/session/{id}`, `Alex LLM/provider/runpod`,
  `Alex LLM/gateway/installation`, `Alex LLM/device-credential`
- bundle identifier `com.alexllm.desktop`, product id `alex-llm`, `alex-llm-desktop`,
  gateway product `alex-llm-gateway`, `product.py`
- sidecar / host binaries `alex-backend.exe`, `alex-host-loop.exe`, `ALEX_*` env vars
- **Alex Cloud** — the shared Gateway service name, a separate brand

The installer installs into `%LOCALAPPDATA%\Programs\<product name>`, so a rename moves the
binaries (never the data). An older `Programs\Alex LLM` folder is left for the user to
uninstall from Apps & features.

The assistant persona is **Canalla LLM** too — chat labels, confirmations, status messages
and the system prompt say Canalla LLM, so do not reintroduce a bare `Alex` in UI text. Tool
descriptions, protocol headers (`X-Alex-*`), comments and fixture names keep `Alex`; they
are prompt-internal or protocol text, not branding.

## Architecture

Thin model + thick deterministic controller.

- MODEL: `orcarouter/Qwen3.8-27B-Uncensored` Q5_K_M (alias `orcarouter-qwen38-27b-q5km`)
- Served context window: `LLM_CONTEXT_WINDOW` (default **32768**, must match llama.cpp `--ctx-size`). One source of truth for the composer's context meter: `docs/context-usage.md`. The frontend never hardcodes a window.
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
- Five-chip status + error/recovery UX + shared RunPod balance
- Central RunPod Gateway / Cloud Control Plane v2 (implemented, **deployed** at
  `https://gateway.12testers.store`, merged into `main`)
- Canalla LLM user-facing brand + composer context meter (`docs/context-usage.md`)
- Upgrade / Backup / Data Preservation — implemented and pushed on `feat/upgrade-backup-data`,
  **not merged** (owner's merge decision pending); see the audit/design/format docs in `docs/`

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

## Two provider modes

- **Production shared:** many installations → Central Alex Gateway (`apps/gateway`) → one RunPod
  account. The master RunPod key exists **only** on the Gateway (server env/secret manager); it is
  never in a Desktop, installer, local backend, frontend, client Credential Manager, client SQLite,
  `localStorage`, client `.env`, client log or API response. Installations authenticate as
  installations (one-time activation code → installation credential → short-lived gateway JWT).
  The Gateway owns global compute (one managed Pod, database lease), the money caps ($1.20/h,
  $3/session), the shared balance cache and production inference (client → Gateway → llama.cpp).
- **Dev / private direct:** the local `Alex LLM/provider/runpod` credential and `RunPodController`
  keep working unchanged. Never migrate that key to the Gateway automatically, never delete it.
- The mode is build/runtime config (`ALEX_AI_MODE`), never a user-facing autonomy setting. An
  enrolled installation runs shared; without an enrollment the local backend stays direct.
- Local users stay local: `user_id`/`email`/`role` are never cloud identity. While shared mode is
  active the local compute lifecycle refuses with `gateway_managed_compute`; starting/stopping
  shared compute goes through the typed Gateway operations only.
- Do not add `/runpod/*` or `/provider/raw` passthrough endpoints, and never let a client raise the
  financial caps.

## Permanent security

- No provider secrets in code, UI, or logs
- No kill-by-name; do not kill unrelated processes
- Confirmations remain bound to an immutable payload
- TinyFish Agent is READ_ONLY
- Tor intent is fail-closed to Tor
- Production mock must not masquerade as real AI
- **RunPod key is ONE installation-global credential** (`Alex LLM/provider/runpod`): never per-user, never in
  the frontend, never deleted by logout, removed only by an explicit delete in Settings
- Status and balance are READ ONLY: they never start, adopt or stop compute, and never spend
- A status chip must not claim more than its subsystem proved (configured ≠ healthy)
- In production shared mode `GET /health` is a **local** liveness answer: readiness is cached
  (`GatewayProvider.READY_TTL_SECONDS`, single-flight background refresh) and must never perform a
  Gateway round trip. The Desktop's runtime probe allows 400 ms, and probing per request both broke
  the app's readiness and turned a healthy client into a hot loop against the shared Gateway.
- The Alex Cloud panel must never keep a transient state: `refreshCloud()` waits (bounded) for the
  first settled answer after a backend restart

## Known focused limitations

- WM-07 TinyFish Browser live execution/lifecycle
- CD-08 REAL stale-SHA coverage debt
- REAL backend restart with a live Pod is not yet live-tested
- No production code signing yet
- Live RunPod balance was accepted read-only **through the production credential path**: the key was saved
  in the installed app (Settings → AI / Compute → RunPod API key), landed in Credential Manager
  `Alex LLM/provider/runpod`, survived logout, full restart and reinstall, and served two users with the same
  shared balance; an explicit delete removed it for everyone. No `.env` is involved in the installed product.
- Web and Tor chips report `configured`, not `ready`: a real provider health probe and a stored verified-Tor-chain proof do not exist yet
- The Central Alex Gateway **is deployed** on the existing 12Testers VPS
  (`https://gateway.12testers.store`, Let's Encrypt, separate service/database) and is merged
  into `main`. Deployment facts, acceptance results, defects found and rollback/backup live in
  `docs/gateway-12testers-deploy-audit.md`; the runbook is `docs/gateway-deployment.md`.
  Enrollment still uses one-time activation codes created by the operator CLI — there is no
  central Alex account service yet, and that stays the documented limitation.
- Shared-mode acceptance is real and now public: two enrolled installations, one shared balance,
  revocation, the protocol guard and the absence of passthrough routes were accepted against the
  **deployed** Gateway over public HTTPS, and the operator's installed Alex was enrolled through the
  real UI (disconnect → restart → enroll → balance → leaks). No Pod was created and no GPU was started.
- The Gateway has not driven real compute yet: no Pod has ever been created through it, so
  `POST /compute/ensure` on the deployed service (and the deployed `/v1/chat/completions` proxy) is
  still unproven against a real Pod. Money caps, the single-Pod lease and the create-unknown path are
  covered by the FakeRunPod suite only.
- Shared compute has no quotes or GPU picker in the UI: the Gateway selects the cheapest GPU inside the
  server-side caps. The Settings → Alex Cloud panel offers «Запустить AI» / «Остановить AI» only.

Do not start LoRA yet. Before future LoRA: baseline + censorship/refusal + coding/tools/security + catastrophic-forgetting regression.

## Next product slice (not this checkout)

UPGRADE / BACKUP / DATA PRESERVATION — do not implement unless explicitly tasked.

Then: RC / 1.0 gates.

(Code signing and the WM-07/CD-08 limitations stay closed until separately tasked.)

## Git safety

Primary repo: this directory. Historical helper worktrees exist (`alex-llm-eval`, `alex-llm-product`, …). Never edit another worktree by accident. Verify `git branch --show-current`, `git status`, and `git rev-parse HEAD` before substantial changes.

Do not commit `docs/screenshots/0.4/*.png` leftover noise.

## Tests

No paid GPU or TinyFish unless the task explicitly requires REAL acceptance. Commands: `docs/AI-HANDOFF.md`.
