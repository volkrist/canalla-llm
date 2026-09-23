"""The bundled Tor runtime: the pin, the packaging and the staged files.

Canalla must bring its own Tor daemon. This suite is what makes that a fact instead of an intention,
and it stays offline: the pin has to describe an official Tor Project archive for each platform, the
bundle configuration has to actually ship the runtime, and anything staged has to match the pin file
for file. Nothing here downloads or starts a daemon.

The fetch step (`scripts/fetch-tor-runtime.py`) is the only thing that touches the network, and it
refuses to stage a byte that does not match these pins.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PIN_FILE = REPO / "scripts" / "tor-runtime.json"
FETCH_SCRIPT = REPO / "scripts" / "fetch-tor-runtime.py"
TAURI_CONFIG = REPO / "apps" / "desktop" / "src-tauri" / "tauri.conf.json"
STAGING = REPO / "apps" / "desktop" / "src-tauri" / "runtime" / "tor"
GITIGNORE = REPO / ".gitignore"
PLATFORMS = ("windows-x86_64", "linux-x86_64")
DAEMON_NAME = {"windows-x86_64": "tor.exe", "linux-x86_64": "tor"}


def pin() -> dict:
    return json.loads(PIN_FILE.read_text(encoding="utf-8"))


def test_the_pin_describes_the_platforms_the_product_claims() -> None:
    manifest = pin()

    assert manifest["product"] == "tor"
    assert manifest["version"].count(".") == 2
    assert set(manifest["platforms"]) == set(PLATFORMS)


def test_every_archive_comes_from_the_official_tor_project() -> None:
    """A mirror, a fork or a random GitHub release must not be able to enter the product."""
    manifest = pin()
    version = manifest["version"]

    for platform, entry in manifest["platforms"].items():
        url = entry["url"]
        assert url.startswith(f"https://dist.torproject.org/torbrowser/{version}/"), url
        assert url.endswith(f"tor-expert-bundle-{platform}-{version}.tar.gz"), url


def test_every_archive_is_pinned_by_size_and_sha256() -> None:
    manifest = pin()

    for platform, entry in manifest["platforms"].items():
        digest = entry["sha256"]
        assert len(digest) == 64 and digest == digest.lower(), platform
        assert all(character in "0123456789abcdef" for character in digest), platform
        assert entry["bytes"] > 10_000_000, platform  # a real daemon, not a placeholder


def test_every_entry_stages_the_daemon_the_data_and_the_licenses() -> None:
    manifest = pin()

    for platform, entry in manifest["platforms"].items():
        assert entry["binary"] == f"tor/{DAEMON_NAME[platform]}", platform
        assert entry["data"], f"{platform}: geoip data is part of the runtime"
        assert "docs/tor.txt" in entry["notices"], f"{platform}: Tor's own license travels with it"
        assert entry["daemon_version"].count(".") == 3, platform


def test_the_licence_and_its_source_are_recorded() -> None:
    """The official builds are compiled with --enable-gpl, so the offer has to be explicit."""
    manifest = pin()

    assert manifest["license"] == "GPL-3.0"
    assert "enable-gpl" in manifest["license_note"]
    daemon = manifest["platforms"]["windows-x86_64"]["daemon_version"]
    assert daemon in manifest["corresponding_source"], manifest["corresponding_source"]
    assert manifest["corresponding_source"].startswith("https://dist.torproject.org/")
    assert any(text["name"] == "gpl-3.0.txt" for text in manifest["license_texts"])


def test_the_fetch_step_is_the_only_thing_that_downloads() -> None:
    script = FETCH_SCRIPT.read_text(encoding="utf-8")

    assert "sha256_of" in script, "the download is verified by hash"
    assert "does not match the pin" in script, "a mismatch must stop the build"
    assert "tor-runtime.json" in script


def test_the_installer_ships_the_runtime() -> None:
    """The source tree is not the product: the bundle configuration is what puts Tor on a machine."""
    resources = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))["bundle"]["resources"]

    assert "runtime/tor" in resources
    # The sidecar the backend runs is still listed: the runtime is an addition, not a replacement.
    assert "sidecar/alex-backend" in resources


def test_the_staged_runtime_is_never_committed() -> None:
    ignored = GITIGNORE.read_text(encoding="utf-8").splitlines()

    assert "apps/desktop/src-tauri/runtime/" in ignored


@pytest.mark.skipif(
    not (STAGING / "runtime.json").is_file(),
    reason="no runtime is staged in this checkout (run scripts/fetch-tor-runtime.py)",
)
def test_a_staged_runtime_matches_the_pin() -> None:
    """What is about to be bundled is exactly what the pin describes, file for file."""
    metadata = json.loads((STAGING / "runtime.json").read_text(encoding="utf-8"))
    manifest = pin()
    platform = next(
        name
        for name, entry in manifest["platforms"].items()
        if entry["binary"] == f"tor/{metadata['binary']}"
    )
    entry = manifest["platforms"][platform]

    assert metadata["archive"]["sha256"] == entry["sha256"]
    assert metadata["archive"]["url"] == entry["url"]
    assert metadata["daemon_version"] == entry["daemon_version"]
    assert metadata["license"] == manifest["license"]
    assert (STAGING / metadata["binary"]).is_file()

    for name, digest in metadata["files"].items():
        staged = STAGING / name
        assert staged.is_file(), name
        assert hashlib.sha256(staged.read_bytes()).hexdigest() == digest, name

    # Everything the pin promises is there, and nothing else is about to ship. The shared
    # libraries are part of that promise on the platforms that need them (the Linux daemon does not
    # start without them); the list is empty on Windows, so this stays exact there too.
    promised = {Path(name).name for name in [entry["binary"], *entry["data"], *entry["notices"]]}
    promised |= {Path(name).name for name in entry.get("libraries", [])}
    promised |= {text["name"] for text in manifest["license_texts"]}
    assert set(metadata["files"]) == promised
