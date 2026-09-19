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
    WebBrowserArgs,
)
from .tinyfish.web import FetchArgs, SearchArgs, TinyFishFetchProvider, TinyFishSearchProvider


def make_registry():
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "web_search",
            "Find public HTTP URLs. WHEN TO USE: simple lookup. WHEN NOT: .onion, Tor, local files, math.",
            SearchArgs,
            "search",
            RiskLevel.READ,
            "free",
            90,
            "tinyfish",
        ),
        TinyFishSearchProvider(),
    )
    registry.register(
        ToolDefinition(
            "web_fetch",
            "Read 1-3 public URLs as text. WHEN TO USE: after search. WHEN NOT: JS-only pages, Tor, .onion.",
            FetchArgs,
            "fetch",
            RiskLevel.READ,
            "free",
            90,
            "tinyfish",
        ),
        TinyFishFetchProvider(),
    )
    agent = TinyFishAgentProvider()
    registry.register(
        ToolDefinition(
            "web_agent",
            "Paid read-only multi-page web research. WHEN TO USE: several public pages to compare. "
            "WHEN NOT: forms, login, buy, Tor, local files, simple lookup.",
            AgentArgs,
            "agent",
            RiskLevel.READ,
            "paid",
            180,
            "tinyfish",
        ),
        agent,
    )
    registry.register(
        ToolDefinition(
            "web_agent_read",
            "Paid multi-step public reading only. Never use for writes, login, submissions or purchases.",
            AgentArgs,
            "agent",
            RiskLevel.READ,
            "paid",
            180,
            "tinyfish",
            auto_route=False,
        ),
        agent,
    )
    browser = TinyFishBrowserProvider()
    registry.register(
        ToolDefinition(
            "web_browser",
            "Alex-controlled cloud browser. Operations: open, read, links, click L-ids, back, wait, close. "
            "WHEN TO USE: JS page or explicit browser open. WHEN NOT: Tor, .onion, purchases, arbitrary JS.",
            WebBrowserArgs,
            "browser",
            RiskLevel.READ,
            "paid",
            180,
            "tinyfish",
        ),
        browser,
    )
    for name, schema, risk, adapter, cost in (
        ("browser_start", BrowserStartArgs, RiskLevel.READ, browser, "paid"),
        ("browser_read", BrowserReadArgs, RiskLevel.READ, BrowserActionProvider(browser), "free"),
        (
            "browser_write",
            BrowserWriteArgs,
            RiskLevel.SENSITIVE,
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
    from .external import register_external_tools
    from .local.provider import register_local_tools
    from .tor.provider import register_tor_tools

    register_tor_tools(registry)
    register_local_tools(registry)
    register_external_tools(registry)
    return registry


def make_orchestrator(registry):
    return ToolOrchestrator(registry, ToolExecutor(registry))
