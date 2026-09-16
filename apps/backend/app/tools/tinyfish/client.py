import asyncio
import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache

import httpx

from ...config import get_settings
from ..contracts import ToolCredentialProvider, ToolError

SEARCH = "https://api.search.tinyfish.ai"
FETCH = "https://api.fetch.tinyfish.ai"
AGENT = "https://agent.tinyfish.ai/v1"
BROWSER = "https://api.browser.tinyfish.ai"


class BackendCredentialProvider(ToolCredentialProvider):
    def configured(self, provider):
        return provider == "tinyfish" and bool(get_settings().tinyfish_api_key.get_secret_value())

    def resolve(self, provider):
        if not self.configured(provider):
            raise ToolError("provider_not_configured")
        return get_settings().tinyfish_api_key.get_secret_value()


def status_error(status):
    return ToolError(
        {401: "provider_auth", 402: "billing_required", 403: "provider_forbidden", 429: "rate_limited"}.get(
            status, "provider_unavailable"
        )
    )


def retry_delay(value, attempt):
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            seconds = 2**attempt
    return max(0, min(seconds, 30))


class TinyFishClient:
    def __init__(self, credentials=None, transport=None, settings=None):
        self.credentials = credentials or BackendCredentialProvider()
        self.transport = transport
        self.settings = settings or get_settings()
        self.search_lock = asyncio.Lock()
        self.next_search = 0

    def session(self, timeout=40):
        return httpx.AsyncClient(
            headers={"X-API-Key": self.credentials.resolve("tinyfish")},
            timeout=httpx.Timeout(timeout, connect=10),
            follow_redirects=False,
            transport=self.transport,
        )

    async def throttle_search(self):
        async with self.search_lock:
            await asyncio.sleep(max(0, self.next_search - time.monotonic()))
            self.next_search = time.monotonic() + self.settings.tinyfish_search_interval_seconds

    async def request(self, method, url, *, body=None, params=None, retry=True, timeout=40):
        if url in {SEARCH, FETCH} and not self.settings.tinyfish_search_fetch_free:
            raise ToolError("pricing_not_confirmed")
        # Endpoints are adapter constants, never supplied by model/browser content.
        if not (
            url in {SEARCH, FETCH, BROWSER}
            or url.startswith(AGENT + "/runs/")
            or url.startswith(BROWSER + "/")
        ):
            raise ToolError("invalid_provider_endpoint")
        try:
            async with self.session(timeout) as client:
                for attempt in range(3 if retry else 1):
                    if url == SEARCH:
                        await self.throttle_search()
                    async with client.stream(method, url, json=body, params=params) as response:
                        if response.status_code in {429, 503} and retry and attempt < 2:
                            delay = retry_delay(response.headers.get("retry-after"), attempt)
                        else:
                            if not response.is_success:
                                raise status_error(response.status_code)
                            if response.status_code == 204:
                                return {"terminated": True}
                            buffer = bytearray()
                            async for block in response.aiter_bytes():
                                buffer.extend(block)
                                if len(buffer) > 2_000_000:
                                    raise ToolError("provider_result_too_large")
                            value = json.loads(buffer)
                            if not isinstance(value, dict):
                                raise ToolError("malformed_provider_result")
                            return value
                    await asyncio.sleep(delay)
        except httpx.TimeoutException:
            raise ToolError("provider_timeout") from None
        except httpx.HTTPError:
            raise ToolError("provider_unavailable") from None
        except (ValueError, TypeError):
            raise ToolError("malformed_provider_result") from None

    async def agent_events(self, body):
        try:
            async with self.session(180) as client:
                # Never retry an ambiguous paid create.
                async with client.stream("POST", AGENT + "/automation/run-sse", json=body) as response:
                    if not response.is_success:
                        raise status_error(response.status_code)
                    buffer, total = b"", 0
                    async for block in response.aiter_bytes():
                        total += len(block)
                        buffer += block
                        if total > 4_000_000 or len(buffer) > 1_000_000:
                            raise ToolError("provider_result_too_large")
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            if line.startswith(b"data:"):
                                value = json.loads(line[5:].strip())
                                if not isinstance(value, dict):
                                    raise ToolError("malformed_provider_result")
                                yield value
        except httpx.TimeoutException:
            raise ToolError("provider_timeout") from None
        except httpx.HTTPError:
            raise ToolError("provider_unavailable") from None
        except (ValueError, TypeError):
            raise ToolError("malformed_provider_result") from None


@lru_cache
def get_tinyfish_client():
    return TinyFishClient()
