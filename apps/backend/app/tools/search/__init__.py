"""Provider-agnostic web search and fetch for the agent tool surface."""

from .chain import SearchOutcome, adapters_for, free_search, search_sources
from .provider import WebFetchProvider, WebSearchProvider
from .providers import ADAPTERS, AdapterError, Hit, SearchAdapter

__all__ = [
    "ADAPTERS",
    "AdapterError",
    "Hit",
    "SearchAdapter",
    "SearchOutcome",
    "WebFetchProvider",
    "WebSearchProvider",
    "adapters_for",
    "free_search",
    "search_sources",
]
