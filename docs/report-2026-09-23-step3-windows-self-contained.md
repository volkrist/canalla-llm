# CANALLA LLM — STEP 3/5 WINDOWS SELF-CONTAINED REPORT

Date: 23 Sep 2026 · Branch: `release/canalla-1.1.0` · Version: **1.1.0** (unchanged, as required)

**HEAD before:** `829996c` (STEP 2 tip, bundled Tor)
**HEAD after (code):** `57a66e7`
**Report commit:** the commit that carries this file.

**Commits**

| Commit | Subject |
|---|---|
| `ac8164a` | `feat(desktop): start once and register for login startup` |
| `973c3bb` | `feat(desktop): add the launch-at-login setting` |
| `cb84e21` | `test(tor): stop binding the host to choose a managed port` |
| `57a66e7` | `test(windows): certify the self-contained installed runtime` |
| *(docs)* | `docs: keep the step reports and record the Windows result` |

Nothing from STEP 1 (Auto Update) or STEP 2 (bundled Tor) was reverted, and the version was not
bumped. The intermediate installer below is a STEP 3 acceptance artifact, **not** the 1.2.0 release.

---

## SELF-CONTAINED

**Environment (exact).** Windows 11 25H2, build `10.0.26200`, WebView2 Runtime `153.0.4234.48`. This
is the operator's working machine, **not a pristine VM** — see the limitation below. Python, Node and
Rust *are* installed on it, and the operator's own Tor Browser sits at
`C:\Users\Volkr\Desktop\Tor Browser`. Nothing was removed from the machine (product files, out of
scope); the acceptance makes them unreachable to the product instead.

| Question | Answer | Evidence |
|---|---|---|
| Python required | **NO** | `PATH` reduced to `C:\WINDOWS\System32;C:\WINDOWS`; the packaged backend served the app on port 8000 |
| Node required | **NO** | same run; no `node`/`npm` process among the desktop's children |
| Rust required | **NO** | same run; no `cargo`/`rustc` process |
| Tor Browser required | **NO** | every folder `candidate_browser_paths()` searches (`USERPROFILE`, `HOME`, `LOCALAPPDATA`, `APPDATA`, `ProgramFiles`, `ProgramFiles(x86)`) redirected into empty directories |
| external `tor.exe` required | **NO** | no `tor` on `PATH`, no `Program Files\Tor`; the route was served from `…\Programs\Canalla LLM\runtime\tor\tor.exe` |
| backend bundled | **YES** | `sidecar/alex-backend/alex-backend.exe`, `a93149ac…bbc944` |
| native host bundled | **YES** | `alex-host-loop.exe`, `8fb5d36e…ca602` (headless E2E variant; the installed desktop carries the host in-process) |
| Tor bundled | **YES** | 8 staged files present and hashing what the build recorded, daemon `0.4.9.12` |
| updater bundled | **YES** | `Settings → Обновления` present in the installed window; the check completed without blocking the app |

`e2e/self-contained.mjs` Phase 1 (sandbox) — **14/14 PASS**:

```
PASS the sandbox holds no Tor Browser and no Tor installation to fall back on
PASS the packaged backend serves the app with no Python, Node or Rust on PATH   [port 8000]
PASS the desktop starts only its own runtime, no development toolchain          [msedgewebview2.exe, alex-backend.exe]
PASS the installed product runs no headless host build: the desktop owns the computer itself
PASS every staged Tor file is installed and hashes what the build recorded      [8 file(s)]
PASS the installed daemon is the pinned official build, not something that arrived later
PASS the first owner is created without any toolchain
PASS TOR becomes ready with no Tor Browser and no Tor on PATH                   [ready]
PASS the route is served by the daemon Canalla ships, from the install directory
PASS the bundled daemon reports the pinned version                              [0.4.9.12 (pin 0.4.9.12)]
PASS the circuit is proven
PASS killing the bundled daemon is recovered by a new process                   [19904 -> 13068]
PASS Tor is ready again after the daemon was killed, still from the bundle      [ready bundled]
PASS the sandbox run leaves no orphan process
```

**Honest limitation.** No separate clean Windows VM was available, so "clean" is produced by
construction on this machine: the product's own search paths are emptied, `PATH` is stripped to
Windows, the data root and the WebView2 profile are per-run temp directories, and every Tor file is
verified against the hash the build recorded. What that cannot disprove is a *machine-wide* implicit
dependency outside those paths (for instance something in the registry or in `System32`). The
inventory says there is none — the desktop and the host are Rust with the UCRT set that Windows 10+
always has, the sidecar is a PyInstaller onedir that carries its own runtime, and Tor is bundled —
but this is an argument from inventory, not a VM experiment, and it is stated as such.

---

## AUTOSTART

| Item | Result |
|---|---|
| Default | **ON** |
| Registration mechanism | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value `Canalla LLM`, written through the Windows registry API (`RegCreateKeyExW`/`RegSetValueExW`/`RegDeleteValueW`/`RegQueryValueExW`), read back after every change |
| Settings toggle | **PASS** — `Settings → Общие`, `data-testid="autostart-toggle"`, shows what the machine reports |
| Enable (first launch) | **PASS** — `"C:\Users\Volkr\AppData\Local\Programs\Canalla LLM\alex-llm.exe"` appeared with nothing pressed |
| Disable | **PASS** — `runValue() === null` after the toggle went off, and the checkbox followed |
| Enable again | **PASS** — the value came back identically |
| Survives a normal quit | **PASS** — still registered after the app closed cleanly |
| Uninstall cleanup | Hook `NSIS_HOOK_POSTUNINSTALL` deletes that one value; both installer policy tests assert the line **and** still forbid any other delete/credential touch |
| Login start (real logout/login) | **NOT RUN** — a real session logout was not performed |

The login-start row is the one place where the step is weaker than the prompt asks. What is proven is
that the operating system holds a correct `Run` entry pointing at the installed executable — the same
value Windows acts on at logon, read back through the same call the UI uses. What is *not* proven is
a real logon cycle executing it. That belongs to a machine where a logout is acceptable, and it is
carried into STEP 5 rather than claimed here.

---

## SINGLE INSTANCE

Proven on the installed build with a second real launch while the first window was running:

| Process | Count |
|---|---|
| `alex-llm.exe` | **1** |
| `alex-backend.exe` | **1** |
| `alex-host-loop.exe` | **0** — the installed desktop runs the host in-process |
| `tor.exe` | **1** |

`PASS a second launch does not become a second desktop [1 alex-llm.exe]`,
`PASS the second launch exits by itself [0]`,
`PASS one backend, one Tor daemon and no headless host build [1 backend / 1 tor / 0 host-loop]`.

Ownership, measured from the OS rather than assumed: the desktop starts `alex-backend.exe`, and the
backend starts `tor.exe`.

```
"alex-llm.exe","16156","10016"
"alex-backend.exe","14320","16156"
"tor.exe","20204","14320"
```

---

## INSTALLED HEALTH

| Item | Result |
|---|---|
| Backend | **PASS** — `/health` serves on 8000 with no toolchain on `PATH` |
| Computer ready without a click | **PASS** — `[Computer: Готово]` |
| Tor ready without a click | **PASS** — `[Tor: Готово]` |
| Tor source | **bundled** — `C:\Users\Volkr\AppData\Local\Programs\Canalla LLM\runtime\tor\tor.exe` |
| Circuit proof | **PASS** — `verified_chain: true`, method `socks5h`, `runtime_version 0.4.9.12` |
| Device pairing without a click | **PASS** — one device, `online: true`, credential written to this run's own target |
| Settings / update section | **PASS** — the panel exists, the check ran, the app stayed `ok` and Tor stayed `Готово` |

The established STEP 2 gate was re-run unchanged against the same build as a regression:
`e2e/always-ready.mjs` — **38/38 PASS**, including the Tor popover (health vs mode vs policy), the
bundled-runtime assertion, a full Quit + relaunch restoring the session and the **same `device_id`**,
and the sidecar-crash recovery.

---

## RECOVERY

| Item | Result | Evidence |
|---|---|---|
| Kill backend → automatic restart | **PASS** | `6768 -> 15704`, new `/health` on port 8000 |
| New backend PID | `15704` (from `6768`) | measured from `Win32_Process` |
| Computer recovered | **PASS** | `[Computer: Готово]` after the crash, no button |
| Tor recovered | **PASS** | `[Tor: Готово]` after the crash (the sidecar restarts its own daemon) |
| Same device after the crash | **PASS** | one device, same id `834e5d50-9c6f-4a2a-b26a-febb3bb0849a`, `online: true` |
| Exactly one runtime after recovery | **PASS** | `1 backend / 1 tor / 0 host-loop` |
| Kill bundled Tor (sandbox phase) | **PASS** | `19904 -> 13068`, ready again, still `bundled`, circuit re-proven |
| Kill native host | **NOT APPLICABLE** | see below |
| Manual click required | **NO** | in any of the above |

**"Kill the native host" is not a test this product can have.** A probe of a normal installed launch
(60 s) shows the complete process set: `alex-llm.exe` → `alex-backend.exe` → `tor.exe`. There is no
`alex-host-loop.exe`: the host modules run *inside* the desktop, and the standalone binary is the
headless E2E variant (`docs/local-computer.md`). `find_native_host()` is referenced only by its own
unit test. Killing "the native host" therefore means killing the desktop.

Rather than invent a process to kill, the equivalent property was asserted where it is real: after
the backend is killed, the desktop's own device loop reconnects to a **brand-new** backend and
re-heartbeats as the **same** device — one device, same id, online, Computer green. That is the
recovery the prompt is asking for, and it is measured, not narrated.

---

## UPDATER REGRESSION

| Item | Result |
|---|---|
| Settings → Обновления exists in the installed window | **PASS** |
| Update check never blocks the app | **PASS** — `/health` still `ok` afterwards, Tor still `Готово` |
| Deployment state | `GET https://gateway.12testers.store/updates/latest` → **HTTP 404** |
| App behaviour on that failure | **PASS** — typed, non-blocking failure; the window keeps working |

The 404 is expected and worth recording: the deployed Gateway is the 1.1.0 build, which predates the
STEP 1 route. The installed client therefore exercised the *failure* path of the update matrix
against a real public endpoint — "endpoint answers nothing usable" — and behaved correctly: no crash,
no blocked startup, no misleading state. Publishing the route and the manifest is STEP 5, and the
manifest stays **NOT PUBLISHED**.

---

## TESTS

| Gate | Result |
|---|---|
| BasedPyright | **0 errors, 0 warnings** |
| Backend `pytest` | **627 passed, 1 skipped** |
| Backend `ruff check` / `format --check` | PASS / 181 files formatted |
| Backend Alembic (`upgrade head` + `check`) | head reached, **no new upgrade operations** |
| Gateway `pytest` | **148 passed** |
| Gateway `ruff check` / `format --check` | PASS / 29 files formatted |
| Frontend Vitest | **202 passed** (18 files) |
| Frontend `tsc -b` | clean |
| Frontend Prettier | clean |
| Frontend `vite build` | PASS |
| Playwright (`npm run test:e2e`) | **20 passed** |
| Rust `cargo test` | **88 passed** — host 18, desktop 58, updater signature 12 |
| `npm audit --omit=dev` | **0 vulnerabilities** |
| Installed `e2e/self-contained.mjs` (STEP 3 gate) | **37/37 PASS** |
| Installed `e2e/always-ready.mjs` (STEP 2 regression) | **38/38 PASS** |
| Tor service suite (`tests/test_tor_*.py`) | **83 tests** collected; `test_tor_service.py` alone 30/30 |

Two defects found by running the gates rather than by reading them:

1. **`test_a_managed_process_starts_when_nothing_answers`** failed after the STEP 2 port policy landed.
   The cause is the machine, not the product: Windows reserves `8868–9467` (and `50000–50059`) here, so
   a real `bind("127.0.0.1", 9050)` fails with `WinError 10013`, the service correctly falls through to
   a free port, and the assertion — which never adopted the `ports` fixture that exists for exactly
   this — saw `54841` instead of `9050`. Fixed by using the fixture (commit `cb84e21`); the port policy
   is still the thing asserted, and it is also proven end-to-end by the installed harness, where the
   real daemon listens on a dynamic port and the route is proven through it.
2. **The harness's `processIds()` was silently returning `[]`.** `Get-Process -Name 'alex-llm.exe'`
   matches nothing on this PowerShell (`Get-CimInstance` does, and so does the name without the
   extension). Every orphan check had been passing vacuously. Fixed; the three checks that then failed
   against the fix were measurement artifacts of the same bug, and the same run afterwards passed all
   37 — including a real "no orphan" assertion and a real process count.

---

## INSTALLER

| Item | Value |
|---|---|
| Path | `apps/desktop/src-tauri/target/release/bundle/nsis/Canalla LLM_1.1.0_x64-setup.exe` |
| Size | 92,454,341 bytes |
| SHA256 | `b3761c0a24f25232073f32d6ba2f5a423eaf61433e09496bf69b509a3565689b` |
| Updater signature | `Canalla LLM_1.1.0_x64-setup.exe.sig` present (minisign, `trusted comment: signature from tauri secret key`) |
| Final release | **NO** — intermediate STEP 3 artifact at 1.1.0 |
| Authenticode | **NOT SIGNED** |

Installed component hashes (from the installed tree, `%LOCALAPPDATA%\Programs\Canalla LLM`):

| Component | SHA256 |
|---|---|
| `alex-llm.exe` | `4e5e72bdc5a505ea6936c2ad7d61252f851185348f2ae8cbbe860033326cf085` |
| `alex-host-loop.exe` | `8fb5d36e8029ded66741807f30a0965df55070d77589404fd648871114cba602` |
| `sidecar/alex-backend/alex-backend.exe` | `a93149ac8aef6ad4fe5ff94de71ac3ad89654187b29729dbe50f95ead7bbc944` |
| `runtime/tor/tor.exe` | `60c45b01938c799862e511a9a5bab12f959a819c6264a24502edc342165f570c` |

Also present and verified by hash inside the install: `geoip`, `geoip6`, `gpl-3.0.txt`,
`openssl.txt`, `libevent.txt`, `tor.txt`, `zlib.txt`, `runtime.json` (staged provenance: archive
`231dad6b…`, daemon `0.4.9.12`, licence `GPL-3.0`, corresponding source
`https://dist.torproject.org/tor-0.4.9.12.tar.gz`).

The installer was built once for this step (`scripts/build-desktop.ps1`, which stages the pinned Tor
runtime and signs the installer non-interactively when the key has no passphrase). No rebuild loop
was needed: all three failures found here were fixed in the harness and re-proven against the same
installed build.

---

## CLEANUP

| Item | Result |
|---|---|
| Orphans (`alex-llm` / `alex-backend` / `alex-host-loop` / `tor`) | **NO** — the last check of both phases is a real process scan now |
| GPU / Pods | **0** — no RunPod action in this step |
| Temp runtimes / listeners | **CLEAN** — per-run temp roots; the probe file used to settle the host question was deleted |
| Test credentials | **CLEAN** — each run used its own `ALEX_DEVICE_CREDENTIAL_TARGET` / `ALEX_GATEWAY_CREDENTIAL_NAME`; the always-ready run removed its own credential |
| Working tree | Clean except the known `docs/screenshots/0.4/*.png` drift, which is untouched and not committed |
| Login entry | **PRESENT, deliberately** (see below) |

The `HKCU\…\Run` value `Canalla LLM` exists and points at
`C:\Users\Volkr\AppData\Local\Programs\Canalla LLM\alex-llm.exe`. This is not harness residue: it is
the product's own default-ON behaviour for an installed app, created by a normal launch, and the
acceptance explicitly asserted that it survives a clean quit. The operator's next normal launch would
re-create it anyway. It can be removed with one click in Settings → Общие, or by uninstalling.

---

## REMAINING FOR STEP 4

Linux/Ubuntu only, plus what objectively carries into STEP 5:

- supported Ubuntu baseline and artifact format (`.deb` vs AppImage) decided against a real machine;
- the Linux Tor runtime is pinned in `scripts/tor-runtime.json` (`tor-expert-bundle-linux-x86_64-15.0.23`, archive `08d49de2…`, daemon `0.4.9.12`, with `libcrypto.so.3` / `libssl.so.3` / `libevent-2.1.so.7`) but is **not staged or bundled** yet;
- Linux secret storage for the device credential (Credential Manager has no Linux equivalent);
- Linux session autostart (XDG, no root daemon) with the same user-facing toggle;
- a Linux updater artifact and platform filtering in the manifest;
- parent build steps: packaging the desktop for Linux at all.

Carried into STEP 5 because it is not a Linux matter: the real logout/login cycle; the production
Gateway deployment that serves `/updates/latest`; the production updater signing identity; the
Authenticode decision; the final hashes; and the version bump to 1.2.0.

---

## FINAL

**PASS** — with four limitations stated instead of hidden:

1. no pristine VM (constructed sandbox on the operator's machine; inventory argues the rest);
2. real login/logout not executed (the OS registration itself is proven);
3. "kill the native host" is not applicable — the installed product has no separate host process;
   the equivalent recovery property is proven with the same device id;
4. Windows Authenticode: **NOT SIGNED** — SmartScreen may warn on first run until the final release
   decides on a certificate. The updater signature exists independently of Authenticode.

Nothing from STEP 1 or STEP 2 was weakened, no version was bumped, `main` was not merged, no tag was
created, and no production update manifest was published.
