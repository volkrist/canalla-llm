import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.config import get_settings
from app.tools.contracts import ToolError
from app.tools.orchestrator import ToolOrchestrator
from app.tools.policy import ToolLimits, computer_planner_tools
from app.tools.registry import make_registry
from app.tools.tinyfish.client import TinyFishClient
from app.tools.tinyfish.web import SearchArgs, TinyFishSearchProvider


class TestCredentials:
    def resolve(self, provider):
        return "test-only-provider-credential"


class Context:
    def __init__(self):
        self.secrets = ("test-only-provider-credential",)
        self.resolver = self.resolve

    async def resolve(self, host):
        return ["93.184.216.34"]


def client(handler):
    return TinyFishClient(
        TestCredentials(),
        httpx.MockTransport(handler),
        get_settings().model_copy(update={"tinyfish_search_interval_seconds": 0}),
    )


def test_a_spent_task_budget_is_not_a_tool_timeout():
    limits = ToolLimits(max_seconds=0)
    with pytest.raises(ToolError, match="task_runtime_limit"):
        limits.consume(
            type("Definition", (), {"capability": "local_fs"})(),
            type("Args", (), {"path": "C:/x"})(),
        )


def test_computer_tools_are_available_without_magic_words():
    allowed = computer_planner_tools("Создай файл hello.txt")
    assert {"read_file", "write_file", "run_python", "run_process"} <= allowed
    assert "run_powershell" not in allowed
    assert "format_volume" not in allowed


def test_planner_offers_process_tools_for_a_generic_computer_prompt():
    context = SimpleNamespace(
        mode="off",
        tor_enabled=False,
        tor_mode="off",
        computer_mode="trusted",
        assigned_device_id=None,
        host_online=False,
        coding_task=False,
        user_prompt="List a local directory and read the file you find.",
        settings=None,
        workspace=None,
    )
    names = {item.name for item in ToolOrchestrator(make_registry(), None).planner_definitions(context)}
    # The installed app is the host; a missing heartbeat must not hide the capability from the
    # model (the executor reports host_offline instead of the model claiming there is no shell).
    assert {"list_directory", "read_file", "run_python", "run_process"} <= names
    assert "run_powershell" not in names


def test_a_transient_timeout_is_retried_for_search():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("temporary", request=request)
        return httpx.Response(
            200,
            json={"results": [{"url": "https://example.com", "title": "Example", "snippet": "ok"}]},
        )

    result = asyncio.run(
        TinyFishSearchProvider(client(handler)).execute(SearchArgs(query="example"), Context())
    )
    assert calls["n"] == 2
    assert result.sources[0]["url"] == "https://example.com"


def test_a_search_with_only_provider_errors_is_failed_not_completed():
    def handler(request):
        return httpx.Response(
            200,
            json={"results": [], "errors": [{"error": "provider details stay out"}]},
        )

    with pytest.raises(ToolError, match="search_failed"):
        asyncio.run(TinyFishSearchProvider(client(handler)).execute(SearchArgs(query="example"), Context()))
