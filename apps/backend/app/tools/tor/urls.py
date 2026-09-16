"""Tor URL rules: never resolve .onion with the local DNS resolver."""

import re

from ..contracts import ToolError

ONION = re.compile(r"^[a-z2-7]{16}(\.onion)$|^[a-z2-7]{56}(\.onion)$")


def is_onion(host: str) -> bool:
    value = (host or "").rstrip(".").lower()
    return bool(re.fullmatch(r"[a-z2-7]{56}\.onion|[a-z2-7]{16}\.onion", value))


async def validate_tor_url(value: str):
    from urllib.parse import urlsplit

    if not value or len(value) > 2048 or any(ord(c) < 33 for c in value) or "\\" in value:
        raise ToolError("unsafe_url")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 80, 443}
        or host in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ToolError("unsafe_url")
    if is_onion(host) or "." in host:
        return value
    raise ToolError("unsafe_url")
