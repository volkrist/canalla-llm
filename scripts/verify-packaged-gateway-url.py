"""Packaged-artifact guard: the production Gateway URL must be compiled in.

The 1.2.0 installer was built without ``ALEX_GATEWAY_URL``, so a fresh install showed an empty
«Адрес Gateway» field and a disabled «Подключить» button — the client silently could not enroll
(new users had to type the address by hand). The root cause is a *build* step, so the guard
belongs to the build too: this script inspects a built desktop executable and fails when the
production Gateway URL is not baked in.

Why the *bare* URL matters
--------------------------
``tauri.conf.json`` also embeds the updater endpoint
``https://gateway.12testers.store/updates/latest?...``, so the host being present proves nothing.
``apps/desktop/src-tauri/src/gateway.rs`` reads the enrollment default from
``option_env!("ALEX_GATEWAY_URL")`` — a literal that ends right after the host. So the check
requires an occurrence of the host that is **not** followed by a path, which is exactly the
signature of the baked value (the 1.2.0 binary had one occurrence, and it was the updater URL).

Usage:
    python scripts/verify-packaged-gateway-url.py <built exe> [--url https://...]
    python scripts/verify-packaged-gateway-url.py --self-test
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

DEFAULT_URL = "https://gateway.12testers.store"


def baked_url_present(data: bytes, url: str) -> bool:
    """Is ``url`` present as a standalone literal (no path, query or fragment after it)?"""
    pattern = re.compile(re.escape(url.encode("utf-8")) + rb"(?![\w.\-/?#])")
    return pattern.search(data) is not None


def verify(path: Path, url: str = DEFAULT_URL) -> int:
    try:
        data = path.read_bytes()
    except OSError as error:
        print(f"PACKAGED-GATEWAY-URL: FAIL — {path} is unreadable ({error})")
        return 2
    if baked_url_present(data, url):
        print(f"PACKAGED-GATEWAY-URL: PASS — {path.name} carries {url}")
        return 0
    print(
        "PACKAGED-GATEWAY-URL: FAIL — "
        f"{path.name} does not carry the production Gateway URL {url}.\n"
        "  The client would show an empty «Адрес Gateway» and a disabled «Подключить».\n"
        "  Build with ALEX_GATEWAY_URL set (scripts/build-desktop.ps1 does this by default)."
    )
    return 1


def self_test() -> int:
    """Prove both directions without needing a build: the 1.2.0 signature fails, a baked one passes."""
    import tempfile

    updater_only = b'... "https://gateway.12testers.store/updates/latest?target=x" ...'
    baked = b"... https://gateway.12testers.store\x00 more section data ..."
    with tempfile.TemporaryDirectory() as folder:
        missing = Path(folder) / "without-url.exe"
        present = Path(folder) / "with-url.exe"
        missing.write_bytes(updater_only * 4)
        present.write_bytes(updater_only * 4 + baked)
        if verify(missing) != 1:
            print("self-test: the missing-URL case was not detected")
            return 1
        if verify(present) != 0:
            print("self-test: the baked-URL case was not accepted")
            return 1
    print(
        "PACKAGED-GATEWAY-URL: self-test PASS (detects a missing URL, accepts a baked one)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "artifact", nargs="?", help="built desktop executable to inspect"
    )
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.artifact:
        parser.error("an artifact path (or --self-test) is required")
    return verify(Path(args.artifact), args.url)


if __name__ == "__main__":
    raise SystemExit(main())
