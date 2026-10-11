"""A09: multi-placement reconciliation and failover — one Pod across every allowed placement.

The model lives on a Network Volume, and RunPod scopes a regional Volume to one datacenter, so a
second placement needs its *own* Volume and a verified copy of the model there (see
``app.compute.replicas``). This file proves the behaviour that makes a second region safe without
ever paying for two GPUs at once, all against the fake provider and a controllable clock:

* the primary region has no capacity, the secondary has it: exactly one Pod is created;
* a repeat ``ensure`` reuses the same Pod, and a Gateway restart finds it again — never a second;
* a lost create response whose Pod appears only after a consistency lag is recovered, not
  duplicated (invariant: provider create count == 1);
* the recorded placement identity (Volume, datacenter) and the actual rate survive the restart;
* more than one *managed* Pod is reported and left alone, never deleted.

Nothing here creates, resumes or stops a paid resource.
"""

from __future__ import annotations

import json

import httpx
from conftest import auth_header, enroll, ensure_body, run

from gateway.compute import ComputeAuthority
from gateway.provider import provider_api


def ensure(client, headers, **body):
    response = client.post("/compute/ensure", json=ensure_body(**body), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def status(client, headers):
    response = client.get("/compute/status", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def configure_secondary(gateway):
    """A second placement with its own Volume and a verified copy of the model."""
    gateway.settings.runpod_datacenters = "CA-MTL-1:vol-secondary"
    gateway.settings.runpod_replicas = json.dumps(
        {
            "replicas": [
                {
                    "volume_id": "vol-secondary",
                    "datacenter": "CA-MTL-1",
                    "model_id": gateway.settings.llm_model,
                    "model_path": "/workspace/models/orcarouter-qwen38/model.gguf",
                    "model_sha256": "a" * 64,
                    "model_bytes": 20_000_000_000,
                    "runtime_version": "",
                    "bootstrap_version": gateway.settings.version,
                    "last_verified": "2026-09-25T10:00:00Z",
                    "ready": True,
                }
            ]
        }
    )
    # The primary region is out of stock; the secondary (with the Volume) has capacity.
    gateway.runpod.datacenters = [("US-TX-3", "NONE"), ("CA-MTL-1", "HIGH")]
    gateway.runpod.price = 0.48


def restart(gateway) -> ComputeAuthority:
    """A brand-new authority over the same database — a real Gateway restart."""
    api = provider_api(gateway.settings, httpx.MockTransport(gateway.runpod.handle))
    authority = ComputeAuthority(gateway.settings, api=api, sessions=gateway.sessions)
    authority.transport = httpx.MockTransport(gateway.llama.handle)
    authority.clock = lambda: gateway.runpod.time
    return authority


def test_secondary_placement_failover_survives_a_restart(gateway, client):
    """Primary unavailable → secondary available → one Pod → ready → reuse → restart → stop."""
    configure_secondary(gateway)
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)

    body = ensure(
        client, headers, operation_id="op-mr-failover-01", max_hourly_price=1.20, session_budget=5.00
    )
    assert body["state"] == "starting_pod"
    assert len(gateway.runpod.pods) == 1
    assert gateway.runpod.creates[0]["dataCenterIds"] == ["CA-MTL-1"]
    assert gateway.runpod.creates[0]["mounts"]["network"][0]["volumeId"] == "vol-secondary"
    session_id = body["session"]["id"]
    assert (body["session"]["datacenter"], body["session"]["volume_id"]) == ("CA-MTL-1", "vol-secondary")
    assert float(body["session"]["hourly_rate_usd"]) > 0

    # The model becomes ready (still one Pod).
    run(gateway.authority.tick())
    assert status(client, headers)["state"] == "ready"

    # A second ensure for the same intent reuses the same Pod.
    again = ensure(client, headers, operation_id="op-mr-failover-02", task_id="task-2", max_hourly_price=1.20)
    assert again["session"]["id"] == session_id
    assert len(gateway.runpod.creates) == 1

    # A Gateway restart finds the same Pod in the secondary placement — never a second.
    restarted = restart(gateway)
    run(restarted.tick())
    assert len(gateway.runpod.creates) == 1
    assert len(gateway.runpod.pods) == 1
    body = status(client, headers)
    assert body["state"] in {"ready", "loading_model"}
    assert body["session"]["id"] == session_id
    # The placement identity and the actual rate survived the restart.
    assert (body["session"]["datacenter"], body["session"]["volume_id"]) == ("CA-MTL-1", "vol-secondary")
    assert float(body["session"]["hourly_rate_usd"]) > 0

    # Stop continues to work after the restart: the GPU goes away.
    stop = client.post("/compute/stop", json={"operation_id": "op-mr-failover-03"}, headers=headers)
    assert stop.json()["state"] == "stopped"
    assert gateway.runpod.pods[0]["status"] == "TERMINATED"


def test_a_lost_secondary_create_is_recovered_not_duplicated(gateway, client):
    """Create on the secondary succeeded but the response was lost: the Pod appears later.

    The first provider read is momentarily empty (a consistency lag, not evidence of absence), the
    Pod then becomes visible and is recovered by its durable identity. Invariant: create count == 1
    throughout, and Stop leaves no billing Pod.
    """
    configure_secondary(gateway)
    gateway.runpod.create_after_timeout = True
    gateway.runpod.create_failure = "timeout"
    # Two empty reads: the ensure's own reconcile, then the first observation tick. The Pod only
    # becomes visible on the third read.
    gateway.runpod.list_empty_reads = 2
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)

    body = ensure(
        client, headers, operation_id="op-mr-lost-000001", max_hourly_price=1.20, session_budget=5.00
    )
    assert body["state"] == "create_unknown"
    assert len(gateway.runpod.creates) == 1
    assert len(gateway.runpod.pods) == 1  # the Pod was created; only the answer was lost

    # First reconcile: the list is empty. The intent is kept, nothing is created.
    run(gateway.authority.tick())
    assert len(gateway.runpod.creates) == 1
    assert status(client, headers)["state"] == "create_unknown"

    # The Pod appears in the secondary placement: ownership is restored, still one create.
    run(gateway.authority.tick())
    assert len(gateway.runpod.creates) == 1
    body = status(client, headers)
    assert body["state"] in {"starting_pod", "loading_model"}
    assert body["session"]["managed"] is True
    assert (body["session"]["datacenter"], body["session"]["volume_id"]) == ("CA-MTL-1", "vol-secondary")

    # A restart still finds it and does not duplicate it.
    restarted = restart(gateway)
    run(restarted.tick())
    assert len(gateway.runpod.creates) == 1

    stop = client.post("/compute/stop", json={"operation_id": "op-mr-lost-stop1"}, headers=headers)
    assert stop.json()["state"] == "stopped"
    assert gateway.runpod.pods[0]["status"] == "TERMINATED"


def test_two_managed_pods_across_placements_are_reported_and_left_alone(gateway, client):
    """The one-Pod invariant is deployment-wide: two managed Pods are reported, never deleted."""
    configure_secondary(gateway)
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.attached_pod(
        "alex-gw-aaa", pod_id="pod-a", mounts={"network": [{"volumeId": "uwgeaie5b0", "path": "/workspace"}]}
    )
    gateway.runpod.attached_pod(
        "alex-gw-bbb",
        pod_id="pod-b",
        mounts={"network": [{"volumeId": "vol-secondary", "path": "/workspace"}]},
    )

    body = ensure(client, headers, operation_id="op-mr-multi-000001", max_hourly_price=1.20)

    assert body["state"] == "multiple_compute"
    assert body["ai"] == "error"
    assert gateway.runpod.creates == []
    assert gateway.runpod.actions == []


def test_an_unmanaged_pod_in_a_secondary_placement_is_never_deleted(gateway, client):
    """A Pod Alex Cloud did not create is reported as external, in any placement, and left alone."""
    configure_secondary(gateway)
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.runpod.attached_pod(
        "someone-elses-pod",
        pod_id="pod-ext",
        mounts={"network": [{"volumeId": "vol-secondary", "path": "/workspace"}]},
    )

    body = ensure(client, headers, operation_id="op-mr-ext-0000001", max_hourly_price=1.20)
    assert body["state"] == "external_compute"
    assert body["session"]["managed"] is False
    assert gateway.runpod.creates == []

    stop = client.post("/compute/stop", json={"operation_id": "op-mr-ext-stop01"}, headers=headers)
    assert stop.status_code == 409
    assert stop.json()["code"] == "external_compute"
    assert gateway.runpod.actions == []
