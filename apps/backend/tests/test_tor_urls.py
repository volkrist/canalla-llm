"""A18 — the Tor URL guard: no special-use literal, in any spelling, reaches the route.

The guard never resolves a name (that is the proxy's job, over SOCKS5h). It does, however, parse
every literal address form a browser accepts and refuse the ones that point inside the machine or
at a cloud metadata service. Each case below is a URL the guard must classify; the IPs are the ones
an SSRF check is supposed to catch.
"""

import asyncio

import pytest

from app.tools.contracts import ToolError
from app.tools.tor.urls import ip_literal, is_onion, unsafe_ip_literal, unsafe_tor_url, validate_tor_url

ONION56 = "duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion"
ONION16 = "abcdefghijklmnop.onion"

UNSAFE_URLS = [
    # loopback in every spelling a browser accepts
    "http://127.0.0.1/",
    "http://127.0.0.2/",
    "http://127.1/",
    "http://127.0.0.1:80/",
    "http://127.0.0.1./",
    "http://2130706433/",  # decimal integer form of 127.0.0.1
    "http://0x7f000001/",  # hex form
    "http://0/",
    "http://localhost/",
    "http://localhost./",
    "http://LocalHost/",
    # private IPv4
    "http://10.0.0.1/",
    "http://10.255.255.254/",
    "http://172.16.5.4/",
    "http://192.168.1.1/",
    "http://192.168.0.1:443/",
    # link-local and the cloud metadata literal
    "http://169.254.169.254/latest/meta-data/",
    "http://169.254.0.1/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://instance-data/",
    # unspecified / broadcast / reserved
    "http://0.0.0.0/",
    "http://255.255.255.255/",
    "http://192.0.2.1/",  # TEST-NET-1
    "http://198.51.100.7/",  # TEST-NET-2
    # multicast
    "http://224.0.0.1/",
    "http://239.255.255.250/",
    # IPv6 and IPv4-mapped IPv6
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://[::ffff:7f00:1]/",
    "http://[::ffff:169.254.169.254]/",
    "http://[fe80::1]/",
    "http://[::]/",
    "http://[fc00::1]/",
    "http://[fd12:3456::1]/",
    "http://[2001:db8::1]/",  # documentation block
    # scheme, authority and port rules
    "ftp://example.com/",
    "file:///C:/Windows/win.ini",
    "javascript:alert(1)",
    "http://user:pass@example.com/",
    "http://example.com:8080/",
    "http://example.com:22/",
    "http://[::1",  # malformed IPv6 authority
    "http://example.com\\@127.0.0.1/",
    "http://exa mple.com/",  # control/space character
    "",
    "http://" + "a" * 2100 + ".com/",  # over the length ceiling
    "http://not-a-domain/",  # no dot, not an onion
]

SAFE_URLS = [
    "http://example.com/",
    "https://example.com/path?q=1#frag",
    "https://sub.domain.example.co.uk/a/b",
    "http://8.8.8.8/",  # a public literal is a legitimate clearnet destination
    "http://[2001:4860:4860::8888]/",
    f"http://{ONION56}/",
    f"https://{ONION56}/search/?q=tor",
    f"http://{ONION16}/",
    f"http://{ONION56}./",  # a trailing dot on an onion is still the same onion
]


@pytest.mark.parametrize("url", UNSAFE_URLS)
def test_special_use_literals_are_refused(url):
    assert unsafe_tor_url(url) is True
    with pytest.raises(ToolError, match="unsafe_url"):
        asyncio.run(validate_tor_url(url))


@pytest.mark.parametrize("url", SAFE_URLS)
def test_names_onions_and_public_literals_are_allowed(url):
    assert unsafe_tor_url(url) is False
    assert asyncio.run(validate_tor_url(url)) == url


def test_ip_literal_normalises_every_textual_form():
    assert str(ip_literal("127.0.0.1")) == "127.0.0.1"
    assert str(ip_literal("2130706433")) == "127.0.0.1"
    assert str(ip_literal("0x7f000001")) == "127.0.0.1"
    assert str(ip_literal("127.1")) == "127.0.0.1"
    mapped = ip_literal("[::ffff:127.0.0.1]")
    # `ipaddress` renders a v4-mapped v6 address as `::ffff:7f00:1` on some Python versions and as
    # `::ffff:127.0.0.1` on others. The address it denotes is the same; assert that, not the spelling.
    assert mapped.ipv4_mapped is not None
    assert str(mapped.ipv4_mapped) == "127.0.0.1"
    # A name is not an IP literal — and is never resolved here to find out.
    assert ip_literal("example.com") is None
    assert ip_literal("") is None


def test_mapped_and_native_ipv6_loopback_are_both_unsafe():
    assert unsafe_ip_literal("::ffff:127.0.0.1") is True
    assert unsafe_ip_literal("::ffff:7f00:1") is True
    assert unsafe_ip_literal("::1") is True
    assert unsafe_ip_literal("::ffff:8.8.8.8") is False  # mapped public address


def test_onion_detection_is_strict():
    assert is_onion(ONION56) is True
    assert is_onion(ONION16) is True
    assert is_onion("not-an-onion.onion") is False
    assert is_onion("short.onion") is False
