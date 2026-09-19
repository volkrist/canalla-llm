# Device security and privacy in 0.8.0

## Pairing identity

The device identity is a random UUID plus a ≥256-bit `token_urlsafe(32)`
credential. It is not derived from hardware. Default display name is
`Windows device` (rename to PC-1, Laptop, …).

The backend stores only the SHA-256 of the credential. The plaintext is returned
once on pair or rotate and is written by the native host to:

1. Windows Credential Manager, target `Alex LLM/device-credential`
2. Fallback DPAPI file `%LOCALAPPDATA%\Alex LLM\device.cred.dpapi`

The renderer never reads the device credential after pairing. `device.json` keeps
`device_id` and the alias only.

## Not collected

Local Device does **not** send or store:

- MAC address
- MachineGuid
- disk serial
- motherboard serial
- CPU hardware identifier
- Wi-Fi identifiers
- hardware fingerprint
- IP address as device identity
- real hostname as identity

`get_system_info` returns a non-identifying summary (`platform=windows` plus OS version, logical CPU count, RAM, and free system-disk space). No hostname, MAC, MachineGuid, or hardware serials.
`get_known_folders` resolves Desktop/Documents/Downloads via the Windows Known Folder API.
`list_processes` is name + PID. `list_volumes` omits volume serials.
`inspect_process` drops window titles and command lines.

## Forget and rotate

- **Forget this device** revokes the backend hash and deletes OS storage.
- **Rotate device credential** issues a new credential, overwrites OS storage,
  and invalidates the previous secret. JWT + device ownership is required; the
  old host credential cannot be reused.

Heartbeat, jobs and `host-result` still require user JWT plus
`X-Alex-Device-Id` / `X-Alex-Device-Credential`. JWT alone cannot forge a host
result.

## Credential broker

`CredentialReference` + `CredentialBroker` + `WindowsCredentialStore`:

- The LLM sees `github-main`.
- The host loads `Alex LLM/user/{name}` only at the execution boundary.
- Backend `resolve()` / `checkout()` raise `credentials_stay_on_host`.
- Secrets are not sent to RunPod, OrcaRouter, TinyFish, logs or model context.
- stdout/stderr redacts `https://user:token@` and known secret patterns.

UAC: `elevate=true` shows Windows UAC via `Start-Process -Verb RunAs`. Alex does
not store an admin password and does not bypass UAC.
