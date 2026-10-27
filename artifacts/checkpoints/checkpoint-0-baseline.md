# Checkpoint 0 — baseline (2026-10-28)

- Git: branch `release/canalla-1.2.4`, HEAD `8829df9`, tag `v1.2.5` = HEAD, in sync with origin.
- Installed: Canalla 1.2.5; desktop `153dc62b…`, sidecar `f9046b83…` (source digest `b9aef542…`),
  host `262a1f84…`, Tor `8c38513b…`.
- DB: integrity ok; users 2, chats 38, messages 76, paired_devices 59 (1 revoked);
  device_id `f3548022-a676-463c-9fc5-b05bbd5ac907`.
- RunPod: `pods []`, balance `$5.7150289234`, watchdog `disarmed`.
- Updater: production manifest serves 1.2.5 (verified in the previous stage).
- Windows stand discovery:
  - `WindowsSandbox.exe` absent; the optional-feature query needs elevation → **Sandbox unavailable
    without UAC**.
  - VirtualBox 7.2.8 installed; only `Ubuntu-Desktop`, `Ubuntu-Desktop-2` VMs; **no Windows VM**.
  - No Windows ISO in Downloads/Documents.
  - Hyper-V module not present; Docker daemon not running; WSL is Ubuntu only.
  - No self-hosted interactive Windows runner.
  → FRESH-OS stand = BLOCKED (no admin/UAC, no Windows image).
- Next: free updater checks, real expired-token regression, device audit, installer content audit.
