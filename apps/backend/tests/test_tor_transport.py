import asyncio
import socket

import pytest

from app.tools.contracts import ToolError
from app.tools.tor.classify import OFFICIAL_AND_REACHABLE, REACHABLE_UNVERIFIED, classify_authority
from app.tools.tor.provider import TorSearchArgs, TorSearchProvider
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
