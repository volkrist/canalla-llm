"""The allocation candidate policy, and direct mode walking it.

Two things are proven here, both deterministically and without a provider:

* ``app.compute.candidates`` — the one place that decides *what* may be booked and in which
  order, shared by direct mode and the Gateway so the two cannot drift apart. It is a pure
  function over a catalogue read: cheapest compatible first, Secure before Community, manual
  selection is a pin, and an empty plan carries the typed reason.
* the direct-mode lifecycle actually *walks* it: a refused placement of the approved card in
  the primary datacenter is followed by the same card in the additively configured one, inside
  one total 60-second budget.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.compute import candidates as policy
from app.compute.controller import RunPodController
from app.compute.runpod_api import RunPodAPI
from app.compute.schemas import ComputePreferences, StartRequest
from app.models import User
from tests.settings_factory import make_settings

SECURE = policy.SECURE
COMMUNITY = policy.COMMUNITY
PRIMARY = policy.Placement(datacenter="US-TX-3", volume_id="uwgeaie5b0")
FALLBACK = policy.Placement(datacenter="US-KS-2", volume_id="vol-peers")
COMMUNITY_PLACEMENT = policy.Placement(datacenter="US-TX-3", volume_id="uwgeaie5b0", community_capable=True)


def offer(gpu_id, *, vram=48, secure_price="0.48", community_price=None, stock=None, centers=None):
    """One catalogue row in the shape ``RunPodAPI.gpu_offers()`` returns."""
    centers = centers or {"US-TX-3": stock or "HIGH"}
    return {
        "id": gpu_id,
        "name": gpu_id,
        "vram_gb": vram,
        "secure": secure_price is not None,
        "community": community_price is not None,
        "price": {
            "secure": None if secure_price is None else Decimal(secure_price),
            "community": None if community_price is None else Decimal(community_price),
        },
        "data_centers": centers,
    }


# --------------------------------------------------------------------- the candidate policy


def test_candidates_are_ordered_cheapest_first_then_by_id():
    plan = policy.build_plan(
        [
            offer("pricey", secure_price="1.20"),
            offer("cheap", secure_price="0.40"),
            offer("same-price-a", secure_price="0.40"),
        ],
        min_vram_gb=48,
        max_hourly_price=Decimal("2"),
        placements=(PRIMARY,),
    )
    assert [item.gpu_id for item in plan.candidates] == ["cheap", "same-price-a", "pricey"]
    assert all(item.cloud == SECURE for item in plan.candidates)
    assert plan.reason is None


def test_a_card_below_the_vram_floor_is_never_offered():
    plan = policy.build_plan(
        [offer("small", vram=24), offer("fits", vram=48)],
        min_vram_gb=48,
        max_hourly_price=Decimal("2"),
        placements=(PRIMARY,),
    )
    assert [item.gpu_id for item in plan.candidates] == ["fits"]


def test_secure_cloud_is_ordered_before_a_cheaper_community_candidate():
    plan = policy.build_plan(
        [offer("card", secure_price="0.50", community_price="0.20")],
        min_vram_gb=48,
        max_hourly_price=Decimal("2"),
        placements=(COMMUNITY_PLACEMENT,),
        tiers=policy.cloud_tiers(allow_community=True),
    )
    assert [(item.cloud, str(item.hourly_rate)) for item in plan.candidates] == [
        (SECURE, "0.50"),
        (COMMUNITY, "0.20"),
    ]


def test_the_community_tier_needs_both_the_tier_and_a_capable_placement():
    row = offer("card", secure_price="0.50", community_price="0.20")
    tiers = policy.cloud_tiers(allow_community=True)
    # The tier is permitted, but no placement can host the Volume from Community Cloud.
    assert [
        item.cloud
        for item in policy.build_plan(
            [row],
            min_vram_gb=48,
            max_hourly_price=Decimal("2"),
            placements=(PRIMARY,),
            tiers=tiers,
        ).candidates
    ] == [SECURE]
    # Secure-only policy: the Community price is never used, even where it would be bookable.
    assert [
        item.cloud
        for item in policy.build_plan(
            [row],
            min_vram_gb=48,
            max_hourly_price=Decimal("2"),
            placements=(COMMUNITY_PLACEMENT,),
            tiers=policy.cloud_tiers(allow_community=False),
        ).candidates
    ] == [SECURE]


def test_a_placement_without_the_volume_stock_is_skipped_not_waited_for():
    plan = policy.build_plan(
        [offer("card", centers={"US-TX-3": "NONE", "US-KS-2": "MEDIUM"})],
        min_vram_gb=48,
        max_hourly_price=Decimal("2"),
        placements=(PRIMARY, FALLBACK),
    )
    assert [(item.gpu_id, item.datacenter, item.volume_id) for item in plan.candidates] == [
        ("card", "US-KS-2", "vol-peers")
    ]


def test_the_plan_is_bounded_by_the_candidate_limit():
    rows = [offer(f"card-{index}", secure_price=f"0.{50 + index}") for index in range(6)]
    plan = policy.build_plan(rows, min_vram_gb=48, max_hourly_price=Decimal("2"), placements=(PRIMARY,))
    assert len(plan.candidates) == policy.MAX_CANDIDATE_ATTEMPTS == 3


def test_a_manual_selection_is_a_pin_and_may_still_change_slot():
    both = {"US-TX-3": "HIGH", "US-KS-2": "LOW"}
    rows = [offer("wanted", centers=both), offer("other", secure_price="0.10", centers=both)]
    plan = policy.build_plan(
        rows,
        min_vram_gb=48,
        max_hourly_price=Decimal("2"),
        placements=(PRIMARY, FALLBACK),
        selection="manual",
        gpu_id="wanted",
    )
    assert [(item.gpu_id, item.datacenter) for item in plan.candidates] == [
        ("wanted", "US-TX-3"),
        ("wanted", "US-KS-2"),
    ]


@pytest.mark.parametrize(
    "rows, expected",
    [
        ([offer("small", vram=24)], "no_compatible_gpu"),
        ([offer("card", stock="NONE")], "gpu_unavailable"),
        ([offer("card", secure_price="2.00")], "price_limit"),
    ],
)
def test_an_empty_plan_carries_the_typed_reason(rows, expected):
    plan = policy.build_plan(rows, min_vram_gb=48, max_hourly_price=Decimal("1.00"), placements=(PRIMARY,))
    assert not plan
    assert plan.reason == expected


def test_the_placement_list_never_drops_a_malformed_entry_silently():
    with pytest.raises(ValueError):
        policy.parse_placements("US-KS-2", primary=PRIMARY)
    with pytest.raises(ValueError):
        policy.parse_placements("US-KS-2:vol-peers:fast", primary=PRIMARY)
    placements = policy.parse_placements("US-KS-2:vol-peers,US-TX-3:uwgeaie5b0:community", primary=PRIMARY)
    assert [(item.datacenter, item.community_capable) for item in placements] == [
        ("US-TX-3", True),
        ("US-KS-2", False),
    ]
    assert policy.cloud_tiers(allow_community=True) == (SECURE, COMMUNITY)
    assert policy.cloud_tiers(allow_community=False) == (SECURE,)


def test_a_refusal_is_classified_for_the_walk():
    from app.compute.runpod_api import RunPodError

    assert policy.create_verdict(RunPodError("placement_rejected", 400)) == "walk"
    assert policy.create_verdict(RunPodError("not_found", 404)) == "walk"
    assert policy.create_verdict(RunPodError("runpod_rate_limit", 429)) == "terminal"
    assert policy.create_verdict(RunPodError("runpod_balance", 402)) == "terminal"
    assert policy.create_verdict(RunPodError("runpod_timeout", 504)) == "ambiguous"
    assert policy.create_verdict(RunPodError("runpod_unavailable", 502)) == "ambiguous"
    assert policy.refusal_reason(RunPodError("not_found", 404)) == "no_compatible_gpu"
    assert policy.refusal_reason(RunPodError("placement_rejected", 400)) == "gpu_unavailable"


# ------------------------------------------------------------- direct mode walking that policy


class Supplier:
    """A provider double whose placements are refused one by one."""

    def __init__(self):
        self.time = datetime(2026, 9, 14, tzinfo=timezone.utc)
        self.pods: list[dict] = []
        self.creates: list[dict] = []
        self.actions: list[dict] = []
        self.price = 0.48
        self.datacenters = {"US-TX-3": "HIGH", "US-KS-2": "HIGH"}
        self.refuse_gpus: set[str] = set()
        self.refuse_datacenters: set[str] = set()
        self.create_delay_seconds = 0
        self.create_timeouts: list[float | None] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert request.url.host == "api.runpod.io"
        assert request.headers["Authorization"] == "Bearer test-only-fake-key"
        if path.endswith("/catalog/gpus"):
            return httpx.Response(
                200,
                json={
                    "gpus": [
                        {
                            "id": gid,
                            "name": gid,
                            "manufacturer": "NVIDIA",
                            "secure": True,
                            "memory": memory,
                            "price": {"secure": self.price if gid == "gpu-48" else 1.19},
                            "dataCenters": [
                                {"id": dc, "availability": stock} for dc, stock in self.datacenters.items()
                            ],
                        }
                        for gid, memory in [("gpu-48", 48), ("NVIDIA L40S", 48)]
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
            return httpx.Response(200, json={"networkVolumeTypes": ["STANDARD"]})
        if path.endswith("/pods") and request.method == "POST":
            body = json.loads(request.content)
            self.creates.append(body)
            timeout = request.extensions.get("timeout")
            self.create_timeouts.append(max(timeout.values()) if isinstance(timeout, dict) else timeout)
            if self.create_delay_seconds:
                self.time += timedelta(seconds=self.create_delay_seconds)
            datacenter = body["dataCenterIds"][0]
            if body["gpu"]["id"] in self.refuse_gpus or datacenter in self.refuse_datacenters:
                return httpx.Response(400, json={"detail": "no capacity for this placement"})
            pod = {
                "id": "pod-test",
                "name": body["name"],
                "status": "RUNNING",
                "cost": self.price,
                "startedAt": self.time.isoformat(),
                "dataCenterId": datacenter,
                "mounts": body["mounts"],
                "gpu": body["gpu"],
            }
            self.pods.append(pod)
            return httpx.Response(201, json=pod)
        if path.endswith("/pods"):
            return httpx.Response(200, json={"pods": self.pods})
        if path.endswith("/action"):
            self.actions.append(json.loads(request.content))
            if self.pods:
                self.pods[0]["status"] = "TERMINATED"
            return httpx.Response(204)
        raise AssertionError(f"Unexpected supplier request {request.method} {path}")


@pytest.fixture
def direct():
    from app.database import SessionLocal

    supplier = Supplier()
    settings = make_settings(
        runpod_api_key=SecretStr("test-only-fake-key"),
        runpod_datacenters="US-KS-2:vol-peers",
    )
    api = RunPodAPI(settings, httpx.MockTransport(supplier.handle))
    controller = RunPodController(settings, api=api, clock=lambda: supplier.time)
    with SessionLocal() as db:
        db.add(User(email="allocation@example.com", password_hash="unused", role="admin"))
        db.commit()
    return controller, supplier


def run_start(controller, *, gpu_id="gpu-48", max_hourly_price="1.20"):
    """Quote, then start exactly as the product does: one approved card, one confirmation."""
    from app.database import SessionLocal

    async def scenario():
        with SessionLocal() as db:
            user = db.query(User).one()
        quote = await controller.search_gpu(user, ComputePreferences(max_hourly_price=max_hourly_price))
        request = StartRequest(
            quote_id=quote["quote_id"], gpu_id=gpu_id, idempotency_key=str(uuid4()), confirmed=True
        )
        return await controller.start_compute(user, request)

    return asyncio.run(scenario())


def test_direct_mode_refused_placement_is_followed_by_the_added_datacenter(direct):
    """The same approved card, refused in the primary datacenter, is placed in the added one."""
    controller, supplier = direct
    supplier.refuse_datacenters = {"US-TX-3"}

    result = run_start(controller)

    assert result["state"] == "starting_pod"
    assert [body["dataCenterIds"] for body in supplier.creates] == [["US-TX-3"], ["US-KS-2"]]
    assert [body["gpu"]["id"] for body in supplier.creates] == ["gpu-48", "gpu-48"]
    assert supplier.creates[1]["mounts"]["network"][0]["volumeId"] == "vol-peers"
    assert len(supplier.pods) == 1


def test_direct_mode_never_places_a_card_in_a_datacenter_without_stock(direct):
    """The walk is bounded by real stock: a placement that cannot book the card is skipped."""
    controller, supplier = direct
    supplier.datacenters = {"US-TX-3": "HIGH", "US-KS-2": "NONE"}
    supplier.refuse_datacenters = {"US-TX-3"}

    result = run_start(controller)

    assert [body["dataCenterIds"] for body in supplier.creates] == [["US-TX-3"]]
    assert supplier.pods == []
    assert supplier.actions == []
    assert result["state"] == "searching"


def test_direct_mode_never_substitutes_a_different_card(direct):
    """Every placement of the approved card refused: no other card is started silently."""
    controller, supplier = direct
    supplier.refuse_gpus = {"gpu-48"}

    result = run_start(controller)

    assert [body["gpu"]["id"] for body in supplier.creates] == ["gpu-48", "gpu-48"]
    assert supplier.pods == []
    assert supplier.actions == []
    assert result["state"] == "searching"


def test_direct_mode_one_candidate_cannot_consume_the_whole_budget(direct):
    """A slow refused candidate ends the walk instead of handing out a second full budget."""
    controller, supplier = direct
    supplier.refuse_datacenters = {"US-TX-3"}
    supplier.create_delay_seconds = policy.ALLOCATION_WINDOW_SECONDS + 1

    result = run_start(controller)

    assert len(supplier.creates) == 1
    assert supplier.pods == []
    assert result["state"] == "searching"
    # One bounded provider call per candidate, never more than what is left of the budget.
    assert max(supplier.create_timeouts) <= policy.CANDIDATE_CALL_TIMEOUT_SECONDS


def test_a_parked_retry_is_never_announced_as_a_transition(direct):
    """A parked retry stays a *failure* on the chip, not a transition: the badge must not lie.

    This is the defect the release user reported as an hour of «Ищем GPU»: `searching` is the
    direct-mode state for "a retry is scheduled", and `llm_public_status` also reported it with
    `search_active=True`, so the badge claimed a live allocation operation for as long as the retry
    kept being rescheduled — `compact_ai` reads exactly that pair as a real transition.

    The scheduled retry is a background availability probe, which the product may keep doing. What
    it may not do is tell the user something is happening while nothing is.
    """
    from app.compute.runtime import compact_ai
    from app.database import SessionLocal

    controller, supplier = direct
    supplier.refuse_gpus = {"gpu-48"}
    run_start(controller)

    with SessionLocal() as db:
        user = db.query(User).one()

    status = controller.get_compute_status(user)
    # The retry is still on the clock — that part of the product is unchanged …
    assert status["state"] == "searching"
    assert status["next_search_at"] is not None
    assert status["error_code"]

    public = controller.llm_public_status(user)
    diagnostic = public["diagnostic"]
    # … and it is not an operation in flight, which is what the chip has to be told.
    assert diagnostic["search_active"] is False
    assert diagnostic["search_deadline"] == status["next_search_at"]

    # The badge itself, derived from exactly what this controller reported: red, not amber.
    ai, _label = compact_ai(
        provider="llamacpp",
        app_env="production",
        configured=True,
        compute_state=status["state"],
        error_code=status["error_code"],
        search_active=diagnostic["search_active"],
    )
    assert ai == "unavailable", ai
