# Canalla LLM 1.0 — release notes

Canalla LLM **1.0.0** is the Windows desktop product: a chat and work application with its own
local backend, local data, and an optional shared cloud for compute. Installer artifact:
`Canalla LLM_1.0.0_x64-setup.exe` (87 180 763 bytes, SHA-256
`3465e6405cd5817be584b75eb11238a86e36b958f44776f9583d6008d1d22362`).

This page is written for two readers: a user installing or upgrading the app, and an operator
running (or supporting) the shared service. Design details live in the linked documents.

## 1. What 1.0 is

| Part | What it is | Where it runs |
|---|---|---|
| **Canalla LLM Desktop** | Tauri 2 + React/TypeScript window: chat, projects, memory, documents, tools, tasks, settings | your PC, per-user install |
| **Local backend** | packaged FastAPI sidecar (`alex-backend.exe`), owned by the Desktop; plus the native host `alex-host-loop.exe` | your PC (`%LOCALAPPDATA%\Alex LLM\` data) |
| **Canalla Cloud** | shared RunPod Gateway: one provider account behind many installations, shared balance, production inference | `https://gateway.12testers.store` |

Generated answers need compute; the rest of the product does not. Local accounts, chat history,
projects, memory, document upload and indexing (local CPU embeddings), settings and backups
work with no GPU and no cloud. An answer from the model needs either a **direct** RunPod key
(dev / private mode) or a **shared** Canalla Cloud enrollment — a packaged install is shared
even without an enrollment, so it reports Canalla Cloud as not connected instead of silently
falling back to a local provider credential. The mode is build/runtime configuration
(`ALEX_AI_MODE`), never a user setting.

What stays local in every mode: local accounts, chats, messages, projects, memories,
documents, tool runs, tasks, run history, and the compute policy of each local user. Canalla
Cloud stores none of it — it owns the provider credential, the global single-Pod rule, the
shared balance snapshot and the inference proxy.

## 2. Install

1. Run `Canalla LLM_1.0.0_x64-setup.exe`.
2. The installer is `currentUser`: it installs into `%LOCALAPPDATA%\Programs\Canalla LLM\`.
   No administrator rights, no Python, no git checkout, no `.venv`.
3. Start **Canalla LLM** from Start and register the first account (this machine's owner).
4. Connect compute, then chat:
   * **Shared (the packaged default):** Settings → **Canalla Cloud** → enroll with a one-time
     activation code, then «Запустить AI».
   * **Direct (dev / private):** Settings → AI / Compute → **RunPod API key**, with the Desktop
     started with `ALEX_AI_MODE=direct` (a build/runtime switch, not a UI setting). The key
     lands in Windows Credential Manager as `Alex LLM/provider/runpod` and is
     installation-global.

Two packaging notes:

* **The installer is unsigned.** Windows SmartScreen shows a warning and there is no publisher
  name yet.
* **There is no auto-updater.** Upgrading means running a newer installer over the existing
  install.

## 3. Upgrade from Alex LLM 0.9.3

Install the new build over the old one — nothing else is required.

| Step | What happens |
|---|---|
| 1 | Quit Canalla LLM / Alex LLM (Quit, not just closing the window) so the owned backend is stopped and the binaries are not in use |
| 2 | Run the new installer; it replaces binaries in `%LOCALAPPDATA%\Programs\Canalla LLM\` and removes a legacy `%LOCALAPPDATA%\Programs\Alex LLM\` programme installation (its binaries, Start Menu/desktop shortcuts, autostart value and Apps & features entry) so exactly one product remains |
| 3 | Nothing else to clean up: the legacy data root `%LOCALAPPDATA%\Alex LLM\`, the Credential Manager entries and the enrollment are untouched by design |
| 4 | First launch: if a database migration is pending, the sidecar creates and verifies a `pre_upgrade` backup **before** migrating |

What survives an over-install:

| Item | Location | After upgrade |
|---|---|---|
| Database (users, chats, memories, projects, preferences) | `%LOCALAPPDATA%\Alex LLM\data\alex.db` | migrated, never dropped |
| Documents | `%LOCALAPPDATA%\Alex LLM\documents\` | untouched |
| Local JWT and machine identity | `runtime\jwt.secret`, `runtime\install.id`, `device.json` | untouched |
| Signed-in device session | Credential Manager `Alex LLM/session/{id}` | still valid |
| RunPod key (direct mode) | Credential Manager `Alex LLM/provider/runpod` | untouched |
| Canalla Cloud enrollment | Credential Manager `Alex LLM/gateway/installation` | still enrolled, no new activation code |
| Compute Preferences of every local user | `compute_preferences` table in the local database | kept, with one intended change: a legacy profile that had no exact GPU is no longer pinned to `NVIDIA L40S` — automatic selection picks the cheapest compatible card instead |
| Backups | `%LOCALAPPDATA%\Alex LLM\backups\` | unchanged |

If the pre-upgrade backup cannot be created, the upgrade is **refused** rather than risking the
database: the sidecar exits with 15 (`pre_upgrade_backup_failed`), the database is untouched and
`runtime/migration.json` records `blocked_no_backup`. A migration that fails keeps both the
database and the backup, exits 12, and never creates an empty database in its place.

Acceptance: `apps/desktop/e2e/upgrade-over-install.mjs prepare-legacy` (older schema + data)
then `verify` (new build → data, session and enrollment intact).

## 4. Where your data lives

| Path | Contents |
|---|---|
| `%LOCALAPPDATA%\Programs\Canalla LLM\` | binaries: the Desktop executable `alex-llm.exe`, `alex-backend\alex-backend.exe` (packaged sidecar), `alex-host-loop.exe` |
| `%LOCALAPPDATA%\Alex LLM\data\alex.db` | SQLite database (Alembic migrates it on sidecar start) |
| `%LOCALAPPDATA%\Alex LLM\backups\` | verified local backups |
| `%LOCALAPPDATA%\Alex LLM\documents\` | your files |
| `%LOCALAPPDATA%\Alex LLM\models\embeddings\` | embedding cache (recreatable) |
| `%LOCALAPPDATA%\Alex LLM\runtime\` | local JWT, install id, shutdown token, `migration.json`, `backup-restore.json` |
| `%LOCALAPPDATA%\Alex LLM\logs\backend.log` | rotating backend log |

**Binary location and data location are deliberately different** (and the data root keeps its
old name). Reinstalling, repairing or uninstalling **never** deletes the data root;
uninstalling removes binaries only. Credential Manager entries are never touched by an
uninstall.

## 5. The five chips

The header shows five chips: **AI**, **Computer**, **Web**, **Tor**, **Memory**. They read the
subsystem that already owns the state and never start, adopt or stop anything.

| Chip | `ready` means |
|---|---|
| AI | the production model alias is healthy (`ready`/`generating`) — a running GPU alone is not enough |
| Computer | a paired local device (native host) answered recently |
| Web | never — a configured provider reports `configured`, because no health request is made for a chip |
| Tor | never — an open SOCKS5 port is `configured` and no verified-chain proof is stored (`verified_chain: false`, fail-closed routing) |
| Memory | memory is enabled and the subsystem answered |

Vocabulary: `ready | starting | configured | off | not_configured | unavailable | error |
degraded`. Every chip carries a message, a stable detail code, whether a retry can help and at
most one safe action; the UI always renders text, never colour alone. Next to the chips the
shared RunPod balance is shown read-only, with a stale marker instead of a fake `$0` when the
provider read fails. Design: [status-recovery-design.md](status-recovery-design.md).

## 6. Context meter

The composer shows a ring with what the next message will take, e.g.
`Context 12 480 / 32 768 · 38%`, and a breakdown of the parts that actually enter the prompt:
system, profile, pinned/project/general memory, documents, history and the current draft.

| Fact | Detail |
|---|---|
| Window | backend setting `LLM_CONTEXT_WINDOW` (default **32768**), which must match llama.cpp `--ctx-size` |
| Where the UI gets it | only from the backend snapshot; the frontend never hardcodes a window |
| Count | an estimate over the parts that are really sent (`ceil(latin/4) + ceil(non-latin/2)` + 4 per message); once a generation has run, the provider-reported prompt size is shown next to it |
| Thresholds | < 70 plain, 70–85 amber warning, > 85 red near-full |
| Side effects | none: no prompt is sent, no compute starts, memory counters are untouched |

Details: [context-usage.md](context-usage.md).

## 7. Compute Preferences (your money policy)

Settings → AI / Compute shows your own limits. Every local user has their own policy, and it
is a *preference*, not a product cap.

| Setting | Default for a new user |
|---|---|
| GPU selection | automatic — cheapest compatible |
| Minimum VRAM | 48 GB |
| Maximum price | **$0.52 / hour** |
| Session budget | **$3.00** |
| Idle auto-stop | 10 minutes |
| Retry GPU search | enabled |

You may raise or lower the price and the budget in both provider modes; the only bounds are
technical ($100/hour, $1000/session). The policy is stored in the local database, so it
survives logout, restart, install-over-install and backup/restore. Automatic mode always picks
the cheapest compatible GPU inside *your* maximum — raising the maximum never buys a more
expensive card. Start/stop of compute stays the machine owner's action, in both modes.

If a shared Pod is already running above your own maximum (you lowered it, or another
installation started it), the panel says so —
«Сейчас работает общий GPU за $X/час — это выше вашего предела $Y/час. Можно повысить предел
в настройках ниже.» — and never changes your setting for you.

Full contract and HTTP details: [compute-preferences.md](compute-preferences.md).

## 8. Canalla Cloud (the shared service)

| | |
|---|---|
| Public URL | `https://gateway.12testers.store` (Let's Encrypt; the internal service/user/unit name is still `alex-gateway`) |
| Enrollment | one-time activation code → installation credential in Credential Manager → short-lived Gateway JWT |
| What it owns | the RunPod master key, the one-managed-Pod lease, the shared balance snapshot, the money enforcement, the inference proxy |
| What it never has | your chats, memory, documents, local users, local keys; the master key never reaches a client |
| Balance | shared account balance, read-only, one cached snapshot for all installations |
| Limits | enforced per request from the policy the authenticated installation sent, inside technical bounds only |

Operations available in Settings → Canalla Cloud: «Запустить AI», «Остановить AI», and
disconnect (which revokes this installation; only an explicit disconnect removes the
credential — logout never does). There is no GPU picker or quote list in shared mode: the
Gateway picks the cheapest GPU inside your policy.

## 9. Backup and restore

* Backups are **directories** under `%LOCALAPPDATA%\Alex LLM\backups\`, each with a manifest,
  a consistent SQLite snapshot and your documents. Every file is hashed (SHA-256) and the
  manifest is written last, so an interrupted backup can never look complete.
* Kinds: `manual` (you asked), `pre_upgrade` (before a migration), `pre_restore` (safety copy
  taken by every restore).
* Retention is bounded: the newest 3 automatic and the newest 10 manual; the newest backup and
  the last verified one are never pruned.
* A restore verifies the backup, takes a `pre_restore` safety copy, stages and validates the
  data, swaps it in and rolls back if validation fails. It runs only while the owned backend is
  stopped and refuses an external backend. Backups and restores never start the GPU and never
  stop shared compute.
* Backups contain **no secrets and no machine identity**: `runtime/jwt.secret`, `install.id`,
  `session.id`, `device.json`, embedding caches and logs stay out, and no Credential Manager
  entry is ever exported. Restoring on another PC restores data only — the new machine keeps
  its own identity and enrolls with its own activation code.

Format and semantics: [backup-format.md](backup-format.md),
[upgrade-backup-design.md](upgrade-backup-design.md).

## 10. Honest limitations

* **The installer is unsigned** — SmartScreen warns on first run; there is no code signing or
  publisher name yet.
* **No auto-updater** — upgrades are manual installer runs; there is no update channel.
* **TinyFish Browser live execution/lifecycle (WM-07) is not closed** and the CD-08 REAL
  stale-SHA coverage debt remains.
* **No central account or licensing service** — enrollment is a one-time activation code
  created by the operator CLI; there is no self-service cloud account.
* **The deployed Gateway drives real compute.** It was certified on 22 Sep 2026: one NVIDIA
  L40S 48 GB at `$1.09/hour` (the cheapest *available* compatible card inside the escalated RC
  ceiling), a real Qwen answer streamed through the public endpoint, Stop and the following
  request on the same Pod, then a clean stop with no Pod left. Three edges are documented rather
  than fixed: the session label stays `generating` after a finished generation until the next
  state change; a single over-window *draft* is not trimmed (history, memory and documents have
  budgets, the draft does not), so the upstream refuses it and the client sees a typed
  `gateway_unavailable` naming the upstream status; and the provider's `currentSpendPerHr` keeps
  reporting a terminated Pod for a while after a stop.
* **Web and Tor chips report `configured`, not `ready`** — no provider health proof and no
  stored verified-Tor-chain proof exist.
* A real backend restart with a live Pod has not been live-tested.
* An access token stays valid until its short expiry after logout.
* **Legacy internal `Alex` identifiers remain on purpose** — data root `%LOCALAPPDATA%\Alex LLM\`,
  credential targets `Alex LLM/session/{id}`, `Alex LLM/provider/runpod`,
  `Alex LLM/gateway/installation`, bundle id `com.alexllm.desktop`, product ids `alex-llm` /
  `alex-llm-desktop` / `alex-llm-gateway`, binaries `alex-llm.exe` / `alex-backend.exe` /
  `alex-host-loop.exe`, `ALEX_*` variables, `X-Alex-*` headers, systemd `alex-gateway.service`
  and the endpoint `gateway.12testers.store`. Renaming them would strand data or enrollment.

## 11. If you operate the shared service

* The Gateway is a separate service and database on the existing VPS, installed under
  `/opt/alex-gateway`, running as `alex-gateway` on `127.0.0.1:9011` behind nginx. Runbook:
  [gateway-deployment.md](gateway-deployment.md); deployment facts and rollback:
  [gateway-12testers-deploy-audit.md](gateway-12testers-deploy-audit.md).
* Operator CLI (`create-code --label "PC A"`, `revoke --installation-id …`, `installations`,
  `audit`, `health`) is the only way enrollment codes are created today.
* Money policy is now client-declared and server-checked: `MAX_HOURLY_PRICE` /
  `MAX_SESSION_BUDGET` on the Gateway are the **defaults** used only when a request carries no
  policy, not ceilings a user cannot exceed. Technical bounds are `$100/hour` and
  `$1000/session`.
* No `/runpod/*` or `/provider/raw` passthrough exists, and no response ever returns the
  RunPod master key, the installation secret or a direct llama.cpp endpoint.
* Versions: the repository, the packaged binaries, the installer and the deployed Gateway all
  report `1.0.0`; the Gateway protocol version stays `1`. Some older documents in `docs/` still
  describe the 0.9.3 money ceilings or the `Alex Cloud` spelling — the current contract for money
  is [compute-preferences.md](compute-preferences.md), and the Naming section of `AGENTS.md`
  lists which identifiers keep the old spelling on purpose.

## 12. Live certification (22 September 2026)

Measured on the production path `desktop client → https://gateway.12testers.store → RunPod →
llama.cpp → Qwen`, one Pod for every step:

| Step | Result |
|---|---|
| Policy `$0.52/hour` | `price_limit` (capacity existed, nothing fitted the user's own maximum) |
| Escalation `$1.20/hour` | NVIDIA L40S 48 GB at `$1.09/hour` |
| Session ceiling | `$3.00` sent by the user, applied as `budget_usd = 3.000000` |
| Cold start | 29.1 s to model readiness |
| First answer | streaming, TTFT 0.95 s after readiness |
| Context 70% / 86% | 20 135 and 24 743 measured `prompt_tokens` vs the meter's 22 957 / 28 213 |
| Over-window input | typed refusal, no crash |
| Long answer | 1 400 chunks / 6 650 characters in 45.4 s, normal completion |
| Stop | cancelled after 5 chunks, same Pod answered the next request |
| Cleanup | stopped, no Pod, Network Volume `uwgeaie5b0` intact |
| Upgrade | `Alex LLM` programme install → one `Canalla LLM 1.0.0`, data root, 28 credentials and enrollment preserved |

## 13. Documentation map

| Topic | Document |
|---|---|
| Compute policy contract | [compute-preferences.md](compute-preferences.md) |
| Context meter | [context-usage.md](context-usage.md) |
| Status chips | [status-recovery-design.md](status-recovery-design.md) |
| Data / install layout | [installer-data-layout.md](installer-data-layout.md) |
| Backup format | [backup-format.md](backup-format.md) |
| Upgrade / backup design | [upgrade-backup-design.md](upgrade-backup-design.md) |
| On-demand AI | [on-demand-ai.md](on-demand-ai.md) |
| Gateway design and deploy | [central-runpod-gateway-design.md](central-runpod-gateway-design.md), [gateway-deployment.md](gateway-deployment.md) |
| Security invariants | [security.md](security.md) |
| Agent handoff | [AI-HANDOFF.md](AI-HANDOFF.md) |
