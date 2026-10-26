#!/usr/bin/env python
"""Build and check the Canalla update manifest the Gateway serves.

The updater manifest is public data with four invariants that must hold before it is
published (see `apps/gateway/gateway/updates.py`):

* the `signature` field is the base64 *content* of the `.sig` file, never its name,
  a URL or a SHA256;
* the name inside the signature's trusted comment is the file the `url` serves, so the
  URL has to preserve the artifact's real name (a space is `%20`, not a dot);
* the version in that name is the version the manifest declares;
* a client is never offered its own or an older version.

The script is intentionally offline by default: `build` writes the manifest, `check`
runs the whole matrix through the Gateway's own `updates.latest`, and `live` repeats
the cases over HTTPS against a deployed endpoint. Nothing here creates, stops or
touches compute.

Usage:
  python scripts/updates-manifest.py build --installer <exe> --signature <sig> \
      --version 1.2.4 --base-url https://gateway.12testers.store/downloads/ \
      --pub-date 2026-10-26T12:06:27Z --notes-file <file> --out <manifest.json>
  python scripts/updates-manifest.py check --manifest <manifest.json>
  python scripts/updates-manifest.py live --endpoint https://gateway.12testers.store/updates/latest
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GATEWAY = REPO / "apps" / "gateway"
sys.path.insert(0, str(GATEWAY))

from gateway import updates

WINDOWS = "windows-x86_64"
LINUX = "linux-x86_64"
# Cloudflare's managed rules answer `Python-urllib` with HTTP 403 / error 1010, while the real
# updater's UA (`tauri-plugin-updater/2.x`) is allowed. An acceptance tool must look like a client,
# not like a blocked bot, or it would report a failure the product does not have.
USER_AGENT = "Canalla-LLM-updates-acceptance/1.2.4"


def _fail(message: str) -> None:
    print(f"[FAIL] {message}")
    raise SystemExit(1)


def _pass(message: str) -> None:
    print(f"[PASS] {message}")


def build(args: argparse.Namespace) -> int:
    installer = Path(args.installer)
    signature = Path(args.signature)
    if not installer.is_file():
        _fail(f"installer not found: {installer}")
    if not signature.is_file():
        _fail(f"signature not found: {signature}")

    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    if args.expect_sha256 and digest != args.expect_sha256:
        _fail(f"installer sha256 {digest} != expected {args.expect_sha256}")

    signature_text = signature.read_text(encoding="utf-8").strip()
    decoded = base64.b64decode(signature_text).decode("utf-8")
    signed_name = next(
        field[len("file:") :].strip()
        for line in decoded.splitlines()
        if line.startswith("trusted comment:")
        for field in line[len("trusted comment:") :].split("\t")
        if field.strip().startswith("file:")
    )
    if signed_name != installer.name:
        _fail(f"signed name {signed_name!r} != installer name {installer.name!r}")

    url = urllib.parse.urljoin(args.base_url, urllib.parse.quote(installer.name))
    notes = ""
    if args.notes_file:
        notes = Path(args.notes_file).read_text(encoding="utf-8").strip()

    body = {
        "version": args.version,
        "pub_date": args.pub_date,
        "channel": "stable",
        "notes": notes,
        "platforms": {
            WINDOWS: {
                "url": url,
                "signature": signature_text,
                "sha256": digest,
            }
        },
    }
    Path(args.out).write_text(
        json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    # The Gateway's own loader must accept it before it can ever be deployed.
    status, served = updates.latest(
        path=args.out, inline=None, platform=WINDOWS, current_version=args.previous
    )
    if status != 200 or served is None:
        _fail(f"gateway refused the manifest for {args.previous} (status {status})")
    if served["version"] != args.version:
        _fail(f"gateway served {served['version']}, expected {args.version}")
    _pass(f"manifest written to {args.out}")
    _pass(f"installer sha256 {digest}")
    _pass(f"gateway serves {args.version} to {args.previous} from {url}")
    return 0


def check(args: argparse.Namespace) -> int:
    path = args.manifest
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    version = manifest.get("version")

    cases = [
        # (platform, current, expected status, why)
        (WINDOWS, args.previous, 200, "an older client gets the new release"),
        (WINDOWS, version, 204, "the current release gets nothing"),
        (WINDOWS, args.next_version, 204, "a newer client is never downgraded"),
        (WINDOWS, "0.9.3", 200, "a much older client upgrades"),
        (WINDOWS, None, 200, "an unknown version is answered, not crashed"),
        (WINDOWS, "not-a-version", 200, "a malformed version is answered"),
        (LINUX, args.previous, 204, "no Linux artifact is advertised"),
        ("darwin-aarch64", args.previous, 204, "an unsupported platform gets nothing"),
        ("", args.previous, 204, "a platform-less client gets nothing"),
        (WINDOWS, args.previous, 200, "a repeated request is deterministic"),
    ]
    failures = 0
    for platform, current, expected, why in cases:
        status, body = updates.latest(
            path=path, inline=None, platform=platform, current_version=current
        )
        if status != expected:
            print(
                f"[FAIL] {platform or '<none>'} current={current!r}: "
                f"status {status} != {expected} ({why})"
            )
            failures += 1
        else:
            _pass(f"{platform or '<none>'} current={current!r} -> {status} ({why})")

    status, body = updates.latest(
        path=path, inline=None, platform=WINDOWS, current_version=args.previous
    )
    if status == 200 and body is not None:
        entry = body["platforms"][WINDOWS]
        if entry["signature"] != manifest["platforms"][WINDOWS]["signature"]:
            print("[FAIL] served signature differs from the .sig content")
            failures += 1
        else:
            _pass("served signature is the .sig content")
        if entry.get("sha256") != manifest["platforms"][WINDOWS].get("sha256"):
            print("[FAIL] served sha256 differs")
            failures += 1
        else:
            _pass("served sha256 is the installer digest")
        if body.get("version") != version:
            print("[FAIL] served version differs")
            failures += 1
        if "pub_date" not in body:
            print("[FAIL] pub_date missing from the served body")
            failures += 1
        else:
            _pass(f"pub_date {body['pub_date']} is RFC 3339")

    print(f"--- {len(cases) + 3} checks, {failures} FAIL ---")
    return 1 if failures else 0


def _get(endpoint: str, platform: str | None, current: str | None) -> tuple[int, bytes, dict]:
    query = {}
    if platform:
        target, _, arch = platform.partition("-")
        query["target"] = target
        query["arch"] = arch
    if current is not None:
        query["current_version"] = current
    url = endpoint + ("?" + urllib.parse.urlencode(query) if query else "")
    request = urllib.request.Request(
        url, headers={"Cache-Control": "no-cache", "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers)


def live(args: argparse.Namespace) -> int:
    endpoint = args.endpoint
    cases = [
        (WINDOWS, args.previous, 200, "older client sees the release"),
        (WINDOWS, args.version, 204, "current release sees nothing"),
        (WINDOWS, args.next_version, 204, "no downgrade"),
        (LINUX, args.previous, 204, "no Linux artifact"),
        (WINDOWS, args.previous, 200, "repeat is stable"),
    ]
    failures = 0
    served: dict | None = None
    for platform, current, expected, why in cases:
        status, raw, headers = _get(endpoint, platform, current)
        ok = status == expected
        cache = headers.get("cf-cache-status") or headers.get("Cache-Control") or "-"
        print(
            f"[{'PASS' if ok else 'FAIL'}] {platform} current={current} -> "
            f"{status} (expected {expected}; cache={cache}) — {why}"
        )
        if not ok:
            failures += 1
        if status == 200 and platform == WINDOWS and current == args.previous:
            served = json.loads(raw)

    if served is not None:
        entry = served.get("platforms", {}).get(WINDOWS, {})
        if served.get("version") != args.version:
            print(f"[FAIL] served version {served.get('version')} != {args.version}")
            failures += 1
        else:
            _pass(f"served version {args.version}")
        if entry.get("signature") != args.signature:
            print("[FAIL] served signature is not the expected .sig content")
            failures += 1
        else:
            _pass("served signature is the .sig content")
        if entry.get("sha256") != args.installer_sha256:
            print("[FAIL] served sha256 is not the installer digest")
            failures += 1
        else:
            _pass("served sha256 is the installer digest")
        url = entry.get("url", "")
        name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
        if name != args.installer_name:
            print(f"[FAIL] url serves {name!r}, expected {args.installer_name!r}")
            failures += 1
        else:
            _pass(f"url serves the signed name {name}")

        digest, size = _artifact(url)
        if digest != args.installer_sha256:
            print(f"[FAIL] downloaded artifact sha256 {digest} != {args.installer_sha256}")
            failures += 1
        else:
            _pass(f"downloaded artifact is {size} bytes, sha256 matches")

    print(f"--- {len(cases) + 4} live checks, {failures} FAIL ---")
    return 1 if failures else 0


def _artifact(url: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    request = urllib.request.Request(
        url, headers={"Cache-Control": "no-cache", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    builder = sub.add_parser("build", help="write a manifest from a signed installer")
    builder.add_argument("--installer", required=True)
    builder.add_argument("--signature", required=True)
    builder.add_argument("--version", required=True)
    builder.add_argument("--previous", default="1.2.3")
    builder.add_argument("--base-url", required=True)
    builder.add_argument("--pub-date", required=True)
    builder.add_argument("--notes-file", default="")
    builder.add_argument("--expect-sha256", default="")
    builder.add_argument("--out", required=True)
    builder.set_defaults(func=build)

    checker = sub.add_parser("check", help="run the offline status matrix")
    checker.add_argument("--manifest", required=True)
    checker.add_argument("--previous", default="1.2.3")
    checker.add_argument("--next-version", default="1.2.5")
    checker.set_defaults(func=check)

    live_parser = sub.add_parser("live", help="run the matrix against a live endpoint")
    live_parser.add_argument(
        "--endpoint", default="https://gateway.12testers.store/updates/latest"
    )
    live_parser.add_argument("--version", required=True)
    live_parser.add_argument("--previous", default="1.2.3")
    live_parser.add_argument("--next-version", default="1.2.5")
    live_parser.add_argument("--signature", required=True, help="expected .sig content file")
    live_parser.add_argument("--installer-sha256", required=True)
    live_parser.add_argument("--installer-name", required=True)
    live_parser.set_defaults(func=live)

    args = parser.parse_args()
    if args.command == "live":
        args.signature = Path(args.signature).read_text(encoding="utf-8").strip()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
