"""Gateway test harness.

All provider traffic is intercepted by ``httpx.MockTransport``: no test in this suite can
create, resume or stop a paid resource, and none of them talks to RunPod. The suite runs
under the shared backend virtualenv (the Gateway reuses the backend provider core), using
plain ``asyncio.run`` wrappers because the repository's pytest setup has no async plugin.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import sessionmaker

from gateway.config import GatewaySettings
from gateway.database import Base, make_engine
from gateway.main import create_app
from gateway.security import activate_code, hash_secret

FAKE_KEY = "test-only-fake-runpod-key"
POD_KEY = "pod-gateway-key-" + "k" * 32
ALIAS = "orcarouter-qwen38-27b-q5km"


def sse(payload: dict) -> bytes:
    """SSE frame builder: the upstream answers real UTF-8, so bytes are encoded here."""
    return ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")


def run(coro):
    """Run one coroutine. The repository's pytest has no async plugin by design."""
    return asyncio.run(coro)


class FakeRunPod:
    """Minimal RunPod REST v2 + read-only GraphQL double, shaped like the real responses."""

    def __init__(self, *, balance="12.34", price=0.48):
        # The cheapest selectable fake GPU must fit the product's default policy ($0.52/h),
        # otherwise every automatic-mode test would legitimately stop at "searching".
        self.time = datetime(2026, 9, 21, tzinfo=timezone.utc)
        self.balance = balance
        self.price = price
        self.availability = None  # None = as listed; a string overrides every row (capacity tests)
        self.pods: list[dict] = []
        self.creates: list[dict] = []
        self.actions: list[dict] = []
        self.queries: list[str] = []
        self.create_failure = None
        self.list_failure = None
        self.balance_failure = None
        # Termination can be made to fail for the D-9 watchdog tests: a status code, or
        # "timeout" for a transport error. ``action_failures`` fails that many calls, so a
        # transient refusal followed by a success can be proven in one test.
        self.action_failure = None
        self.action_failures = 0
        self.volume_types = ["STANDARD"]
        # ---- allocation-walk knobs. Every default keeps the historical single-placement
        # catalogue, so no existing test changes behaviour because these exist.
        self.datacenters = None  # None = one US-TX-3 row per GPU, as before
        self.community = False  # advertise the second cloud tier (with its own price)
        self.community_price = None
        self.refuse_gpus: set[str] = set()  # creates refused for these GPU ids (400/404/409)
        self.refuse_status = 400
        self.create_refusal = None  # status code refusing every create (a "no capacity" host)
        self.create_delay_seconds = 0  # mock seconds a create "takes" before answering
        self.create_timeouts: list[float | None] = []  # httpx timeout of every create call
        self.catalog_timeouts: list[float | None] = []

    def _centers(self, availability: str) -> list[dict]:
        if self.datacenters is None:
            return [{"id": "US-TX-3", "availability": self.availability or availability}]
        return [
            {"id": dc, "availability": stock if self.availability is None else self.availability}
            for dc, stock in self.datacenters
        ]

    @staticmethod
    def _timeout_of(request: httpx.Request):
        value = request.extensions.get("timeout")
        if isinstance(value, dict):
            return max(value.values()) if value else None
        return value if isinstance(value, (int, float)) else None

    # ------------------------------------------------------------------ fake pod model

    def pod(self, name: str, *, mounts=None, status="RUNNING", cost=None, pod_id="pod-1", started=None):
        return {
            "id": pod_id,
            "name": name,
            "status": status,
            "cost": self.price if cost is None else cost,
            "startedAt": (started or self.time).isoformat(),
            "dataCenterId": "US-TX-3",
            "mounts": mounts or {"network": [{"volumeId": "uwgeaie5b0", "path": "/workspace"}]},
            "gpu": {"id": "NVIDIA L40S", "count": 1},
        }

    def attached_pod(self, name="alex-gw-existing", **kwargs):
        pod = self.pod(name, **kwargs)
        self.pods.append(pod)
        return pod

    # ------------------------------------------------------------------------- handler

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.runpod.io" and request.url.path == "/graphql":
            self.queries.append(json.loads(request.content)["query"])
            if self.balance_failure:
                return httpx.Response(self.balance_failure, json={"errors": [{"message": "nope"}]})
            return httpx.Response(
                200,
                json={
                    "data": {
                        "myself": {
                            "id": "user-1",
                            "clientBalance": self.balance,
                            "currentSpendPerHr": "0.79",
                        }
                    }
                },
            )
        assert request.headers["Authorization"] == "Bearer " + FAKE_KEY
        path = request.url.path
        if path.endswith("/catalog/gpus"):
            self.catalog_timeouts.append(self._timeout_of(request))
            return httpx.Response(
                200,
                json={
                    "gpus": [
                        {
                            "id": gid,
                            "name": gid,
                            "manufacturer": "NVIDIA",
                            "secure": True,
                            "community": self.community,
                            "memory": memory,
                            "price": {
                                "secure": price,
                                "community": self.community_price
                                if self.community_price is not None
                                else price / 2,
                            },
                            "dataCenters": self._centers(availability),
                        }
                        for gid, memory, price, availability in [
                            ("gpu-cheap-unavailable", 48, 0.4, "NONE"),
                            ("gpu-small", 24, 0.3, "HIGH"),
                            ("gpu-48", 48, self.price, "LOW"),
                            ("NVIDIA L40S", 48, self.price if self.price > 1.20 else 1.09, "HIGH"),
                            ("gpu-80", 80, 1.60, "HIGH"),
                        ]
                    ]
                },
            )
        if "/network-volumes/" in path:
            return httpx.Response(
                200,
                json={
                    "id": "uwgeaie5b0",
                    "name": "storage",
                    "size": 50,
                    "dataCenter": "US-TX-3",
                    "type": "STANDARD",
                },
            )
        if "/catalog/datacenters/" in path:
            return httpx.Response(200, json={"networkVolumeTypes": self.volume_types})
        if path.endswith("/pods") and request.method == "POST":
            body = json.loads(request.content)
            self.creates.append(body)
            self.create_timeouts.append(self._timeout_of(request))
            if self.create_delay_seconds:
                self.time += timedelta(seconds=self.create_delay_seconds)
            if self.create_failure == "timeout":
                raise httpx.ReadTimeout("upstream detail must never surface")
            if self.create_failure == "server":
                return httpx.Response(503, json={"detail": "provider internal detail"})
            if isinstance(self.create_failure, int):
                return httpx.Response(self.create_failure, json={"detail": "private provider detail"})
            if self.create_refusal or body["gpu"]["id"] in self.refuse_gpus:
                # A host that cannot place this candidate: nothing is created, no Pod exists.
                return httpx.Response(
                    self.create_refusal or self.refuse_status,
                    json={"detail": "no capacity for this placement"},
                )
            cost = self.price
            if body.get("cloud") == "COMMUNITY" and self.community_price is not None:
                cost = self.community_price
            pod = self.pod(body["name"], mounts=body["mounts"], pod_id=f"pod-{len(self.pods) + 1}", cost=cost)
            pod["dataCenterId"] = body["dataCenterIds"][0]
            self.pods.append(pod)
            return httpx.Response(201, json=pod)
        if path.endswith("/action"):
            self.actions.append(json.loads(request.content))
            if self.action_failures > 0:
                self.action_failures -= 1
                if self.action_failure == "timeout":
                    raise httpx.ReadTimeout("provider detail must never surface")
                return httpx.Response(self.action_failure or 503, json={"detail": "provider internal detail"})
            if self.pods:
                self.pods[0]["status"] = "TERMINATED"
            return httpx.Response(204)
        if path.endswith("/pods") and request.method == "GET":
            if self.list_failure:
                return httpx.Response(self.list_failure, json={"detail": "provider internal detail"})
            return httpx.Response(200, json={"pods": self.pods})
        if "/pods/" in path and request.method == "GET":
            wanted = path.rsplit("/", 1)[-1]
            found = next((pod for pod in self.pods if str(pod.get("id")) == wanted), None)
            if found is None:
                # A terminated or vanished Pod answers 404, which the provider maps to `not_found`.
                return httpx.Response(404, json={"detail": "pod not found"})
            return httpx.Response(200, json=found)
        if "/billing/pods" in path:
            return httpx.Response(200, json={"records": []})
        raise AssertionError(f"Unexpected provider request {request.method} {path}")


class FakeLlama:
    """Upstream model gateway double for the inference proxy.

    ``gate`` (an ``asyncio.Event``) lets a test hold the *second* SSE delta open and prove
    that the proxy forwards the first one before the upstream answer is complete.
    """

    def __init__(self):
        self.requests: list[dict] = []
        self.stream_started = False
        self.stream_closed = False
        self.failure = None
        self.expected_key = POD_KEY
        self.gate: asyncio.Event | None = None
        self.pending_second_chunk = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != "Bearer " + self.expected_key:
            return httpx.Response(401)
        if request.url.path.endswith("/v1/models"):
            return httpx.Response(200, json={"object": "list", "data": [{"id": ALIAS, "object": "model"}]})
        body = json.loads(request.content)
        self.requests.append(body)
        if self.failure:
            return httpx.Response(self.failure, json={"detail": "internal upstream detail"})
        if body.get("stream"):
            self.stream_started = True
            owner = self
            gate = self.gate

            async def events():
                try:
                    yield sse({"choices": [{"delta": {"content": "Привет"}}]})
                    if gate is not None:
                        owner.pending_second_chunk = True
                        await gate.wait()
                        owner.pending_second_chunk = False
                    yield sse({"choices": [{"delta": {"content": ", мир"}}]})
                    yield b"data: [DONE]\n\n"
                finally:
                    owner.stream_closed = True

            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=events())
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "model": ALIAS,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "Привет, мир"}}],
            },
        )


@pytest.fixture
def gateway(tmp_path):
    """A fully wired gateway app on a private SQLite database with fake providers."""
    database_url = "sqlite:///" + (tmp_path / "gateway.db").as_posix()
    settings = GatewaySettings(
        app_env="test",
        database_url=database_url,
        jwt_secret="gateway-test-secret-" + "s" * 40,
        jwt_expire_minutes=15,
        runpod_api_key=SecretStr(FAKE_KEY),
        llm_provider="llamacpp",
        llm_api_key=POD_KEY,
        llm_model=ALIAS,
    )
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    runpod = FakeRunPod()
    llama = FakeLlama()

    class Wiring:
        def __init__(self, app, authority, proxy, sessions, settings, runpod, llama):
            self.app = app
            self.authority = authority
            self.proxy = proxy
            self.sessions = sessions
            self.settings = settings
            self.runpod = runpod
            self.llama = llama

        @property
        def control(self):
            from gateway.models import GatewayCompute

            with self.sessions() as db:
                return db.get(GatewayCompute, 1)

        @property
        def sessions_rows(self):
            from gateway.models import GatewaySession

            with self.sessions() as db:
                return db.query(GatewaySession).order_by(GatewaySession.created_at).all()

        def set_provider_key(self, value: str):
            self.settings.runpod_api_key = SecretStr(value)

    from gateway.provider import provider_api

    api = provider_api(settings, httpx.MockTransport(runpod.handle))
    app = create_app(settings, api=api, sessions=sessions, background=False)
    # The inference proxy and the readiness probe talk to the Pod with the same double.
    app.state.proxy.transport = httpx.MockTransport(llama.handle)
    authority = app.state.authority
    authority.transport = httpx.MockTransport(llama.handle)
    # One controllable clock for the whole wiring: compute, balance and timeouts.
    authority.clock = lambda: runpod.time
    authority.balance.clock = lambda: runpod.time
    wiring = Wiring(app, authority, app.state.proxy, sessions, settings, runpod, llama)
    return wiring


@pytest.fixture
def client(gateway):
    with TestClient(gateway.app) as client:
        yield client


def enroll(gateway, client, *, label="PC A", ttl=60) -> dict:
    """Create an activation code server-side and enroll one installation through HTTP."""
    with gateway.sessions() as db:
        code, digest = activate_code(db, "", ttl_minutes=ttl, label=label)
    response = client.post(
        "/enroll",
        json={"activation_code": code, "name": label, "platform": "windows", "client_version": "0.9.3"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    payload["activation_code"] = code
    payload["activation_digest"] = digest
    return payload


def token_for(client, installation) -> str:
    response = client.post(
        "/auth/token",
        json={
            "installation_id": installation["installation_id"],
            "installation_secret": installation["installation_secret"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth_header(client, installation) -> dict:
    return {"Authorization": "Bearer " + token_for(client, installation)}


def make_existing_installation(gateway, name="PC B"):
    """An installation inserted directly, for tests that need two without enrollment."""
    from gateway.models import Installation

    secret = "direct-" + "d" * 40
    with gateway.sessions() as db:
        row = Installation(
            name=name,
            platform="windows",
            client_version="0.9.3",
            secret_hash=hash_secret(secret),
            created_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
            meta={},
        )
        db.add(row)
        db.commit()
        installation_id = row.id
    return {"installation_id": installation_id, "installation_secret": secret}


def ensure_body(operation_id="op-0000000000001", **extra):
    body = {"operation_id": operation_id}
    body.update(extra)
    return body


def money(value) -> Decimal:
    return Decimal(str(value))


async def wait_for(predicate, timeout=3.0):
    """Poll until ``predicate()`` is true; keeps cancellation tests deterministic."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()
