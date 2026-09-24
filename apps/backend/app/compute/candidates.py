"""Ordered allocation candidates: the one place that decides *what* may be booked, and in
what order. Direct mode and the Central Gateway both import this module, so the two provider
modes cannot drift into two different allocation policies (the Gateway reuses the backend
package as a library — see ``gateway/provider.py``).

Why this exists
---------------
Canalla sat in «Ищем GPU» for about an hour, more than once, and never obtained a usable
model, because an allocation was pinned to one physical placement: one GPU id, one cloud
tier, one datacenter. A model-required request must evaluate a *set* of compatible
placements and move to the next one the moment a placement answers "no capacity", "allocation
conflict" or a placement failure. This module builds that set from the product's own
configuration — never from guesswork:

* the configured model's runtime requirement (the CUDA floor sent to the catalogue),
* the VRAM floor (``runpod_min_vram_gb``, 48 GB) and the user's own ``min_vram_gb``,
* the user's own money policy (``max_hourly_price``) and ``selection`` (``automatic`` /
  ``manual``),
* the deployment's cloud-tier policy (Secure always; Community only when the operator
  permits it *and* the placement can host the Network Volume where the model lives),
* the placements that can mount the Network Volume, which is what makes a Pod able to serve
  the configured model at all.

Ordering is deliberate and documented: cloud tier first (Secure preferred, then Community),
then price ascending (automatic mode always prefers the cheapest compatible GPU inside the
user's own maximum), then the deployment's placement preference, then the GPU id for a total
order. With a single tier and a single placement this is exactly the historical order
(cheapest first, ties by id), so no existing decision changes.

A ``manual`` selection is a *pin*, never a substitution: the candidate set is the named GPU
only, and it may still be tried in another placement (the same card, a different slot).

Nothing here performs I/O, reads a credential or creates anything: it is a pure function over
a provider catalogue read.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

SECURE = "SECURE"
COMMUNITY = "COMMUNITY"
TIERS = (SECURE, COMMUNITY)

# The provider lets us schedule only on these availability levels; anything else means the
# hardware exists in the catalogue but cannot be booked right now.
USABLE_STOCK = {"LOW", "MEDIUM", "HIGH"}

# The product's total allocation budget, in seconds, and the hard bound on how many placements
# one allocation may *try*. The budget is total, never per candidate. Direct mode uses these
# constants; the Gateway reads the same 60 s bound from ``COMPUTE_SEARCH_TIMEOUT_SECONDS``
# (5..60, server-side) and passes the remaining budget in.
ALLOCATION_WINDOW_SECONDS = 60
MAX_CANDIDATE_ATTEMPTS = 3
# One provider call (catalogue read or create) may never consume more than this, and never more
# than the budget that is left. That is what makes "60 s x N candidates" impossible.
CANDIDATE_CALL_TIMEOUT_SECONDS = 15

# A RunPod Community Cloud Pod cannot mount a Network Volume, and the model lives on one, so a
# placement is only usable from the Community tier when the operator declares that it can host
# the Volume from there. Default: never.
COMMUNITY_BLOCKED_BY_NETWORK_VOLUME = "network_volume"
COMMUNITY_BLOCKED_BY_POLICY = "policy"


@dataclass(frozen=True)
class Placement:
    """A (datacenter, Network Volume) pair that a Pod may be created in.

    The Volume is part of the placement on purpose: the model, the startup scripts and the
    llama.cpp state live on the Network Volume, so a datacenter without that Volume cannot
    serve the configured model. Scheduling is therefore never widened to a datacenter the
    Volume cannot be mounted in.
    """

    datacenter: str
    volume_id: str
    community_capable: bool = False

    def audit(self) -> dict:
        return {
            "datacenter": self.datacenter,
            "volume_id": self.volume_id,
            "community_capable": bool(self.community_capable),
        }


@dataclass(frozen=True)
class AllocationCandidate:
    """One bookable placement of one GPU: the unit the allocator walks."""

    gpu_id: str
    gpu_name: str
    vram_gb: int
    hourly_rate: Decimal
    cloud: str
    datacenter: str
    volume_id: str
    availability: str

    @property
    def key(self) -> str:
        return f"{self.cloud}:{self.datacenter}:{self.gpu_id}"

    def audit(self) -> dict:
        """The non-secret record of one candidate. Never a credential, never a URL."""
        return {
            "gpu": self.gpu_id,
            "vram_gb": int(self.vram_gb),
            "hourly_rate": str(self.hourly_rate),
            "cloud": self.cloud,
            "datacenter": self.datacenter,
            "volume_id": self.volume_id,
            "availability": self.availability,
        }


@dataclass(frozen=True)
class AllocationPlan:
    """The ordered candidate set plus the typed conclusion when it is empty."""

    candidates: tuple[AllocationCandidate, ...]
    reason: str | None
    considered: int

    def __bool__(self) -> bool:  # an empty plan is a concluded search, not a candidate
        return bool(self.candidates)

    def audit(self) -> dict:
        return {
            "planned": len(self.candidates),
            "considered": int(self.considered),
            "reason": self.reason,
            "candidates": [candidate.audit() for candidate in self.candidates],
        }


def parse_placements(spec: str, *, primary: Placement) -> tuple[Placement, ...]:
    """Deployment placements from a ``DC:VOLUME[:community]`` list, primary first.

    The primary placement (the Volume's own datacenter, discovered from the provider) always
    comes first and is never removed: a datacenter without the Network Volume cannot serve the
    model. Extra placements are additive, so an operator can widen scheduling without editing
    code, and a malformed entry fails closed instead of being silently dropped.

    ``community`` declares that this placement can host its Volume from the Community tier too.
    It is a *provider fact* the operator must prove, not a preference: it is never assumed, and
    a repeat of an already-known datacenter only adds that declaration.
    """
    placements = [primary]
    seen = {primary.datacenter: 0}
    for raw in (spec or "").split(","):
        item = raw.strip()
        if not item:
            continue
        parts = [part.strip() for part in item.split(":")]
        if len(parts) not in (2, 3) or not parts[0] or not parts[1]:
            raise ValueError(f"Некорректное размещение «{item}»: ожидается DC:VOLUME[:community]")
        community = len(parts) == 3 and parts[2].lower() == "community"
        if len(parts) == 3 and not community:
            raise ValueError(f"Некорректное размещение «{item}»: третий элемент — только community")
        if parts[0] in seen:
            if community:
                position = seen[parts[0]]
                current = placements[position]
                placements[position] = Placement(current.datacenter, current.volume_id, True)
            continue
        seen[parts[0]] = len(placements)
        placements.append(Placement(datacenter=parts[0], volume_id=parts[1], community_capable=community))
    return tuple(placements)


def validate_placement_spec(spec: str) -> str:
    """Fail-closed syntax check for a deployment's placement list; returns it unchanged.

    Called from both settings classes, so a typo in ``RUNPOD_DATACENTERS`` is a startup error
    rather than a silently ignored entry that would pin the allocator back to one slot.
    """
    parse_placements(spec or "", primary=Placement(datacenter="_validate_", volume_id="_validate_"))
    return spec


def cloud_tiers(*, allow_community: bool) -> tuple[str, ...]:
    """The tier order the allocator may use. Secure is preferred; Community is opt-in."""
    return (SECURE, COMMUNITY) if allow_community else (SECURE,)


def build_plan(
    offers,
    *,
    min_vram_gb: int,
    max_hourly_price: Decimal,
    placements: tuple[Placement, ...],
    tiers: tuple[str, ...] = (SECURE,),
    selection: str = "automatic",
    gpu_id: str | None = None,
    limit: int = MAX_CANDIDATE_ATTEMPTS,
) -> AllocationPlan:
    """The ordered, bounded candidate list for one model-required allocation.

    ``offers`` are the catalogue rows as ``RunPodAPI.gpu_offers()`` returns them. An empty plan
    carries the typed reason: ``no_compatible_gpu`` (nothing fits the model), ``gpu_unavailable``
    (compatible hardware exists but nothing is bookable) or ``price_limit`` (bookable, but above
    the user's own maximum). Those three conclusions are the product's existing vocabulary.
    """
    allowed_tiers = [tier for index, tier in enumerate(tiers) if tier in TIERS and tier not in tiers[:index]]
    if not allowed_tiers:
        allowed_tiers = [SECURE]
    priced: list[tuple[int, AllocationCandidate, int]] = []
    compatible = False
    in_stock = False
    for row in offers:
        # Compatibility is a property of the card against the configured model; bookability is a
        # property of the tier and the placement. Keeping them apart is what lets the empty plan
        # say which one actually failed.
        vram_fits = _vram(row) >= int(min_vram_gb)
        if vram_fits:
            compatible = True
        for tier in allowed_tiers:
            price = _tier_price(row, tier)
            if price is None:
                continue
            if not vram_fits:
                continue
            if selection == "manual" and gpu_id and row.get("id") != gpu_id:
                continue
            for position, placement in enumerate(placements):
                if tier == COMMUNITY and not placement.community_capable:
                    continue
                stock = _stock(row, placement.datacenter)
                if stock not in USABLE_STOCK:
                    continue
                in_stock = True
                if price > max_hourly_price:
                    continue
                priced.append(
                    (
                        allowed_tiers.index(tier),
                        AllocationCandidate(
                            gpu_id=str(row.get("id") or ""),
                            gpu_name=str(row.get("name") or row.get("id") or ""),
                            vram_gb=_vram(row),
                            hourly_rate=price,
                            cloud=tier,
                            datacenter=placement.datacenter,
                            volume_id=placement.volume_id,
                            availability=stock,
                        ),
                        position,
                    )
                )
    ordered: list[AllocationCandidate] = []
    seen: set[str] = set()
    for _, candidate, _ in sorted(
        priced, key=lambda item: (item[0], item[1].hourly_rate, item[2], item[1].gpu_id)
    ):
        if candidate.key in seen:
            continue
        seen.add(candidate.key)
        ordered.append(candidate)
    if ordered:
        return AllocationPlan(tuple(ordered[: max(1, int(limit))]), None, len(offers))
    if not compatible:
        reason = "no_compatible_gpu"
    elif not in_stock:
        reason = "gpu_unavailable"
    else:
        reason = "price_limit"
    return AllocationPlan((), reason, len(offers))


def community_blocked_by(*, allow_community: bool, placements: tuple[Placement, ...]) -> str | None:
    """Why the Community tier is not in play, in the product's own words. ``None`` = it is."""
    if allow_community:
        return None
    if not any(placement.community_capable for placement in placements):
        return COMMUNITY_BLOCKED_BY_NETWORK_VOLUME
    return COMMUNITY_BLOCKED_BY_POLICY


def create_verdict(error) -> str:
    """What a failed create means for the candidate walk: ``walk``, ``ambiguous`` or ``terminal``.

    Only a *definitive refusal of a placement* (nothing was created) may be followed by the next
    candidate. An ambiguous answer — a timeout or a 5xx — must never be, because it may already
    have created a Pod: that is the existing ``create_unknown`` rule, and it is what keeps
    "one Pod" true. Both provider modes classify with this one function.
    """
    code = str(getattr(error, "code", "runpod_unavailable"))
    status = getattr(error, "status", 502) or 502
    if code in {"runpod_timeout", "runpod_unavailable", "malformed_response"} or status >= 500:
        return "ambiguous"
    if code in {"placement_rejected", "gpu_unavailable", "not_found", "runpod_invalid_request"}:
        return "walk"
    if status in {400, 403, 404, 409, 422}:
        return "walk"
    return "terminal"


def refusal_reason(error) -> str:
    """The product code a walked-past refusal ends with: capacity, unless the placement is gone."""
    code = str(getattr(error, "code", "runpod_unavailable"))
    if code in {"not_found", "runpod_invalid_request"}:
        return "no_compatible_gpu"
    return "gpu_unavailable"


def _tier_price(row, tier: str) -> Decimal | None:
    """The price for one cloud tier, or ``None`` when the row cannot be booked that way."""
    if tier == SECURE and not row.get("secure"):
        return None
    if tier == COMMUNITY and not row.get("community"):
        return None
    prices = row.get("price") or {}
    value = prices.get(tier.lower())
    if value is None:
        return None
    try:
        price = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None
    if not price.is_finite() or price <= 0:
        return None
    return price


def _vram(row) -> int:
    try:
        return int(row.get("vram_gb") or 0)
    except (TypeError, ValueError):
        return 0


def _stock(row, datacenter: str) -> str:
    centers = row.get("data_centers") or {}
    value = centers.get(datacenter)
    return str(value) if value else "NONE"
