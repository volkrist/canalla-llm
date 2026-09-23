# CANALLA LLM — STEP 4/5 UBUNTU REPORT · + GLOBAL AI CONNECTION STATUS

Date: 24 Sep 2026 · Branch: `release/canalla-1.1.0` · Version: **1.1.0** (unchanged, as required)

**HEAD before:** `d9e14cf` (STEP 3 report commit)
**HEAD after:** `3a72df5`

| Commit | Subject |
|---|---|
| `51fe9a3` | `fix(ui): make global connection reflect AI readiness` |
| `854e8c2` | `test(status): prove a missing configuration starts no polling` |
| `65094d6` | `test(linux): stop asserting a Windows layout on Linux` |
| `73f9952` | `fix(tor): let the shipped daemon find its own libraries` |
| `0b9ec3f` | `test(linux): certify the shipped runtime on Linux` |
| `3a72df5` | `docs: record the Ubuntu baseline and the measured gap` |

**Verdict in one line:** the global-AI-status half is **PASS and complete**; the Ubuntu half is
**BLOCKED**, with both blockers measured rather than assumed (see §UBUNTU and §FINAL).

---

## AI GLOBAL STATUS

The defect was real and precisely where the brief said: `Workspace.tsx` rendered `Connected` from the
local backend's liveness (`health ? "Connected" : "Offline"`), so the header could read `Connected`
while the AI chip said `Не настроено` and the balance line said `RunPod не настроен`.

`src/lib/ai-connection.ts` is now the single owner of that word. It reads the backend's own state
(`compact_ai`, `compute_state`, `configured` — no second vocabulary) and decides how much of it may be
green. Nothing else in the UI may produce the global word.

| Case | Brief | Result |
|---|---|---|
| AI unconfigured | Disconnected (red) | **PASS** — `data-code="not_configured"`, «AI не настроен» |
| Missing RunPod key | Disconnected (red) | **PASS** — `data-code="credentials_missing"`, «RunPod не настроен — добавьте API key» |
| Key exists, no Pod | Disconnected | **PASS** — `data-code="off"` |
| Pod provisioning (`searching`, `creating`, `starting_pod`, `mounting_storage`) | Connecting (amber) | **PASS** — each stage named («Ищем GPU», «Создаём Pod», …) |
| Pod running, model loading | Connecting | **PASS** — «Загружаем модель» |
| Model health ready (`ready`, `generating`) | Connected (green) | **PASS** |
| Model health lost | Disconnected | **PASS** |
| Pod terminated / stopping | Disconnected | **PASS** — `off` / `stopping` |
| Provider error | Disconnected | **PASS** — keeps the backend's own recovery action |
| Stale snapshot (last read failed) | not Connected | **PASS** — `snapshot_stale`, even when the last snapshot was green |
| Recovery to healthy | Connected | **PASS** — a transition test walks red → amber → green → red |
| False Connected | must be NO | **NO** |
| False endless «Проверяем состояние…» | must be NO | **NO** — a missing configuration is named, never polled |

Three further guarantees the brief asked for, all asserted:

* **An amber state is only ever a real transition.** `degraded` is amber only while
  `compute_state` is in the backend's own `STARTING` set; `create_unknown` / `external_compute` are
  red, not a promise.
* **Configured ≠ healthy.** A chip state of `configured` renders as Disconnected
  («Настроено, но готовность не подтверждена»).
* **The infrastructure facts survive, renamed** (§4 of the brief). The badge's popover carries
  `Backend: Готов / Не отвечает` and `Canalla Cloud: Подключено / Недоступно / …` as their own rows;
  the words `Connected`/`Offline` no longer appear as a headline anywhere. `Backend` comes from the
  liveness probe, `Canalla Cloud` from the existing cloud store the panels already read (one store, no
  second poller).

**Nothing starts compute.** For a state only compute may change (a Pod that is off, a transition in
flight), the popover explains where to act and offers **no button** — the status surface stays
read-only, exactly as before.

**No pointless loops when the configuration is missing** (§3): the backend already refused (the
balance refresh returns early and `start()` creates no task without a key), and that was true but
never asserted. `tests/test_status_recovery.py` now states it as its own pair of tests: no key → no
background loop and zero upstream calls; a configured balance really does start and stop its loop.

Proof:
`apps/desktop/src/lib/ai-connection.test.tsx` — **28 tests**, the whole matrix above including the
five §6 independence cases (Computer/Tor/Memory stay green while the AI is Disconnected).
`apps/desktop/e2e/app.spec.ts` now proves the same rule in a real browser: with `/health` **and**
`/status` aborted, the badge may not keep a green it was showing a moment ago
(`data-code="snapshot_stale"`), the backend row flips to «Не отвечает», and after recovery the row is
«Готов» again while the state is again the AI's own.

---

## UBUNTU

**FINAL: BLOCKED.** Two independent blockers, both measured.

### Blocker 1 — no Ubuntu desktop environment exists on this machine

| Probe | Result |
|---|---|
| `wsl.exe --list --verbose` | `Ubuntu` 24.04.4 LTS (noble), x86_64 — **headless**: no session manager, no keyring, no XDG session, no logon |
| `/usr/bin/gnome-keyring-daemon`, `/usr/bin/secret-tool` | not found |
| Docker Desktop engine (`docker ps`) | not running (`npipe:////./pipe/dockerDesktopLinuxEngine` missing) |
| Hyper-V feature query | fails without elevation; no VirtualBox/Vagrant installed |

The brief says a WSL-only proof is not desktop acceptance when GUI/session/autostart/keyring cannot
be checked, and to stop rather than report a pass. That is what this is: **§19–§22 (clean VM
install, session/reboot acceptance, GUI, keyring, login autostart) were not performed**, and no `.deb`
was installed anywhere.

### Blocker 2 — the desktop runtime is Windows-only today

`cargo check` in `apps/desktop/src-tauri` on Ubuntu 24.04 (every dependency built; only our crate
fails):

| File | Errors |
|---|---|
| `src/host.rs` | 19 |
| `src/process.rs` | 13 |
| `src/credential.rs` | 12 |
| `src/backend.rs` | 12 |
| `src/autostart.rs` | 3 |
| `alex-llm` / `alex-host-loop` binaries | **59 / 44** (`81 × cannot find`, `13 × no method`, `9 × unresolved import`) |

These are missing platform implementations, not logic errors: the Win32 **job object** that owns and
kills the sidecar, `CREATE_NO_WINDOW`/`CREATE_BREAKAWAY_FROM_JOB` process flags,
`std::os::windows::ffi`, the **Credential Manager**, and the registry `Run` value. The Python host
tool loop is Windows-only as well: 16 of the Linux test failures are exactly that
(the file tools produce nothing), and they are the *only* remaining failures — see below.

**So Ubuntu is not merely unpackaged; the computer half has no Linux implementation yet.** Packaging
it would ship a desktop that starts and then cannot use its own machine.

### What was verified on Linux anyway (real, not narrated)

The half that needs no desktop session was built and certified on Ubuntu 24.04:

| Item | Result |
|---|---|
| Pinned runtime staged from the official expert bundle | **PASS** — `fetch-tor-runtime.py --platform linux-x86_64`, SHA256 verified, 10 files |
| Packaged backend (`alex-backend.spec`, PyInstaller onedir built on Linux) | **PASS** — answers `/health` with `product: alex-llm` while started with `PATH` pointing at an **empty directory** |
| Bundled Tor bootstrap + circuit | **PASS** — `verified: true`, `method: socks5h`, `/proc/<pid>/exe == runtime/tor/tor`, version `0.4.9.12` (pin `0.4.9.12`) |
| Kill the daemon → recovery | **PASS** — new pid with a fresh proof (`1622 → 1755`), backend keeps serving |
| Stop the backend → no orphan | **PASS** — no `tor` process left |
| **`scripts/acceptance-linux-runtime.sh`** | **9/9 PASS** |

Two real defects were found by running it, not by reading it:

1. **`the bundled daemon needs its own libraries`.** A Windows loader searches the executable's
   directory; a POSIX loader does not. The managed spawn therefore died at `exec` (**exit 127**)
   before Tor started, and the supervisor could only report `tor_managed_exited code=127` and retry
   with backoff — which it did, correctly, forever. Fixed: `LD_LIBRARY_PATH` names the runtime
   directory on POSIX only (Windows passes no custom environment, as before), and the runtime
   directory is resolved to an absolute path once, at discovery. Test:
   `test_the_managed_daemon_can_find_the_libraries_beside_it` (asserts the opposite on Windows).
2. **Three tests asserted a Windows layout on Linux** — the data root (the product was right: XDG),
   the Tor pin's `libraries` (an incomplete promise, not a product defect), and a `chmod 0444`
   trigger that root ignores. All three are now `sys.platform`/`geteuid`-guarded; the Windows
   expectations are unchanged.

### Supported baseline (§8) — determined from the machine, not guessed

| Item | Value | Evidence |
|---|---|---|
| Verified distribution | **Ubuntu 24.04.4 LTS (noble), x86_64** | `lsb_release -a` |
| WebKitGTK (Tauri v2's hard dependency) | **webkit2gtk-4.1 = 2.52.6**, `javascriptcoregtk-4.1 = 2.52.6` | `pkg-config --modversion` after installing `libwebkit2gtk-4.1-dev` |
| glibc | **2.39** | `tor --version` report |
| Rust that compiles the tree | **1.98.1** | `cargo --version` |
| Node for the frontend build | **22.23.2** (vite 7.3.6 needs ≥ 20.19) | `node --version`, `package-lock.json` |
| Secret storage | Secret Service (gnome-keyring/KWallet) — **absent here** | `which gnome-keyring-daemon secret-tool` |

**A `.deb` built on noble links glibc 2.39, so it would not run on 22.04 (2.35).** The baseline
choice — build on jammy to support 22.04→24.04, or declare 24.04 the floor — is a deliberate decision
for the next attempt; nothing was built for either.

### Artifact, secret storage, autostart, updater, single instance

| Item | Result |
|---|---|
| Linux `.deb` | **NOT BUILT** — no supported baseline chosen, packaging deltas not applied (nsis target, `.ico` icon, `.exe` resource names, batch `beforeBundleCommand`, `build.rs` sidecar name) |
| SHA256 | — |
| Python / Node / Rust / Tor required from the user | **NO** for the certified half (that is what the 9/9 says); **not applicable** to a desktop that does not exist yet |
| Secret storage on Linux | **NOT IMPLEMENTED** — no Linux credential backend exists; **no plaintext fallback was added, and none exists** |
| XDG autostart | **NOT IMPLEMENTED** |
| Single instance on Linux | **NOT RUN** (the plugin is cross-platform, but the desktop does not build) |
| Linux updater artifact / platform filtering | **NOT BUILT** |
| Real login / reboot | **NOT RUN** |
| Computer Ready on Linux | **NOT POSSIBLE YET** — unported (see Blocker 2) |

---

## TESTS

| Gate | Result |
|---|---|
| BasedPyright | **0 errors, 0 warnings** |
| Backend `pytest` (Windows) | **630 passed, 1 skipped** |
| Backend `ruff check` / `format --check` | PASS / 181 files formatted |
| Backend `pytest` (Ubuntu 24.04) | **606 passed, 16 failed, 8 skipped** — all 16 are the host tool loop (8 × `test_093_reliability`, 7 × `test_autonomous_tasks`, 1 × `test_091_reliability`); the three test-portability issues from the first run are fixed |
| Gateway `pytest` | **148 passed** |
| Frontend Vitest | **230 passed** (19 files; +28 new for the AI state) |
| Frontend `tsc -b` / Prettier / `vite build` | clean / clean / PASS |
| Playwright | **20 passed** (includes the rewritten offline-recovery case) |
| Rust `cargo test` | **88 passed** (host 18, desktop 58, updater signature 12) |
| Linux runtime acceptance | **9/9 PASS** |
| Linux package / secret-store / XDG-autostart / single-instance tests | **NOT WRITTEN** — the features they would cover do not exist yet |

Windows was not rebuilt in this step (no rebuild loop): the changed code is covered by the gate set
above, including 20 Playwright cases in a real browser against a real backend. The next Windows
artifact is STEP 5's.

---

## CLEANUP

| Item | Result |
|---|---|
| Orphan processes (Windows) | **none** (`alex-llm` / `alex-backend` / `alex-host-loop` / `tor`) |
| Orphan processes (WSL) | **none** (`pgrep -x tor` → 0) |
| GPU / Pods | **0** — no RunPod action in this step |
| WSL build copy (`/root/canalla`, 2.7 GB) | **removed**; the WSL disk went 6.6 GB → 4.0 GB |
| WSL toolchain left behind | **yes, deliberately**: apt packages (webkit2gtk-4.1-dev, build-essential, …), rustup 1.98.1, Node 22.23.2, a Python venv, PyInstaller. Nothing outside the WSL distro was changed and no operator data was touched |
| Working tree | clean except the known `docs/screenshots/0.4/*.png` drift (untouched, never committed) |
| Test data in the repo | none — the Linux runs used `/tmp` roots inside WSL, the Windows runs their own temp roots |

---

## REMAINING FOR STEP 5

Only release-specific items, plus the Linux work STEP 4 could not finish:

1. **Ubuntu (blocked, not done):** choose the baseline (24.04 vs a jammy build); port `process.rs`
   (process group + parent-death signal instead of the job object), `host.rs`, `credential.rs`
   (Secret Service), `backend.rs`, `autostart.rs` (XDG `.desktop`); port the Python host tool loop;
   add `tauri.linux.conf.json` (deb target, PNG icons, platform resource names, no Windows batch);
   build the Linux sidecar and stage the pinned Tor runtime; then the acceptance §19–§22 needs a real
   Ubuntu desktop VM.
2. A real logout/login cycle on Windows (carried from STEP 3).
3. Production Gateway deployment that serves `/updates/latest` (currently 404) and the production
   update manifest **last**.
4. Production updater signing identity (separate from the test one).
5. Authenticode decision (currently **NOT SIGNED**).
6. Version bump 1.1.0 → 1.2.0 and the final hashes, after the code is frozen.
7. Live GPU sanity only if release policy requires it — 60-second capacity window, never longer.

---

## FINAL

**BLOCKED — Ubuntu.** With the exact reasons:

* no Ubuntu desktop environment is available on this machine (§19 forbids accepting on WSL), so
  install/GUI/session/autostart/keyring/reboot acceptance could not be performed; and
* independently, the desktop runtime does not build for Linux (59 + 44 errors in five modules) and
  the Python host tool loop is Windows-only (16 Linux test failures), so there is nothing to package
  yet.

**PASS — the global AI connection status.** The first half of the brief is delivered, tested and
committed: the global word now means AI readiness, never infrastructure, with the full transition
matrix, the honest «not configured» path, the renamed infrastructure rows, and no compute action on a
read-only status surface.

Also delivered on the way: a real Linux fix (§13's library problem, found by running it), a 9/9 Linux
runtime acceptance that STEP 5 can reuse on a VM, and a measured baseline document
(`docs/linux-ubuntu.md`).

Nothing from STEP 1–3 was weakened: no version bump, no merge, no tag, no update manifest, no GPU.
