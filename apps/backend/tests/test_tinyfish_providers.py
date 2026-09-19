import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.config import get_settings
from app.tools.contracts import ToolError
from app.tools.policy import ToolLimits, WebSettings
from app.tools.tinyfish.agent import AgentArgs, TinyFishAgentProvider
from app.tools.tinyfish.client import BROWSER, FETCH, SEARCH, TinyFishClient
from app.tools.tinyfish.web import FetchArgs, SearchArgs, TinyFishFetchProvider, TinyFishSearchProvider


class TestCredentials:
    def resolve(self, provider):
        return "test-only-provider-credential"


class Context:
    def __init__(self):
        self.secrets = ("test-only-provider-credential",)
        self.settings = WebSettings(agent_enabled=True)
        self.limits = ToolLimits()
        self.events = []
        self.resolver = self.resolve

    async def resolve(self, host):
        return ["93.184.216.34"]

    async def progress(self, **values):
        self.events.append(values)


def client(handler, **settings):
    return TinyFishClient(
        TestCredentials(),
        httpx.MockTransport(handler),
        get_settings().model_copy(
            update={
                "tinyfish_search_interval_seconds": 0,
                **settings,
            }
        ),
    )


def test_search_contract_and_bounded_result():
    def handler(request):
        assert str(request.url).startswith(SEARCH)
        assert request.url.params["query"] == "Python docs"
        assert request.url.params["recency_minutes"] == "60"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://docs.python.org/3",
                        "title": "Python",
                        "snippet": "x" * 3000,
                        "date": "2026-09-15",
                    },
                    {"url": "http://169.254.169.254", "title": "bad", "snippet": "metadata"},
                ]
            },
        )

    result = asyncio.run(
        TinyFishSearchProvider(client(handler)).execute(
            SearchArgs(query="Python docs", recency_minutes=60), Context()
        )
    )
    assert len(result.sources) == 1 and len(result.sources[0]["excerpt"]) == 1500
    assert result.errors == ["unsafe_source"]
    assert result.cost_actual is None and result.cost_estimate == 0


def test_fetch_fresh_partial_and_unsafe_redirect():
    def handler(request):
        assert str(request.url) == FETCH
        body = json.loads(request.content)
        assert body["ttl"] == 0 and body["format"] == "markdown"
        assert body["include_etag_and_last_modified"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com/a",
                        "final_url": "https://example.com/doc",
                        "text": "content",
                        "title": "A",
                    },
                    {"url": "https://example.com/b", "final_url": "http://127.0.0.1", "text": "secret"},
                ],
                "errors": [{"url": "https://example.com/c", "error": "raw sensitive provider debug"}],
            },
        )

    result = asyncio.run(
        TinyFishFetchProvider(client(handler)).execute(
            FetchArgs(
                urls=["https://example.com/a", "https://example.com/b", "https://example.com/c"], fresh=True
            ),
            Context(),
        )
    )
    assert len(result.sources) == 1
    assert result.sources[0]["final_url"].endswith("/doc")
    assert result.errors == ["fetch_failed", "unsafe_redirect"]


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "provider_auth"),
        (402, "billing_required"),
        (403, "provider_forbidden"),
        (500, "provider_unavailable"),
    ],
)
def test_errors_sanitized(status, code):
    provider = TinyFishSearchProvider(client(lambda request: httpx.Response(status, text="RAW-SECRET-DEBUG")))
    with pytest.raises(ToolError, match=code) as caught:
        asyncio.run(provider.execute(SearchArgs(query="hello"), Context()))
    assert "RAW-SECRET" not in str(caught.value)


def test_rate_limit_bounded_retry_after():
    calls = []

    def handler(request):
        calls.append(True)
        return httpx.Response(429, headers={"retry-after": "0"}, json={})

    with pytest.raises(ToolError, match="rate_limited"):
        asyncio.run(client(handler).request("GET", SEARCH))
    assert len(calls) == 3


def test_timeout_and_oversized_result():
    def timeout(request):
        raise httpx.ReadTimeout("secret-endpoint")

    with pytest.raises(ToolError, match="provider_timeout"):
        asyncio.run(client(timeout).request("GET", SEARCH))
    with pytest.raises(ToolError, match="provider_result_too_large"):
        asyncio.run(
            client(lambda request: httpx.Response(200, content=b"x" * 2000001)).request("GET", SEARCH)
        )


def test_agent_sse_contract_steps_and_no_beta_assumption():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith("run-sse"):
            body = json.loads(request.content)
            assert "max_steps" not in body["agent_config"]
            assert not body["use_vault"] and not body["use_profile"]
            return httpx.Response(
                200,
                text='data: {"type":"STARTED","run_id":"run_1"}\n\ndata: {"type":"COMPLETE","run_id":"run_1","status":"COMPLETED","result":{"title":"Public facts"}}\n\n',
            )
        assert request.url.path == "/v1/runs/run_1"
        return httpx.Response(200, json={"status": "COMPLETED", "num_of_steps": 3})

    result = asyncio.run(
        TinyFishAgentProvider(client(handler)).execute(
            AgentArgs(url="https://example.com", goal="Read title"), Context()
        )
    )
    assert result.cost_estimate == pytest.approx(0.048)
    assert result.cost_actual is None and result.provider_run_id == "run_1"
    assert len(calls) == 2


def test_agent_budget_cancels_supplier():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("run-sse"):
            return httpx.Response(
                200,
                text='data: {"type":"STARTED","run_id":"run_2"}\n\ndata: {"type":"PROGRESS","run_id":"run_2","num_of_steps":20}\n\n',
            )
        assert request.url.path.endswith("/run_2/cancel")
        return httpx.Response(200, json={"status": "CANCELLED"})

    context = Context()
    with pytest.raises(ToolError, match="run_budget"):
        asyncio.run(
            TinyFishAgentProvider(client(handler)).execute(
                AgentArgs(url="https://example.com", goal="Read title"), context
            )
        )
    assert context.events[-1]["supplier_stop_confirmed"] is True
    assert calls[-1].endswith("/cancel")


def test_agent_task_cancellation_closes_upstream():
    closed, cancelled = [], []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"type":"STARTED","run_id":"run_3"}\n\n'
            await asyncio.sleep(10)

        async def aclose(self):
            closed.append(True)

    def handler(request):
        if request.url.path.endswith("run-sse"):
            return httpx.Response(200, stream=Stream())
        cancelled.append(True)
        return httpx.Response(200, json={"status": "CANCELLED"})

    async def execute():
        task = asyncio.create_task(
            TinyFishAgentProvider(client(handler)).execute(
                AgentArgs(url="https://example.com", goal="Read title"), Context()
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(execute())
    assert cancelled and closed


def test_agent_write_goal_never_created():
    provider = TinyFishAgentProvider(client(lambda request: pytest.fail("No supplier call allowed")))
    with pytest.raises(ToolError, match="agent_side_effect_not_supported"):
        asyncio.run(provider.execute(AgentArgs(url="https://example.com", goal="Submit form"), Context()))


def test_browser_termination_contract_and_no_fake_confirmation():
    from app.tools.tinyfish.browser import TinyFishBrowserProvider

    def handler(request):
        assert request.method == "DELETE" and str(request.url) == BROWSER + "/br-safe"
        return httpx.Response(204)

    provider = TinyFishBrowserProvider(client(handler))
    assert asyncio.run(provider.terminate_supplier("br-safe")) is True
    provider = TinyFishBrowserProvider(client(handler, tinyfish_browser_delete_supported=False))
    assert asyncio.run(provider.terminate_supplier("br-safe")) is False


def test_browser_controller_typed_actions_no_script():
    from app.tools.tinyfish.browser import BrowserController, BrowserReadArgs, BrowserWriteArgs

    class Page:
        url = "https://example.com"
        first = None

        def __init__(self):
            self.first = self
            self.filled = None

        def locator(self, selector):
            return self

        async def get_attribute(self, name):
            return "password" if name == "type" else ""

        async def inner_text(self, **kwargs):
            return "public text"

        async def fill(self, value, **kwargs):
            self.filled = value

    session = SimpleNamespace(page=Page(), active=True, allow_write=False)
    context = Context()
    assert (
        asyncio.run(
            BrowserController().execute(
                session, BrowserReadArgs(session_id="s", action="read"), context.resolver
            )
        )
        == "public text"
    )
    with pytest.raises(ToolError, match="credentials_not_supported"):
        asyncio.run(
            BrowserController().execute(
                session,
                BrowserWriteArgs(session_id="s", action="type", selector="#password", text="do not fill"),
                context.resolver,
            )
        )
    assert session.page.filled is None


def test_unknown_search_pricing_fails_before_network():
    provider = TinyFishSearchProvider(
        client(lambda request: pytest.fail("No call"), tinyfish_search_fetch_free=False)
    )
    with pytest.raises(ToolError, match="pricing_not_confirmed"):
        asyncio.run(provider.execute(SearchArgs(query="docs"), Context()))


def test_browser_start_guards_ownership_duplicate_and_cleanup():
    from app.tools.tinyfish.browser import BrowserStartArgs, TinyFishBrowserProvider

    class Page:
        url = "about:blank"

        async def goto(self, url, **kwargs):
            self.url = url

    class BrowserContext:
        pages = [Page()]

        async def route(self, pattern, handler):
            self.guard = handler

        async def route_web_socket(self, pattern, handler):
            self.socket_guard = handler

    class Runtime:
        contexts = [BrowserContext()]

        def __init__(self):
            self.chromium = self
            self.closed = False

        async def start(self):
            return self

        async def connect_over_cdp(self, url, **kwargs):
            return self

        async def new_context(self, **kwargs):
            assert kwargs == {"service_workers": "block", "accept_downloads": False}
            return self.contexts[0]

        async def close(self):
            self.closed = True

        async def stop(self):
            pass

    runtime = Runtime()
    requests = []

    def handler(request):
        requests.append(request.method)
        if request.method == "POST":
            assert "url" not in json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "session_id": "br_1",
                    "cdp_url": "wss://example.com/private",
                    "base_url": "https://example.com",
                },
            )
        return httpx.Response(204)

    context = Context()
    context.user_id = "owner"
    context.run_id = "nonexistent"
    provider = TinyFishBrowserProvider(client(handler), lambda: runtime)

    async def run():
        result = await provider.execute(BrowserStartArgs(url="https://example.com"), context)
        assert "cdp" not in str(result) and "/private" not in str(result)
        with pytest.raises(ToolError, match="not_found"):
            provider.owned("br_1", "other")
        with pytest.raises(ToolError, match="browser_already_active"):
            await provider.execute(BrowserStartArgs(url="https://example.com"), context)

        class Route:
            request = SimpleNamespace(url="http://127.0.0.1", method="GET")
            aborted = False

            async def abort(self):
                self.aborted = True

            async def continue_(self):
                pytest.fail("Private URL must be blocked")

            async def fallback(self):
                pytest.fail("Private URL must be blocked")

        route = Route()
        await runtime.contexts[0].guard(route)
        assert route.aborted
        result = await provider.stop("br_1", "owner")
        assert result["supplier_stop_confirmed"] and runtime.closed and not provider.sessions

    asyncio.run(run())
    assert requests == ["POST", "DELETE"]


def test_agent_timeout_cancels_stream():
    cancelled = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"type":"STARTED","run_id":"run_timeout"}\n\n'
            await asyncio.sleep(10)

        async def aclose(self):
            return None

    def handler(request):
        if request.url.path.endswith("run-sse"):
            return httpx.Response(200, stream=Stream())
        cancelled.append(request.url.path)
        return httpx.Response(200, json={"status": "CANCELLED"})

    context = Context()
    context.settings.agent_max_runtime = 1
    context.limits = ToolLimits(max_seconds=1)
    with pytest.raises(ToolError, match="provider_timeout"):
        asyncio.run(
            TinyFishAgentProvider(client(handler)).execute(
                AgentArgs(url="https://example.com", goal="Read title"), context
            )
        )
    assert cancelled and cancelled[-1].endswith("/cancel")


def test_agent_stops_when_local_max_steps_reached():
    cancelled = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"type":"STARTED","run_id":"run_steps","num_of_steps":0}\n\n'
            yield b'data: {"type":"PROGRESS","run_id":"run_steps","num_of_steps":3}\n\n'
            yield b'data: {"type":"PROGRESS","run_id":"run_steps","num_of_steps":4}\n\n'
            await asyncio.sleep(10)

        async def aclose(self):
            return None

    def handler(request):
        if request.url.path.endswith("run-sse"):
            return httpx.Response(200, stream=Stream())
        cancelled.append(request.url.path)
        return httpx.Response(200, json={"status": "CANCELLED"})

    context = Context()
    context.settings.agent_max_steps = 3
    context.settings.agent_run_budget = 1.0
    with pytest.raises(ToolError, match="run_budget"):
        asyncio.run(
            TinyFishAgentProvider(client(handler)).execute(
                AgentArgs(url="https://example.com", goal="Read title"), context
            )
        )
    assert cancelled and cancelled[-1].endswith("/cancel")


def test_potential_side_effect_goal_never_reaches_provider():
    provider = TinyFishAgentProvider(client(lambda request: pytest.fail("No supplier call allowed")))
    with pytest.raises(ToolError, match="agent_side_effect_not_supported"):
        asyncio.run(
            provider.execute(
                AgentArgs(url="https://example.com", goal="Click the documentation link"),
                Context(),
            )
        )
