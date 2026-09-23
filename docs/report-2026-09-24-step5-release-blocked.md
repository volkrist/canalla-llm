# CANALLA LLM 1.2.0 — FINAL RELEASE REPORT

Date: 24 Sep 2026 · Branch: `release/canalla-1.1.0` · **Verdict: RELEASE BLOCKED**

STEP 5 is gated on STEP 4 being PASS. STEP 4 is **BLOCKED** (Ubuntu), and the Ubuntu
package / secret-store / autostart block is therefore missing from the tree. The stage was not
started, nothing was released, and no partial release work was left behind. This report records the
verified state, the exact blocking conditions and the ordered path out.

## VERSION

| Item | Value |
|---|---|
| release commit | none — the stage did not run |
| HEAD | `43fd06a` (`release/canalla-1.1.0`) |
| `main` | `9bd7548` |
| `origin/main` | `9bd7548` (identical) |
| tags | `v1.0.0` only → **`v1.2.0` is free** |
| product version | **1.1.0** everywhere (not bumped — §5 is a release act) |
| working tree | clean except the known `docs/screenshots/0.4/*.png` drift |

### Required blocks — present or missing (§0)

| Block | Commits | Status |
|---|---|---|
| STEP 1 Auto Update | `5648751`, `005f13f`, `16633af` | **present** |
| STEP 2 bundled Tor | `a2a8b5e`, `4dab4f3`, `68294a7`, `829996c` | **present** |
| STEP 3 Windows self-contained + autostart + single instance | `ac8164a`, `973c3bb`, `57a66e7` | **present** |
| **STEP 4 Ubuntu package / secret-store / autostart** | — | **MISSING** |
| backend crash supervisor | `f846b11`, `1fe8da1`, `9bc01e0` | **present** |
| Computer always-ready | `cfb31c3`, `8e7fe33`, `7633f37`, `18e73ae` | **present** |
| global AI connection fix | `51fe9a3`, `854e8c2` | **present** |
| compact UI + Settings | `71e582d` | **present** |
| D-9 startup deadline | `81402f0` | **present** |
| context meter | `2271e12` | **present** |
| backup / restore | 1.0.x line (`dab54b9`, …) | **present** |
| Linux Tor library fix (found in STEP 4) | `73f9952`, `0b9ec3f` | **present** |

Evidence for the missing block: `find . -name "*.deb" -o -name "*.AppImage"` → **nothing**;
`target/release/bundle/` contains only `nsis/Canalla LLM_1.1.0_x64-setup.exe`;
`cargo check` on Ubuntu fails with **59 + 44** errors in the Windows-only modules.

---

## AI CONNECTION STATUS

Deterministic matrix (STEP 4, `src/lib/ai-connection.ts` + 28 tests + Playwright) — **all PASS**:

| Case | Result |
|---|---|
| Unconfigured → Disconnected | **PASS** |
| Provisioning → Connecting | **PASS** |
| Model loading → Connecting | **PASS** |
| LLM Ready → Connected | **PASS** |
| Loss of LLM → Disconnected | **PASS** |
| **False Connected** | **NO** |
| Live-LLM E2E (Pod dies / endpoint dies / recovery against a real Pod) | **NOT RUN** — optional per §24/§25; would need the 60-second capacity window |

---

## WINDOWS

| Gate | Result |
|---|---|
| Pristine VM | **NOT RUN** — no VM host on this machine (Hyper-V needs elevation, Docker engine down, no VirtualBox) |
| No Python / Node / Rust on PATH | **PASS** — proven in STEP 3 by construction on the operator's machine (`PATH=C:\WINDOWS\System32;C:\WINDOWS`) |
| No Tor Browser / external tor.exe | **PASS** — STEP 3 phase 1, plus the STEP 2 harness on a machine that *has* Tor Browser |
| Computer | **PASS** (installed build, 38/38 `always-ready.mjs`) |
| Tor (bundled, circuit proof, kill→recovery) | **PASS** (37/37 `self-contained.mjs`) |
| Real login / logout cycle | **NOT RUN** — the OS registration itself is proven; a logon cycle is not |
| Autostart (toggle really edits the registry both ways, removed on uninstall) | **PASS** |
| Single instance | **PASS** (1 desktop / 1 backend / 1 tor, second launch exits) |
| Upgrade 1.1 → 1.2 | **NOT RUN** — there is no 1.2.0 artifact to upgrade to |

---

## UBUNTU

| Gate | Result |
|---|---|
| Version tested | Ubuntu 24.04.4 LTS (noble) x86_64 — **headless WSL2, no desktop session** |
| Clean VM | **NOT RUN** — §19 of STEP 4 forbids accepting on WSL, and no desktop VM exists here |
| Artifact | **does not exist** (no `.deb` built) |
| Computer | **NOT POSSIBLE** — the host/computer half has no Linux implementation |
| Tor | **PASS for the runtime half**: bundled daemon bootstraps, proves a `socks5h` circuit, recovers from a kill, leaves no orphan (`scripts/acceptance-linux-runtime.sh`, **9/9**) |
| Backend (packaged sidecar) | **PASS** — serves `/health` with `PATH` pointing at an empty directory |
| Secret storage | **NOT IMPLEMENTED** — no Linux backend; **no plaintext fallback exists** |
| Real login / autostart | **NOT IMPLEMENTED** |
| Updater | **NOT BUILT** for Linux |

---

## TOR REQUEST ROUTING

| Gate | Result |
|---|---|
| Natural-language request ("открой/проверь через Tor") → policy = TOR REQUIRED → actual SOCKS5h → circuit proof | **NOT RUN as an E2E** — §2's acceptance was not written. The routing capability and its fail-closed behaviour exist and are unit-tested (`test_tor_router.py`, `test_tor_transport.py`; 83 Tor tests in total) |
| Clearnet requests for Tor-required work | **0** in the tested paths (fail-closed is asserted in the Tor suite) |
| Failure is fail-closed | **PASS** at unit level; the end-to-end request path is exactly what §2 asks to prove and is **still open** |

A green Tor chip is *not* accepted as routing evidence — that is why this row is not PASS.

---

## UPDATER

| Gate | Result |
|---|---|
| Production key | **NOT CREATED** — `~/.canalla-updater/` holds only `canalla-updater-test.key` + `.pub` |
| Private key outside repo | **PASS** (both keys are outside; nothing key-shaped is tracked) |
| Gateway route | **FAIL** — production `GET https://gateway.12testers.store/updates/latest` → **HTTP 404** (the deployed Gateway is the 1.1.0 build; `/health` → 200). §8's deploy did not happen |
| Artifact hosting | **NOT PREPARED** |
| Windows / Linux E2E | **NOT RUN** — no 1.2.0 artifacts exist |
| Bad signature / downgrade / offline / 404 / 500 / timeout / interrupted download | **PASS at unit level** (STEP 1 matrix: 148 gateway tests, 12 updater-signature tests). The *installed* failure path was also exercised: the STEP 3 build met a real 404 and degraded gracefully |
| Production manifest | **NOT PUBLISHED** (correct) |

---

## TESTS (last full measurement, HEAD `43fd06a`)

| Suite | Result |
|---|---|
| BasedPyright | **0 errors, 0 warnings** |
| Backend pytest (Windows) | **630 passed, 1 skipped** |
| Backend ruff check / format | PASS / 181 files |
| Backend pytest (Ubuntu 24.04) | 606 passed, **16 failed**, 8 skipped — all 16 are the unported host tool loop |
| Gateway pytest | **148 passed** |
| Frontend Vitest | **230 passed** (19 files) |
| Frontend tsc / Prettier / Vite | clean / clean / PASS |
| Playwright | **20 passed** |
| Rust `cargo test` | **88 passed** (18 + 58 + 12) |
| `cargo clippy` | not run this stage |
| npm audit --omit=dev | **0 vulnerabilities** |
| pip-audit | not re-run this stage |
| Tor suite | **83 tests** |
| Python packaging / Linux packaging tests | **NOT WRITTEN** |
| AI global-state tests | **28 + 2** |
| Natural-language Tor route tests | **NOT WRITTEN** |

No gate was re-run for a 1.2.0 tree, because no 1.2.0 tree exists.

---

## ARTIFACTS

| Item | Value |
|---|---|
| Windows installer | **no 1.2.0 artifact.** Only the STEP 3 intermediate: `Canalla LLM_1.1.0_x64-setup.exe`, 92,454,341 bytes, SHA256 `b3761c0a24f25232073f32d6ba2f5a423eaf61433e09496bf69b509a3565689b`, `.sig` present |
| Windows desktop / sidecar / host (that build) | `4e5e72bd…`, `a93149ac…`, `8fb5d36e…` |
| Bundled Tor (that build) | `60c45b01938c799862e511a9a5bab12f959a819c6264a24502edc342165f570c` (0.4.9.12, GPL-3.0) |
| Linux `.deb` | **does not exist** |
| Updater signature (1.2.0) | **does not exist** |
| Authenticode | **NOT SIGNED** (unchanged) |

---

## DATA PRESERVATION

| Item | Result |
|---|---|
| users / chats / messages / projects / memory / documents / credentials / device / settings / compute preferences / backups | **NOT RE-MEASURED** — no 1.2.0 install or upgrade was performed. Inherited evidence: the 1.0.0/1.1.0 upgrade + backup/restore acceptance remains green (`docs/upgrade-backup-audit.md`), and the pre-upgrade backup gate is enforced in code (`exit 15` without a verified snapshot) |

---

## OPEN ITEMS (§4 — classified, none silently dropped)

| Item | Status |
|---|---|
| WM-07 TinyFish Browser live execution/lifecycle | **DOCUMENTED NON-BLOCKING LIMITATION** (unchanged; no live lane in this stage) |
| CD-08 REAL stale-SHA coverage debt | **DOCUMENTED NON-BLOCKING LIMITATION** (still open, as recorded in 1.1.0) |
| `client_version` metadata staleness on the Gateway | **DOCUMENTED NON-BLOCKING LIMITATION** — metadata only, never a security authority |
| sticky `generating` label | **DOCUMENTED NON-BLOCKING LIMITATION** (cosmetic; clears on the next state change) |
| over-window draft not trimmed | **DOCUMENTED NON-BLOCKING LIMITATION** — upstream refuses it and the client sees a typed `gateway_unavailable` |
| provider spend telemetry lag | **DOCUMENTED NON-BLOCKING LIMITATION** (external provider behaviour) |
| Web chip `configured` vs true health | **DOCUMENTED NON-BLOCKING LIMITATION** — no cheap honest probe exists; the chip deliberately does not claim «Готово» |
| `requireSignedVersion` (artifact ↔ declared version binding) | **OPEN DECISION** — minisign signs the artifact bytes; the *version* is manifest metadata, so a compromised manifest endpoint could label an older signed artifact as newer. Resolve in the release stage: prove the binding or document the mitigation |
| Authenticode policy | **OPEN DECISION** — currently NOT SIGNED; SmartScreen may warn. Must be declared, not fabricated |

---

## GPU

| Item | Value |
|---|---|
| Pods created | **0** |
| Spend | **$0.00** |
| Max capacity wait | 0 seconds (no live lane was run) |

---

## CLEANUP

| Item | Result |
|---|---|
| Orphan processes | **none** (Windows and WSL verified in STEP 4) |
| Temp build material | **removed** (WSL copy deleted; disk 6.6 GB → 4.0 GB) |
| Secrets leaked | **NO** — only the pre-existing *test* key, outside the repo; nothing new was created |
| Working tree | clean except the known screenshot drift |
| Version / tag / manifest | untouched: 1.1.0, `v1.0.0`, no manifest |

---

## FINAL VERDICT

**RELEASE BLOCKED — STEP 4 is not PASS, so STEP 5 never started.**

Two independent, verified reasons:

1. **The Ubuntu half does not exist.** No `.deb` was ever built (`find` → nothing), the desktop
   runtime does not compile for Linux (**59 + 44** errors in `host.rs`, `process.rs`,
   `credential.rs`, `backend.rs`, `autostart.rs`), the Python host tool loop is Windows-only
   (all 16 Linux test failures), and no Linux secret store or XDG autostart exists. The only Linux
   thing proven is the runtime half: packaged backend + bundled Tor, **9/9**.
2. **The mandatory environment gates cannot be executed here.** §12 requires a pristine Windows VM,
   §13 a real logout/login cycle, §15 a clean Ubuntu desktop VM. This machine offers neither:
   Hyper-V needs elevation, the Docker engine is not running, there is no VirtualBox/Vagrant, and
   the only Linux present is a headless WSL2 container — which STEP 4 already ruled out as desktop
   acceptance. A PASS here would be fabricated, and rule 13 forbids that.

Nothing was released, tagged, merged, bumped or published, and no production signing material was
created.

### Ordered path out (each step is a prerequisite of the next)

1. **Port the Linux desktop**: `process.rs` (process group + parent-death signal instead of the job
   object), `host.rs`, `credential.rs` (Secret Service), `backend.rs`, `autostart.rs` (XDG), and the
   Python host tool loop; add `tauri.linux.conf.json` (deb target, PNG icons, platform resource
   names, no Windows batch) and choose the baseline (build on jammy for 22.04, or declare 24.04).
2. **Get the environments**: one pristine Windows VM and one clean Ubuntu desktop VM (the login-cycle
   and install gates need them; they cannot be simulated).
3. **Deploy the Gateway update route** (§8) — `/updates/latest` must answer 204/no-update rather than
   404 — and prepare HTTPS artifact hosting.
4. **Create the production updater identity** (password-protected, outside the repo, non-interactive
   signing via a secret mechanism) and re-run the updater crypto tests against the new public key.
5. **Decide and document** `requireSignedVersion` and Authenticode.
6. **Then** the release order of §23: bump → free gate → Windows build → Linux build → sign → hashes →
   pristine installs → login cycles → upgrade → Tor E2E → natural-language Tor routing → updater E2E →
   upload → verify hashes → docs → merge → tag `v1.2.0` → **manifest last**.

Items 1 and 2 are the bulk of the remaining work; items 3–5 need operator access (VPS, passphrase,
certificate decision) rather than more engineering.
