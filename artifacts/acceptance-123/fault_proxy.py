"""A test-only HTTP/HTTPS forward proxy with a switch that simulates a network outage.

Why this exists
---------------
The H6 scenario has to prove that a multi-step task survives a short backend -> Gateway
interruption without replaying a completed write. Firewalling the production Gateway or
suspending the backend would either touch shared infrastructure or freeze the host heartbeat,
so neither is acceptable.

This proxy sits on localhost. The installed app is launched with ``HTTPS_PROXY`` pointing at it;
normal traffic is forwarded byte-for-byte to the real Gateway. ``POST /block?seconds=6`` on the
control port refuses new tunnels and closes the live ones, which is exactly the transport failure
the product must recover from. Nothing in the product or in the Gateway is modified, and the
proxy is never installed.

Usage
-----
    python fault_proxy.py --port 8765 --control-port 8766
    python fault_proxy.py --self-test
    python fault_proxy.py --probe https://gateway.12testers.store/health

Control:
    POST http://127.0.0.1:8766/block?seconds=6
    POST http://127.0.0.1:8766/clear
    GET  http://127.0.0.1:8766/status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.parse

CONNECT_OK = b"HTTP/1.1 200 Connection Established\r\n\r\n"
BAD_GATEWAY = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


class State:
    blocked_until = 0.0
    active: set[tuple[asyncio.StreamWriter, asyncio.StreamWriter]] = set()
    forwarded = 0
    refused = 0


STATE = State()


def is_blocked() -> bool:
    return time.monotonic() < STATE.blocked_until


def remaining() -> float:
    return max(0.0, STATE.blocked_until - time.monotonic())


def close_active() -> None:
    for client, upstream in list(STATE.active):
        for writer in (client, upstream):
            try:
                writer.close()
            except Exception:
                pass
    STATE.active.clear()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
            STATE.forwarded += len(chunk)
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
    except (TimeoutError, asyncio.IncompleteReadError, ConnectionError):
        writer.close()
        return
    try:
        request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        method, target, _version = request_line.split(" ", 2)
    except ValueError:
        writer.write(BAD_GATEWAY)
        await writer.drain()
        writer.close()
        return

    if is_blocked():
        STATE.refused += 1
        writer.write(BAD_GATEWAY)
        await writer.drain()
        writer.close()
        return

    if method.upper() == "CONNECT":
        host, _, port_text = target.rpartition(":")
        try:
            port = int(port_text)
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=15
            )
        except (OSError, ValueError, TimeoutError):
            STATE.refused += 1
            writer.write(BAD_GATEWAY)
            await writer.drain()
            writer.close()
            return
        writer.write(CONNECT_OK)
        await writer.drain()
        STATE.active.add((writer, up_writer))
        try:
            await asyncio.gather(
                _pipe(reader, up_writer),
                _pipe(up_reader, writer),
            )
        finally:
            STATE.active.discard((writer, up_writer))
        return

    # Plain HTTP absolute-form request: forward it to the origin.
    parsed = urllib.parse.urlsplit(target)
    if not parsed.hostname:
        writer.write(BAD_GATEWAY)
        await writer.drain()
        writer.close()
        return
    try:
        up_reader, up_writer = await asyncio.wait_for(
            asyncio.open_connection(parsed.hostname, parsed.port or 80), timeout=15
        )
    except (OSError, TimeoutError):
        STATE.refused += 1
        writer.write(BAD_GATEWAY)
        await writer.drain()
        writer.close()
        return
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    rest = head.split(b"\r\n", 1)[1]
    up_writer.write(f"{method} {path} HTTP/1.1\r\n".encode("latin-1") + rest)
    await up_writer.drain()
    STATE.active.add((writer, up_writer))
    try:
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
    finally:
        STATE.active.discard((writer, up_writer))


async def _control(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
    except (TimeoutError, asyncio.IncompleteReadError, ConnectionError):
        writer.close()
        return
    try:
        method, target, _ = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ", 2)
    except ValueError:
        method, target = "GET", "/"
    parsed = urllib.parse.urlsplit(target)
    query = urllib.parse.parse_qs(parsed.query)
    status = 200
    if parsed.path == "/block":
        seconds = max(0.5, min(60.0, float(query.get("seconds", ["6"])[0])))
        STATE.blocked_until = time.monotonic() + seconds
        close_active()
        body = {"blocked_for": seconds}
    elif parsed.path == "/clear":
        STATE.blocked_until = 0.0
        body = {"blocked": False}
    elif parsed.path == "/status":
        body = {
            "blocked": is_blocked(),
            "remaining_seconds": round(remaining(), 1),
            "forwarded_bytes": STATE.forwarded,
            "refused": STATE.refused,
        }
    else:
        status = 404
        body = {"error": "unknown"}
    payload = json.dumps(body).encode()
    writer.write(
        f"HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
        + payload
    )
    await writer.drain()
    writer.close()


async def serve(port: int, control_port: int) -> None:
    server = await asyncio.start_server(_proxy, "127.0.0.1", port, limit=1 << 20)
    control = await asyncio.start_server(_control, "127.0.0.1", control_port)
    print(f"fault proxy http://127.0.0.1:{port} control http://127.0.0.1:{control_port}", flush=True)
    async with server, control:
        await asyncio.gather(server.serve_forever(), control.serve_forever())


def self_test() -> int:
    """Free proof: forwarding works, blocking refuses, clearing restores (no production)."""
    import http.server
    import threading

    import httpx

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = b"FAULT_PROXY_OK"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=origin.serve_forever, daemon=True).start()
    origin_port = origin.server_address[1]

    async def main() -> None:
        server = await asyncio.start_server(_proxy, "127.0.0.1", 0, limit=1 << 20)
        control = await asyncio.start_server(_control, "127.0.0.1", 0)
        proxy_port = server.sockets[0].getsockname()[1]
        control_port = control.sockets[0].getsockname()[1]
        proxy_url = f"http://127.0.0.1:{proxy_port}"
        control_url = f"http://127.0.0.1:{control_port}"
        origin_url = f"http://127.0.0.1:{origin_port}/"

        def get():
            with httpx.Client(proxy=proxy_url, timeout=10) as client:
                return client.get(origin_url)

        def control_do(path):
            with httpx.Client(trust_env=False, timeout=10) as client:
                return client.post(control_url + path)

        first = await asyncio.to_thread(get)
        assert first.status_code == 200 and first.text == "FAULT_PROXY_OK", first.text
        armed = await asyncio.to_thread(control_do, "/block?seconds=1")
        assert armed.json()["blocked_for"] == 1.0
        blocked = await asyncio.to_thread(get)
        assert blocked.status_code == 502, blocked.status_code
        cleared = await asyncio.to_thread(control_do, "/clear")
        assert cleared.json()["blocked"] is False
        recovered = await asyncio.to_thread(get)
        assert recovered.status_code == 200 and recovered.text == "FAULT_PROXY_OK"
        async with server, control:
            pass
        print("SELF-TEST PASS: forward, block, recover")

    asyncio.run(main())
    origin.shutdown()
    return 0


def probe(url: str) -> int:
    """Free proof against the real Gateway through the proxy (no compute, no Pod)."""
    import httpx

    async def main() -> None:
        server = await asyncio.start_server(_proxy, "127.0.0.1", 0, limit=1 << 20)
        control = await asyncio.start_server(_control, "127.0.0.1", 0)
        proxy_port = server.sockets[0].getsockname()[1]
        control_port = control.sockets[0].getsockname()[1]
        proxy_url = f"http://127.0.0.1:{proxy_port}"
        control_url = f"http://127.0.0.1:{control_port}"
        async with server, control:
            def request():
                with httpx.Client(proxy=proxy_url, timeout=20, trust_env=False) as client:
                    return client.get(url)

            def control_do(path):
                with httpx.Client(trust_env=False, timeout=10) as client:
                    return client.post(control_url + path)

            ok = await asyncio.to_thread(request)
            assert ok.status_code == 200, ok.status_code
            await asyncio.to_thread(control_do, "/block?seconds=1")
            failed = False
            try:
                await asyncio.to_thread(request)
            except httpx.ProxyError:
                failed = True
            assert failed, "a blocked CONNECT must fail"
            await asyncio.to_thread(control_do, "/clear")
            recovered = await asyncio.to_thread(request)
            assert recovered.status_code == 200, recovered.status_code
        print("PROBE PASS: HTTPS CONNECT forwarded, blocked, recovered")

    asyncio.run(main())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--control-port", type=int, default=8766)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--probe")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.probe:
        return probe(args.probe)
    try:
        asyncio.run(serve(args.port, args.control_port))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
