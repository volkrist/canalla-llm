"""Tor URL rules: never resolve .onion with the local DNS resolver."""

import re
from urllib.parse import urlsplit

from ..contracts import ToolError

ONION = re.compile(r"^[a-z2-7]{16}(\.onion)$|^[a-z2-7]{56}(\.onion)$")


def is_onion(host: str) -> bool:
    value = (host or "").rstrip(".").lower()
    return bool(re.fullmatch(r"[a-z2-7]{56}\.onion|[a-z2-7]{16}\.onion", value))


def unsafe_tor_url(value: str) -> bool:
    """The rule :func:`validate_tor_url` enforces, as a predicate pure helpers can call."""
    if not value or len(value) > 2048 or any(ord(c) < 33 for c in value) or "\\" in value:
        return True
    parsed = urlsplit(value)
    host = (parsed.hostname or "").rstrip(".").lower()
    try:
        port = parsed.port
    except ValueError:
        return True
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username
        or parsed.password
        or port not in {None, 80, 443}
        or host in {"localhost", "127.0.0.1", "::1"}
    ):
        return True
    return not (is_onion(host) or "." in host)


async def validate_tor_url(value: str):
    if unsafe_tor_url(value):
        raise ToolError("unsafe_url")
    return value
