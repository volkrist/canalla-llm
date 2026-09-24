"""Update metadata for the Canalla LLM desktop client.

Read-only and cheap by construction: the manifest is a file (or an inline JSON string) that the
operator publishes, and this module only *serves* it. Nothing here starts compute, touches the
provider, or needs a RunPod key - an update check must work even when the AI does not.

Four rules matter more than the shape:

* nothing private is ever served. The file is public data, and a manifest that looks like it carries
  a secret is refused instead of being passed on;
* a client is never offered a version it already has, or an older one. Downgrades are rejected
  server-side, so a stale mirror cannot roll an installation back;
* an entry is never served unless its artifact is *bound* to the version it advertises: the name
  inside the signature's trusted comment has to be the file the `url` serves, and that name has to
  carry the manifest version. Minisign covers the trusted comment with the signature's global
  signature field, so this is the one authenticated name a release has - without the binding, a
  genuine signature for an old artifact could be republished under a newer version;
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
from urllib.parse import unquote, urlsplit

logger = logging.getLogger("gateway.updates")

# The platforms a manifest may describe, named the way the Tauri updater names them.
SUPPORTED_PLATFORMS = ("windows-x86_64", "linux-x86_64")

VERSION = re.compile(r"^\d+\.\d+\.\d+\s*$")
# The shape the client parses with `OffsetDateTime::parse(..., Rfc3339)`.
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
# A version-shaped token inside an artifact name, e.g. `Canalla LLM_1.2.0_x64-setup.exe`. The
# optional suffix belongs to the token, so a pre-release name yields `1.2.0-beta.1` and can never be
# mistaken for the `1.2.0` release it precedes.
VERSION_IN_NAME = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]*)?")
SECRET_HINTS = ("secret", "api_key", "apikey", "token", "password", "private_key", "credential")
TRUSTED_COMMENT = "trusted comment:"
SIGNED_NAME_FIELD = "file:"


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


def _minisign_text(value: Any) -> str | None:
    """Base64 -> the minisign signature *file* inside, or `None` if this is not one.

    Both minisign lines are required, and the trusted comment is matched from the start of its own
    line: the word `trusted comment:` also occurs *inside* the untrusted one. The decoded text is
    returned because the binding check has to read what the signature was made for.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        return None
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text.startswith("untrusted comment:") and f"\n{TRUSTED_COMMENT}" in text:
        return text
    return None


def _signed_name(text: str) -> str | None:
    """The `file:` field of the trusted comment: the artifact this signature was made for.

    Fields are tab separated (`timestamp:...\tfile:...`) and the name itself may contain spaces, so
    the split is on tabs only.
    """
    for line in text.splitlines():
        if not line.startswith(TRUSTED_COMMENT):
            continue
        for field in line[len(TRUSTED_COMMENT) :].split("\t"):
            field = field.strip()
            if field.startswith(SIGNED_NAME_FIELD):
                name = field[len(SIGNED_NAME_FIELD) :].strip()
                if name:
                    return name
    return None


def _served_name(url: str) -> str:
    """The file the `url` serves: the percent-decoded basename of its path."""
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def _assert_version_bound(platform: str, url: str, text: str, version: Any) -> None:
    """The signed name must be the file the url serves, and it must carry this version.

    Minisign covers its trusted comment with the signature's global signature field, so the `file:`
    name is authenticated whenever the signature verifies. The release keeps its version in the
    artifact's file name, so requiring the two to agree is what makes "version 1.2.0" mean "these
    bytes": a validly signed older artifact cannot be republished as a newer release.
    """
    name = _signed_name(text)
    served = _served_name(url)
    bound = parse_version(version) if isinstance(version, str) else None
    named = [parse_version(token) for token in VERSION_IN_NAME.findall(name)] if name else []
    if name is None or name != served or not named or any(token != bound for token in named):
        logger.warning(
            "updates_entry_unbound platform=%s signed_name=%s served_name=%s version=%s",
            platform,
            name,
            served,
            version,
        )
        raise UpdateManifestError(f"updates_manifest_entry_unbound:{platform}")


def _entry(platform: str, entry: Any, version: Any) -> dict[str, str]:
    """One platform's artifact, reduced to the fields the client reads and can trust."""
    if not isinstance(entry, dict):
        raise UpdateManifestError(f"updates_manifest_entry_invalid:{platform}")
    url = entry.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        # The client refuses anything that is not a secure protocol, so this is a broken manifest.
        raise UpdateManifestError(f"updates_manifest_entry_insecure:{platform}")
    text = _minisign_text(entry.get("signature"))
    if text is None:
        raise UpdateManifestError(f"updates_manifest_entry_incomplete:{platform}")
    _assert_version_bound(platform, url, text, version)
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

    described = _entry(platform, platforms[platform], version)
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
