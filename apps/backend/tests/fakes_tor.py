"""Test-only Tor fakes: a real loopback SOCKS5h listener and a service with nothing behind it.

Nothing here starts Tor, opens a non-loopback socket or reaches the network. The listener is a plain
loopback TCP server that speaks the SOCKS5h handshake itself — so a test can assert the bytes the
route actually carried — and the service answers the two questions the tools ask it (which endpoint,
which proof) without a process, a port or a proof file.
"""

from __future__ import annotations

import socket
import threading

PAGE = "<html><head><title>Example Domain</title></head><body>clearnet page</body></html>"
LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "testserver"}


def dns_tripwire(monkeypatch):
    """Refuse local resolution of **every** non-loopback host, on both resolver paths.

    A Tor-required action must hand the destination name to the proxy; a lookup for it here is the
    failure the test is looking for, so the tripwire raises instead of returning an address and
    records the attempt either way. Returns the attempts and an installer for the in-loop guard
    (the socket-level one already covers a loop that resolves through ``socket.getaddrinfo``).
    """
    attempts: list[str] = []
    real = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        if str(host).lower() not in LOOPBACK:
            attempts.append(str(host))
            raise AssertionError(f"local DNS must not resolve {host!r}")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)

    async def install_loop_guard():
        import asyncio

        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo

        async def loop_guarded(host, *args, **kwargs):
            if str(host).lower() not in LOOPBACK:
                attempts.append(str(host))
                raise AssertionError(f"local DNS must not resolve {host!r}")
            return await original(host, *args, **kwargs)

        loop.getaddrinfo = loop_guarded

    return attempts, install_loop_guard


class FakeSocks5hServer:
    """A loopback SOCKS5h proxy that records CONNECTs and answers HTTP/1.1 itself.

    Only ATYP 3 (domain name) is served, so the fake is itself a remote-DNS assertion: a client that
    resolved the destination locally would show up as an ATYP 1/4 request and get no page.
    """

    def __init__(self, *, body: str = PAGE, status: int = 200, refuse: list[str] | None = None):
        self.requests: list[tuple[str, int, int]] = []
        self.body, self.status = body, status
        self.refuse = {host.lower() for host in (refuse or [])}
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(16)
        self.host, self.port = self._server.getsockname()
        self.endpoint = (self.host, self.port)
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._accept, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self.close()

    def close(self):
        self._closed.set()
        try:
            self._server.close()
        except OSError:
            pass
        self._thread.join(timeout=5)

    def _accept(self):
        while not self._closed.is_set():
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _read(self, conn, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = conn.recv(size - len(data))
            if not chunk:
                raise OSError("short read")
            data += chunk
        return data

    def _handle(self, conn):
        with conn:
            conn.settimeout(10)
            try:
                if self._read(conn, 3)[:1] != b"\x05":
                    return
                conn.sendall(b"\x05\x00")
                header = self._read(conn, 4)
                if header[0] != 5 or header[3] != 3:
                    return
                length = self._read(conn, 1)[0]
                host = self._read(conn, length).decode()
                port = int.from_bytes(self._read(conn, 2), "big")
                self.requests.append((host, port, header[3]))
                if host.lower() in self.refuse:
                    # A SOCKS "general failure": the route exists, the destination does not.
                    conn.sendall(b"\x05\x01\x00\x01" + b"\x00" * 6)
                    return
                conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")
                first = conn.recv(4096)
                if not first.startswith(b"GET "):
                    # Not an HTTP request (a TLS ClientHello, for instance): answer nothing.
                    return
                body = self.body.encode()
                conn.sendall(
                    f"HTTP/1.1 {self.status} OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n".encode()
                    + body
                )
            except (OSError, TimeoutError, ValueError):
                return


class FakeTorService:
    """The running Tor service, minus the process, the port and the proof file.

    ``endpoint`` is what the tools route through, ``verified_chain`` is what the snapshot claims and
    ``recover`` is what a bounded ``reestablish()`` answers. Both calls are counted, so a test can
    assert that recovery happened exactly once.
    """

    def __init__(self, endpoint, *, verified: bool = True, recover: bool = False):
        self.endpoint = endpoint
        self.verified = verified
        self.recover = recover
        self.recovery_calls = 0
        self.snapshot_calls = 0
        self.deadlines: list[float] = []

    def active(self):
        return self.endpoint

    def snapshot(self):
        self.snapshot_calls += 1
        host, port = self.endpoint
        return {
            "state": "ready" if self.verified else "unavailable",
            "managed": True,
            "endpoint": {"host": host, "port": port},
            "listening": True,
            "verified_chain": self.verified,
            "verified_at": "2026-09-24T00:00:00+00:00" if self.verified else None,
            "method": "socks5h",
        }

    async def reestablish(self, *, deadline_seconds: float) -> bool:
        self.recovery_calls += 1
        self.deadlines.append(float(deadline_seconds))
        return self.recover


def closed_port() -> int:
    """A loopback port nothing listens on: bind, learn the number, close the socket."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port
