# Canalla LLM 1.2.7 — release notes and checklist

1.2.7 is the release after 1.2.6. It hardens the autonomous path the product promises — a real
model that plans, acts with tools, builds and verifies its own result — and it changes the
signing policy: the Windows installer ships **unsigned by Authenticode** by explicit owner
decision, while every integrity, security and operability check stays in place.

- Branch: `feature/canalla-1.2.7-autonomous-agent`
- Installer: `Canalla LLM_1.2.7_x64-setup.exe`
- Updater signature: **production key `9B328EFF111D1FB2`** (minisign, `.sig` next to the installer)
- Authenticode: **WAIVED_BY_OWNER — NOT A RELEASE BLOCKER**
- Runtime protocol and Gateway protocol: both still `1`
- Migrations: head `0017`, no new revision in this release

## 1. What changed for a user

**The agent has to prove its own work.** A task used to be able to close as `COMPLETED` on the
strength of a green test run alone. A real LIVE run did exactly that: it wrote, ran and served an
application, and the provenance record still said `INCOMPLETE_TRACE`, because the model never
called the independent verifier and nothing else asked it to. The controller now owes that step
itself: after the project's tests pass it calls `verify_result` with checks derived from the files
the task actually wrote and from the project's own test command. Any later edit makes the check
owed again, so a stale verification cannot close a task.

**The tests run where the project keeps them.** A model that builds `taskdesk/app/__init__.py`
with `taskdesk/tests/` runs `pytest` from `taskdesk/`, not from the workspace root, because the
backend is installed editable as `app` and shadows the project's own package at the root. The
forced retest and the acceptance check now pick the directory the tests live in, which is the
difference between a passing suite and a reported `ImportError`.

**Evidence survives the run.** A LIVE workspace lives in `%TEMP%` and Windows deletes it, so the
verifier could not be re-run against the project a model produced and the run's `TOOL_RESULT`
rows were never exported. Runs now snapshot the produced sources into the evidence directory and
write the whole provenance journal, so a finished run can be re-read and audited afterwards. The
free self-test also writes to its own directory instead of a paid run's evidence folder.

**Module-scope and settings fixes found by the paid series.** `install_software` only fires on an
explicitly named package; a transient provider `404` no longer closes a session as
`external_stop` (which had created a second Pod); an empty Pod list is confirmed by a second read
(a single empty list had produced a false `CLEAN`); tool arguments that were not valid JSON are
no longer echoed back to the model (which made llama.cpp answer non-2xx).

## 2. Install and upgrade

Run `Canalla LLM_1.2.7_x64-setup.exe` over the installed Canalla LLM. The installer is per user,
installs into `%LOCALAPPDATA%\Programs\Canalla LLM\`, and never touches the data root
(`%LOCALAPPDATA%\Alex LLM\`), Windows Credential Manager, the Gateway enrollment or the saved
Compute Preferences.

1.2.6 → 1.2.7 is a no-op migration (head `0017` in both). The sidecar stamp, the packaged
`ALEX_GATEWAY_URL` and the updater signature are verified by `scripts/release-gate.py` before an
artifact is called a candidate.

## 3. Signing policy (owner decision, 15 Nov 2026)

The owner has decided, finally:

1. Authenticode is **not** a release blocker: `WAIVED_BY_OWNER`.
2. No certificate and no signing service is purchased.
3. An **unsigned** (Authenticode) EXE installer may be released.
4. Every integrity, security and operability check of the installer is kept.
5. This decision is not revisited.

What that means in practice:

| Check | Status |
|---|---|
| Authenticode publisher signature | **not required** (waived). Windows SmartScreen may warn on first run. |
| Tauri **updater** signature (`.sig`, minisign) | **still required** — the client refuses an update it cannot verify. Production key `9B328EFF111D1FB2`. |
| Installer SHA-256 recorded and published | **required** |
| Artifact name/version bind to the checkout | **required** (`release-gate.py verify-artifact`) |
| Signature trusted comment binds the artifact name | **required** |
| `ALEX_GATEWAY_URL` baked into the client | **required** |
| Sidecar stamp matches the source tree | **required** |
| Packaged backend runs without Python | **required** (`verify_sidecar_standalone.py`) |
| Backup / upgrade / restore smoke | **required** on a clean VM (procedure: `acceptance-clean-vm-1.2.1.md`) |

The waiver removes one procurement dependency, not the integrity chain: a user can still verify
what they downloaded by SHA-256, and the application still refuses an unsigned update package.

## 4. Acceptance performed for this candidate

All of the following ran on this branch at the commit recorded in the release gate.

| Gate | Result |
|---|---|
| Backend pytest | **1184 passed, 1 skipped** |
| Backend ruff (`check` + `format --check`) | PASS (245 files) |
| BasedPyright, shipped code (`apps/backend/app`, `apps/gateway/gateway`) | **0 errors, 0 warnings, 153 files** |
| Version consistency (backend, Tauri, npm, locks) | **9/9 PASS** at `1.2.7` |
| Vitest | **328 passed** |
| TypeScript (`tsc -b`), Prettier, Vite build | PASS |
| Playwright | **25 passed** |
| `cargo test` | **134 passed** (40 host-loop, 78 desktop, 16 updater; 2 network-ignored) |
| `npm audit --omit=dev` | 0 vulnerabilities |
| GPU monitor self-test | **66/66 PASS** |
| Live acceptance self-test (`live_acceptance.py --self-test`) | **31/31 PASS** |
| `acceptance-live-rc-final.py --self-test` | **28/28 PASS** |
| `acceptance-post-release-1.1.0.py --self-test` | **16/16 PASS** |
| Watchdog self-test | PASS |
| Deployed Gateway `--dry-run` | **PASS** — fresh enrollment, compute status, balance, typed `price_limit`, malformed policy rejected, **no Pod**; the bounded search window closed to `offline` |
| Installed always-ready (`e2e/always-ready.mjs`) | **PASS** — Computer and Tor green on launch, after relaunch and after a real sidecar kill; one device, same `device_id` |
| Installer build (`production-signing.ps1 -Action Build`) | **PASS** — updater-signed by the production key |
| `release-gate.py verify-artifact` | **PASS** (8/8 steps: name, SHA-256, key id, Ed25519, signed name, Gateway URL, commit) |
| Packaged sidecar standalone | **PROOF_PASS** — `/health` ok, no Python, no repo, clean shutdown |
| Production 1.2.6 database | unchanged (2 users / 54 chats / 120 messages / 63 devices / 30 tasks, integrity ok, 0 FK) |

## 5. Release checklist

- [x] Version bumped to `1.2.7` in the backend, the Tauri config, npm and both locks
- [x] Sidecar rebuilt from this tree; `build-stamp.json` current (161 source files)
- [x] Installer built: `Canalla LLM_1.2.7_x64-setup.exe`
- [x] Updater signature present and verified against the production public key
- [x] SHA-256 recorded in `artifacts/SHA256SUMS-1.2.7.txt`
- [x] Package gateway URL baked and verified
- [x] Free CI on the release commit (backend, gateway, frontend, Rust, acceptance self-tests)
- [x] Deployed Gateway dry run (no Pod, no spend)
- [x] Production database untouched
- [ ] Installed install / upgrade / rollback on a clean VM (procedure `acceptance-clean-vm-1.2.1.md`;
      no clean VM or Windows Sandbox on the build host)
- [ ] Ubuntu runtime acceptance (`scripts/acceptance-linux-runtime.sh`; needs a Linux host)
- [ ] Publication — **requires a separate owner authorization**; `release-gate.py publish` refuses
- [x] Authenticode — **WAIVED_BY_OWNER**, removed from the blocker list

## 6. Honest limitations of this release

- **Autonomy LEVEL 6 and 7 are not proven.** LEVEL 1–4 are proven by a real LIVE; LEVEL 5 is
  proven within a run; LEVEL 6 needs one completed run where the independent checks and
  `REAL_MODEL_CONFIRMED` provenance agree, and the paid batch ended at `PAID_BATCH_EXHAUSTED`
  ($0.358 left, below the useful window). The missing link is now closed in code and covered by
  tests, but a test is not a LIVE.
- **A clean-VM install and the Ubuntu runtime are not executed here.** Both need an environment
  the build host does not have.
- **Windows SmartScreen will warn** because the installer is not Authenticode-signed. That is the
  owner's accepted trade-off, not a defect to be fixed in a later session.
- WM-07 (TinyFish Browser live lifecycle) and CD-08 (REAL stale-SHA coverage) remain open.
- Web chips report `configured`, not `ready`: TinyFish has no health probe. Tor is `ready` only
  with a persisted SOCKS5h proof; Computer is `ready` on a live heartbeat.
- Canalla still does not start itself at Windows login; the Computer host returns when Canalla is
  launched, with the same pairing and no button.

## 7. Documentation map

- [CHANGELOG.md](../CHANGELOG.md) — the same changes in changelog form.
- [release-gate.md](release-gate.md) — the gate that turns an artifact into a candidate.
- [AI-HANDOFF.md](AI-HANDOFF.md) — the current state, commands and known limitations.
- [compute-preferences.md](compute-preferences.md) — the money policy.
- [gateway-deployment.md](gateway-deployment.md) — the shared service, deploy/upgrade/rollback.
- [tor.md](tor.md), [local-computer.md](local-computer.md) — the always-ready services.
- [installer-data-layout.md](installer-data-layout.md) — what the installer writes and what it never touches.
