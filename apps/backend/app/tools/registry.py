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
    registry.register(
        ToolDefinition(
            "web_search",
            "Find public sources for a query, with dates, domains and language filters.",
            SearchArgs,
            "search",
            RiskLevel.READ_ONLY,
            "free",
            90,
            "tinyfish",
        ),
        TinyFishSearchProvider(),
    )
    registry.register(
        ToolDefinition(
            "web_fetch",
            "Read public URLs as reference text. Use fresh=true for current information.",
            FetchArgs,
            "fetch",
            RiskLevel.READ_ONLY,
            "free",
            90,
            "tinyfish",
        ),
        TinyFishFetchProvider(),
    )
    registry.register(
        ToolDefinition(
            "web_agent_read",
            "Paid multi-step public reading only. Never use for writes, login, submissions or purchases.",
            AgentArgs,
            "agent",
            RiskLevel.READ_ONLY,
            "paid",
            180,
            "tinyfish",
            auto_route=False,
        ),
        TinyFishAgentProvider(),
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
    from .local.provider import register_local_tools
    from .tor.provider import register_tor_tools

    register_tor_tools(registry)
    register_local_tools(registry)
    return registry


def make_orchestrator(registry):
    return ToolOrchestrator(registry, ToolExecutor(registry))
