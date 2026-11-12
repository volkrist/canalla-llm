"""A small, honest web client for research: bounded, robots-aware, SSRF-safe.

Only `http`/`https`, only public addresses (loopback is opt-in for tests), a byte cap, a timeout,
and `robots.txt` is honoured per host and cached for the process. Nothing here executes page
content; the caller turns bytes into text.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

USER_AGENT = "CanallaResearch/1.2.7 (+https://github.com/volkrist/canalla-llm)"
MAX_BYTES = 2_000_000
TIMEOUT = 20.0


class FetchError(Exception):
    pass


@dataclass
class _Robots:
    disallow: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)

    def permitted(self, path: str) -> bool:
        longest_allow = max((len(p) for p in self.allow if path.startswith(p)), default=-1)
        longest_disallow = max((len(p) for p in self.disallow if path.startswith(p)), default=-1)
        return longest_allow >= longest_disallow


_ROBOTS_CACHE: dict[str, _Robots] = {}


def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as error:
        raise FetchError("dns_failed") from error
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            return False
    return True


def _robots_for(origin: str, timeout: float) -> _Robots:
    if origin in _ROBOTS_CACHE:
        return _ROBOTS_CACHE[origin]
    robots = _Robots()
    try:
        request = urllib.request.Request(f"{origin}/robots.txt", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status == 200:
                current = None
                for raw in response.read(200_000).decode("utf-8", "replace").splitlines():
                    line = raw.split("#", 1)[0].strip()
                    if not line or ":" not in line:
                        continue
                    key, value = line.split(":", 1)
                    key, value = key.strip().lower(), value.strip()
                    if key == "user-agent":
                        current = value
                    elif current in {"*", "canallaresearch", "canalla"} and key == "disallow" and value:
                        robots.disallow.append(value)
                    elif current in {"*", "canallaresearch", "canalla"} and key == "allow" and value:
                        robots.allow.append(value)
    except (urllib.error.URLError, OSError):
        pass
    _ROBOTS_CACHE[origin] = robots
    return robots


@dataclass
class Page:
    url: str
    status: int
    content_type: str
    text: str
    bytes: int


def fetch_page(
    url: str,
    *,
    timeout: float = TIMEOUT,
    max_bytes: int = MAX_BYTES,
    allow_local: bool = False,
) -> Page:
    parts = urlsplit(url or "")
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise FetchError("unsupported_scheme")
    if not allow_local and not _public_host(parts.hostname):
        raise FetchError("private_address")
    origin = f"{parts.scheme}://{parts.netloc}"
    if not allow_local and not _robots_for(origin, timeout).permitted(parts.path or "/"):
        raise FetchError("robots_disallow")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text" not in content_type and "html" not in content_type and "json" not in content_type:
                raise FetchError("unsupported_content")
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                payload = payload[:max_bytes]
            charset = response.headers.get_content_charset() or "utf-8"
            return Page(
                url=response.geturl(),
                status=response.status,
                content_type=content_type,
                text=payload.decode(charset, "replace"),
                bytes=len(payload),
            )
    except urllib.error.HTTPError as error:
        raise FetchError(f"http_{error.code}") from error
    except (urllib.error.URLError, OSError) as error:
        raise FetchError(type(error).__name__) from error


def resolve(base: str, link: str) -> str | None:
    if not link:
        return None
    if link.startswith(("http://", "https://")):
        return link
    if link.startswith(("#", "mailto:", "javascript:")):
        return None
    joined = urljoin(base, link)
    return joined if joined.startswith(("http://", "https://")) else None
