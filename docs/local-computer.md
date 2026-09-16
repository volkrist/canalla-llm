# Local Computer Host in 0.7.1

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
