#!/usr/bin/env python3
"""Fetch, verify and stage the Tor runtime Canalla ships.

Canalla ships the Tor daemon so nobody has to install Tor Browser. The source is the Tor Project's
official *expert bundle* for the platform (the daemon, its libraries, geoip data and the license
notices -- no browser and no pluggable transports), pinned by version, size and SHA256 in
``scripts/tor-runtime.json``. This script is the only thing that ever downloads it:

* a download is refused unless its size and SHA256 match the pin exactly, so a mirror cannot swap a
  binary in and no build can silently link an unexpected runtime;
* only the files the product needs are extracted, flattened into the staging directory that Tauri
  bundles as ``runtime/tor``;
* nothing here runs on a user's machine. The runtime is part of the release artifact, and updating it
  means updating the pin in a new Canalla release.

Usage::

    python scripts/fetch-tor-runtime.py                       # fetch and stage for this host
    python scripts/fetch-tor-runtime.py --archive FILE        # use an already downloaded archive
    python scripts/fetch-tor-runtime.py --dest DIR --platform linux-x86_64
    python scripts/fetch-tor-runtime.py --verify-only         # check the pins, touch nothing
    python scripts/fetch-tor-runtime.py --clean               # remove the staged runtime
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PIN_FILE = REPO / "scripts" / "tor-runtime.json"
STAGING = REPO / "apps" / "desktop" / "src-tauri" / "runtime" / "tor"
HOST_PLATFORMS = {"win32": "windows-x86_64", "linux": "linux-x86_64"}
CHUNK = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 120.0
METADATA_NAME = "runtime.json"


def pins() -> dict:
    data = json.loads(PIN_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("platforms"), dict):
        raise SystemExit(f"{PIN_FILE} is not a pinned runtime manifest")
    return data


def host_platform() -> str:
    platform = HOST_PLATFORMS.get(sys.platform)
    if platform is None:
        raise SystemExit(f"no pinned runtime for platform {sys.platform!r}; pass --platform")
    return platform


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def wanted_members(entry: dict) -> list[str]:
    members = [entry.get("binary"), *(entry.get("libraries") or []), *(entry.get("data") or [])]
    members += list(entry.get("notices") or [])
    resolved = [str(member) for member in members if member]
    if not resolved:
        raise SystemExit("the pin lists no files to stage")
    for member in resolved:
        if member.startswith("/") or ".." in Path(member).parts:
            raise SystemExit(f"the pin names an unsafe archive path: {member}")
    return resolved


def download(url: str, target: Path) -> None:
    _fetch(url, target)


def _fetch(url: str, target: Path) -> None:
    """Download one pinned artefact, with a fallback for environments where urllib cannot reach out.

    Some build machines route network access through a proxy that only the system tools know about,
    so a failing urllib attempt is retried with ``curl`` (or ``wget``) before the build gives up. The
    download is verified by the caller either way: a fallback can never change what gets staged.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "canalla-llm-build"})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # noqa: S310
            with target.open("wb") as handle:
                shutil.copyfileobj(response, handle, CHUNK)
        return
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        logger_reason = type(error).__name__
    curl = shutil.which("curl")
    if curl:
        result = _run([curl, "-sS", "-L", "--max-time", "600", "-o", str(target), url])
        if result == 0 and target.is_file():
            return
    wget = shutil.which("wget")
    if wget:
        result = _run([wget, "-q", "-O", str(target), url])
        if result == 0 and target.is_file():
            return
    raise SystemExit(f"could not download {url} ({logger_reason})")


def _run(args: list[str]) -> int:
    import subprocess

    try:
        return subprocess.run(args, check=False).returncode
    except OSError:
        return 1


def verify(archive: Path, entry: dict, platform: str) -> None:
    expected_bytes = entry.get("bytes")
    size = archive.stat().st_size
    if expected_bytes and size != expected_bytes:
        raise SystemExit(f"{platform}: {archive.name} is {size} bytes, expected {expected_bytes}")
    digest = sha256_of(archive)
    if digest != entry.get("sha256"):
        raise SystemExit(
            f"{platform}: {archive.name} has sha256 {digest}, expected {entry.get('sha256')}"
        )


def stage_license_texts(texts: list[dict], dest: Path) -> dict[str, str]:
    """The pinned license texts, verified exactly like the archive itself.

    A text that is already staged with the right hash is kept as it is, so an offline build that
    was staged before does not need the network again.
    """
    staged: dict[str, str] = {}
    for text in texts:
        name = str(text.get("name") or "")
        url = str(text.get("url") or "")
        digest = str(text.get("sha256") or "")
        if not name or not url or not digest:
            raise SystemExit("a pinned license text needs a name, a url and a sha256")
        target = dest / name
        if target.is_file() and sha256_of(target) == digest:
            staged[name] = digest
            continue
        _fetch(url, target)
        found = sha256_of(target)
        if found != digest:
            raise SystemExit(f"{name}: sha256 {found} does not match the pin {digest}")
        target.chmod(0o644)
        staged[name] = found
    return staged


def stage(archive: Path, entry: dict, dest: Path) -> dict:
    """Extract only the pinned files, flattened, and record what was staged."""
    names = wanted_members(entry)
    dest.mkdir(parents=True, exist_ok=True)
    staged: dict[str, str] = {}
    binary_name = Path(str(entry["binary"])).name

    with tarfile.open(archive, "r:gz") as tar:
        available = tar.getnames()
        for name in names:
            if name not in available:
                raise SystemExit(f"the archive does not contain {name}")
            member = tar.extractfile(name)
            if member is None:
                raise SystemExit(f"{name} is not a regular file in the archive")
            target = dest / Path(name).name
            target.write_bytes(member.read())
            executable = Path(name).name == binary_name or name in (entry.get("libraries") or [])
            target.chmod(0o755 if executable else 0o644)
            staged[target.name] = sha256_of(target)

    staged.update(stage_license_texts(entry.get("_license_texts") or [], dest))

    # Anything the previous staging left behind would be bundled as well: keep exactly the pin.
    for leftover in dest.iterdir():
        if leftover.is_file() and leftover.name not in staged and leftover.name != METADATA_NAME:
            leftover.unlink()

    metadata = {
        "product": "tor",
        "version": entry.get("_bundle_version"),
        "daemon_version": entry.get("daemon_version") or "",
        "license": entry.get("_license"),
        "license_note": entry.get("_license_note"),
        "corresponding_source": entry.get("_corresponding_source"),
        "source": entry.get("_source"),
        "archive": {
            "url": entry.get("url"),
            "sha256": entry.get("sha256"),
            "bytes": entry.get("bytes"),
        },
        "binary": binary_name,
        "files": staged,
    }
    (dest / METADATA_NAME).write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return metadata


def clean(dest: Path) -> None:
    if dest.is_dir():
        shutil.rmtree(dest)
        print(f"removed {dest}")
    else:
        print(f"nothing staged at {dest}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch, verify and stage the Tor runtime Canalla ships."
    )
    parser.add_argument("--platform", default=None, help="windows-x86_64 or linux-x86_64")
    parser.add_argument("--dest", default=None, help="staging directory")
    parser.add_argument("--archive", default=None, help="an already downloaded archive")
    parser.add_argument("--verify-only", action="store_true", help="check the pins and stop")
    parser.add_argument("--clean", action="store_true", help="remove the staged runtime")
    parser.add_argument("--print", dest="show", action="store_true", help="print the staged files")
    args = parser.parse_args(argv)

    platform = args.platform or host_platform()
    manifest = pins()
    if platform not in manifest["platforms"]:
        raise SystemExit(f"the pin does not describe {platform}")
    entry = dict(manifest["platforms"][platform])
    entry["_bundle_version"] = manifest.get("version")
    entry["_license"] = manifest.get("license")
    entry["_license_note"] = manifest.get("license_note")
    entry["_corresponding_source"] = manifest.get("corresponding_source")
    entry["_source"] = manifest.get("source")
    entry["_license_texts"] = manifest.get("license_texts") or []

    dest = Path(args.dest) if args.dest else STAGING

    if args.clean:
        clean(dest)
        return 0

    if args.verify_only:
        print(
            f"{manifest['product']} {manifest['version']} ({manifest['license']}) "
            f"{platform}: {entry['url']} sha256={entry['sha256']} bytes={entry['bytes']}"
        )
        for name in wanted_members(entry):
            print(f"  stages {name}")
        for text in manifest.get("license_texts") or []:
            print(f"  stages {text['name']} from {text['url']} sha256={text['sha256']}")
        return 0

    archive = Path(args.archive) if args.archive else None
    temporary: Path | None = None
    try:
        if archive is None:
            handle, name = tempfile.mkstemp(prefix="tor-expert-bundle-", suffix=".tar.gz")
            os.close(handle)
            temporary = Path(name)
            archive = temporary
            print(f"downloading {entry['url']}")
            download(str(entry["url"]), archive)
        if not archive.is_file():
            raise SystemExit(f"{archive} does not exist")
        verify(archive, entry, platform)
        metadata = stage(archive, entry, dest)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    print(
        f"staged tor {metadata['version']} ({platform}) into {dest}: "
        f"{len(metadata['files'])} files, binary {metadata['binary']}"
    )
    if args.show:
        for name, digest in sorted(metadata["files"].items()):
            print(f"  {name} {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
