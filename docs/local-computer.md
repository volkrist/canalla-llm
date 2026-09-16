# Local Computer Host in 0.7.0

Local tools run on the paired Tauri host, not as a FastAPI shell. FastAPI only
creates `waiting_host` jobs and records the host result.

## Pairing

An authenticated desktop user registers a device. The backend issues a random
`device_id` (UUID) and a high-entropy credential. The credential is stored as a
SHA-256 hash. The plaintext is returned once on pair and is stored by the native
host:

1. Windows Credential Manager target `Alex LLM/device-credential`
2. Fallback: DPAPI-protected `%LOCALAPPDATA%\Alex LLM\device.cred.dpapi`

The renderer never receives the credential after `pair_device`. `device.json`
keeps only `device_id` and display name. Frontend JS cannot read Credential
Manager; the Tauri fs capability is save-dialog write only.

Heartbeat, jobs and `host-result` require both the user JWT and
`X-Alex-Device-Id` / `X-Alex-Device-Credential`. JWT alone cannot forge a host
result. The result must match assigned device, owner, payload digest, pending
`waiting_host` status, one-time consume and a five-minute expiry.

Pairing is privacy-preserving: random device id and credential, no MAC, serial,
MachineGuid, disk serial or hardware fingerprint. Hostname is not collected.
Display name defaults to `Alex-PC` or a user alias.

## Modes

Default is **Ask**. `web_mode` does not change this.

- Off — no local tools
- Ask — every local tool waits for confirmation
- Trusted Workspace — auto-allows only `list_directory`, `read_file`,
  `search_files`, `create_directory`, `write_file`, `copy_file`, `move_file`
  inside canonical trusted roots

`run_process`, `run_powershell` and `run_python` always require confirmation,
including in Trusted Workspace. There is no Trusted Execution mode in 0.7.

Forbidden tools are unregistered: delete, registry, services, install, UAC,
credential stores, shutdown, disk/partition.

## Files

Roots are canonicalized. `..`, UNC, junctions/symlinks that escape the root and
secret paths (`.env`, SSH keys, browser stores, wallets, password databases) are
denied with no override. `write_file` uses temp + fsync + `ReplaceFileW` (or
rename for a new file) and records `before_sha256` / `after_sha256`.
`expected_before_sha256` refuses a raced overwrite.

## Processes

Child processes get a sanitized environment (SystemRoot, sanitized PATH,
TEMP/TMP, USERPROFILE, a few OS facts). Known secrets are not inherited.
Windows Job Objects use `KILL_ON_JOB_CLOSE`. Stop closes only that job. Native
pytest (ctypes) proves parent+child die and an unrelated process survives.
