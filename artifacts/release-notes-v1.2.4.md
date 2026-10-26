# Canalla LLM 1.2.4 — release notes (draft, NOT PUBLISHED)

> This draft is prepared for the operator. It is deliberately not published: live H5/H6 and the
> updater signature are still open. See `docs/release-1.2.4.md` for the full evidence.

## Artifact

- `Canalla LLM_1.2.4_x64-setup.exe` — 95 465 293 B
- SHA256 `8db307489f0b5f0da077158e50d329a57d4fcd2c64707c3f149a2196f9c8de7e`
- Not Authenticode-signed; updater `.sig` is not present in this build.

## Fixed

- **Stop AI now stops the answer and the Pod.** It aborts the active generation before stopping
  shared compute, so the Gateway terminates the Pod promptly instead of waiting for the whole
  answer to finish.
- **Honest status after Stop.** The AI chip no longer reports `AI Ready / generating` after the
  user stopped the session; it shows `AI Waiting` while the Pod drains, then `AI Off`, with no
  stale connection error.
- **Separate Gateway version.** A desktop release no longer requires the separately deployed
  Gateway to carry the same version string.

## Verification

- Backend 988 passed; Gateway 261; frontend 313; Rust 126; Playwright 21; developer gate 9/9.
- Installed always-ready E2E 32/32: Computer/Tor ready on launch, after relaunch and after a real
  sidecar crash, with the same device id.
- Live Qwen: H4, T2, T5, T6, T7 PASS. H5/H6 BLOCKED (8-Pod cap).
- Installed 1.2.4 preserves 31 chats, the same device, compute preferences and the Canalla Cloud
  enrollment; a verified pre-install backup `20261026T100723Z-manual` exists.
