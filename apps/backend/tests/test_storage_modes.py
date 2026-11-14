"""Storage placement: a mount set is derived, not assumed, and a bad one never reaches a Pod.

The failure this prevents is expensive and quiet: the product starts a Pod in a datacenter whose
storage does not hold *this* model, or points llama.cpp at ``/workspace`` while the volume is
mounted at ``/workspace-global``, and the run bills while the model never appears. So every
combination below is decided before the provider call, with one typed verdict.

The global-volume cases are deliberately not "pass": the provider's own documentation says the
mount is object-backed (no file locks, no atomic rename, no permission bits, eventual consistency),
and llama.cpp loading a 20.8 GB GGUF over it through mmap has not been measured. The honest verdict
is ``storage_unverified`` with the checks that must pass named, never ``ready``.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.compute import candidates as policy
from app.compute import storage
from app.compute.schemas import GpuOption

REGIONAL = storage.Attachment(
    mode=storage.REGIONAL_VOLUME, volume_id="uwgeaie5b0", has_model=True, has_runtime=True
)


def global_attachment(volume_id="gvol-1", mount_path="", has_model=True):
    return storage.Attachment(
        mode=storage.GLOBAL_VOLUME, volume_id=volume_id, mount_path=mount_path, has_model=has_model
    )


def test_a_regional_volume_mounts_where_it_always_did():
    plan = storage.resolve((REGIONAL,))
    assert plan.verdict == storage.READY
    assert plan.model_root == "/workspace"
    assert plan.mounts == (storage.Mount("network", "uwgeaie5b0", "/workspace"),)
    assert plan.model_path == "/workspace/" + storage.MODEL_RELATIVE_PATH
    assert plan.runtime_path == "/workspace/" + storage.RUNTIME_RELATIVE_PATH


def test_a_global_volume_alone_takes_the_provider_default_mount():
    plan = storage.resolve((global_attachment(),))
    assert plan.verdict == storage.READY
    assert plan.model_root == "/workspace"
    assert plan.mounts == (storage.Mount("global", "gvol-1", "/workspace"),)


def test_a_global_volume_next_to_a_network_volume_moves_off_workspace():
    roleless = storage.Attachment(mode=storage.REGIONAL_VOLUME, volume_id="uwgeaie5b0", has_runtime=True)
    plan = storage.resolve((roleless, global_attachment()))
    assert plan.verdict == storage.READY
    by_kind = {mount.kind: mount.path for mount in plan.mounts}
    assert by_kind == {"network": "/workspace", "global": "/workspace-global"}
    # The model lives on the global volume in this arrangement, so that is the root.
    assert plan.model_root == "/workspace-global"


def test_an_explicit_global_mount_path_moves_the_network_volume_instead():
    roleless = storage.Attachment(mode=storage.REGIONAL_VOLUME, volume_id="uwgeaie5b0", has_runtime=True)
    plan = storage.resolve((roleless, global_attachment(mount_path="/workspace")))
    by_kind = {mount.kind: mount.path for mount in plan.mounts}
    assert by_kind == {"network": "/workspace-2", "global": "/workspace"}
    assert plan.model_root == "/workspace"


def test_each_volume_may_state_its_own_mount_path():
    roleless = storage.Attachment(mode=storage.REGIONAL_VOLUME, volume_id="v2", mount_path="/data")
    model = storage.Attachment(
        mode=storage.GLOBAL_VOLUME, volume_id="gvol-1", mount_path="/model", has_model=True
    )
    plan = storage.resolve((roleless, model))
    assert {mount.kind: mount.path for mount in plan.mounts} == {"network": "/data", "global": "/model"}
    assert plan.model_path == "/model/" + storage.MODEL_RELATIVE_PATH


def test_two_global_volumes_are_refused_because_the_provider_allows_one():
    plan = storage.resolve((global_attachment("g1"), global_attachment("g2")))
    assert plan.verdict == storage.MULTIPLE_GLOBAL_VOLUMES
    assert not plan


def test_an_unknown_storage_kind_is_refused_instead_of_treated_as_regional():
    plan = storage.resolve((storage.Attachment(mode="quantum", volume_id="v", has_model=True),))
    assert plan.verdict == storage.UNSUPPORTED_KIND
    assert storage.classify("quantum") == storage.UNSUPPORTED_STORAGE
    # ...while the allocation policy fails closed and pins the datacenter for the same value.
    assert policy.Placement(datacenter="US-TX-3", volume_id="v", storage_type="quantum").pins_datacenter


def test_a_kind_without_a_volume_is_refused():
    plan = storage.resolve((storage.Attachment(mode=storage.GLOBAL_VOLUME, has_model=True),))
    assert plan.verdict == storage.NO_STORAGE
    assert plan.reason == "volume_id_missing"


def test_no_attachment_at_all_is_refused():
    assert storage.resolve(()).verdict == storage.NO_STORAGE


def test_a_model_source_must_be_stated():
    plan = storage.resolve((storage.Attachment(mode=storage.REGIONAL_VOLUME, volume_id="v"),))
    assert plan.verdict == storage.MODEL_NOT_FOUND
    assert plan.reason == "no_model_source"


def test_two_model_sources_are_ambiguous_not_a_preference():
    plan = storage.resolve(
        (
            storage.Attachment(mode=storage.REGIONAL_VOLUME, volume_id="v1", has_model=True),
            storage.Attachment(mode=storage.REGIONAL_VOLUME, volume_id="v2", has_model=True),
        )
    )
    assert plan.verdict == storage.AMBIGUOUS_MODEL_SOURCE


def test_a_local_copy_is_not_combined_with_a_mounted_model():
    plan = storage.resolve(
        (
            storage.Attachment(mode=storage.LOCAL_MODEL_COPY, has_model=True),
            storage.Attachment(mode=storage.REGIONAL_VOLUME, volume_id="v"),
        )
    )
    assert plan.verdict == storage.AMBIGUOUS_MODEL_SOURCE


def test_a_local_copy_alone_gets_a_container_path():
    plan = storage.resolve((storage.Attachment(mode=storage.LOCAL_MODEL_COPY, has_model=True),))
    assert plan.verdict == storage.READY
    assert plan.model_root == "/models"
    assert plan.mounts == ()


def test_duplicate_mount_paths_are_a_conflict_not_a_coin_toss():
    plan = storage.resolve(
        (
            storage.Attachment(
                mode=storage.REGIONAL_VOLUME, volume_id="a", mount_path="/data", has_model=True
            ),
            storage.Attachment(mode=storage.REGIONAL_VOLUME, volume_id="b", mount_path="/data"),
        )
    )
    assert plan.verdict == storage.MOUNT_PATH_CONFLICT
    assert plan.reason == "duplicate_mount_path"


@pytest.mark.parametrize("bad", ["workspace", "/workspace/"])
def test_a_relative_or_trailing_slash_mount_path_is_refused(bad):
    plan = storage.resolve(
        (storage.Attachment(mode=storage.REGIONAL_VOLUME, volume_id="v", mount_path=bad, has_model=True),)
    )
    assert plan.verdict == storage.MOUNT_PATH_CONFLICT


def test_the_storage_modes_match_the_allocation_policy_constants():
    assert storage.REGIONAL_VOLUME == policy.REGIONAL_VOLUME
    assert storage.GLOBAL_VOLUME == policy.GLOBAL_VOLUME
    assert storage.STORAGE_MODES == (
        policy.REGIONAL_VOLUME,
        policy.GLOBAL_VOLUME,
        storage.LOCAL_MODEL_COPY,
    )


def test_a_regional_plan_is_the_proven_compatible_path():
    verdict, reasons = storage.check_compatibility(storage.resolve((REGIONAL,)))
    assert verdict == storage.READY
    assert reasons == ()


def test_a_global_plan_with_a_writing_launch_path_is_incompatible():
    """The measured start-llm.sh writes its log and pid next to the model."""
    verdict, reasons = storage.check_compatibility(
        storage.resolve((global_attachment(),)), writes_to_storage=True
    )
    assert verdict == storage.STORAGE_INCOMPATIBLE_RUNTIME
    assert reasons == ("launch_path_writes_to_object_storage",)


def test_a_read_only_global_plan_is_unverified_not_ready():
    verdict, reasons = storage.check_compatibility(
        storage.resolve((global_attachment(),)), writes_to_storage=False
    )
    assert verdict == storage.STORAGE_UNVERIFIED
    assert "mmap_on_object_storage_unmeasured" in reasons
    assert verdict != storage.READY


def test_a_local_copy_needs_room_for_the_model_and_a_reserve():
    plan = storage.resolve((storage.Attachment(mode=storage.LOCAL_MODEL_COPY, has_model=True),))
    verdict, reasons = storage.check_compatibility(plan, model_bytes=20_000, local_free_bytes=21_000)
    assert verdict == storage.INSUFFICIENT_LOCAL_DISK
    assert reasons[0] == "need=22000"

    ok, _ = storage.check_compatibility(plan, model_bytes=20_000, local_free_bytes=30_000)
    assert ok == storage.READY


def test_an_unmountable_plan_keeps_its_own_verdict():
    verdict, reasons = storage.check_compatibility(storage.resolve(()))
    assert verdict == storage.NO_STORAGE
    assert reasons == ("no_volume_requested",)


def test_network_storage_cost_uses_the_measured_rate():
    # 50 GB at $0.07/GB-month is exactly the $3.50/month this account is billed per volume.
    assert storage.monthly_storage_cost(50, storage.NETWORK_STORAGE_PER_GB_MONTH) == Decimal("3.5000")
    assert storage.monthly_storage_cost(100, Decimal("0.05")) == Decimal("5.0000")


def test_global_request_cost_uses_the_documented_classes():
    assert storage.global_request_cost(1000, 1000) == Decimal("0.005500")
    assert storage.global_request_cost(0, 0) == Decimal("0.000000")


def test_the_migration_projection_reports_bounds_not_a_pretend_measurement():
    projection = storage.migration_projection(20_752_787_712, files=6_377)
    assert projection["bytes"] == 20_752_787_712
    assert projection["class_a_requests"] == {"floor": 1, "per_file": 6_377}
    assert "storage rate must be supplied" in projection["note"]


class Recorder:
    def __init__(self):
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({"method": request.method, "url": str(request.url), "body": request.read()})
        return httpx.Response(
            200,
            json={
                "id": "pod-1",
                "name": "canalla-live",
                "status": "RUNNING",
                "cost": 1.09,
                "startedAt": "2026-11-14T00:00:00Z",
            },
        )


def settings_for(**overrides):
    from app.config import Settings

    base = {
        "app_env": "test",
        "jwt_secret": "x" * 48,
        "llm_provider": "mock",
        "runpod_api_key": "k" * 40,
        "runpod_network_volume_id": "uwgeaie5b0",
        "runpod_datacenter": "US-TX-3",
    }
    base.update(overrides)
    return Settings(**base)


def create_pod_body(recorder: Recorder, **settings_overrides):
    import asyncio
    import json

    from app.compute.runpod_api import RunPodAPI

    api = RunPodAPI(settings_for(**settings_overrides), httpx.MockTransport(recorder.handler))
    gpu = GpuOption(
        id="NVIDIA L40S",
        name="L40S",
        vram_gb=48,
        hourly_rate=Decimal("1.09"),
        availability="LOW",
        compatible=True,
        selectable=True,
    )
    pod = asyncio.run(api.create_pod("canalla-live", gpu, datacenter="US-MO-1", volume_id="y92bt25ru8"))
    assert pod.id == "pod-1"
    return json.loads(recorder.requests[0]["body"])


def test_a_regional_pod_still_mounts_one_network_volume_at_workspace():
    recorder = Recorder()
    body = create_pod_body(recorder)
    assert body["mounts"] == {"network": [{"volumeId": "y92bt25ru8", "path": "/workspace"}]}
    assert body["env"]["ALEX_LLM_ROOT"] == "/workspace"
    assert body["env"]["ALEX_LLM_MODEL_PATH"].endswith(storage.MODEL_RELATIVE_PATH)


def test_a_global_pod_mounts_the_global_volume_and_no_network_volume():
    recorder = Recorder()
    body = create_pod_body(recorder, runpod_storage_mode="global_volume", runpod_global_volume_id="gvol-1")
    assert body["mounts"] == {"global": [{"volumeId": "gvol-1", "path": "/workspace"}]}
    assert "network" not in body["mounts"]
    assert body["env"]["ALEX_LLM_ROOT"] == "/workspace"


def test_an_impossible_storage_is_refused_before_the_provider_call():
    from app.compute.runpod_api import RunPodAPI, RunPodError

    recorder = Recorder()
    api = RunPodAPI(settings_for(), httpx.MockTransport(recorder.handler))
    gpu = GpuOption(
        id="NVIDIA L40S",
        name="L40S",
        vram_gb=48,
        hourly_rate=Decimal("1.09"),
        availability="LOW",
        compatible=True,
        selectable=True,
    )
    import asyncio

    with pytest.raises(RunPodError) as error:
        asyncio.run(api.create_pod("canary", gpu, storage_type="global_volume"))
    assert error.value.code == "no_storage"
    assert recorder.requests == []


def test_a_global_storage_mode_without_a_volume_id_is_a_startup_error():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        settings_for(runpod_storage_mode="global_volume")


def test_a_relative_global_mount_path_is_a_startup_error():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        settings_for(
            runpod_storage_mode="global_volume",
            runpod_global_volume_id="gvol-1",
            runpod_global_mount_path="workspace",
        )


def test_the_status_payload_reports_storage_and_its_verdict():
    from app.compute.controller import RunPodController

    controller = RunPodController(settings_for())
    payload = controller.allocation_policy_payload()
    assert payload["storage"]["verdict"] == storage.READY
    assert payload["storage"]["storage_mode"] == storage.REGIONAL_VOLUME
    assert payload["storage"]["compatibility"] == storage.READY
    assert payload["storage"]["mounts"][0]["path"] == "/workspace"

    global_controller = RunPodController(
        settings_for(runpod_storage_mode="global_volume", runpod_global_volume_id="gvol-1")
    )
    global_payload = global_controller.allocation_policy_payload()
    assert global_payload["storage"]["storage_mode"] == storage.GLOBAL_VOLUME
    # The measured launch path writes to the mount, so the verdict is a refusal, not a promise.
    assert global_payload["storage"]["compatibility"] == storage.STORAGE_INCOMPATIBLE_RUNTIME
    assert global_payload["datacenters"][0]["pins_datacenter"] is False
