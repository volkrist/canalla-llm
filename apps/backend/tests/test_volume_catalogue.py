"""The volume/datacenter compatibility read that gates every GPU quote.

US-MO-1's datacenter catalogue answers ``networkVolumeTypes: []`` while a STANDARD volume
demonstrably exists there. Treating that empty list as a refusal turned a usable placement into
``volume_mismatch``; the run then ended as an interrupted stream with no Pod and no task. These
tests pin the three cases apart: silence, an explicit omission, and a datacenter mismatch.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.compute.runpod_api import RunPodAPI, RunPodError
from app.config import Settings


def api_for(datacenter: str, *, volume_dc: str, volume_type: str, types) -> RunPodAPI:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/network-volumes/" in path:
            return httpx.Response(
                200,
                json={
                    "id": "volume-test",
                    "name": "storage",
                    "size": 50,
                    "dataCenter": volume_dc,
                    "type": volume_type,
                },
            )
        if "/catalog/datacenters/" in path:
            payload = {} if types is None else {"networkVolumeTypes": types}
            return httpx.Response(200, json=payload)
        raise AssertionError(f"unexpected request {request.method} {path}")

    settings = Settings(
        app_env="test",
        jwt_secret="x" * 48,
        runpod_api_key="test-only-fake-key",
        runpod_network_volume_id="volume-test",
        runpod_datacenter=datacenter,
    )
    return RunPodAPI(settings, transport=httpx.MockTransport(handler))


def test_an_empty_catalogue_is_silence_not_a_refusal():
    """The real US-MO-1 answer. The volume exists there, so the quote must proceed."""

    async def scenario():
        volume = await api_for(
            "US-MO-1", volume_dc="US-MO-1", volume_type="STANDARD", types=[]
        ).volume()
        assert volume.dataCenter == "US-MO-1"
        assert volume.type == "STANDARD"

    asyncio.run(scenario())


def test_a_missing_type_list_is_also_silence():
    async def scenario():
        volume = await api_for(
            "US-MO-1", volume_dc="US-MO-1", volume_type="STANDARD", types=None
        ).volume()
        assert volume.dataCenter == "US-MO-1"

    asyncio.run(scenario())


def test_an_explicit_list_that_omits_the_type_is_refused():
    """A datacenter that names its supported storage and leaves ours out is a real refusal."""

    async def scenario():
        with pytest.raises(RunPodError) as caught:
            await api_for(
                "US-MO-1", volume_dc="US-MO-1", volume_type="STANDARD", types=["HIGH_PERFORMANCE"]
            ).volume()
        assert caught.value.code == "volume_mismatch"

    asyncio.run(scenario())


def test_a_volume_in_another_datacenter_is_refused_even_with_silence():
    """A regional volume is its own datacenter: silence never widens that."""

    async def scenario():
        with pytest.raises(RunPodError) as caught:
            await api_for(
                "US-MO-1", volume_dc="US-TX-3", volume_type="STANDARD", types=[]
            ).volume()
        assert caught.value.code == "volume_mismatch"

    asyncio.run(scenario())
