from .contracts import RiskLevel, ToolDefinition, ToolRegistry
from .executor import ToolExecutor
from .orchestrator import ToolOrchestrator
from .tinyfish.agent import AgentArgs, TinyFishAgentProvider
from .tinyfish.browser import (
    BrowserActionProvider,
    BrowserReadArgs,
    BrowserStartArgs,
    BrowserWriteArgs,
    TinyFishBrowserProvider,
)
from .tinyfish.web import FetchArgs, SearchArgs, TinyFishFetchProvider, TinyFishSearchProvider


def make_registry():
    registry = ToolRegistry()
    for name, description, schema, capability, cost, timeout, provider in (
        (
            "web_search",
            "Find public sources for a query, with dates, domains and language filters.",
            SearchArgs,
            "search",
            "free",
            90,
            TinyFishSearchProvider(),
        ),
        (
            "web_fetch",
            "Read public URLs as reference text. Use fresh=true for current information.",
            FetchArgs,
            "fetch",
            "free",
            90,
            TinyFishFetchProvider(),
        ),
        (
            "web_agent_read",
            "Paid multi-step public reading only. Never use for writes, login, submissions or purchases.",
            AgentArgs,
            "agent",
            "paid",
            180,
            TinyFishAgentProvider(),
        ),
    ):
        registry.register(
            ToolDefinition(
                name, description, schema, capability, RiskLevel.READ_ONLY, cost, timeout, "tinyfish"
            ),
            provider,
        )
    browser = TinyFishBrowserProvider()
    for name, schema, risk, adapter, cost in (
        ("browser_start", BrowserStartArgs, RiskLevel.READ_ONLY, browser, "paid"),
        ("browser_read", BrowserReadArgs, RiskLevel.READ_ONLY, BrowserActionProvider(browser), "free"),
        (
            "browser_write",
            BrowserWriteArgs,
            RiskLevel.EXTERNAL_SIDE_EFFECT,
            BrowserActionProvider(browser),
            "free",
        ),
    ):
        registry.register(
            ToolDefinition(
                name,
                "Advanced typed Browser action; explicit user operation only.",
                schema,
                "browser",
                risk,
                cost,
                180,
                "tinyfish",
                auto_route=False,
            ),
            adapter,
        )
    return registry


def make_orchestrator(registry):
    return ToolOrchestrator(registry, ToolExecutor(registry))
