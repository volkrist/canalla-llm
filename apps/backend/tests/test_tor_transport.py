import asyncio
import json
import socket

import pytest

from app.tools.contracts import ToolError
from app.tools.tor.classify import OFFICIAL_AND_REACHABLE, REACHABLE_UNVERIFIED, classify_authority
from app.tools.tor.provider import TorSearchArgs, TorSearchProvider, extract_search_hits
from app.tools.tor.socks import Socks5hConnector, TorTransport
from app.tools.tor.urls import validate_tor_url

ONION = "duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion"
OTHER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion"


class Settings:
    tor_official_mapping = [
        {
            "name": "Example Official",
            "clearnet": "https://official.example",
            "onion": f"http://{ONION}",
        }
    ]


async def _handle(reader, writer, received):
    await reader.readexactly(3)
    writer.write(b"\x05\x00")
    await writer.drain()
    header = await reader.readexactly(4)
    assert header[0] == 5 and header[3] == 3  # ATYP domain — remote DNS
    length = (await reader.readexactly(1))[0]
    name = await reader.readexactly(length)
    port = int.from_bytes(await reader.readexactly(2), "big")
    received.append((name.decode(), port, header[3]))
    writer.write(b"\x05\x00\x00\x01" + b"\x00\x00\x00\x00" + b"\x00\x00")
    await writer.drain()
    rest = await reader.read(4096)
    if rest.startswith(b"GET "):
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\n\r\nhello onion")
        await writer.drain()
    writer.close()


def test_onion_never_uses_local_dns(monkeypatch):
    onion_lookups = []
    real = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        if ".onion" in str(host).lower():
            onion_lookups.append(host)
            raise AssertionError("local DNS must not resolve .onion")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)

    async def run():
        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo

        async def loop_guarded(host, *args, **kwargs):
            if ".onion" in str(host).lower():
                onion_lookups.append(host)
                raise AssertionError("local DNS must not resolve .onion")
            return await original(host, *args, **kwargs)

        loop.getaddrinfo = loop_guarded
        received = []
        server = await asyncio.start_server(lambda r, w: _handle(r, w, received), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            page = await TorTransport(host="127.0.0.1", port=port).fetch(f"http://{ONION}/")
        assert page["text"] == "hello onion"
        assert received == [(ONION, 80, 3)]
        assert page["socks"]["atyp"] == 3
        assert page["socks"]["local_dns"] is False
        assert page["socks"]["dest_host"] == ONION
        with pytest.raises(ToolError, match="tor_not_configured"):
            await Socks5hConnector("127.0.0.1", 1).open(ONION, 80)

    asyncio.run(run())
    assert onion_lookups == []


def test_validate_onion_and_authority():
    onion = f"http://{ONION}/"
    assert asyncio.run(validate_tor_url(onion))
    assert classify_authority(onion, True, Settings()) == OFFICIAL_AND_REACHABLE
    assert classify_authority(f"http://{OTHER}", True, Settings()) == REACHABLE_UNVERIFIED


def test_tor_search_requires_configured_provider(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "tor_search_providers", [])
    with pytest.raises(ToolError, match="tor_search_not_configured"):
        asyncio.run(TorSearchProvider().execute(TorSearchArgs(query="test"), None))


def test_tor_provider_json_allows_query_placeholder():
    from app.config import Settings

    raw = '[{"name":"ahmia","url_template":"http://example.onion/search/?q={query}"}]'
    parsed = Settings.parse_json_object_list(raw)
    assert parsed[0]["url_template"].endswith("{query}")
    alt = Settings.parse_json_object_list(
        '[{"name":"ahmia","url_template":"http://example.onion/search/?q=__QUERY__"}]'
    )
    assert alt[0]["url_template"].endswith("__QUERY__")


def test_tor_providers_file_loads_when_env_list_empty(tmp_path, monkeypatch):
    from app.config import Settings, get_settings

    path = tmp_path / "providers.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "ahmia",
                    "url_template": "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/?q=__QUERY__",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("TOR_SEARCH_PROVIDERS", raising=False)
    monkeypatch.setenv("TOR_SEARCH_PROVIDERS_FILE", str(path))
    get_settings.cache_clear()
    settings = Settings()
    assert settings.tor_search_providers[0]["name"] == "ahmia"
    get_settings.cache_clear()


class _FakeTorTransport:
    def __init__(self):
        self.urls = []

    async def fetch(self, url, timeout=45):
        self.urls.append(url)
        return {
            "status": 200,
            "text": (
                '<a href="http://2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion/">'
                "Tor Project</a>"
            ),
            "socks": {
                "atyp": 3,
                "local_dns": False,
                "dest_host": "juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion",
                "proxy_host": "127.0.0.1",
                "proxy_port": 9050,
            },
        }


def test_tor_search_configured_template_uses_transport(monkeypatch):
    from app.config import get_settings

    transport = _FakeTorTransport()
    monkeypatch.setattr(
        get_settings(),
        "tor_search_providers",
        [
            {
                "name": "ahmia",
                "url_template": "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/?q=__QUERY__",
            }
        ],
    )
    result = asyncio.run(
        TorSearchProvider(transport=transport).execute(TorSearchArgs(query="Tor Project"), None)
    )
    assert transport.urls and "Tor%20Project" in transport.urls[0]
    assert "__QUERY__" not in transport.urls[0]
    assert "{query}" not in transport.urls[0]
    assert result.sources
    assert result.metadata["transport"] == "tor-socks5h"
    assert result.metadata["socks"]["atyp"] == 3
    assert result.metadata["socks"]["local_dns"] is False


def test_search_hits_follow_relative_redirects_and_onions():
    html = (
        '<a href="https://ahmia.fi/about/">About</a>'
        '<a href="/search/redirect?search_term=Tor+Project&amp;redirect_url=http://'
        '2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion/">Tor Project</a>'
        "<p>also juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion</p>"
    )
    hits = extract_search_hits("http://example.search.onion/search/?q=tor", html)
    assert hits[0][0].startswith("http://2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion")
    assert any("juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion" in url for url, _ in hits)
    assert all(".onion" in url for url, _ in hits)


def test_search_hits_skip_provider_host_nav_links():
    ahmia = "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion"
    html = (
        f'<a href="{ahmia}/">Home</a>'
        * 8
        + '<a href="/search/redirect?search_term=x&amp;redirect_url=http://'
        '2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion/">Tor Project</a>'
        + "<cite>duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion</cite>"
    )
    hits = extract_search_hits(ahmia + "/search/?q=x", html)
    assert hits[0][0].startswith("http://2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion")
    assert not any("juhanurmihxl" in url for url, _ in hits)
    assert any("duckduckgogg" in url for url, _ in hits)


def test_hidden_search_form_fields_are_appended():
    from app.tools.tor.provider import apply_search_form_fields, extract_hidden_fields

    html = (
        '<form action="/search/" method="get">'
        '<input id="id_q" type="search" name="q">'
        '<input type="hidden" name="abc123" value="tok789">'
        "</form>"
    )
    assert extract_hidden_fields(html) == {"abc123": "tok789"}
    url = apply_search_form_fields(
        "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/?q=Tor",
        html,
    )
    assert "q=Tor" in url
    assert "abc123=tok789" in url


class _TokenTransport:
    def __init__(self):
        self.urls = []

    async def fetch(self, url, timeout=45):
        self.urls.append(url)
        if "/search/" not in url:
            return {
                "status": 200,
                "text": '<input type="hidden" name="tokfld" value="live-token">',
                "socks": {
                    "atyp": 3,
                    "local_dns": False,
                    "dest_host": "juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion",
                    "proxy_host": "127.0.0.1",
                    "proxy_port": 9050,
                },
            }
        assert "tokfld=live-token" in url
        return {
            "status": 200,
            "url": url,
            "text": (
                '<a href="http://2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion/">'
                "Tor Project</a>"
            ),
            "socks": {
                "atyp": 3,
                "local_dns": False,
                "dest_host": "juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion",
                "proxy_host": "127.0.0.1",
                "proxy_port": 9050,
            },
        }


def test_tor_search_uses_public_form_token(monkeypatch):
    from app.config import get_settings

    transport = _TokenTransport()
    monkeypatch.setattr(
        get_settings(),
        "tor_search_providers",
        [
            {
                "name": "ahmia",
                "url_template": "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/?q=__QUERY__",
                "form_url": "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/",
            }
        ],
    )
    result = asyncio.run(
        TorSearchProvider(transport=transport).execute(TorSearchArgs(query="Tor Project"), None)
    )
    assert any("/search/" in url and "tokfld=live-token" in url for url in transport.urls)
    assert result.sources
    assert result.sources[0]["url"].startswith("http://2gzyxa5")
    assert result.metadata["socks"]["atyp"] == 3
    assert result.metadata["socks"]["local_dns"] is False


def test_tor_search_empty_hits_are_failed_not_unconfigured(monkeypatch):
    from app.config import get_settings

    class EmptyTransport:
        async def fetch(self, url, timeout=45):
            return {"status": 200, "text": "<html><body>no onions here</body></html>", "socks": {}}

    monkeypatch.setattr(
        get_settings(),
        "tor_search_providers",
        [{"name": "ahmia", "url_template": "http://example.onion/search/?q=__QUERY__"}],
    )
    with pytest.raises(ToolError, match="tor_search_failed"):
        asyncio.run(TorSearchProvider(transport=EmptyTransport()).execute(TorSearchArgs(query="test"), None))


async def _handle_redirect(reader, writer, received):
    await reader.readexactly(3)
    writer.write(b"\x05\x00")
    await writer.drain()
    header = await reader.readexactly(4)
    assert header[0] == 5 and header[3] == 3
    length = (await reader.readexactly(1))[0]
    name = await reader.readexactly(length)
    port = int.from_bytes(await reader.readexactly(2), "big")
    received.append((name.decode(), port, header[3]))
    writer.write(b"\x05\x00\x00\x01" + b"\x00\x00\x00\x00" + b"\x00\x00")
    await writer.drain()
    rest = await reader.read(4096)
    if rest.startswith(b"GET /start"):
        location = f"http://{ONION}/final"
        writer.write(f"HTTP/1.1 302 Found\r\nLocation: {location}\r\n\r\n".encode())
        await writer.drain()
    elif rest.startswith(b"GET /final"):
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nland")
        await writer.drain()
    writer.close()


def test_fetch_follows_http_redirect_over_socks5h(monkeypatch):
    onion_lookups = []
    real = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        if ".onion" in str(host).lower():
            onion_lookups.append(host)
            raise AssertionError("local DNS must not resolve .onion")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)

    async def run():
        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo

        async def loop_guarded(host, *args, **kwargs):
            if ".onion" in str(host).lower():
                onion_lookups.append(host)
                raise AssertionError("local DNS must not resolve .onion")
            return await original(host, *args, **kwargs)

        loop.getaddrinfo = loop_guarded
        received = []
        server = await asyncio.start_server(lambda r, w: _handle_redirect(r, w, received), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            page = await TorTransport(host="127.0.0.1", port=port).fetch(f"http://{ONION}/start")
        assert page["text"] == "land"
        assert page["url"].endswith("/final")
        assert page.get("redirected_from", "").endswith("/start")
        assert received[0][2] == 3
        assert all(item[0] == ONION for item in received)

    asyncio.run(run())
    assert onion_lookups == []
