import asyncio
import json
from datetime import timedelta

import httpx
from test_compute import compute, start  # noqa: F401 — reuse the isolated supplier fixture

from app.compute.controller import RunPodController
from app.compute.runpod_api import RunPodAPI
from app.providers import LlamaCppProvider


def test_dynamic_connection_loading_readiness_recovery_and_stop(compute):  # noqa: F811
    original, supplier, user = compute
    settings = original.settings.model_copy(
        update={"llm_provider": "llamacpp", "llm_api_key": "test-gateway-key-" + "x" * 40}
    )
    controller = RunPodController(
        settings, api=RunPodAPI(settings, httpx.MockTransport(supplier.handle)), clock=original.clock
    )
    loaded = False
    targets = []

    def model(request):
        targets.append(request.url.host)
        assert request.headers["Authorization"] == "Bearer " + settings.llm_api_key
        return (
            httpx.Response(200, json={"data": [{"id": settings.llm_model}]})
            if loaded
            else httpx.Response(503)
        )

    controller.llm = LlamaCppProvider(
        settings, target=controller.connection_target, transport=httpx.MockTransport(model)
    )

    async def scenario():
        nonlocal loaded
        await start(controller, user)
        assert supplier.creates[0]["ports"] == ["9000/http"]
        assert "8080/http" not in supplier.creates[0]["ports"]
        assert supplier.creates[0]["env"]["ALEX_GATEWAY_KEY"] == settings.llm_api_key
        await controller.tick()
        assert controller.get_compute_status(user)["state"] == "loading_model"
        supplier.time += timedelta(minutes=2)
        assert not supplier.actions
        loaded = True
        await controller.tick()
        assert controller.get_compute_status(user)["state"] == "ready"
        restored = RunPodController(settings, api=controller.api, clock=controller.clock)
        restored.llm = LlamaCppProvider(
            settings, target=restored.connection_target, transport=httpx.MockTransport(model)
        )
        await restored.recover()
        assert restored.get_compute_status(user)["state"] == "ready"
        assert len(supplier.creates) == 1
        assert settings.llm_api_key not in json.dumps(restored.get_compute_status(user))
        supplier.pods[0]["status"] = "EXITED"
        await restored.tick()
        assert restored.connection_target() is None
        assert restored.get_compute_status(user)["state"] == "stopped"

    asyncio.run(scenario())
    assert targets and set(targets) == {"pod-test-9000.proxy.runpod.net"}
