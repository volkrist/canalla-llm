"""SOCKS5h client: destination hostname is sent to the proxy, never locally resolved."""

import asyncio
import ssl
from urllib.parse import urljoin, urlsplit

from ..contracts import ToolError
from .urls import validate_tor_url


def header_value(raw_headers: str, name: str) -> str:
    needle = name.lower() + ":"
    for line in raw_headers.splitlines():
        if line.lower().startswith(needle):
            return line.split(":", 1)[1].strip()
    return ""


class Socks5hConnector:
    """SOCKS5 with ATYP=0x03 (domain). Equivalent to socks5h; no local DNS for the destination."""

    def __init__(self, host="127.0.0.1", port=9050, timeout=45):
        self.host, self.port, self.timeout = host, port, timeout
        self.last_handshake = None

    async def open(self, dest_host: str, dest_port: int, *, tls=False, server_hostname=None, timeout=None):
        if self.host not in {"127.0.0.1", "::1", "localhost"}:
            raise ToolError("unsafe_url")
        dest_host = (dest_host or "").rstrip(".").lower()
        if not dest_host or dest_host in {"localhost", "127.0.0.1", "::1"}:
            raise ToolError("unsafe_url")
        wait = timeout if timeout is not None else self.timeout
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), wait)
        except (OSError, asyncio.TimeoutError) as error:
            raise ToolError("tor_not_configured") from error
        try:
            writer.write(b"\x05\x01\x00")
            await writer.drain()
            greeting = await asyncio.wait_for(reader.readexactly(2), wait)
            if greeting != b"\x05\x00":
                raise ToolError("tor_not_configured")
            host_bytes = dest_host.encode("idna")
            request = (
                b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + dest_port.to_bytes(2, "big")
            )
            self.last_handshake = {
                "atyp": 0x03,
                "dest_host": dest_host,
                "dest_port": dest_port,
                "proxy_host": self.host,
                "proxy_port": self.port,
                "local_dns": False,
            }
            writer.write(request)
            await writer.drain()
            reply = await asyncio.wait_for(reader.readexactly(4), wait)
            if reply[0] != 5 or reply[1] != 0:
                raise ToolError("tor_unavailable")
            atyp = reply[3]
            if atyp == 1:
                await reader.readexactly(4 + 2)
            elif atyp == 4:
                await reader.readexactly(16 + 2)
            elif atyp == 3:
                length = (await reader.readexactly(1))[0]
                await reader.readexactly(length + 2)
            else:
                raise ToolError("tor_unavailable")
            if tls:
                context = ssl.create_default_context()
                if dest_host.endswith(".onion"):
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                protocol = writer.transport.get_protocol()
                new_transport = await asyncio.get_running_loop().start_tls(
                    writer.transport,
                    protocol,
                    context,
                    server_hostname=server_hostname or dest_host,
                )
                writer._transport = new_transport
            return reader, writer
        except ToolError:
            writer.close()
            raise
        except Exception as error:
            writer.close()
            raise ToolError("tor_unavailable") from error


class TorTransport:
    def __init__(self, connector=None, host="127.0.0.1", port=9050):
        self.connector = connector or Socks5hConnector(host, port)

    async def fetch(self, url: str, *, timeout=45, _hops=0) -> dict:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        status = 0
        raw_headers = ""
        page = {"url": url, "status": 0, "text": "", "raw_headers": "", "socks": {}}
        reader, writer = await self.connector.open(
            host, port, tls=parsed.scheme == "https", server_hostname=host, timeout=timeout
        )
        try:
            request = (
                f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: AlexLLM-Tor/0.8\r\n"
                "Accept: text/html,application/json,text/plain,*/*\r\nConnection: close\r\n\r\n"
            )
            writer.write(request.encode())
            await writer.drain()
            chunks = []
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(65536), timeout)
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                if sum(len(part) for part in chunks) > 500_000:
                    break
            raw = b"".join(chunks)
            header, _, body = raw.partition(b"\r\n\r\n")
            status_line = header.split(b"\r\n", 1)[0].decode("latin1", "replace")
            status = 0
            parts = status_line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                status = int(parts[1])
            text = body.decode("utf-8", "replace")
            handshake = getattr(self.connector, "last_handshake", None) or {}
            raw_headers = header.decode("latin1", "replace")
            page = {
                "url": url,
                "status": status,
                "text": text,
                "raw_headers": raw_headers,
                "socks": handshake,
            }
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        if 300 <= status < 400 and _hops < 3:
            location = header_value(raw_headers, "location")
            if location:
                next_url = urljoin(url, location)
                await validate_tor_url(next_url)
                followed = await self.fetch(next_url, timeout=timeout, _hops=_hops + 1)
                followed["redirected_from"] = url
                return followed
        return page


class TorTransportProvider:
    """Named transport adapter. Not TinyFish and not a curated onion catalog."""

    def __init__(self, transport=None, host="127.0.0.1", port=9050):
        self.transport = transport or TorTransport(host=host, port=port)

    async def fetch(self, url: str, *, timeout=45) -> dict:
        return await self.transport.fetch(url, timeout=timeout)
