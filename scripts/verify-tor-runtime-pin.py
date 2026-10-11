#!/usr/bin/env python3
"""Verify the Tor runtime pin against the Tor Project's own signed checksums.

The pin in ``scripts/tor-runtime.json`` is only as trustworthy as what it was taken from. This
script proves the chain end to end, without privilege and without downloading the 22 MB bundle:

1. the official ``sha256sums-signed-build.txt`` for the pinned Tor Browser release is downloaded
   and its own SHA256 is checked against a second, independent anchor in the manifest;
2. when ``gpg`` is available the detached signature is verified against the pinned Tor signing-key
   fingerprint — the key is fetched *by* that fingerprint from ``keys.openpgp.org`` and the
   fingerprint it actually reports is re-checked before the signature is trusted;
3. every platform's pinned archive SHA256 is looked up in the signed file and must match exactly.

A mismatch is a hard failure. A missing ``gpg`` when a signature is required is BLOCKED (exit 3),
never a silent pass. ``--self-test`` proves the parsing and the mismatch detection with no network.

Nothing here downloads or runs the daemon; ``scripts/fetch-tor-runtime.py`` stages the archive and
verifies it against this now-proven pin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PIN_FILE = REPO / "scripts" / "tor-runtime.json"
CHUNK = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 60.0
PASS, FAIL, BLOCKED = 0, 1, 3


def load_pin(path: Path = PIN_FILE) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("platforms"), dict):
        raise SystemExit(f"{path} is not a pinned runtime manifest")
    return data


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_checksums(text: str) -> dict[str, str]:
    """The ``sha256sum`` format: ``<hex>  <name>``. Comments and blanks are ignored."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest, name = parts[0], parts[-1].lstrip("*")
        if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest.lower()):
            found[name] = digest.lower()
    return found


def archive_name(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def compare_pins(manifest: dict, official: dict[str, str]) -> list[str]:
    """Every platform pin must appear, byte-identical, in the official signed checksums."""
    problems: list[str] = []
    for platform, entry in sorted(manifest["platforms"].items()):
        name = archive_name(str(entry.get("url") or ""))
        if not name:
            problems.append(f"{platform}: no archive url")
            continue
        published = official.get(name)
        if published is None:
            problems.append(f"{platform}: {name} is not in the official checksums")
            continue
        if published != str(entry.get("sha256") or "").lower():
            problems.append(f"{platform}: pin {entry.get('sha256')} != official {published} for {name}")
    if not manifest["platforms"]:
        problems.append("the pin describes no platform")
    return problems


# ------------------------------------------------------------------------------- network


def _fetch(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "canalla-llm-build"})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # noqa: S310
            with target.open("wb") as handle:
                shutil.copyfileobj(response, handle, CHUNK)
        return
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    curl = shutil.which("curl")
    if (
        curl
        and subprocess.run([curl, "-sS", "-L", "--max-time", "120", "-o", str(target), url]).returncode == 0
    ):
        return
    raise SystemExit(f"could not download {url}")


def find_gpg() -> str | None:
    found = shutil.which("gpg")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\Git\usr\bin\gpg.exe",
        r"C:\Program Files (x86)\GnuPG\bin\gpg.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def _msys_gpg(gpg: str) -> bool:
    """Is this the MSYS2/Git GnuPG, which wants POSIX paths, not ``C:\\`` ones?"""
    lowered = gpg.replace("\\", "/").lower()
    return "/usr/bin/" in lowered or "/usr/local/bin/" in lowered


def _gpg_path(path: Path, msys: bool) -> str:
    text = str(path)
    if not msys:
        return text
    text = text.replace("\\", "/")
    if len(text) >= 2 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def _gpg(gpg: str, home: Path, args: list[str]) -> subprocess.CompletedProcess:
    msys = _msys_gpg(gpg)
    home.mkdir(parents=True, exist_ok=True)
    converted = [_gpg_path(Path(arg), msys) if not arg.startswith("-") else arg for arg in args]
    return subprocess.run(
        [gpg, "--batch", "--no-tty", "--yes", *converted],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={
            "GNUPGHOME": _gpg_path(home, msys),
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        },
    )


def verify_signature(gpg: str, home: Path, checks: dict, sums: Path) -> list[str]:
    """Import the key by fingerprint and verify the detached signature over the checksums."""
    key = sums.parent / "tor-signing-key.asc"
    sig = sums.parent / "sha256sums-signed-build.txt.asc"
    _fetch(str(checks["key_url"]), key)
    _fetch(str(checks["signature_url"]), sig)

    imported = _gpg(gpg, home, ["--import", str(key)])
    if imported.returncode != 0:
        return [f"gpg could not import the signing key: {(imported.stderr or '').strip()}"]

    expected = str(checks["key_fingerprint"]).replace(" ", "").upper()
    listing = _gpg(gpg, home, ["--with-colons", "--fingerprint", expected])
    fingerprints = {
        line.split(":")[9].strip().upper()
        for line in (listing.stdout or "").splitlines()
        if line.startswith("fpr:") and len(line.split(":")) > 9
    }
    if expected not in fingerprints:
        return [f"the fetched key does not carry fingerprint {expected}"]

    verified = _gpg(gpg, home, ["--verify", str(sig), str(sums)])
    output = (verified.stdout or "") + (verified.stderr or "")
    if verified.returncode != 0 or "Good signature" not in output:
        return [f"the checksums signature did not verify: {output.strip()[-200:]}"]
    if expected not in output.replace(" ", "").upper():
        return [f"the good signature is not from {expected}"]
    return []


def run() -> int:
    manifest = load_pin()
    checks = manifest.get("checksums")
    if not isinstance(checks, dict) or not all(
        checks.get(key) for key in ("url", "sha256", "signature_url", "key_fingerprint", "key_url")
    ):
        print("TOR-PIN: FAIL — the manifest has no complete checksums provenance block")
        return FAIL

    with tempfile.TemporaryDirectory(prefix="tor-pin-") as directory:
        tmp = Path(directory)
        sums = tmp / "sha256sums-signed-build.txt"
        _fetch(str(checks["url"]), sums)

        actual = sha256_of(sums)
        if actual != str(checks["sha256"]).lower():
            print(f"TOR-PIN: FAIL — signed checksums file is {actual}, pin says {checks['sha256']}")
            return FAIL

        official = parse_checksums(sums.read_text(encoding="utf-8", errors="replace"))
        problems = compare_pins(manifest, official)

        gpg = find_gpg()
        signature = "not verified (no gpg)"
        if gpg:
            signature_problems = verify_signature(gpg, tmp / "gnupg", checks, sums)
            problems += signature_problems
            signature = "verified" if not signature_problems else "FAILED"
        else:
            problems.append("BLOCKED: gpg is required to verify the Tor signing key")

        if problems:
            for problem in problems:
                print(f"TOR-PIN: {problem}")
            blocked = all(p.startswith("BLOCKED") for p in problems)
            print("TOR-PIN: BLOCKED" if blocked else "TOR-PIN: FAIL")
            return BLOCKED if blocked else FAIL

    print(
        f"TOR-PIN: PASS — {manifest['version']} ({manifest['platforms']['windows-x86_64']['daemon_version']}); "
        f"{len(manifest['platforms'])} platform pins match the official signed checksums; signature {signature}"
    )
    return PASS


# ------------------------------------------------------------------------------- self-test


def self_test() -> int:
    sample = (
        "8e012ec6815d7899cb64011582e2dade88e74119c6661068a2a3252de0ccd7f2  tor-expert-bundle-linux-x86_64-15.0.24.tar.gz\n"
        "e9dc6ccc93cd6afa507193f4de284d6424233ff5102155cd2c94b259e8a22b65  tor-expert-bundle-windows-x86_64-15.0.24.tar.gz\n"
        "# a comment\n"
        "1111111111111111111111111111111111111111111111111111111111111111 *a-star-prefixed-name\n"
    )
    official = parse_checksums(sample)
    failures = 0

    def check(name: str, condition: bool):
        nonlocal failures
        if not condition:
            failures += 1
            print(f"  FAIL {name}")

    check("parses three entries", len(official) == 3)
    check("strips the * marker", "a-star-prefixed-name" in official)
    check(
        "reads the windows hash",
        official["tor-expert-bundle-windows-x86_64-15.0.24.tar.gz"]
        == "e9dc6ccc93cd6afa507193f4de284d6424233ff5102155cd2c94b259e8a22b65",
    )

    good = {
        "platforms": {
            "windows-x86_64": {
                "url": "https://x/tor-expert-bundle-windows-x86_64-15.0.24.tar.gz",
                "sha256": "e9dc6ccc93cd6afa507193f4de284d6424233ff5102155cd2c94b259e8a22b65",
            }
        }
    }
    check("a matching pin has no problems", compare_pins(good, official) == [])

    bad = json.loads(json.dumps(good))
    bad["platforms"]["windows-x86_64"]["sha256"] = "0" * 64
    check("a drifted pin is caught", len(compare_pins(bad, official)) == 1)

    missing = json.loads(json.dumps(good))
    missing["platforms"]["windows-x86_64"]["url"] = "https://x/absent-15.0.24.tar.gz"
    check("an absent archive is caught", len(compare_pins(missing, official)) == 1)

    if failures:
        print(f"TOR-PIN: self-test FAILED ({failures})")
        return FAIL
    print("TOR-PIN: self-test PASS")
    return PASS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true", help="offline checks of the logic")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
