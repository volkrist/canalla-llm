"""A bounded HTTP/1.1 response reader for the SOCKS5h Tor transport.

The transport used to ``read()`` until a timeout and treat whatever arrived as the page. That made
a slow or truncated transfer indistinguishable from a complete ``HTTP 200`` — half a document could
be handed to the model as if it were the source. This module owns the framing instead: status line,
headers, ``Content-Length`` or chunked framing, bounded decompression, and an explicit error for
anything that is not a complete, honest response.

It is deliberately small: it speaks exactly the response grammar the transport provokes (``GET``,
``Connection: close``, HTTP/1.1) and refuses everything it cannot prove it framed correctly. Nothing
here opens a socket or resolves a name — it reads one already-connected Tor stream.
"""

from __future__ import annotations

import asyncio
import zlib
from dataclasses import dataclass, field

from ..contracts import ToolError

# Bounds. Headers are small; a document is capped well under the transport's own read ceiling so a
# hostile server cannot make the backend buffer without limit.
MAX_HEADER_BYTES = 64 * 1024
MAX_LINE_BYTES = 8 * 1024
MAX_BODY_BYTES = 1_000_000
MAX_DECOMPRESSED_BYTES = 4_000_000
READ_CHUNK = 65536

# Typed failures. A response that is not a complete document is its own honest answer — never a
# truncated body dressed up as a 200.
TRUNCATED = "tor_response_truncated"
TOO_LARGE = "tor_response_too_large"
INVALID = "tor_response_invalid"
TIMEOUT = "tor_response_timeout"
RESPONSE_ERRORS = frozenset({TRUNCATED, TOO_LARGE, INVALID, TIMEOUT})


@dataclass
class HttpResponse:
    status: int
    reason: str = ""
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""
    content_length: int | None = None
    transfer_encoding: str = ""
    content_encoding: str = ""

    def header(self, name: str) -> str:
        """The last value of a header (case-insensitive), or ``""``."""
        needle = name.lower()
        found = ""
        for key, value in self.headers:
            if key == needle:
                found = value
        return found

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    @property
    def head_text(self) -> str:
        lines = [f"HTTP/1.1 {self.status} {self.reason}".rstrip(), *[f"{k}: {v}" for k, v in self.headers]]
        return "\r\n".join(lines)


class _BoundedReader:
    """A thin buffer over the Tor stream with one overall deadline and an explicit EOF."""

    def __init__(self, reader, *, timeout: float):
        self._reader = reader
        self._deadline = asyncio.get_running_loop().time() + max(0.001, float(timeout))
        self._buffer = b""
        self._eof = False

    async def _pull(self) -> bool:
        if self._eof:
            return False
        remaining = self._deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise ToolError(TIMEOUT)
        try:
            chunk = await asyncio.wait_for(self._reader.read(READ_CHUNK), remaining)
        except asyncio.TimeoutError as error:
            raise ToolError(TIMEOUT) from error
        except OSError as error:
            raise ToolError(TRUNCATED) from error
        if not chunk:
            self._eof = True
            return False
        self._buffer += chunk
        return True

    async def _need(self, count: int) -> None:
        while len(self._buffer) < count:
            if not await self._pull():
                raise ToolError(TRUNCATED)

    async def read_exactly(self, count: int) -> bytes:
        if count < 0:
            raise ToolError(INVALID)
        await self._need(count)
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    async def read_line(self, limit: int = MAX_LINE_BYTES) -> bytes:
        while b"\r\n" not in self._buffer:
            if len(self._buffer) > limit:
                raise ToolError(INVALID)
            if not await self._pull():
                raise ToolError(TRUNCATED)
        index = self._buffer.index(b"\r\n")
        line, self._buffer = self._buffer[:index], self._buffer[index + 2 :]
        return line

    async def read_head(self) -> bytes:
        """The status line and headers, up to (but not including) the terminating blank line."""
        while b"\r\n\r\n" not in self._buffer:
            if len(self._buffer) > MAX_HEADER_BYTES:
                raise ToolError(TOO_LARGE)
            if not await self._pull():
                raise ToolError(TRUNCATED)
        end = self._buffer.index(b"\r\n\r\n")
        head, self._buffer = self._buffer[:end], self._buffer[end + 4 :]
        return head

    async def read_to_eof(self, limit: int) -> bytes:
        while True:
            if len(self._buffer) > limit:
                raise ToolError(TOO_LARGE)
            if not await self._pull():
                data, self._buffer = self._buffer, b""
                return data

    async def read_chunked(self, limit: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            size_line = await self.read_line()
            token = size_line.split(b";", 1)[0].strip()
            try:
                size = int(token.decode("ascii"), 16)
            except (ValueError, UnicodeDecodeError) as error:
                raise ToolError(INVALID) from error
            if size < 0:
                raise ToolError(INVALID)
            if size == 0:
                # Trailers, terminated by a blank line; ignore their content but require the CRLF.
                while await self.read_line() != b"":
                    pass
                break
            total += size
            if total > limit:
                raise ToolError(TOO_LARGE)
            chunks.append(await self.read_exactly(size))
            if await self.read_exactly(2) != b"\r\n":
                raise ToolError(INVALID)
        return b"".join(chunks)


def _parse_head(head: bytes) -> tuple[int, str, list[tuple[str, str]]]:
    lines = head.split(b"\r\n")
    status_line = lines[0].decode("latin1", "replace")
    parts = status_line.split(None, 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise ToolError(INVALID)
    status = int(parts[1])
    reason = parts[2].strip() if len(parts) > 2 else ""
    headers: list[tuple[str, str]] = []
    for raw in lines[1:]:
        if not raw:
            continue
        if b":" not in raw:
            raise ToolError(INVALID)
        name, _, value = raw.partition(b":")
        headers.append((name.decode("latin1").strip().lower(), value.decode("latin1").strip()))
    return status, reason, headers


def _inflate(raw: bytes, *, gzip: bool) -> bytes:
    """Decompress and prove the stream was complete and within the bomb ceiling."""
    if gzip:
        decoders = [zlib.decompressobj(16 + zlib.MAX_WBITS)]
    else:
        # HTTP "deflate" is meant to be zlib-wrapped, but raw deflate is common: accept either.
        decoders = [zlib.decompressobj(zlib.MAX_WBITS), zlib.decompressobj(-zlib.MAX_WBITS)]
    last_error: Exception | None = None
    for decoder in decoders:
        try:
            out = decoder.decompress(raw, MAX_DECOMPRESSED_BYTES)
            if decoder.unconsumed_tail or len(out) >= MAX_DECOMPRESSED_BYTES:
                raise ToolError(TOO_LARGE)
            out += decoder.flush()
            if not decoder.eof:
                last_error = ToolError(TRUNCATED)
                continue
            if len(out) > MAX_DECOMPRESSED_BYTES:
                raise ToolError(TOO_LARGE)
            return out
        except zlib.error as error:
            last_error = error
            continue
    raise ToolError(INVALID) from last_error


def _decode_body(raw: bytes, headers: list[tuple[str, str]]) -> bytes:
    encoding = ""
    for key, value in headers:
        if key == "content-encoding":
            encoding = value
    encoding = encoding.lower().strip()
    if not encoding or encoding == "identity":
        return raw
    if encoding in {"gzip", "x-gzip"}:
        return _inflate(raw, gzip=True)
    if encoding == "deflate":
        return _inflate(raw, gzip=False)
    # A coding we cannot decode correctly is refused rather than handed on as a mangled document.
    raise ToolError(INVALID)


async def read_http_response(reader, *, timeout: float, max_body: int = MAX_BODY_BYTES) -> HttpResponse:
    """Read exactly one HTTP/1.1 response from a connected Tor stream.

    Every failure is typed: an incomplete ``Content-Length`` body, a chunked stream that ends before
    its terminator, a decompression that never finishes, an over-large body, or a read that blows
    the deadline. None of them is reported as a successful status.
    """
    bounded = _BoundedReader(reader, timeout=timeout)
    head = await bounded.read_head()
    status, reason, headers = _parse_head(head)

    transfer = ""
    length_header = ""
    for key, value in headers:
        if key == "transfer-encoding":
            transfer = value
        elif key == "content-length":
            length_header = value

    no_body = status < 200 or status in {204, 304}
    content_length: int | None = None
    if no_body:
        raw = b""
    elif "chunked" in [coding.strip().lower() for coding in transfer.split(",")]:
        raw = await bounded.read_chunked(max_body)
    elif length_header:
        first = length_header.split(",")[0].strip()
        try:
            content_length = int(first)
        except ValueError as error:
            raise ToolError(INVALID) from error
        if content_length < 0:
            raise ToolError(INVALID)
        if content_length > max_body:
            raise ToolError(TOO_LARGE)
        raw = await bounded.read_exactly(content_length)
    else:
        # Framing by connection close: the body is complete when the peer closes. We sent
        # ``Connection: close``, so this is a well-defined end rather than a guess.
        raw = await bounded.read_to_eof(max_body)

    body = _decode_body(raw, headers)
    return HttpResponse(
        status=status,
        reason=reason,
        headers=headers,
        body=body,
        content_length=content_length,
        transfer_encoding=transfer,
        content_encoding=", ".join(v for k, v in headers if k == "content-encoding"),
    )
