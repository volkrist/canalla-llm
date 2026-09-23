# Third-party notices

Every binary Canalla LLM distributes that it did not build itself, with its version,
licence and provenance. Anything listed here travels inside the installer, and the
licence texts travel with it (in the same directory as the binary, so an installed
copy carries its own notices).

## Tor daemon (bundled)

| | Windows x86_64 | Linux x86_64 |
|---|---|---|
| File | `runtime/tor/tor.exe` | `runtime/tor/tor` (+ `libcrypto.so.3`, `libevent-2.1.so.7`, `libssl.so.3`) |
| Version | Tor 0.4.9.12 (Tor Browser release 15.0.23) | same |
| Source | `https://dist.torproject.org/torbrowser/15.0.23/tor-expert-bundle-windows-x86_64-15.0.23.tar.gz` | `https://dist.torproject.org/torbrowser/15.0.23/tor-expert-bundle-linux-x86_64-15.0.23.tar.gz` |
| Archive SHA256 | `231dad6b9cb401a54c260db7046965ef04e4f72ff071b140d423fb5da281ab1e` (22 432 027 bytes) | `08d49de27f542b8f73e2014e064d8320562b5d20019c03d4725c5a5249d97985` (32 339 495 bytes) |
| Staged SHA256 (`tor` binary) | `60c45b01938c799862e511a9a5bab12f959a819c6264a24502edc342165f570c` | pinned by the build step; see `runtime.json` next to the binary |
| Licence | **GPL-3.0** | **GPL-3.0** |
| Licence texts shipped | `runtime/tor/tor.txt`, `openssl.txt`, `libevent.txt`, `zlib.txt`, `gpl-3.0.txt` | `runtime/tor/tor.txt`, `openssl.txt`, `libevent.txt`, `gpl-3.0.txt` |
| Corresponding source | `https://dist.torproject.org/tor-0.4.9.12.tar.gz` | same |
| Build scripts | `https://gitlab.torproject.org/tpo/applications/tor-browser-build` | same |

The Tor Project compiles its released binaries with `--enable-gpl` (its own
`configure.ac`: *"allow the inclusion of GPL-licensed code, building a version of tor
and libtor covered by the GPL rather than its usual 3-clause BSD license"*), and the
daemon prints that notice in `tor --version`. The product therefore distributes the
daemon under the GPL-3.0: the licence texts are shipped next to the binary, the
corresponding source is published by the Tor Project at the URL above, and nothing
in Canalla's terms restricts the rights the GPL grants over that daemon. Canalla
starts it as a separate process and does not link it into the application.

The bundle's own `docs/tor.txt` lists the licences of every component inside the
build (Tor, OpenSSL, libevent, zlib, and the pluggable transports the product does
**not** ship: only `tor/tor[.exe]`, the geoip data and the licence texts are staged).

## Not bundled

- **Tor Browser** — not shipped and not required. The product manages its own
  daemon; a user's Tor Browser is only ever *discovered* as an already-working
  endpoint on `127.0.0.1:9150` and is never modified.
- **Pluggable transports** (`lyrebird`, `conjure-client`, `snowflake`) — present in
  the official archive, deliberately not staged: the product does not use bridges.
- **Python, Node, Rust, Tor** — no runtime dependency for the user; the backend is a
  packaged sidecar and the Tor daemon is the one listed above.

## Canalla's own binaries

`sidecar/alex-backend/` (PyInstaller onedir), `alex-host-loop.exe` and the desktop
executable are built from this repository and contain their dependencies (Python
runtime, Rust, and the Python packages pinned in `apps/backend/requirements*.txt`).
They are not third-party distributions in the sense above, and their versions move
with the Canalla release.

## Keeping this file honest

`scripts/tor-runtime.json` is the machine-readable form of the Tor row above, and
`apps/backend/tests/test_tor_bundle.py` fails when the two drift, when an archive
stops coming from `dist.torproject.org`, when a pin loses its SHA256, or when the
bundle configuration stops shipping the runtime. A new Tor version is a deliberate
act: update the pin, re-run the fetch step, and the tests and this file follow.
