# Changelog

User-visible changes of the Canalla LLM Windows product (desktop app, local backend and the
shared Canalla Cloud Gateway). Internal `Alex LLM` names — data root, credential targets,
product/bundle ids, binaries, environment variables, `X-Alex-*` protocol headers and the
public endpoint — are unchanged on purpose; see the Naming section of `AGENTS.md`.

## [1.1.0]

Release candidate on `release/canalla-1.1.0`. Installer artifact:
`Canalla LLM_1.1.0_x64-setup.exe`. Runtime and Gateway protocols stay at `1`.

### Added

- **Computer and Tor are always-ready services, not on-demand switches.** After a normal launch,
  with no button pressed, both chips turn green on their own, and they stay ready when the mode is
  `Ask`/`Off`/`Auto` because the mode is policy, not health.
  - **Tor** is discovered (configured endpoint → `127.0.0.1:9050` → `127.0.0.1:9150`), started when
    nothing answers (Tor Browser's `tor.exe`, `PATH`, `Program Files\Tor`), and it turns `ready`
    only after a SOCKS5h round trip came back as Tor — an open listener is `Настроено`, never
    «Готово». A cold bootstrap is reported as «Подключается…» with the real percentage from Tor's
    own log. Recovery is automatic and bounded; the manual action stays as «Проверить снова» /
    «Запустить Tor». A request that needs Tor still fails closed — there is no clearnet fallback —
    and the popover separates `Состояние` from `Режим`, `SOCKS`, the proof and its age.
  - **Computer** pairs the device, heartbeats it every 8 s and reconnects by itself: a heartbeat
    younger than 45 s is «Готово», a host that was alive moments ago is
    «восстанавливает соединение» instead of a red chip with a button, and only a genuinely absent
    host reaches `Недоступно` / `host_offline`. Pairing is reused across restarts (same
    `device_id`, no second device) and the manual «Подключить» is the fallback for a revoked or
    missing credential.

### Fixed

- **A Pod that never became ready no longer bills forever (D-9).** The Gateway now enforces a
  server-side startup deadline (`COMPUTE_STARTUP_TIMEOUT_SECONDS`, 5 minutes by default): a
  managed Pod still in `creating`, `starting_pod` or `loading_model` when the deadline closes is
  stopped by the Gateway itself, classified `startup_timeout`, reported as recoverable, and the
  Network Volume and any second Pod are untouched. The deadline is measured from persisted
  timestamps, so it survives a vanished client, a Gateway restart or a killed process. A real
  incident billed 39.5 minutes ($0.72) before this existed.
- **The context meter works with long drafts.** The composer's preview travelled in a GET query
  string, so a large Cyrillic draft failed at the request line (about 11 000 characters) long
  before the composer's own 32 000 character limit. The draft now travels in a POST body, and the
  read-only GET stays for compatibility.
- **A failed meter read no longer shows a stale number.** The meter says «Context недоступен» and
  recovers on the next successful read instead of presenting an older percent as current.
- **A refused compute stop is never reported as a completed one.** A termination the provider
  refuses stays visible on the control row and is retried on the next lifecycle tick.

### Changed

- **A compact workspace instead of a dashboard.** AI, Computer, Web, Tor and Memory are small
  chips in the top bar next to Connected, with details in a popover; the balance is one thin
  secondary line; an offline Computer is a single line with its Connect action; the startup banner
  is a thin transient strip. The chat workspace starts about 170 px higher.
- **Settings groups its 14 sections** (Общие, Личное, AI и инструменты, Данные, Система), and
  Профиль, Пользователи, Проекты and Поиск по файлам (the embedding model) live inside the
  Settings dialog instead of a panel over the app. One device snapshot feeds the chip, the
  Computer bar and Settings, so they can no longer disagree.
- **The composer is one control row** (Web, Tor, Computer, paperclip, device dot, send) without the
  decorative helper texts; the context breakdown stays behind a click.

### Internal

- **Strict project type checking.** `pyrightconfig.json` with a checked project baseline (mode
  `standard`, the real import roots, generated directories excluded) and the annotations and
  None-narrowing that go with it: BasedPyright reports 0 errors and 0 warnings over 226 files
  (previously 670 diagnostics), with four typed runtime edge cases fixed rather than silenced.
- **Release and acceptance tooling.** `scripts/acceptance-post-release-1.1.0.py` certifies the
  installed sidecar on an isolated root, always stops managed compute in a `finally` block and
  keeps evidence when a run fails; its capacity window is 60 seconds, and a capacity drought is
  reported as an external blocker instead of being waited out. `scripts/cloud-smoke-local.py`
  makes the installed cloud smoke runnable from scratch (temporary local Gateway, temporary
  database, throwaway activation code).
- **Acceptance harnesses no longer touch the machine's real device credential.** `ALEX_DEVICE_DIR`
  moves `device.json` but not the credential, which is one machine-wide Credential Manager entry,
  so the installed smokes now borrow a run-owned `ALEX_DEVICE_CREDENTIAL_TARGET`, delete it (and
  their own session credentials) again, and say so in their pass/fail output;
  `e2e/always-ready.mjs` also relaunches the same install to prove that the pairing and the session
  come back unchanged. A unit test pins the redirection.

### Known limitations

- **The installer is unsigned.** Windows SmartScreen warns on first run and there is no
  publisher name yet.
- **There is no auto-updater.** Upgrading means running the newer installer over the existing
  install; there is no network updater and no update channel.
- **TinyFish Browser live execution/lifecycle (WM-07) is not closed**, and the CD-08 REAL
  stale-SHA coverage debt remains.
- **There is no central account or licensing service.** Enrollments come from one-time
  activation codes created by the operator CLI on the Gateway.
- **An installation's `client_version` is written once, at enrollment.** A token refresh updates
  `last_seen_at` only, so the Gateway's installation list can name an older client after an
  upgrade. It is operator metadata: nothing in routing, policy or security reads it, and updating
  it would add a field to the token request, so it stays documented instead.
- **Canalla does not start itself at Windows login.** The installer registers no `Run` entry (on
  upgrade it only removes the legacy `Alex LLM` one) and 1.1.0 ships no «start with Windows»
  toggle, so a *restarted PC* brings the Computer host back when Canalla is launched — not before.
  The pairing, the credential and the `device_id` are preserved, so that launch needs no button
  and no re-pair; a user who wants the app up earlier can add the Startup folder or a Task
  Scheduler entry. The shipped `alex-host-loop.exe` is the headless variant for E2E and is not
  started by the product.
- **The live GPU sanity for 1.1.0 is externally blocked by provider capacity** (RunPod reported
  `gpu_unavailable` for every catalogue entry in two 60-second windows, so no Pod was created and
  nothing was spent). The release candidate is otherwise fully accepted; see
  [release-1.1.md](docs/release-1.1.md).
- **The deployed Gateway drives real compute**, and the operational edges that the first live
  certification found are documented rather than fixed: the session label stays `generating`
  after a finished generation until the next state change (the Pod is available and the next
  request answers on it); an over-window *draft* is not trimmed by the context builder — history,
  memory and documents have budgets, a single oversized draft does not — so the upstream refuses
  it and the client sees a typed `gateway_unavailable` naming the upstream status; and the
  provider's `currentSpendPerHr` keeps reporting a terminated Pod for a while after a stop.
- **Web reports `configured`, never `ready`** — TinyFish has no health probe yet. Tor does have a
  real proof since 1.1.0: a persisted SOCKS5h round trip that came back as Tor.
- A real backend restart with a live Pod has not been live-tested.
- An access token stays valid until its short expiry after logout.
- Shared compute has no quotes or GPU picker in the UI: the Gateway selects the cheapest GPU
  inside the user's own policy, and the Canalla Cloud panel only offers «Запустить AI» /
  «Остановить AI».
- `Alex` remains in internal identifiers, protocol headers and tool descriptions.

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
- **Legacy programme install on upgrade** — the installer removes only a previous
  `%LOCALAPPDATA%\Programs\Alex LLM\` *binary* installation (guarded by its `alex-llm.exe`),
  its Start Menu and desktop shortcuts, its autostart value and its Apps & features entry, so an
  upgrade leaves exactly one installed product. The data root, the Credential Manager entries
  and the enrollment are never touched.

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

### Verified on 22 September 2026 (the production path)

The complete path was accepted against the deployed service, not against a mock:

| Step | Measured |
|---|---|
| User policy `$0.52/hour` | `price_limit` — capacity existed, nothing fitted the user's own maximum |
| RC escalation `$1.20/hour` | one NVIDIA L40S 48 GB at **$1.09/hour**, the cheapest *available* compatible card |
| Session ceiling sent | `$3.00` (the user's saved budget) → session `budget_usd = 3.000000`, never the balance |
| Cold start | Pod create → model ready in **29.1 s** (22.5 s after create) |
| First answer | streaming, TTFT **0.95 s** after readiness, one terminal event, no replay |
| Context ~70% | measured `prompt_tokens` **20 135** against the meter's estimate 22 957 (−14.0%) |
| Context ~86% | measured `prompt_tokens` **24 743** (19 619 of them cached from the previous call) |
| Over-window input | refused by the upstream — the client sees a typed `gateway_unavailable` with the upstream status in the detail, no crash |
| Long answer | **1 400 chunks / 6 650 characters** in 45.4 s through Cloudflare and nginx, no buffering, normal completion |
| Stop | cancelled after 5 chunks; the Pod stayed available and the **next request answered on the same Pod** |
| Cleanup | managed stop accepted, no session, no Pod left, Network Volume `uwgeaie5b0` untouched |

The same cut was accepted as an installer: `Canalla LLM_1.0.0_x64-setup.exe` installed over a
legacy `Alex LLM` programme install left exactly one product, removed the legacy binaries,
shortcut, autostart value and uninstall entry, and preserved the data root (identical database
hash), all 28 Credential Manager entries and the Gateway enrollment.

### Known limitations

- **The installer is unsigned.** Windows SmartScreen warns on first run and there is no
  publisher name yet.
- **There is no auto-updater.** Upgrading means running the newer installer over the existing
  install; there is no network updater and no update channel.
- **TinyFish Browser live execution/lifecycle (WM-07) is not closed**, and the CD-08 REAL
  stale-SHA coverage debt remains.
- **There is no central account or licensing service.** Enrollments come from one-time
  activation codes created by the operator CLI on the Gateway.
- **The deployed Gateway drives real compute**, and the operational edges that the first live
  certification found are documented rather than fixed: the session label stays `generating`
  after a finished generation until the next state change (the Pod is available and the next
  request answers on it); an over-window *draft* is not trimmed by the context builder — history,
  memory and documents have budgets, a single oversized draft does not — so the upstream refuses
  it and the client sees a typed `gateway_unavailable` naming the upstream status; and the
  provider's `currentSpendPerHr` keeps reporting a terminated Pod for a while after a stop.
- **Web and Tor report `configured`, never `ready`** — a real provider health probe and a
  stored verified-Tor-chain proof do not exist yet.
- A real backend restart with a live Pod has not been live-tested.
- An access token stays valid until its short expiry after logout.
- Shared compute has no quotes or GPU picker in the UI: the Gateway selects the cheapest GPU
  inside the user's own policy, and the Canalla Cloud panel only offers «Запустить AI» /
  «Остановить AI».
- `Alex` remains in internal identifiers, protocol headers and tool descriptions.
