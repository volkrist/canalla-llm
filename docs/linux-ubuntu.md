# Ubuntu / Linux support — baseline, the port, what is left

Status: **the engineering port is done (STEP 4A); the desktop-VM acceptance is STEP 4B.**
Ubuntu 24.04 LTS x86_64 is the baseline for 1.2.0. Ubuntu 22.04 is deliberately not supported in this
release: the measured environment is noble's glibc 2.39 and WebKitGTK 4.1, and a package built there
will not run on 22.04's glibc 2.35. Supporting 22.04 is a later, separate decision.

This file records what was actually measured, so the next person starts from evidence instead of an
assumption. Nothing here is a promise: every claim names the command or the file it came from.

Nothing on this page is a substitute for running the product on a real Ubuntu **desktop** — see §6.
No GUI, session, keyring or login claim is made from the headless build environment, and none should
be read into the results below.

## 1. Supported baseline (determined, not guessed)

| Item | Value | How it was determined |
|---|---|---|
| Verified distribution | **Ubuntu 24.04.4 LTS (noble), x86_64** | `lsb_release -a` inside the WSL2 distro used for the build probe |
| WebKitGTK | **webkit2gtk-4.1 = 2.52.6**, `javascriptcoregtk-4.1 = 2.52.6` | `pkg-config --modversion webkit2gtk-4.1` after installing `libwebkit2gtk-4.1-dev` from the noble archive |
| glibc on the machine | **2.39** | `tor --version` reports `Glibc 2.39` |
| Rust toolchain that compiles the tree | **1.98.1** (stable, minimal profile) | `cargo --version` in the same distro |
| Node used for the frontend build | **22.23.2** (vite 7.3.6 needs ≥ 20.19) | `node --version`; `package-lock.json` pins vite 7.3.6 |
| Bundled Tor daemon | **Tor 0.4.9.12**, Libevent 2.1.13, OpenSSL 3.5.8, Zlib 1.3, glibc 2.39 | `LD_LIBRARY_PATH=… ./tor --version` on the staged linux-x86_64 expert bundle |
| Secret storage | Ubuntu's Secret Service (GNOME Keyring / compatible provider) over D-Bus, through `secret-tool` | `apps/desktop/src-tauri/src/platform/linux.rs` — see §3c |
| Autostart | XDG user-session autostart (`$XDG_CONFIG_HOME/autostart/canalla-llm.desktop`) | `platform/linux.rs::login_write` — see §3c |

**Debian package floor follows glibc.** A package built on noble links glibc 2.39, so it will not run
on 22.04 (glibc 2.35). Declaring **24.04 as the supported baseline** and building on noble is the
decision this release takes; building on 22.04 to widen the range stays available for a later release.

## 2. What works on Linux (measured)

| Component | Result |
|---|---|
| Bundled Tor runtime (linux-x86_64) | **works**: `scripts/fetch-tor-runtime.py --platform linux-x86_64` verifies the pinned SHA256 and stages 10 files; the daemon reports `0.4.9.12` and runs |
| **Packaged backend sidecar** | **works**: a PyInstaller onedir built on Linux (`alex-backend.spec`) answers `/health` with `product: alex-llm` while started with **`PATH` pointing at an empty directory** — no interpreter, no shell tool |
| **Bundled Tor end to end** | **works**: bootstraps, proves a real `socks5h` route (`verified: true`), the serving process is `/proc/<pid>/exe == runtime/tor/tor`, the pinned `0.4.9.12` |
| **Tor recovery** | **works**: killing the daemon gives a new pid with a fresh proof; the device keeps serving through the restart |
| **Clean shutdown** | **works**: stopping the backend leaves no `tor` process behind |
| **Desktop runtime compiles and tests** | **works**: `cargo check` 0 errors; `cargo test` **99 passed** (25 + 62 + 12) on Ubuntu 24.04 (Windows: 91 passed) |
| Backend (Python) test suite | **623 passed, 0 failed**, 8 skipped (Windows: 630 passed, 1 skipped) — the 16 Linux failures of STEP 4 are gone, see §3d |
| **Installed `.deb` acceptance** | **9/9 PASS** — `scripts/acceptance-linux-runtime.sh` run against the *installed* `/usr/lib/Canalla LLM/…`, not the source tree |
| Product data paths | **correct on Linux**: `data_paths.py` uses `XDG_DATA_HOME` or `~/.local/share` and `alex-llm` as the app directory; the Rust side agrees (`platform::data_root_default`) |
| Tor runtime pin honesty | the pin's `libraries` list is part of the promise; the test now asserts it |

All of the backend/Tor rows above are one command, `scripts/acceptance-linux-runtime.sh`, and it is
also the one gate that runs against the **installed package**: see §5. It needs no desktop session and
no GPU.

### The Tor daemon needs its own libraries — and now gets them

The expert-bundle daemon has no rpath:

```
$ ldd ./tor | grep 'not found'
libevent-2.1.so.7 => not found
$ ./tor --version
./tor: error while loading shared libraries: libevent-2.1.so.7: cannot open shared object file
$ LD_LIBRARY_PATH=<runtime dir> ./tor --version
Tor version 0.4.9.12 (git-78923280eed3eff6).
```

A Windows loader searches the executable's own directory; a POSIX one does not. The managed spawn
therefore exited **127** before Tor ever started, and the supervisor could only report a crash loop
(`tor_managed_exited code=127`, retried with backoff). `TorService._spawn_tor` now sets
`LD_LIBRARY_PATH` to the runtime directory **on POSIX only** — Windows passes no custom environment, as
before — and the runtime directory is resolved to an absolute path once, where it is discovered.
Both are asserted: `test_the_managed_daemon_can_find_the_libraries_beside_it`, and 9/9 on the Linux
acceptance above.

## 3. The port (STEP 4A)

### 3a. One platform module, not `cfg` scattered over the tree

`apps/desktop/src-tauri/src/platform/` is the whole difference between the two operating systems:

```
platform/mod.rs      the contract, and the one typed refusal both sides share
platform/windows.rs  the Win32 implementation (moved verbatim, behaviour unchanged)
platform/linux.rs    the POSIX implementation
```

`mod.rs` re-exports the same names on both targets, so callers read the same on either OS:
process ownership, sanitized spawn environment, data root, known folders, system info, atomic file
replace, secret read/write/delete, login (autostart) read/write. A platform is chosen once, by
`#[cfg]`, inside that module — the rest of the tree has no `target_os` branches for these concerns.

| Concern | Windows | Linux |
|---|---|---|
| Child ownership | Win32 **Job Object** (`kill-on-close`) | a **process group** (`process_group(0)`) plus **`PR_SET_PDEATHSIG`** in `pre_exec`, with a `getppid() == 1` guard for the race where the parent already died |
| "is it alive" | `OpenProcess` + exit code | `/proc/<pid>/stat`, reading the **state** so a zombie counts as dead |
| Termination | `taskkill /T /F` | `SIGTERM` to the group, `SIGKILL` after ~3 s |
| Secrets | Credential Manager, with a DPAPI file fallback | **Secret Service** via `secret-tool` |
| Autostart | `HKCU\…\Run` | `$XDG_CONFIG_HOME/autostart/canalla-llm.desktop` |
| Data root | `%LOCALAPPDATA%\Alex LLM` | `$XDG_DATA_HOME/alex-llm` |

The Windows side is a verbatim move: no Windows behaviour was changed to make Linux compile. Both
platform modules carry their own unit tests, and the shared contract is asserted from both.

Only three of the rows above can be *proven* rather than asserted, and all three are, in
`platform/linux.rs`:

| Proof | What it would catch |
|---|---|
| `an_owned_child_is_its_own_process_group` — reads the `pgrp` field of `/proc/<pid>/stat` | a child left in the desktop's group, which would make `terminate_tree` signal the desktop itself |
| `stopping_an_owned_child_takes_the_processes_it_started` — a shell starts a background job, then the group is stopped | an orphaned grandchild, i.e. the Tor daemon surviving the sidecar that started it |
| `an_armed_child_does_not_outlive_the_parent_that_died` — the test re-invokes its own binary as a parent that arms a child and exits without cleaning up | a `PR_SET_PDEATHSIG` that was never armed, or armed on the wrong process: the only honest way to prove a parent-death signal is to let a real parent die |

The third one is the guarantee `kill` alone cannot give, and the one the whole POSIX ownership
model rests on, so it is checked against the kernel rather than against the call to `prctl`.

### 3b. Four real Linux defects, not compile errors

Making the tree compile was the easy half. Four things were **wrong on Linux** rather than missing:

1. **`fs_guard.rs` denied every absolute path on POSIX.** The guard asked whether a path began with a
   drive letter (`C:`), which no Linux path does, so its own rule rejected everything. It now asks
   "is this a local absolute path?" — drive letter on Windows, leading `/` on POSIX. The file-system
   tools therefore work on Linux for the first time; the Windows tests still assert the Windows
   spelling.
2. **`git.rs` only looked for Git in `C:\Program Files\Git\…`.** A POSIX box puts `git` on `PATH`, the
   same rule the user's own shell follows; the Windows candidates are still tried first there.
3. **The updater test fixture was CRLF in the working tree and LF in the repository.** `.gitattributes`
   says `* text=auto eol=lf`, the committed blob is 264 bytes of LF, and the checked-out fixture had
   grown to 269 bytes of CRLF — while the signature had been made over the CRLF bytes. The test passed
   on the machine that produced it and failed on a fresh Linux checkout. Fixed by materialising the
   bytes that are actually committed and **re-signing** them with the test key; the Gateway contract
   fixture (`apps/gateway/tests/fixtures/updates-response-1.2.0.json`) carries the new signature and
   the new `sha256`. This was a latent trap for any future checkout, not a Linux-only problem.
4. **The Windows-only host tools answered "not found" on Linux.** `registry_op`, `service_op`,
   `service_named` and `run_powershell` have no Linux meaning at all. They now answer a typed
   `unsupported_platform` instead of pretending the tool exists and failing later.

`.gitattributes` also gained `*.sh text eol=lf`, because a CRLF `stage-native-runtime.sh` fails with
`No such file or directory` — the same class of trap as (3).

### 3c. Secrets and autostart on Linux

**Secrets.** `platform/linux.rs` talks to the Secret Service over `secret-tool` with a fixed
attribute pair (`service=canalla-llm`, `target=<target>`). There is **no plaintext fallback**: when the
bus is missing, the collection is locked, or `secret-tool` is not installed, the operation fails with
the typed constant `secure_storage_unavailable` (`platform::SECURE_STORAGE_UNAVAILABLE`) and the UI
surfaces it. The Rust tests assert exactly that: either the round-trip works, **or** the refusal is
returned *and nothing is written to disk*.

`secret-tool store` exits 0 even when it fails ("Cannot create an item in a locked collection"), so
the mapping is done on its **stderr text** — a locked/unreachable store becomes the typed refusal, and
every other failure carries its own message. `libsecret-tools` therefore is a declared package
dependency (§4).

**Autostart.** `login_write` writes `$XDG_CONFIG_HOME/autostart/canalla-llm.desktop` (falling back to
`~/.config/autostart`) with `Type=Application`, `Name=Canalla LLM`, `Exec=<installed binary>` and
`X-GNOME-Autostart-enabled=true`, and `login_read` derives the setting from that file — including
treating `X-GNOME-Autostart-enabled=false` as "off", so the UI reports the **OS state**, not a cached
boolean. Removing the toggle deletes the file. No root service, no system-wide daemon. The toggle is
the same "Запускать Canalla вместе с системой" the Windows build has, written in front of one
platform-neutral pair of functions.

### 3d. The Python side: 16 failures → 0

The 16 Linux failures STEP 4 recorded were one root cause plus three test assumptions; the port
removed the cause and kept the Windows expectations exactly as they were (the test-side fixes are
`sys.platform`-guarded, not relaxed):

| Group | Tests | Outcome |
|---|---|---|
| `test_093_reliability.py` (8), `test_autonomous_tasks.py` (7), `test_091_reliability.py` (1) | 16 | **fixed**: the host tool loop now resolves POSIX paths, so the file tools actually write on Linux |
| `test_runtime_paths.py` | 1 | asserts the XDG layout on Linux, `%LOCALAPPDATA%` on Windows |
| `test_tor_bundle.py` | 1 | the pin's `libraries` are part of the promised set |
| `test_upgrade.py` | 1 | the deterministic trigger (`chmod 0444`) cannot stop root; skipped as root **with that reason** |

`app/tools/local/{paths,scope,targets,intent,facts,locks,secrets}.py` were the files at fault:
`native_path()` only understood backslashes, the normaliser and the containment check compared against
`\`, the scratch directory assumed the Windows data root, and `assert_local_path` refused POSIX
absolute paths. They now use `os.sep` and `data_paths.default_root()`, so one implementation serves
both platforms.

### 3e. Packaging

`apps/desktop/src-tauri/tauri.linux.conf.json` is the Linux overlay: the `deb` target, PNG icons, the
platform resource names (`sidecar/alex-backend`, `sidecar/alex-host-loop` — no `.exe`), and the one
dependency Tauri cannot derive. It carries **no** Windows material (no NSIS block, no `.ico`, no
batch command), and the Windows config is untouched by it.

`stage-native-runtime.sh` runs as `beforeBundleCommand`. Note that this hook executes with the
**frontend** directory as its working directory, which is why the command is
`bash src-tauri/stage-native-runtime.sh` rather than a path relative to `src-tauri`. It stages the
pinned Tor runtime with `scripts/fetch-tor-runtime.py --platform linux-x86_64` and then **fails the
build** if the backend sidecar or the host loop is missing, naming what is absent — a package that
installs and cannot serve anything is worse than a failed build.

Build:

```
cd apps/desktop
npm ci && npm run build
CARGO_BUILD_JOBS=3 npx tauri build --bundles deb --no-sign
```

`--no-sign` is deliberate: the updater signing identity is a test key in this stage, and a build must
never wait on an interactive password (see the STEP 3 report).

## 4. The Ubuntu artifact

| Field | Value |
|---|---|
| Path | `apps/desktop/src-tauri/target/release/bundle/deb/Canalla LLM_1.1.0_amd64.deb` |
| Size | **294,746,692** bytes |
| SHA256 | `1c52e24633d67f62d243b67ec9fc132b3beb11041b870738136cb99d91c980d2` |
| Package / version / arch | `canalla-llm` 1.1.0 `amd64` (intermediate STEP 4A artifact — **not** a release) |
| Installed size | 711,625 KB |
| `Depends` | `libwebkit2gtk-4.1-0, libgtk-3-0, libayatana-appindicator3-1, librsvg2-2, libsecret-tools, libwebkit2gtk-4.1-0, libgtk-3-0` |

Contents (verified in the built package, not assumed from the source tree):

* `usr/bin/alex-llm` — the desktop binary
* `usr/bin/alex-host-loop` — the headless host loop
* `usr/share/applications/Canalla LLM.desktop`
* `usr/share/icons/hicolor/{32x32,64x64,128x128,256x256@2,512x512}/apps/alex-llm.png`
* `usr/lib/Canalla LLM/sidecar/alex-backend/{alex-backend,_internal/…}` — the packaged backend
* `usr/lib/Canalla LLM/runtime/tor/{tor, libcrypto.so.3, libssl.so.3, libevent-2.1.so.7, geoip, geoip6,
  runtime.json, gpl-3.0.txt, openssl.txt, libevent.txt, tor.txt}` — the pinned daemon and the
  licences it must be redistributed with

**On the repeated entries in `Depends`.** Tauri derives `libwebkit2gtk-4.1-0`, `libgtk-3-0`,
`libayatana-appindicator3-1` and `librsvg2-2` from the linked libraries and appends the two it always
adds again after the configured list; `libsecret-tools` is ours, appended in between. The repetition
is what the bundler emits — a repeated clause in a comma-separated `Depends` is valid and redundant,
`apt` resolved it without complaint, and the alternative would be post-processing the generated
control file for no functional gain. Packaging our own `secret-tool` shim to avoid the dependency
would be worse: the Secret Service client belongs to the desktop, not the product.

## 5. Installing it

```
sudo apt install './Canalla LLM_1.1.0_amd64.deb'
# the shipped runtime, against the *installed* tree, no source checkout needed:
scripts/acceptance-linux-runtime.sh \
  --sidecar "/usr/lib/Canalla LLM/sidecar/alex-backend/alex-backend" \
  --tor-runtime "/usr/lib/Canalla LLM/runtime/tor"
```

That is the gate that matters for this stage: it proves the **installed** backend answers `/health`
with no interpreter on `PATH`, and the **installed** Tor bootstraps, proves a `socks5h` circuit, is
the pinned build, is the process that serves the proof, recovers from being killed with a new pid and a
fresh proof, keeps the backend serving across the restart, and leaves no daemon behind when the
backend stops. 9/9 PASS on Ubuntu 24.04 against the package above.

## 6. What a desktop VM still has to prove (STEP 4B)

This is the honest boundary of STEP 4A. The build environment is a **headless** WSL2 container: it has
no session manager, no display, no keyring, and nothing that consumes `~/.config/autostart`. The
following are therefore **unverified**, and STEP 4A does not claim them:

| Unverified | Why it cannot be settled here |
|---|---|
| First launch of the installed `.deb` in a real session | needs a desktop session |
| GNOME Keyring unlocked at login → device credential, Gateway credential and tokens stored there; **no** secret in a config file | needs a session keyring (the Rust suite was run under a temporary `dbus-run-session` + unlocked `gnome-keyring-daemon` to test the code path, which is *not* the same as an accepted desktop) |
| Autostart registration survives and actually starts Canalla at login | needs a session that reads XDG autostart |
| Single instance when autostart and a manual launch race | the official `tauri-plugin-single-instance` is in the dependency tree and compiles for Linux, but the race needs a session |
| Computer reaches Ready with no click, heartbeat and pairing across a backend restart | needs the desktop UI |
| Global AI badge shows red **Disconnected** with no provider configured | needs the desktop UI |
| Updater panel and a local test endpoint on Linux | needs the desktop UI |
| Killing the desktop and confirming no orphan backend or Tor | needs a session to start from |

Until that run happens, the correct summary is **LINUX ENGINEERING PORT = PASS, DESKTOP VM
ACCEPTANCE = PENDING**, and a green `.deb` install alone is not a Linux release.

Everything in §1–§5 was measured on 23–24 Sep 2026; re-measure rather than trust it.
