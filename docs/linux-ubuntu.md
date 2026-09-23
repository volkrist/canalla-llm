# Ubuntu / Linux support — baseline, measured gap, plan

Status: **not a supported target yet.** This file records what was actually measured on 23 Sep 2026
(STEP 4 of the 1.2.0 work), so the next attempt starts from evidence instead of an assumption.

Nothing here is a promise: every claim below names the command or the file it came from.

## 1. Supported baseline (determined, not guessed)

| Item | Value | How it was determined |
|---|---|---|
| Verified distribution | **Ubuntu 24.04.4 LTS (noble), x86_64** | `lsb_release -a` inside the WSL2 distro used for the build probe |
| WebKitGTK | **webkit2gtk-4.1 = 2.52.6**, `javascriptcoregtk-4.1 = 2.52.6` | `pkg-config --modversion webkit2gtk-4.1` after installing `libwebkit2gtk-4.1-dev` from the noble archive |
| glibc on the machine | **2.39** | `tor --version` reports `Glibc 2.39` |
| Rust toolchain that compiles the tree | **1.98.1** (stable, minimal profile) | `cargo --version` in the same distro |
| Node used for the frontend build | **22.23.2** (vite 7.3.6 needs ≥ 20.19) | `node --version`; `package-lock.json` pins vite 7.3.6 |
| Bundled Tor daemon | **Tor 0.4.9.12**, Libevent 2.1.13, OpenSSL 3.5.8, Zlib 1.3, glibc 2.39 | `LD_LIBRARY_PATH=… ./tor --version` on the staged linux-x86_64 expert bundle |
| Secret storage | Ubuntu's Secret Service (gnome-keyring / KWallet) — **absent in the probe environment** | `ls /usr/bin/gnome-keyring-daemon /usr/bin/secret-tool` → not found |
| Desktop session / XDG autostart | **not testable in the probe environment** | no session manager, no `~/.config/autostart` consumer |

**Debian package floor follows glibc.** A package built on noble links glibc 2.39, so it will not run
on 22.04 (glibc 2.35). Two honest options, to be chosen deliberately in the next attempt:

* build the `.deb` on 22.04 (jammy) to support 22.04 → 24.04, or
* declare **24.04 as the supported baseline** and build on noble.

Nothing was built for either yet — see §4.

## 2. What already works on Linux (measured)

| Component | Result |
|---|---|
| Bundled Tor runtime (linux-x86_64) | **works**: `scripts/fetch-tor-runtime.py --platform linux-x86_64` verifies the pinned SHA256 and stages 10 files; the daemon reports `0.4.9.12` and runs |
| **Packaged backend sidecar** | **works**: a PyInstaller onedir built on Linux (`alex-backend.spec`) answers `/health` with `product: alex-llm` while started with **`PATH` pointing at an empty directory** — no interpreter, no shell tool |
| **Bundled Tor end to end** | **works**: bootstraps, proves a real `socks5h` route (`verified: true`), the serving process is `/proc/<pid>/exe == runtime/tor/tor`, the pinned `0.4.9.12` |
| **Tor recovery** | **works**: killing the daemon gives a new pid with a fresh proof; the device keeps serving through the restart |
| **Clean shutdown** | **works**: stopping the backend leaves no `tor` process behind |
| Backend (Python) test suite | **606 passed, 16 failed**, 8 skipped (Windows: 627 passed, 1 skipped) — see §3 |
| Product data paths | **correct on Linux**: `data_paths.py` uses `XDG_DATA_HOME` or `~/.local/share` and `alex-llm` as the app directory; only the *test* assumed `%LOCALAPPDATA%` |
| Tor runtime pin honesty | the pin's `libraries` list is part of the promise; only the *test* forgot it |

All of the above is one command: `scripts/acceptance-linux-runtime.sh` (9/9 PASS on Ubuntu 24.04,
`alex-backend` built on that machine, `runtime/tor` staged from the pin). It needs no desktop session
and no GPU.

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
`LD_LIBRARY_PATH` to the runtime directory on POSIX only — Windows passes no custom environment, as
before — and the runtime directory is resolved to an absolute path once, where it is discovered.
Both are asserted: `test_the_managed_daemon_can_find_the_libraries_beside_it`, and 9/9 on the Linux
acceptance above.

## 3. The measured gap

### 3a. The desktop runtime does not compile for Linux

`cargo check` in `apps/desktop/src-tauri` on Ubuntu 24.04 (all dependencies built fine; only our own
crate fails):

| File | Errors |
|---|---|
| `src/host.rs` | 19 |
| `src/process.rs` | 13 |
| `src/credential.rs` | 12 |
| `src/backend.rs` | 12 |
| `src/autostart.rs` | 3 |
| **`alex-llm` binary** | **59** (`81 × E0433 cannot find`, `13 × E0599 no method`, `9 × E0432 unresolved import`) |
| **`alex-host-loop` binary** | **44** (same modules) |

The failures are missing platform implementations, not logic errors:

* `process.rs` — `CreateProcess` flags (`creation_flags`, `CREATE_NO_WINDOW`,
  `CREATE_BREAKAWAY_FROM_JOB`), `Child::as_raw_handle`, and the Win32 **Job Object** that owns the
  sidecar and kills it with the desktop.
* `host.rs` — `std::os::windows::ffi::OsStrExt::encode_wide` and other Win32-only path handlings.
* `credential.rs` — the Windows Credential Manager (`CredReadW`/`CredWriteW`/`CredDeleteW`).
* `backend.rs` — `AssignProcessToJobObject` on the spawned sidecar.
* `autostart.rs` — the registry `Run` value.

Each needs a Linux counterpart, and the *supervision* one is the hard part: the Windows binary is
killed with its job object, so Linux needs an equivalent guarantee (a process group with
`PR_SET_PDEATHSIG`, or a cgroup) rather than a changed `cfg`.

### 3b. The local computer (host tool loop) is Windows-only in the Python backend too

The 16 Linux failures are not spread out — they are one root cause plus three test assumptions:

| Group | Tests | Kind |
|---|---|---|
| `test_093_reliability.py` (8), `test_autonomous_tasks.py` (7), `test_091_reliability.py` (1: `test_git_commit_when_explicitly_requested`) | 16 | **product gap**: the native host that executes file tools produces nothing on Linux (`assert 'write_file' in []`, `FileNotFoundError` for the file it was asked to write) |
| `test_runtime_paths.py` | 1 | **test-only**: asserted the Windows layout; fixed to assert the XDG layout on Linux |
| `test_tor_bundle.py` | 1 | **test-only**: the pin's `libraries` were not part of the promised set; fixed |
| `test_upgrade.py` | 1 | **test-only**: the deterministic trigger (`chmod 0444`) does not stop root; now skipped as root with that reason |

The three test-side fixes keep the Windows expectations exactly as they were (they are
`sys.platform`-guarded, not relaxed): the point is that the remaining failures *are* the gap, not a
mixture of the gap and the harness.

"Computer Ready" therefore has **no Linux implementation at all** today — the same module family as
the Rust errors above. This is the single biggest piece of the port.

### 3c. Packaging deltas (not attempted yet)

| Item | Windows today | Linux needs |
|---|---|---|
| Bundle target | `"targets": ["nsis"]` | a `deb` target (Tauri supports it; verify on the chosen baseline) |
| Icons | `icons/icon.ico` | PNG icons (Tauri's Linux bundler requires them) |
| Resources | `sidecar/alex-host-loop.exe`, `runtime/tor` | platform-aware names (`alex-host-loop`, `alex-backend` — no `.exe`); a `tauri.linux.conf.json` overlay is the natural place |
| `build.rs` | panics with `BACKEND_SIDECAR_MISSING` when `sidecar/alex-backend/alex-backend.exe` is absent in a release build; fabricates an empty `alex-host-loop.exe` | the same check for the Linux sidecar name |
| `beforeBundleCommand` | `stage-native-runtime.cmd` (a Windows batch) | must not run on Linux; needs a shell equivalent or a platform-guarded command |
| Sidecar | PyInstaller onedir `alex-backend.exe` | a PyInstaller onedir built **on Linux** (same `alex-backend.spec`), then the runtime is inside it |
| Secret storage | Credential Manager | Secret Service (libsecret / `secret-tool` over D-Bus), with an explicit failure when no keyring is present — never a plaintext fallback |
| Autostart | `HKCU\…\Run` | `~/.config/autostart/canalla-llm.desktop` written by the app, removed on toggle-off |

## 4. Why this step stopped

Two independent blockers, both measured rather than assumed:

1. **No Ubuntu desktop environment is available on this machine.** The only Linux here is a headless
   WSL2 Ubuntu 24.04 container: no session manager, no keyring/Secret Service, no XDG session, no
   logon. STEP 4's acceptance requires exactly those (GUI, session autostart, keyring, reboot
   recovery). A `.deb` cannot be installed and accepted there, and STEP 4 says to stop rather than
   report a pass. Docker Desktop's engine is not running and no Hyper-V/VirtualBox VM host is
   available here.
2. **The product has no Linux implementation of the computer half.** Beyond packaging, `host.rs` /
   `process.rs` / the Python host tool loop must be ported (see §3a/§3b).

## 5. Plan for the next attempt (in order)

1. On a real Ubuntu 24.04 desktop VM (or 22.04 if that becomes the baseline), with a normal user
   account and a running keyring:
   `sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev patchelf`
2. Port the runtime, module by module, keeping every Windows behaviour asserted:
   `process.rs` (process group + parent-death signal instead of the job object), `host.rs`,
   `credential.rs` (Secret Service), `backend.rs`, `autostart.rs` (XDG `.desktop`).
3. Port the Python host tool loop so the file tools work on POSIX paths.
4. Add `tauri.linux.conf.json`: `deb` target, PNG icons, platform resource names, no Windows batch.
5. Build the Linux sidecar with `alex-backend.spec` and stage the pinned Tor runtime.
6. Then the acceptance STEP 4 describes: install, first run, Computer/Tor ready, kill backend and
   Tor and see recovery with no click, logout/login, uninstall — with secrets in the keyring and
   none in a config file.

Everything in §1 was measured in one session on 23 Sep 2026; re-measure rather than trust it.
