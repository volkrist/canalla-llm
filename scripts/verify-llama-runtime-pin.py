#!/usr/bin/env python3
"""Verify the llama.cpp runtime pin against the official GitHub release it came from.

The pin in ``scripts/llama-runtime.json`` is only as trustworthy as what it was taken from. This
script proves that chain without downloading the ~765 MB of archives:

1. the release the pin names is read from the repository's own Releases API
   (``ggml-org/llama.cpp``), so the *official publisher* answers, not a mirror;
2. the release's tag and the commit it was built from must equal the pin's;
3. every pinned asset must exist in that release with the same byte count and the same SHA256
   digest the release itself publishes for it.

A mismatch is a hard failure. If the API cannot be reached the check is BLOCKED (exit 3), never a
silent pass: an unverifiable pin is not a verified one. ``--self-test`` proves the comparison logic
offline, with no network.

Nothing here downloads an archive or runs the binary; ``scripts/provision-llama-runtime.py`` stages
the archives and verifies them against this now-proven pin.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PIN_FILE = REPO / "scripts" / "llama-runtime.json"
USER_AGENT = "canalla-llm-build/1.2.1"
API_TIMEOUT_SECONDS = 60.0
PASS, FAIL, BLOCKED = 0, 1, 3


def load_pin(path: Path = PIN_FILE) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("assets"), dict):
        raise SystemExit(f"{path} is not a pinned runtime manifest")
    for field in ("tag", "commit", "repository"):
        if not data.get(field):
            raise SystemExit(f"{path} has no {field!r}")
    return data


def release_url(pin: dict) -> str:
    return f"https://api.github.com/repos/{pin['repository']}/releases/tags/{pin['tag']}"


def fetch_release(url: str, *, attempts: int = 3) -> dict:
    """The release document, or a hard failure. The pin is never trusted from the network alone."""
    last = "unknown"
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:  # noqa: S310
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            last = f"HTTP {error.code}"
            if error.code == 404:
                raise SystemExit(f"{url} → {last}: the pin names a release that does not exist")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            last = type(error).__name__
        if attempt < attempts:
            time.sleep(2 * attempt)
    raise SystemExit(f"could not read {url} ({last})")


def compare(pin: dict, release: dict) -> list[str]:
    """Every pinned claim must be repeated, byte-identical, by the official release."""
    problems: list[str] = []
    if str(release.get("tag_name") or "") != str(pin["tag"]):
        problems.append(f"release tag {release.get('tag_name')!r} != pin {pin['tag']!r}")
    commit = str(release.get("target_commitish") or "")
    if commit != str(pin["commit"]):
        problems.append(f"release commit {commit!r} != pin {pin['commit']!r}")

    by_name = {
        str(asset.get("name") or ""): asset for asset in (release.get("assets") or [])
    }
    for key, entry in sorted(pin["assets"].items()):
        name = str(entry.get("name") or "")
        if not name:
            problems.append(f"{key}: the pin names no asset")
            continue
        asset = by_name.get(name)
        if asset is None:
            problems.append(f"{key}: {name} is not among the release's assets")
            continue
        size = asset.get("size")
        if size != entry.get("bytes"):
            problems.append(f"{key}: {name} is {size} bytes officially, pin says {entry.get('bytes')}")
        digest = str(asset.get("digest") or "")
        expected = "sha256:" + str(entry.get("sha256") or "")
        if digest != expected:
            problems.append(f"{key}: {name} digest {digest!r} != pin {expected!r}")
    return problems


def run() -> int:
    pin = load_pin()
    try:
        release = fetch_release(release_url(pin))
    except SystemExit as error:
        print(f"LLAMA-PIN: BLOCKED — {error}")
        return BLOCKED

    problems = compare(pin, release)
    if problems:
        for problem in problems:
            print(f"LLAMA-PIN: {problem}")
        print("LLAMA-PIN: FAIL")
        return FAIL

    names = ", ".join(sorted(pin["assets"]))
    print(
        f"LLAMA-PIN: PASS — {pin['product']} {pin['tag']} ({pin['commit'][:12]}, cuda {pin['cuda']}, "
        f"{pin['platform']}); {len(pin['assets'])} assets ({names}) match the official release digests"
    )
    return PASS


# ------------------------------------------------------------------------------- self-test


def self_test() -> int:
    good_release = {
        "tag_name": "b11565",
        "target_commitish": "f" * 40,
        "assets": [
            {"name": "bin.tar.gz", "size": 5, "digest": "sha256:" + "a" * 64},
            {"name": "cudart.tar.gz", "size": 7, "digest": "sha256:" + "b" * 64},
        ],
    }
    good_pin = {
        "tag": "b11565",
        "commit": "f" * 40,
        "assets": {
            "bin": {"name": "bin.tar.gz", "bytes": 5, "sha256": "a" * 64},
            "cudart": {"name": "cudart.tar.gz", "bytes": 7, "sha256": "b" * 64},
        },
    }
    failures = 0

    def check(name: str, condition: bool):
        nonlocal failures
        if not condition:
            failures += 1
            print(f"  FAIL {name}")

    check("matching pin has no problems", compare(good_pin, good_release) == [])

    drifted_hash = json.loads(json.dumps(good_pin))
    drifted_hash["assets"]["bin"]["sha256"] = "0" * 64
    check("a drifted hash is caught", len(compare(drifted_hash, good_release)) == 1)

    drifted_size = json.loads(json.dumps(good_pin))
    drifted_size["assets"]["cudart"]["bytes"] = 6
    check("a drifted size is caught", len(compare(drifted_size, good_release)) == 1)

    missing_asset = json.loads(json.dumps(good_pin))
    missing_asset["assets"]["bin"]["name"] = "absent.tar.gz"
    check("an absent asset is caught", len(compare(missing_asset, good_release)) == 1)

    wrong_tag = json.loads(json.dumps(good_release))
    wrong_tag["tag_name"] = "b11564"
    check("a wrong tag is caught", len(compare(good_pin, wrong_tag)) == 1)

    wrong_commit = json.loads(json.dumps(good_release))
    wrong_commit["target_commitish"] = "e" * 40
    check("a wrong commit is caught", len(compare(good_pin, wrong_commit)) == 1)

    if failures:
        print(f"LLAMA-PIN: self-test FAILED ({failures})")
        return FAIL
    print("LLAMA-PIN: self-test PASS")
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
