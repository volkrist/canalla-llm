"""Update metadata for the Canalla LLM desktop client.

Read-only and cheap by construction: the manifest is a file (or an inline JSON string) that the
operator publishes, and this module only *serves* it. Nothing here starts compute, touches the
provider, or needs a RunPod key - an update check must work even when the AI does not.

Three rules matter more than the shape:

* nothing private is ever served. The file is public data, and a manifest that looks like it carries
  a secret is refused instead of being passed on;
* a client is never offered a version it already has, or an older one. Downgrades are rejected
  server-side, so a stale mirror cannot roll an installation back;
* what goes out must be what the *client* can parse. `tauri-plugin-updater` reads `pub_date` as an
  RFC 3339 timestamp, `url` as an absolute URL and `signature` as the base64 minisign signature, so
  a manifest that would fail in the client is refused here instead of being published broken.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("gateway.updates")

# The platforms a manifest may describe, named the way the Tauri updater names them.
SUPPORTED_PLATFORMS = ("windows-x86_64", "linux-x86_64")

VERSION = re.compile(r"^\d+\.\d+\.\d+\s*$")
# The shape the client parses with `OffsetDateTime::parse(..., Rfc3339)`.
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
SECRET_HINTS = ("secret", "api_key", "apikey", "token", "password", "private_key", "credential")


class UpdateManifestError(Exception):
    """The manifest exists but cannot be trusted: answer with an error, never with an update."""


def parse_version(value: str | None) -> tuple[int, int, int] | None:
    if not value or not VERSION.match(value.strip()):
        return None
    major, minor, patch = value.strip().split(".")
    return int(major), int(minor), int(patch)


def is_newer(candidate: str | None, current: str | None) -> bool:
    """A version without `current` is offered; an equal or older one never is."""
    offer = parse_version(candidate)
    if offer is None:
        return False
    running = parse_version(current)
    if running is None:
        return True
    return offer > running


def _load(path: str | None, inline: str | None) -> dict[str, Any] | None:
    if inline and inline.strip():
        try:
            data = json.loads(inline)
        except ValueError as error:
            raise UpdateManifestError("updates_manifest_invalid") from error
    elif path and Path(path).is_file():
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise UpdateManifestError("updates_manifest_unreadable") from error
    else:
        return None
    if not isinstance(data, dict):
        raise UpdateManifestError("updates_manifest_invalid")
    return data


def _assert_public(data: dict[str, Any]) -> None:
    """A manifest is published to unauthenticated clients: it may not carry private material."""

    def walk(node: Any, trail: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = str(key).lower()
                if any(hint in lowered for hint in SECRET_HINTS):
                    raise UpdateManifestError(f"updates_manifest_not_public:{trail}{key}")
                walk(value, f"{trail}{key}.")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{trail}{index}.")

    walk(data, "")


def _timestamp(value: Any) -> str | None:
    """The date the client can parse, or `None`: an empty one is left out, never sent as ""."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not TIMESTAMP.match(value.strip()):
        raise UpdateManifestError("updates_manifest_pub_date")
    return value.strip()


def _is_signature(value: Any) -> bool:
    """Mirror what the client does: base64, then a minisign signature file inside.

    Both minisign lines are required, and the trusted comment is matched from the start of its own
    line: the word `trusted comment:` also occurs *inside* the untrusted one.
    """
    if not isinstance(value, str) or not value:
        return False
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        return False
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return text.startswith("untrusted comment:") and "\ntrusted comment:" in text


def _entry(platform: str, entry: Any) -> dict[str, str]:
    """One platform's artifact, reduced to the fields the client reads and can trust."""
    if not isinstance(entry, dict):
        raise UpdateManifestError(f"updates_manifest_entry_invalid:{platform}")
    url = entry.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        # The client refuses anything that is not a secure protocol, so this is a broken manifest.
        raise UpdateManifestError(f"updates_manifest_entry_insecure:{platform}")
    if not _is_signature(entry.get("signature")):
        raise UpdateManifestError(f"updates_manifest_entry_incomplete:{platform}")
    described = {"url": url, "signature": entry["signature"]}
    digest = entry.get("sha256")
    if digest is not None:
        if not isinstance(digest, str) or not DIGEST.match(digest):
            raise UpdateManifestError(f"updates_manifest_entry_digest:{platform}")
        described["sha256"] = digest
    return described


def latest(
    *,
    path: str | None,
    inline: str | None,
    platform: str | None,
    current_version: str | None,
) -> tuple[int, dict[str, Any] | None]:
    """What to answer for one client: 204 when there is nothing to offer, 200 with the manifest."""
    manifest = _load(path, inline)
    if manifest is None:
        return 204, None
    _assert_public(manifest)

    if not platform or platform not in SUPPORTED_PLATFORMS:
        # A platform we do not publish for is "no update", never another platform's artifact.
        logger.info("updates_platform_unsupported platform=%s", platform)
        return 204, None

    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict) or platform not in platforms:
        return 204, None

    version = manifest.get("version")
    if not is_newer(version, current_version):
        logger.info("updates_no_newer_offer version=%s current=%s", version, current_version)
        return 204, None

    described = _entry(platform, platforms[platform])
    published = _timestamp(manifest.get("pub_date"))

    body: dict[str, Any] = {
        "version": version,
        "notes": manifest.get("notes") or "",
        "channel": manifest.get("channel") or "stable",
        "platforms": {platform: described},
    }
    if published is not None:
        body["pub_date"] = published
    for extra in ("minimum_supported_version", "mandatory"):
        if extra in manifest:
            body[extra] = manifest[extra]
    return 200, body
