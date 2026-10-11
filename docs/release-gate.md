# Release gate (A25)

One script, three modes, two workflow files. The point is that a *release candidate* is a thing
that is checked, not a thing that is assembled and hoped for.

```
python scripts/release-gate.py developer       # local tree: lint + tests + typecheck + build
python scripts/release-gate.py candidate --installer <setup.exe> [--version 1.2.1] \
       [--expect-sha256 <hex>] [--expect-commit <sha>]
python scripts/release-gate.py verify-artifact --installer <setup.exe>
python scripts/release-gate.py publish         # refuses: a separate, authorized action
python scripts/release-gate.py --self-test
```

Exit codes: `0` PASS · `1` FAIL · `2` INCOMPLETE (a required step was `BLOCKED`, never a PASS).

## developer

`ruff` (backend + gateway), backend `pytest`, gateway `pytest`, frontend `vitest`, `tsc`,
`prettier --check`, `vite build`. No artifact, no production secret — safe on an untrusted host.
`BLOCKED` (e.g. no `node_modules`) is reported as `BLOCKED`, never as PASS.

## candidate

Everything in *developer*, plus:

| Step | Missing / wrong → |
|---|---|
| Rust `cargo check --all-targets` / `cargo test` | BLOCKED when no toolchain |
| Playwright | BLOCKED when not installed |
| sidecar `build-stamp.json` check | BLOCKED when no built sidecar |
| packaged Gateway URL guard | BLOCKED when no built desktop exe |
| **artifact verification** | **FAIL** |

The artifact verifier is the part that must not be faked. It is a hard **FAIL** when:

* the installer is missing;
* the filename is not `Canalla LLM_<version>_x64-setup.exe`;
* the recorder SHA-256 (`--expect-sha256`) does not match the bytes;
* the signature file is missing or unparsable;
* the signature's key id is not the **production** key id from `tauri.conf.json`;
* the Ed25519 signature does not verify over the file bytes (needs `cryptography`; otherwise the
  step is `BLOCKED`, never PASS);
* the signed trusted comment does not bind `file:<artifact name>`;
* the bare `ALEX_GATEWAY_URL` is not baked into the installer;
* `--expect-commit` is given and does not equal `git rev-parse HEAD`.

No production secret is involved: the verifier reads only the **public** key already committed in
`apps/desktop/src-tauri/tauri.conf.json`. The private signing key never enters this script, the CI
runners or any untrusted host.

## publish

Refused here on purpose. Signing and uploading a real 1.2.1 happens later, from a trusted host, by
a separate authorized action (`scripts/production-signing.ps1`).

## Workflows

* `.github/workflows/ci.yml` — the developer gate on push/PR. No secrets.
* `.github/workflows/release-candidate.yml` — `workflow_dispatch`: installs Python/Node/Rust, runs
  the candidate gate against the `release-installer` artifact. It requires the installer to exist;
  a missing artifact fails the job. The production private key is never a secret of this workflow.

## Known limits of this block

The candidate workflow is authored but **has not been executed** (no clean CI runner here), and the
Rust steps are `BLOCKED` on the current untrusted Windows host. A real candidate run — a signed
installer, Rust, Playwright and the installed acceptance on a clean VM/CI — remains the release
precondition.
