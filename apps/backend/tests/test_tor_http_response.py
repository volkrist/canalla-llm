"""A19 — bounded HTTP response handling over the SOCKS5h route.

The old reader stopped on a timeout and handed the bytes on as a page, so half a transfer could pass
for a completed ``HTTP 200``. These cases pin the framing that replaced it: exact ``Content-Length``,
chunked reassembly, bounded decompression and a typed error for every way a response can be
incomplete or hostile. A couple of them run end to end through the real transport and a fake SOCKS
proxy, so the reader is exercised where it actually lives.
"""

import asyncio
import gzip
import zlib

import pytest
from fakes_tor import FakeSocks5hServer

from app.tools.contracts import ToolError
from app.tools.tor.http_response import (
    INVALID,
    TIMEOUT,
    TOO_LARGE,
    TRUNCATED,
    read_http_response,
)
from app.tools.tor.socks import TorTransport


def exchange(parts, *, timeout=5.0, max_body=None, delay=0.0):
    """Serve ``parts`` (each written then, optionally, paused) and read one response.

    Returns ``("ok", HttpResponse)`` or ``("error", code)`` so a test can assert on either without
    the helper deciding what a failure means.
    """

    async def run():
        async def handler(reader, writer):
            try:
                for part in parts:
                    writer.write(part)
                    await writer.drain()
                    if delay:
                        await asyncio.sleep(delay)
            except Exception:
                pass
            finally:
                writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                kwargs = {"timeout": timeout}
                if max_body is not None:
                    kwargs["max_body"] = max_body
                return ("ok", await read_http_response(reader, **kwargs))
            except ToolError as error:
                return ("error", error.code)
            finally:
                writer.close()

    return asyncio.run(run())


# -------------------------------------------------------------------- Content-Length framing


def test_content_length_body_is_read_exactly():
    kind, response = exchange([b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\nhello onion"])
    assert kind == "ok"
    assert response.status == 200
    assert response.text == "hello onion"
    assert response.content_length == 11
    assert response.header("content-length") == "11"


def test_a_body_shorter_than_content_length_is_truncated():
    kind, code = exchange([b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nonly a few"])
    assert (kind, code) == ("error", TRUNCATED)


def test_content_length_over_the_cap_is_too_large():
    kind, code = exchange([b"HTTP/1.1 200 OK\r\nContent-Length: 1000000\r\n\r\n"], max_body=64)
    assert (kind, code) == ("error", TOO_LARGE)


def test_connection_close_framing_reads_until_eof():
    kind, response = exchange([b"HTTP/1.1 200 OK\r\n\r\nodd body without a length"])
    assert kind == "ok"
    assert response.text == "odd body without a length"
    assert response.content_length is None


def test_no_body_status_codes_have_no_body():
    kind, response = exchange([b"HTTP/1.1 204 No Content\r\nContent-Length: 5\r\n\r\nhello"])
    assert kind == "ok"
    assert response.status == 204
    assert response.body == b""


# --------------------------------------------------------------------------- chunked framing


def test_chunked_body_is_reassembled():
    payload = [
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n",
        b"5\r\nhello\r\n",
        b"6\r\n world\r\n",
        b"0\r\n\r\n",
    ]
    kind, response = exchange(payload)
    assert kind == "ok"
    assert response.text == "hello world"
    assert response.transfer_encoding.lower() == "chunked"


def test_chunked_with_extension_and_trailers():
    payload = [
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n",
        b"5;ext=1\r\nhello\r\n",
        b"0\r\nX-Trailer: done\r\n\r\n",
    ]
    kind, response = exchange(payload)
    assert kind == "ok"
    assert response.text == "hello"


def test_a_chunked_stream_cut_short_is_truncated():
    # Missing the terminating zero chunk: the document never ended.
    payload = [
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n",
        b"5\r\nhello\r\n",
    ]
    kind, code = exchange(payload)
    assert (kind, code) == ("error", TRUNCATED)


def test_chunked_total_over_the_cap_is_too_large():
    payload = [
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n",
        b"40\r\n" + b"a" * 64 + b"\r\n",
        b"0\r\n\r\n",
    ]
    kind, code = exchange(payload, max_body=16)
    assert (kind, code) == ("error", TOO_LARGE)


# ---------------------------------------------------------------------- content encoding


def test_gzip_content_encoding_is_decoded():
    body = gzip.compress("hello gzip world".encode())
    payload = [
        b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body
    ]
    kind, response = exchange(payload)
    assert kind == "ok"
    assert response.text == "hello gzip world"
    assert response.content_encoding == "gzip"


def test_deflate_content_encoding_is_decoded():
    body = zlib.compress("hello deflate".encode())
    payload = [
        b"HTTP/1.1 200 OK\r\nContent-Encoding: deflate\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body
    ]
    kind, response = exchange(payload)
    assert kind == "ok"
    assert response.text == "hello deflate"


def test_raw_deflate_content_encoding_is_decoded():
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    body = compressor.compress(b"raw deflate") + compressor.flush()
    payload = [
        b"HTTP/1.1 200 OK\r\nContent-Encoding: deflate\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body
    ]
    kind, response = exchange(payload)
    assert kind == "ok"
    assert response.text == "raw deflate"


def test_unknown_content_encoding_is_refused():
    payload = [b"HTTP/1.1 200 OK\r\nContent-Encoding: br\r\nContent-Length: 4\r\n\r\n\x00\x01\x02\x03"]
    kind, code = exchange(payload)
    assert (kind, code) == ("error", INVALID)


def test_decompression_bomb_is_refused():
    body = gzip.compress(b"\x00" * (8 * 1024 * 1024))
    assert len(body) < 64 * 1024  # the compressed form is small; the expansion is the attack
    payload = [
        b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body
    ]
    kind, code = exchange(payload)
    assert (kind, code) == ("error", TOO_LARGE)


def test_truncated_gzip_stream_is_refused():
    body = gzip.compress(b"hello gzip world")[:10]
    payload = [
        b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body
    ]
    kind, code = exchange(payload)
    assert kind == "error"
    assert code in {INVALID, TRUNCATED}


# ------------------------------------------------------------------------------ time & syntax


def test_a_slow_body_is_a_timeout_not_a_200():
    parts = [b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nfirst", b"...and then never the rest"]
    kind, code = exchange(parts, delay=1.0, timeout=0.25)
    assert (kind, code) == ("error", TIMEOUT)


def test_garbage_instead_of_a_status_line_is_invalid():
    kind, code = exchange([b"NOT-HTTP\r\n\r\n"])
    assert (kind, code) == ("error", INVALID)


def test_status_and_headers_are_reported():
    payload = [
        b"HTTP/1.1 302 Found\r\nLocation: http://example.onion/final\r\nSet-Cookie: a=1\r\n"
        b"Content-Length: 0\r\n\r\n"
    ]
    kind, response = exchange(payload)
    assert kind == "ok"
    assert response.status == 302
    assert response.reason == "Found"
    assert response.header("location") == "http://example.onion/final"
    assert response.header("SET-COOKIE") == "a=1"


# ---------------------------------------------------------------- end to end over SOCKS5h


def test_transport_decodes_a_chunked_gzip_page_over_socks5h():
    body = gzip.compress(b"<html>compressed onion page</html>")
    raw = (
        b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nTransfer-Encoding: chunked\r\n\r\n"
        + f"{len(body):x}\r\n".encode()
        + body
        + b"\r\n0\r\n\r\n"
    )
    with FakeSocks5hServer(raw_response=raw) as server:
        page = asyncio.run(
            TorTransport(host="127.0.0.1", port=server.port).fetch("http://example.onion/page")
        )
    assert page["text"] == "<html>compressed onion page</html>"
    assert page["status"] == 200
    assert page["http"]["content_encoding"] == "gzip"
    assert page["http"]["transfer_encoding"].lower() == "chunked"


def test_transport_reports_a_truncated_page_as_a_typed_failure():
    raw = b"HTTP/1.1 200 OK\r\nContent-Length: 500\r\n\r\nfar too little"
    with FakeSocks5hServer(raw_response=raw) as server:

        async def run():
            with pytest.raises(ToolError) as failure:
                await TorTransport(host="127.0.0.1", port=server.port).fetch("http://example.onion/")
            return failure.value.code

        assert asyncio.run(run()) == TRUNCATED
