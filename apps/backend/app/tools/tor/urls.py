"""Tor URL rules: never resolve a name with the local DNS resolver, and never hand a special-use
address to the SOCKS route.

A hidden service or a clearnet name is sent to the proxy as a name (ATYP 3) and resolved there —
nothing here ever calls ``getaddrinfo``. But an address that is already an IP is a destination a
hostile page can aim anywhere, so every textual form a browser accepts is parsed and classified
before any byte reaches the route: loopback, private, link-local (the cloud metadata range),
multicast, reserved, unspecified, IPv4-mapped IPv6 and the metadata literals are all refused.

This is a *literal-address* guard. It cannot defeat a name that the remote resolver maps onto a
private address; that needs a server-side route policy, and without one the honest statement is
"no unsafe literal leaves this process", not "no request can ever reach a private host".
"""

import ipaddress
import re
import socket
from urllib.parse import urlsplit

from ..contracts import ToolError

# Names that are only ever a cloud metadata service: refused as a name, without resolving them. A
# metadata *literal* address is caught by the IP classifier below, which needs no lookup either.
METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata.goog", "metadata.google", "instance-data"})
# ``localhost`` is matched as a name; its address forms are covered by the IP classifier.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_onion(host: str) -> bool:
    value = (host or "").rstrip(".").lower()
    return bool(re.fullmatch(r"[a-z2-7]{56}\.onion|[a-z2-7]{16}\.onion", value))


def ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse ``host`` as an IP literal in every textual form a browser may accept, or ``None``.

    ``ipaddress`` covers the dotted quad and both IPv6 spellings — including the IPv4-mapped form
    ``::ffff:127.0.0.1``. Browsers also accept a bare integer (``2130706433``), hex (``0x7f000001``)
    and the shortened IPv4 forms (``127.1``); ``int(x, 0)`` and ``inet_aton`` cover those, so the same
    address cannot be smuggled past the guard by spelling it differently.
    """
    if not host:
        return None
    candidate = host.strip().strip("[]")
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        pass
    try:
        return ipaddress.ip_address(int(candidate, 0))
    except (ValueError, TypeError, OverflowError):
        pass
    try:
        packed = socket.inet_aton(candidate)
    except (OSError, ValueError):
        return None
    try:
        return ipaddress.ip_address(packed)
    except ValueError:
        return None


def unsafe_ip_literal(host: str) -> bool:
    """True when ``host`` is an IP literal that is not a globally routable address.

    ``is_global`` covers loopback, RFC 1918 private space, IPv6 unique-local, link-local (where
    ``169.254.169.254`` lives), the unspecified address and every reserved/documentation block —
    but it does *not* exclude multicast, so that is named explicitly here. A public literal of
    either family stays allowed: it is a legitimate clearnet destination, served over Tor.
    """
    address = ip_literal(host)
    if address is None:
        return False
    # A v4-mapped v6 address is the v4 address: judge the destination it actually reaches. Without
    # this, `::ffff:8.8.8.8` is "not global" on one Python version and "global" on another, because
    # the mapped IPv6 block is classified differently there - the safety answer must not depend on
    # the interpreter.
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    if not address.is_global:
        return True
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def unsafe_tor_url(value: str) -> bool:
    """The rule :func:`validate_tor_url` enforces, as a predicate pure helpers can call."""
    if not value or len(value) > 2048 or any(ord(c) < 33 for c in value) or "\\" in value:
        return True
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except ValueError:
        # A malformed authority — a broken IPv6 literal, a non-numeric port — is never a
        # destination; refuse it instead of letting a parse error pick a route.
        return True
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username
        or parsed.password
        or port not in {None, 80, 443}
    ):
        return True
    if host in LOOPBACK_HOSTS or host in METADATA_HOSTS:
        return True
    if unsafe_ip_literal(host):
        return True
    if is_onion(host):
        return False
    # A global IP literal — the unsafe ones were already refused — is a destination like any other;
    # everything else must look like a real domain name (it is the proxy, not this process, that
    # resolves it).
    if ip_literal(host) is not None:
        return False
    return "." not in host


async def validate_tor_url(value: str):
    if unsafe_tor_url(value):
        raise ToolError("unsafe_url")
    return value
