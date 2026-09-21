# Changelog

User-visible changes of the Canalla LLM Windows product (desktop app, local backend and the
shared Canalla Cloud Gateway). Internal `Alex LLM` names — data root, credential targets,
product/bundle ids, binaries, environment variables, `X-Alex-*` protocol headers and the
public endpoint — are unchanged on purpose; see the Naming section of `AGENTS.md`.

## [1.0.0]

Release candidate on `release/canalla-1.0-rc`. Installer artifact:
`Canalla LLM_1.0.0_x64-setup.exe`.

### Added

- **Desktop product in installable shape** — Tauri 2 + React/TypeScript window, packaged
  PyInstaller sidecar `alex-backend.exe` and the native host `alex-host-loop.exe`, installed
  per user. The production package needs no Python, no git checkout and no `.venv`.
- **Persistent sessions** — access JWT (60 min) plus a device session whose refresh secret is
  rotated on every refresh, stored in Windows Credential Manager (`Alex LLM/session/{id}`),
  sliding 30 days bounded by an absolute 90; first-run owner bootstrap (`/auth/bootstrap`,
  closes permanently).
- **Local accounts and private data** — registration/login, chats, projects, memories and
  documents per user, with ownership checks on every endpoint.
- **Memory and RAG** — pinned, general and project memories; document upload, local CPU
  embedding, owner/project-scoped retrieval and visible sources under an answer.
- **Tools** — TinyFish Web (Off/Auto/On, Search/Fetch, read-only Agent), Local Computer with a
  risk policy, Tor with fail-closed routing, and the autonomous task agent (persistent plan,
  pause/resume/stop, budgets, verification before Completed).
- **On-demand RunPod** — the GPU does not start with the app; a model-needed task may start
  exactly one managed Pod, the same task resumes when the model is ready, no duplicate Pods,
  and Network Volume `uwgeaie5b0` is never deleted automatically.
- **Canalla Cloud (shared compute)** — one-time activation code → installation credential →
  short-lived Gateway JWT, one shared RunPod balance, production inference proxied through
  the Gateway. The master RunPod key exists only server-side.
- **Five-chip truthful status** — AI, Computer, Web, Tor, Memory with one vocabulary
  (`ready | starting | configured | off | not_configured | unavailable | error | degraded`),
  a message, a stable detail code, recoverability and at most one safe action per chip.
- **Local verified backup and restore** — backups are directories with a SHA-256 inventory and
  a manifest written last (`manual`, `pre_upgrade`, `pre_restore`), bounded retention, a
  pre-restore safety copy and rollback. A restore is a Desktop-owned operation and never runs
  against a live backend.
- **Pre-upgrade protection** — a pending migration runs only after a verified `pre_upgrade`
  snapshot; if the snapshot cannot be created the upgrade is refused (exit 15) and the
  database is left untouched.
- **Composer context meter** — a ring with `Context 12 480 / 32 768 · 38%` and a per-part
  breakdown of system, profile, memory, documents, history and draft.
- **Per-user Compute Preferences** — every local user owns their own money policy (defaults
  `$0.52/hour`, `$3.00/session`, 48 GB VRAM floor, 10-minute idle stop, automatic cheapest
  compatible selection, retry search), editable in both provider modes and stored locally.
  See [docs/compute-preferences.md](docs/compute-preferences.md).
- **Public HTTPS Gateway** — the shared service is deployed (`https://gateway.12testers.store`,
  Let's Encrypt, own service and database) and was accepted over public HTTPS with two
  enrolled installations, one shared balance and a working revocation.
- **Installer over-install upgrade path** — a newer build installed over an older one keeps
  data, session and enrollment; accepted by `apps/desktop/e2e/upgrade-over-install.mjs`.

### Changed

- **Brand** — user-visible text says **Canalla LLM**; the shared cloud service says
  **Canalla Cloud**. Internals keep the `Alex LLM` spelling (data root, Credential Manager
  targets, `com.alexllm.desktop`, `alex-llm`, `alex-llm-desktop`, `alex-llm-gateway`,
  `alex-llm.exe` / `alex-backend.exe` / `alex-host-loop.exe`, `ALEX_*`, `X-Alex-*`,
  `gateway.12testers.store`).
- **Money semantics** — the `$1.20/hour` and `$3.00/session` *product caps* are gone.
  `$0.52/hour` and `$3.00/session` are now defaults a new user starts with, and the values are
  the user's own: the Gateway enforces the policy the authenticated installation sent, inside
  technical bounds only (`$100/hour`, `$1000/session`).
- **Policy validation instead of clamping** — a malformed or out-of-range policy is rejected
  with the typed error `compute_policy_invalid` (HTTP 422) rather than silently replaced by a
  server value.
- **Selection** — automatic mode always picks the cheapest compatible GPU inside the user's own
  maximum, so a higher maximum never buys a more expensive GPU; a stray `gpu_id` is ignored
  unless the client explicitly asks for `selection: "manual"`.
- **Policy lifetime** — the saved policy follows the user across logout, restart, upgrade and
  backup/restore, and saving it no longer rewrites another user's running session.
- **Install location** — the installer places binaries in
  `%LOCALAPPDATA%\Programs\Canalla LLM\`. The data root, credentials and enrollment keep their
  existing paths, so an upgrade from Alex LLM 0.9.3 keeps everything.
- **Cloud copy** — UI and backend messages about the shared service say Canalla Cloud.

### Fixed

- A user's own policy is honoured instead of being clamped to a product ceiling; the old clamp
  silently replaced the value the user had set.
- Preferences without an exact GPU no longer get `NVIDIA L40S` injected, so a legacy profile
  cannot pin automatic selection to a more expensive card.
- Saving your own limits is no longer refused with `403` while somebody else's managed session
  is running; that running session is now simply left untouched.
- A GPU that still exists but no longer fits the policy reports `price_changed` instead of
  silently restarting the search — no silent price escalation in either direction.
- A running shared Pod that costs more than the current user's own maximum is now stated as a
  fact («Сейчас работает общий GPU за $X/час — это выше вашего предела $Y/час…») instead of
  staying silent — and it is never repaired automatically.

### Known limitations

- **The installer is unsigned.** Windows SmartScreen warns on first run and there is no
  publisher name yet.
- **There is no auto-updater.** Upgrading means running the newer installer over the existing
  install; there is no network updater and no update channel.
- **TinyFish Browser live execution/lifecycle (WM-07) is not closed**, and the CD-08 REAL
  stale-SHA coverage debt remains.
- **There is no central account or licensing service.** Enrollments come from one-time
  activation codes created by the operator CLI on the Gateway.
- **The deployed Gateway has never driven real compute.** No Pod has been created through it,
  so `POST /compute/ensure` and the deployed `/v1/chat/completions` proxy are unproven against
  a real Pod; the money rules, the single-Pod lease and the create-unknown path are covered by
  the FakeRunPod suite only.
- **Web and Tor report `configured`, never `ready`** — a real provider health probe and a
  stored verified-Tor-chain proof do not exist yet.
- A real backend restart with a live Pod has not been live-tested.
- An access token stays valid until its short expiry after logout.
- Shared compute has no quotes or GPU picker in the UI: the Gateway selects the cheapest GPU
  inside the user's own policy, and the Canalla Cloud panel only offers «Запустить AI» /
  «Остановить AI».
- `Alex` remains in internal identifiers, protocol headers and tool descriptions.
