# Local Computer Host in 0.8.0

0.9 autonomous tasks bind Local Computer actions to one paired device and an
exclusive workspace WRITE lock. See [autonomous-tasks.md](autonomous-tasks.md).

Local tools run on the paired Tauri host, not as a FastAPI shell. FastAPI only
creates `waiting_host` jobs and records the host result.

## Full computer access

Alex can read and change the local computer: all drive letters, mounted volumes,
system paths and user directories. UNC, `..`, NUL and secret files (`.env`, SSH
keys, browser stores, wallets) stay denied.

**Trusted Workspace** is not a jail. It is the area where safe `NORMAL_CHANGE`
actions (create/write/copy/move and processes whose `cwd` is inside the roots)
run with fewer confirmations. `READ` is allowed on the whole computer in Trusted
mode. `SENSITIVE` and `CRITICAL` always wait for an explicit one-time approval.

## Risk levels

| Level | Examples |
| --- | --- |
| READ | list/read/search, system info, process status, registry read |
| NORMAL_CHANGE | write/copy/move/mkdir, run_process, Python, PowerShell, stop_process |
| SENSITIVE | delete file/directory, registry write, services, scheduled tasks, firewall, env, install/uninstall, credential use, anything with `elevate` |
| CRITICAL | mass delete, format/partition, boot config, BitLocker, shutdown/reboot |

Before every SENSITIVE/CRITICAL action the UI shows reason, action, target,
consequences, risk level and whether UAC/admin is required, then waits for
**Разрешить один раз** or **Отмена**. CRITICAL has no Always allow. Approval is
bound to a digest of `action` + `arguments` (path/command included). If the
payload changes, the old approval is invalid.

Alex may start an elevated operation (`elevate=true` → Windows UAC via
`Start-Process -Verb RunAs`). It does not bypass UAC and does not store an
admin password. The explanation is shown before UAC.

Delete, registry, services and install remain typed tools. Automated tests use
disposable files, `HKCU\Software\AlexLLM\Test`, mocks and contract tests. They
never format disks, change boot config, or shut down the machine. Irreversible
disk/boot/power host actions also require `ALEX_EXECUTE_CRITICAL=1`.

## Pairing

An authenticated desktop user registers a device. The backend issues a random
`device_id` (UUID) and a ≥256-bit credential (`token_urlsafe(32)`). The
credential is stored as a SHA-256 hash. The plaintext is returned once on pair
and is stored by the native host:

1. Windows Credential Manager target `Alex LLM/device-credential`
2. Fallback: DPAPI-protected `%LOCALAPPDATA%\Alex LLM\device.cred.dpapi`

The renderer never receives the device credential after `pair_device`.
`device.json` keeps only `device_id` and a user-defined display name. Default
display name is `Windows device` (rename to `PC-1`, `Laptop`, …). Pairing does
not collect MAC, motherboard/disk serial, MachineGuid, CPU id, Wi-Fi ids,
hardware fingerprint, IP-as-identity, or the real hostname.

**Forget this device** revokes the backend credential hash and deletes OS
storage.

### Always ready (1.1.0)

The device is a service, not a mode: the Desktop pairs it, heartbeats it and
reconnects it by itself, so the Computer chip is green after a normal launch
without anyone pressing «Подключить».

| State | When | Chip |
|---|---|---|
| `ready` | a heartbeat younger than 45 s | «Готово» |
| `starting` | an app started less than `host_connect_grace_seconds` (60 s) ago, or a heartbeat younger than `host_reconnect_grace_seconds` (120 s) | «Проверяем…» with the honest reason (`подключается` / `восстанавливает соединение`) |
| `unavailable` | nothing seen for longer than those windows | «Недоступно», `host_offline`, manual «Подключить» |
| `not_configured` / `off` | this installation never paired / the user turned it off | as named |

Lifecycle, all of it owned by the running Desktop:

1. on launch the frontend loop reads `/tools/preferences`, calls `pair_device` and then
   heartbeats every 8 s (`Workspace.tsx`);
2. `pair_device` is **idempotent**: with a stored `device.json` and credential it only sends
   `POST /tools/devices/heartbeat` — it never pairs a second device;
3. the credential lives in Windows Credential Manager (`Alex LLM/device-credential`, DPAPI file
   fallback under `ALEX_DEVICE_DIR`), the record in `device.json`, so a restart of the app or of
   Windows restores the *same* pairing, with the same `device_id`;
4. if the credential is gone, the next launch pairs once more and re-creates it — the manual
   «Подключить» button is only for that case and for a revoked device;
5. `computer_mode` (`Ask` / `Trusted` / `Off`) is **usage policy**: it never changes the health
   answer and never hides a healthy host.

The chip, the compact bar and Settings all read one frontend source of truth
(`publishDevice`), so they cannot drift apart.

**Honest limitation — no autostart at Windows login.** The installer deliberately registers no
`Run` entry (on upgrade it only *deletes* the legacy `Alex LLM` value), and 1.1.0 ships no
«start with Windows» toggle. After a Windows restart the device comes back when Canalla is
launched — which is when the device is usable anyway. A user who wants the app up before that can
add it to the Startup folder or a Task Scheduler entry manually; nothing in the product claims
otherwise. The standalone `alex-host-loop.exe` (the same host modules without a WebView) is shipped
for headless/E2E use and is **not** started by the product.

Acceptance harnesses must isolate the device credential: `ALEX_DEVICE_DIR` moves `device.json`
only, while the credential is one machine-wide entry. `ALEX_DEVICE_CREDENTIAL_TARGET`
(`Alex LLM/...`, ≤ 80 chars) points it at a run-owned target which the harness deletes again
(`e2e/always-ready.mjs`, `gui-smoke.mjs`, `backup-smoke.mjs`, `cloud-smoke.mjs`,
`cloud-default-check.mjs`, `upgrade-over-install.mjs`), proven by the
`device_credential_target_redirects_the_machine_wide_entry` unit test.

Heartbeat, jobs and `host-result` require both the user JWT and
`X-Alex-Device-Id` / `X-Alex-Device-Credential`. JWT alone cannot forge a host
result. The result must match assigned device, owner, payload digest, pending
`waiting_host` status, one-time consume and a five-minute expiry.

## Credentials

`CredentialReference` / `LocalCredentialProvider`: the LLM sees a logical name
such as `github-main`. The host reads the secret from
`Alex LLM/user/{name}` in Windows Credential Manager. Raw secrets are not
returned to the model, OrcaRouter, RunPod or TinyFish. Backend `resolve()`
raises `credentials_stay_on_host`.

## Network

Device pairing does not change routing. Tor tools use only Tor transport.
Direct tools use the direct network. There is no silent Tor → Direct fallback.
Network-sensitive ToolRun metadata and UI show `Network: Direct` or
`Network: Tor`.

## Processes

Child processes get a sanitized environment (SystemRoot, sanitized PATH,
TEMP/TMP, USERPROFILE, a few OS facts). Known secrets
(`RUNPOD_API_KEY`, `TINYFISH_API_KEY`, `LLM_API_KEY`, JWT/gateway/device
credentials) are not inherited. Windows Job Objects use `KILL_ON_JOB_CLOSE`.
Stop closes only that job. Elevated processes go through UAC and are not
guaranteed to join the Alex job.

## Coding and Git (0.8.0)

See [coding-agent.md](coding-agent.md), [device-security.md](device-security.md)
and [local-risk-policy.md](local-risk-policy.md). Typed git tools never embed
credentials. Rotate device credential is in Web & Tools next to Forget this device.
`patch_file` returns CONFLICT when `expected_before_sha256` does not match.
