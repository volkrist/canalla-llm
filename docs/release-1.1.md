# Canalla LLM 1.1 — release notes

1.1.0 is the release after 1.0.0. It closes one real product defect, finishes the compact
workspace the 1.0.0 certification asked for, and puts the project's type checking and acceptance
tooling on a checked baseline. Nothing about the contract changed: the runtime protocol and the
Gateway protocol are both still `1`.

- Branch: `release/canalla-1.1.0`
- Installer: `Canalla LLM_1.1.0_x64-setup.exe`
- Gateway: product version `1.1.0`, protocol `1`

## 1. What changed for a user

**The Pod that never became ready.** A managed Pod that stayed in `creating`, `starting_pod` or
`loading_model` kept billing until the session budget ran out, because the idle policy only owned
`ready` and `generating` compute. One real incident billed 39.5 minutes ($0.72). The Gateway now
has a server-side startup deadline — five minutes by default, configurable only on the server —
measured from the persisted create intent. When it closes, the Gateway stops the Pod through the
normal managed path, reports `startup_timeout` (recoverable: the user may start again), keeps the
audit trail and leaves the Network Volume and any second Pod alone. This protects the user even
when the client, the harness or the process has already gone: the Gateway is the authority, not
the `finally` block of whoever started it.

**The context meter and long drafts.** The composer's preview was a GET with the draft in the
query string. A large Cyrillic draft is about 5.5× longer on the wire once percent-encoded, and a
request line over ~65 536 bytes is refused — so the meter failed at roughly 11 000 characters
while the composer itself allows 32 000. The preview is now a POST with the draft in the body
(`{"prompt": …}`); the read-only GET remains for compatibility. A failed snapshot no longer leaves
the previous percentage on screen as if it were current: the meter shows «Context недоступен» and
recovers by itself on the next successful read.

**A calmer screen.** AI, Computer, Web, Tor and Memory are small chips in the top bar, immediately
before Connected, with their details in a popover. The balance is one thin secondary line. An
offline Computer is a single line with its Connect action. The compute state is one line, and the
long explanation lives in AI / Compute. The startup banner is a thin strip that disappears the
moment the backend is ready. The composer is one control row. The chat workspace starts about
170 px higher than in 1.0.0.

**Settings is the one place for the service state.** The 14 sections are grouped (Общие, Личное,
AI и инструменты, Данные, Система), and Профиль, Пользователи, Проекты and Память render inside
the Settings dialog rather than in a panel over the app. Поиск по файлам (the embedding model,
with its prepare/retry actions) now has a home in Данные. One device snapshot feeds the top-bar
chip, the Computer bar and Settings, so they can no longer disagree by up to a polling interval.

**Computer and Tor are ready before you touch anything.** They are services, not switches. On a
normal launch, with no button pressed, both chips turn green on their own and stay green: the mode
(`Ask`, `Off`, `Auto`) is policy and never makes a healthy service look offline. Tor is discovered
(configured endpoint, then `127.0.0.1:9050`, then `127.0.0.1:9150`), started when nothing answers,
and it claims «Готово» only after a real SOCKS5h round trip came back as Tor — an open listener is
«Настроено», never «Готово», and a cold bootstrap says «Подключается…» with Tor's own percentage.
Recovery is automatic; the manual actions («Проверить снова», «Запустить Tor», «Подключить») stay
as fallbacks, and a request that needs Tor still fails closed instead of falling back to clearnet.
The Computer host pairs, heartbeats and reconnects by itself, keeps the same `device_id` across
restarts and no longer shows a red chip with a Connect button for a reconnect that is already in
flight. The popovers separate health (`Состояние`) from policy (`Режим`) for both.

## 2. Install and upgrade

Run `Canalla LLM_1.1.0_x64-setup.exe` over the installed Canalla LLM. The installer is per user,
installs into `%LOCALAPPDATA%\Programs\Canalla LLM\`, and never touches the data root
(`%LOCALAPPDATA%\Alex LLM\`) or Windows Credential Manager. Upgrading from 1.0.0 preserves the
local session, chats, projects, memories, documents, the Gateway enrollment, the shared balance
and the saved Compute Preferences; the acceptance run that proves it is in section 5.

## 3. Where your data lives

| | Path |
|---|---|
| Binaries | `%LOCALAPPDATA%\Programs\Canalla LLM\` |
| Data root | `%LOCALAPPDATA%\Alex LLM\` |
| Database | `<data root>\data\alex.db` |
| Backups | `<data root>\backups\` (3 automatic / 10 manual, newest and last verified never pruned) |
| Logs | `<data root>\logs\backend.log` |

The `Alex LLM` spelling in the data root, the credential targets and the internal product ids is
deliberate and unchanged: renaming them would strand user data. See the Naming section of
`AGENTS.md`.

## 4. Compute money policy

Unchanged from 1.0.0, and worth repeating because it is the user's own decision: the defaults for
a new user are **$0.52/hour** and **$3.00/session** (48 GB VRAM floor, 10-minute idle stop,
automatic selection), and any authenticated local user may raise or lower both for themselves in
both provider modes. The only bounds are technical ($100/hour, $1000/session); the Gateway
honours the value the installation sent and answers `compute_policy_invalid` (422) for a malformed
one instead of silently replacing it. Automatic mode always picks the cheapest compatible GPU
inside the user's own maximum.

## 5. Acceptance performed for this candidate

Everything below ran on the exact 1.1.0 artifact. A live GPU sanity was attempted twice and is
externally blocked (section 7).

| Gate | Result |
|---|---|
| BasedPyright (`npx basedpyright`) | 0 errors, 0 warnings, 226 files |
| Backend pytest | 606 passed, 1 skipped |
| Backend ruff (`check` + `format --check`) | PASS |
| Backend alembic (`upgrade head`, `check`) | PASS (head `0015`, no new operations) |
| Gateway pytest | 130 passed (112 + 18 D-9 cases) |
| Gateway ruff, alembic | PASS (head `0001_gateway_core`) |
| Vitest | 161 passed |
| TypeScript (`tsc -b`), Prettier, Vite build | PASS |
| Playwright | 20 passed |
| `cargo check`, `cargo test` | PASS (69 tests: host 18, desktop 51) |
| `npm audit --omit=dev`, `pip-audit` | 0 vulnerabilities / clean |
| Harness lifecycle matrix (`acceptance-post-release-1.1.0.py --self-test`) | 16/16 PASS |
| Harness lifecycle matrix (`acceptance-live-rc-final.py --self-test`) | 28/28 PASS |
| Installed always-ready (`e2e/always-ready.mjs`) | 21/21 PASS — Computer and Tor green on a normal launch *and* after a restart, no clicks; same `device_id`, one device |
| Installed cloud-default-check | 15/15 PASS |
| Installed GUI smoke | PASS |
| Installed backup smoke | PASS |
| Installed cloud smoke (`scripts/cloud-smoke-local.py`) | PASS (Scenario J skipped: needs `ALEX_SMOKE_SETUP`) |
| Upgrade 1.0.0 → 1.1.0 (real installer over the installed build) | 8/8 PASS — data, session, enrollment, identity and balance preserved, migration `0014` → `0015` with a verified pre-upgrade backup |

## 6. Operator notes (the shared service)

The deployed Gateway was upgraded to 1.1.0 from a verified database snapshot:

- `/opt/alex-gateway/releases/adb568734430` (built from the release branch), `current` switched
  atomically, only `alex-gateway.service` restarted.
- `/health` → `version 1.1.0`, `gateway_protocol_version 1`, `ready true`, `database ok`,
  `provider_configured true`.
- The database was snapshotted first (`alex-gateway-backup.sh`, integrity and foreign keys
  verified); the 12Testers site, its API, nginx, its backend service and its PM2 application were
  left running and verified healthy.
- A rollback is `current` → `releases/9bd75486c34b` (the 1.0.0 release) plus a service restart;
  no migration ran for this release, so the previous release works against the same schema.

## 7. Honest limitations of this release

- **The live GPU sanity is externally blocked.** Two 60-second capacity windows (the release rule
  forbids waiting longer) both answered `gpu_unavailable`: RunPod's catalogue lists compatible
  GPUs but reports no usable stock for the account's datacentre, so no Pod was created and nothing
  was spent. The Basic → Stop → After-Stop cases therefore remain *unverified on 1.1.0 hardware*,
  while the same path was proven on 1.0.0 on 22 September 2026 and the Gateway code that serves it
  carries 18 new deterministic D-9 cases.
- **The installer is unsigned** (SmartScreen warns), there is no auto-updater, and WM-07
  (TinyFish Browser live lifecycle) and CD-08 (REAL stale-SHA coverage) stay open.
- **The startup deadline is server-side and fixed at 300 seconds by default.** It is configurable
  through the Gateway's environment, never from a client; there is no per-user setting.
- **An installation's `client_version` is enrollment-time metadata.** It is not refreshed on token
  refresh, so the operator's installation list can name an older client after an upgrade. Nothing
  in routing, policy or security reads it; updating it would add a field to the token request, so
  it stays documented rather than changed in a patch release.
- **Web still reports `configured`, not `ready`** — TinyFish has no health probe. Tor does: it
  turns green only on a persisted SOCKS5h proof that the route is really Tor.
- **Canalla does not start itself at Windows login** (no `Run` entry, no «start with Windows»
  toggle). After a PC restart the Computer host is back as soon as Canalla is launched — which is
  when the device is usable — and that launch restores the same pairing without a button; the
  manual path for an earlier start is the Startup folder or Task Scheduler. The managed Tor needs
  a local Tor (Tor Browser's bundle, `PATH` or `Program Files\Tor`); without one, the honest state
  is `unavailable` with `tor_not_installed` instead of a green chip.
- A real backend restart with a live Pod has still not been live-tested.

## 8. Documentation map

- [CHANGELOG.md](../CHANGELOG.md) — the same changes in changelog form.
- [context-usage.md](context-usage.md) — the context meter's contract, including the new POST preview.
- [tor.md](tor.md) — the Tor service, its proof, its states and the routing rules that are unchanged.
- [local-computer.md](local-computer.md) — the host, pairing, the always-ready windows and the no-autostart limitation.
- [compute-preferences.md](compute-preferences.md) — the money policy and the startup deadline.
- [gateway-deployment.md](gateway-deployment.md) — deploy, upgrade and rollback on the operator side.
- [gateway-12testers-deploy-audit.md](gateway-12testers-deploy-audit.md) — what the first deployment found.
- [AI-HANDOFF.md](AI-HANDOFF.md) — the current state, commands and known limitations for the next session.
