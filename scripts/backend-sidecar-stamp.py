#!/usr/bin/env python3
"""Provenance for the packaged backend: a stamp the bundle step refuses to trust blindly.

The 1.2.0 release candidate shipped a backend frozen on 23 September — built before the version bump
and before that release's own Tor routing work — because the bundle step checked that a sidecar
*existed* and never checked where it came from. The installer therefore advertised 1.2.0 while its
backend answered `/health` with 1.1.0, and the headline feature of the release was not in it.

Two things are recorded when the sidecar is built and verified before it is bundled:

* `product_version` — read from `app/product.py`, the authoritative product version;
* `source_digest`    — SHA-256 over the *content* of the backend source inputs.

The digest deliberately does not use mtime or size: a restored file, a fresh checkout or a rebuild
that changed nothing would all look different, while a genuinely stale binary can keep its size. It
normalises CRLF to LF so that a Windows checkout and a Linux checkout of the same revision produce
the same digest instead of a false mismatch.

The stamp also carries the built binary's SHA-256, and the check verifies it, so a stamp cannot
describe a sidecar that was replaced after it was written.

What this does not do: prove the binary was compiled from exactly that source. It proves the source
has not moved on since the build, which is the failure that actually happened. A rebuild is always
the answer to a mismatch, never editing the stamp.

Usage:
    backend-sidecar-stamp.py write --backend apps/backend --stamp <path>
    backend-sidecar-stamp.py check --backend apps/backend --stamp <path>

Exit codes:
    0  the stamp describes the current source and the sidecar on disk
    3  not trustworthy — prints BACKEND_SIDECAR_STALE and a reason (the bundle step must fail)
    2  usage error
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# The inputs that end up inside the packaged backend. `tests` is excluded on purpose: a test change
# must not force a sidecar rebuild, because tests are not shipped.
SOURCE_INPUTS = ("app", "alembic", "alembic.ini", "alex-backend.spec", "pyproject.toml")
SKIP_DIRS = {"__pycache__", ".venv", "dist", "build", ".pytest_cache", ".mypy_cache", "tests"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".db", ".db-wal", ".db-shm")

STALE = "BACKEND_SIDECAR_STALE"
STAMP_NAME = "build-stamp.json"
VERSION_LINE = re.compile(r'^VERSION\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


def product_version(backend: Path) -> str:
    """The authoritative product version, straight from `app/product.py`."""
    source = (backend / "app" / "product.py").read_text(encoding="utf-8")
    found = VERSION_LINE.search(source)
    if not found:
        raise SystemExit(f"cannot read VERSION from {backend / 'app' / 'product.py'}")
    return found.group(1)


def _inputs(backend: Path) -> list[Path]:
    files: list[Path] = []
    for name in SOURCE_INPUTS:
        target = backend / name
        if target.is_dir():
            for path in sorted(target.rglob("*")):
                if not path.is_file():
                    continue
                if any(part in SKIP_DIRS for part in path.relative_to(backend).parts):
                    continue
                if path.suffix in SKIP_SUFFIXES:
                    continue
                files.append(path)
        elif target.is_file():
            files.append(target)
    return sorted(files, key=lambda path: path.relative_to(backend).as_posix())


def source_digest(backend: Path) -> tuple[str, int]:
    """SHA-256 over `path \\0 content` for every backend source input, CRLF normalised to LF."""
    digest = hashlib.sha256()
    files = _inputs(backend)
    for path in files:
        content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(path.relative_to(backend).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest(), len(files)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def backend_executable(stamp_path: Path) -> Path:
    """The sidecar binary the stamp sits beside (`<staged sidecar dir>/alex-backend.exe`)."""
    directory = stamp_path.parent
    for name in ("alex-backend.exe", "alex-backend"):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    raise SystemExit(f"no packaged backend beside {stamp_path}")


def write_stamp(backend: Path, stamp_path: Path) -> dict:
    digest, count = source_digest(backend)
    executable = backend_executable(stamp_path)
    stamp = {
        "product": "alex-llm",
        "product_version": product_version(backend),
        "source_digest": digest,
        "source_files": count,
        "backend_exe": executable.name,
        "backend_exe_sha256": file_sha256(executable),
    }
    stamp_path.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    return stamp


def check_stamp(backend: Path, stamp_path: Path) -> str | None:
    """`None` when the stamp describes the current source and the sidecar; a reason otherwise."""
    if not stamp_path.is_file():
        return "stamp_missing"
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return f"stamp_unreadable:{error}"
    if not isinstance(stamp, dict):
        return "stamp_unreadable:not_an_object"

    expected = product_version(backend)
    stamped = stamp.get("product_version")
    if stamped != expected:
        return f"product_version:{stamped!s}!=expected:{expected}"

    digest, count = source_digest(backend)
    if stamp.get("source_digest") != digest:
        return f"source_digest:stamped={stamp.get('source_digest')} current={digest} files={count}"

    try:
        executable = backend_executable(stamp_path)
    except SystemExit:
        return "backend_executable_missing"
    if stamp.get("backend_exe_sha256") != file_sha256(executable):
        return "backend_executable_changed_since_the_stamp_was_written"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("write", "check"))
    parser.add_argument("--backend", required=True, type=Path)
    parser.add_argument("--stamp", required=True, type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    backend = args.backend.resolve()
    stamp = args.stamp.resolve()
    if not (backend / "app" / "product.py").is_file():
        print(f"not a backend tree: {backend}", file=sys.stderr)
        return 2

    if args.action == "write":
        written = write_stamp(backend, stamp)
        if not args.quiet:
            print(f"product_version={written['product_version']}")
            print(f"source_digest={written['source_digest']}")
            print(f"source_files={written['source_files']}")
            print(f"backend_exe_sha256={written['backend_exe_sha256']}")
        return 0

    reason = check_stamp(backend, stamp)
    if reason is None:
        if not args.quiet:
            print(f"backend sidecar is current ({stamp})")
        return 0
    # A warning would be ignored; the bundle step must fail.
    print(f"{STALE} {reason}", file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
