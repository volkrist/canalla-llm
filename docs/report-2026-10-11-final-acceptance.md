# Canalla LLM 1.2.1 — final acceptance

Date: 11 October 2026 · Branch `integration/canalla-1.2.1` · initial HEAD `c36a576` · final HEAD
`7b6d16b` (this report adds one documentation commit on top).

The honest headline: **the merged tree is a verified developer/pytest candidate; the trusted
Windows build, the signed installer and the installed acceptance are BLOCKED here and must run on a
clean GitHub Actions Windows runner.** No false PASS is recorded.

---

## 1. Trusted Windows build — BLOCKED

**Why:** there is no clean runner reachable from this session, and this host still carries the open
`Trojan:Win32/LummaStealer.DO!MTB` detection (`DidThreatExecute=True`, false positive **not**
proven). Nothing was signed and no production secret was used.

What is ready instead:

* `.github/workflows/ci.yml` — the developer gate on a `windows-latest` runner (Python 3.12, Node 24,
  `pip install -r apps/backend/requirements.lock`, gateway reqs, `npm ci`, `cryptography`). Job:
  **developer-gate**. Status: **NOT RUN** (not dispatched from here).
* `.github/workflows/release-candidate.yml` — job **candidate**: adds Rust
  (`dtolnay/rust-toolchain@stable`), the backend `.venv` the Playwright webServer expects, Playwright
  browsers, and the artifact verification against the `release-installer` artifact. It carries **no
  production private key**. Status: **NOT RUN**.

Run it with: `gh workflow run ci.yml` and
`gh workflow run release-candidate.yml -f version=1.2.1 -f expect_sha256=<hex> -f expect_commit=<sha>`
(the installer must first be uploaded as the `release-installer` artifact).

`cargo` exists on this host (`~/.cargo/bin`) but is deliberately **not** used: `cargo test` builds
and executes the very Rust binaries Defender flags. `rust.check` / `rust.test` are reported
**BLOCKED**, never PASS.

## 2. Playwright and installation

| Check | Result |
|---|---|
| Playwright mock suites | **BLOCKED locally**; **NOT RUN on CI** |
| Test Windows installer (no production signature) | **NOT BUILT** |
| Clean install / upgrade over a test install | **BLOCKED** (needs the built installer) |
| Backend sidecar start / Computer always-ready / Tor always-ready | **BLOCKED** (installed sidecar) |
| Gateway URL baked into the built client | **BLOCKED** (no built exe); the guard itself is verified |
| User data / chats / credentials preserved | **BLOCKED** (no installed upgrade) |

Playwright browsers are present here, but the e2e config starts the backend with
`apps/backend/.venv\Scripts\python.exe tests/run_e2e_server.py`; that venv is not provisioned in this
worktree (a local run would need a full backend venv). The candidate workflow now creates it and
installs browsers, so the mock suites are expected to run on the runner.

**Trusted environment needed:** a clean Windows 11 VM or the GitHub Windows runner, with
`apps/backend/.venv` (from `requirements.lock`), Playwright browsers, and — for the install/upgrade
part — a *test* (unsigned) NSIS build. No production key and no paid GPU are required.

## 3. Security / release gate

| Property | Result |
|---|---|
| developer gate needs no production key | **PASS** (ran green: 9 steps, 0 FAIL, 0 BLOCKED) |
| missing installer / signature → FAIL (not PASS) | **PASS** (`verify-artifact` → exit 1, `FAIL`) |
| wrong key id / bad signature / wrong name / missing URL / wrong SHA / wrong commit → FAIL | **PASS** (`--self-test`, incl. the new `--expect-commit` case) |
| gate works on a **real** built artifact | **BLOCKED** (no installer built/signable here) |
| Git commit + version bind to the artifact | implemented (`--expect-commit` vs `HEAD`, signed-name/version binding); real run **BLOCKED** |
| installer + sidecar from a verified checkout | the candidate workflow checks out the ref and verifies the hash/commit; real run **BLOCKED** |
| Defender incident not called a false positive | **upheld** — recorded as open, no evidence |

```
TEST BUILD PASS          — developer gate (Ruff, pytest, Vitest, tsc, Prettier, Vite)
PRODUCTION SIGNING BLOCKED — no trusted host, no signing, no installer built
```

## 4. Remaining audit items — decision each

None of A20–A24/A26–A29/V01–V04 was silently deferred: any data-, secret- or money-adjacent item is
named explicitly and, where it is a secret leak, fixed now.

| ID | Topic | Decision |
|---|---|---|
| A20 | backup blocks the backend event loop | **Defer to 1.3** — availability/UX; the snapshot is consistent, no data loss; fix = `asyncio.to_thread`/worker with cancellation |
| A21 | `fs_read`/`fs_hash` load the whole file (confirmed open) | **Defer to 1.3** — OOM risk on huge files, no writes and no data loss; fix = streaming hash + bounded read |
| A22 | refresh secret checked/replaced non-atomically | **Defer to 1.3** as security hardening — worst case is a replayed rotation / unexpected logout, **not** a secret leak; fix = CAS `UPDATE … WHERE token_hash = :old` |
| A23 | the declared total inference timeout is not enforced (confirmed open) | **Defer to 1.3** — a client can occupy the single shared slot longer, but spend stays bounded by *its own* session budget and the idle policy; fix = one total deadline around the generation |
| A24 | rate limiter keeps unbounded keys | **Defer to 1.3** — memory growth / potential DoS on the Gateway; nginx rate-limiting mitigates; fix = TTL eviction + capacity + pre-auth IP bucket |
| A26 | offline install / WebView2 / Authenticode | **Known, documented limitation** — not a code blocker; publication needs an operator decision on Authenticode and an offline-WebView2 choice |
| A27 | updater check/download version desync | **Defer to 1.3** — UX; signatures/version are still re-verified, so no unsigned-install bypass |
| A28 | hard crash loses the already-shown part of an answer | **Explicit deferral** — the user's message and completed history are safe; only the in-flight partial assistant text is lost, and only on an abrupt kill between stream end and persistence. Fix = a checkpointed interrupted marker in 1.3; workaround now = regenerate |
| A29 | `credential_use` reports use without acting (confirmed open) | **Defer to 1.3** — contract/UX naming; fix = rename to an availability check |
| V01 | multiple Gateway workers break local queue/lease | **Defer** — single-worker is the documented deployment; a second worker would need a distributed queue/fencing |
| V02 | restore has no proven power-loss recovery between swaps | **Explicit deferral** — ordinary exceptions roll back and safety/pre-restore backups give a recovery path; a durable restore journal is 1.3 work |
| V03 | redaction did not cover the appended traceback | **FIXED here** — the backend formatter now redacts the final formatted record (incl. `exc_text`), with a regression test. Gateway logger: no redacting handler is installed (uvicorn's own); no secret is logged by the audited paths — **residual, documented** |
| V04 | native HTTP IPC accepts an arbitrary `backend_url` | **Defer, NEEDS VERIFICATION** — potential credential-to-arbitrary-host, but it needs a second defect (no XSS found) and local IPC; harden with an origin pin in 1.3. Not a proven leak |

## 5. Fixes made in this session

* **V03** — `apps/backend/app/runtime_log.py`: `RedactingFormatter` redacts the whole record and
  `RedactTicket` redacts `exc_text`; `test_sidecar_packaging.py` proves a secret inside a traceback
  never reaches the log file.
* `.github/workflows/release-candidate.yml` — provisions the backend `.venv` and Playwright browsers
  so the mock suites can actually run on the candidate runner.
* `scripts/release-gate.py` — the self-test now also covers a wrong `--expect-commit`.

## 6. Exact test counts (final HEAD `7b6d16b`)

| Gate | Result |
|---|---|
| Backend pytest | **906 passed, 6 skipped** |
| Gateway pytest | **247 passed** |
| Frontend Vitest | **294 passed, 0 failed** |
| tsc / Prettier / Vite | clean / clean / PASS |
| Backend + Gateway Ruff check / format | PASS |
| Alembic (backend `0016`, gateway `0005_*) | `check` clean |
| Release gate `developer` | 9 steps, 0 FAIL, 0 BLOCKED |
| Release gate `--self-test` | PASS (missing installer, wrong name, bad signature, key mismatch, no URL, wrong SHA, wrong commit) |
| `pip-audit` backend lock / gateway | 0 / 0 |
| `npm audit --omit=dev` | 0 |

## 7. Test installer state

**NOT BUILT.** `apps/desktop/src-tauri/target/release/bundle/nsis/…setup.exe` does not exist in this
worktree; no test build was produced (Rust/Tauri build is BLOCKED here) and no production signing
was performed. `PRODUCTION SIGNING BLOCKED`.

## 8. Remaining blockers

1. Rust `cargo check`/`cargo test` on a trusted runner (BLOCKED here).
2. Playwright mock suites + a test (unsigned) installer build + clean install/upgrade + always-ready
   — on a trusted runner/VM (BLOCKED here).
3. The candidate workflow executes against a real signed installer (BLOCKED — none built).
4. Authenticode code-signing decision (SmartScreen) — operator decision.
5. A10/A11 operator quotas must be set before any public rollout.

## 9. Exact steps to publish 1.2.1 (later, authorized)

1. On a **trusted** host: `git checkout integration/canalla-1.2.1` at the release commit; confirm
   `git status` is clean.
2. Build the sidecar from the (now updated) `requirements.lock` and write the stamp.
3. Build + sign the installer from a trusted environment via `scripts/production-signing.ps1`
   (it bakes `ALEX_GATEWAY_URL=https://gateway.12testers.store` and runs the packaged-URL guard).
4. Upload the signed installer as the `release-installer` artifact and run
   `release-candidate.yml` with `version`, `expect_sha256`, `expect_commit`.
5. Run the installed acceptance on a clean VM (`always-ready.mjs`, upgrade-over-install, GUI/backup
   smokes) — no production secret needed for the candidate build.
6. Only then: upload the artifact, publish the manifest, deploy the Gateway — each a separate,
   authorized action. No Pod, no Volume, no 12Testers change is needed for any of the above.

## 10. Boundaries honoured

No paid GPU, no Pod, no Volume, no production deploy, no 12Testers change, no signing, no publishing,
no `main` merge, no new worktree. Fixes were committed and pushed only where a real defect was fixed
(V03) or a workflow was made correct.
