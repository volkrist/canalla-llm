"""Explicit opt-in only: ALEX_LIVE_WEB=1; preconfigured backend credential."""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values
from pydantic import SecretStr

from app.config import get_settings
from app.tools.tinyfish.client import TinyFishClient
from app.tools.tinyfish.web import FetchArgs, SearchArgs, TinyFishFetchProvider, TinyFishSearchProvider


@pytest.mark.live_web
@pytest.mark.skipif(os.environ.get("ALEX_LIVE_WEB") != "1", reason="Live Search/Fetch requires opt-in")
def test_live_search_fetch_only(live_web_key):
    key = live_web_key or dotenv_values(Path(__file__).parents[1] / ".env").get("TINYFISH_API_KEY")
    if not key:
        pytest.skip("Backend TinyFish key is not configured")

    class Credentials:
        def resolve(self, provider):
            return key

    settings = get_settings().model_copy(update={"tinyfish_api_key": SecretStr(key)})
    client = TinyFishClient(Credentials(), settings=settings)
    context = SimpleNamespace(resolver=None)

    async def run():
        search = await TinyFishSearchProvider(client).execute(
            SearchArgs(query="official Python documentation", top_results=2), context
        )
        assert search.sources
        fetch = await TinyFishFetchProvider(client).execute(
            FetchArgs(urls=["https://docs.python.org/3/"]), context
        )
        assert fetch.sources and fetch.sources[0]["excerpt"]

    asyncio.run(run())
