"""Read-only: which datacenters could host a *secondary* replica of this model?

The question a second placement has to answer, from the provider's own catalogue and nothing else:

* Secure Cloud — the tier the model's Network Volume requires;
* Network Volume support — a datacenter that cannot create one cannot hold a replica
  (``GET /v2/catalog/datacenters`` → ``networkVolumeTypes``);
* a compatible NVIDIA card: VRAM ≥ the model floor, price ≤ the operator's own $/hour ceiling;
* **meaningful current availability** — ``availability == NONE`` is not a candidate, however cheap.

It prints every datacenter with its compatible cards, then the ranking that decides the choice:
bookable cards first, then the cheapest, then the widest choice. Nothing is created, nothing is
bought, and no price is written down by hand — every number comes from the live catalogue.

    python scripts/secondary-dc-discovery.py
    python scripts/secondary-dc-discovery.py --max-price 2.00 --min-vram 48 --json
"""

from __future__ import annotations

import argparse
import json
import sys

from runpod_api_tools import call, key_or_exit

USABLE_STOCK = {"LOW", "MEDIUM", "HIGH"}
# Datacenter-level attributes the secondary placement needs beyond "it exists".
ACCEPTED_TIERS = {"SECURE"}


def gpu_rows(key: str) -> list[dict]:
    """The live POD catalogue with per-datacenter availability, as the provider returns it."""
    status, payload = call(
        "/v2/catalog/gpus?include=AVAILABILITY&product=POD&count=1", key
    )
    if status != 200:
        raise SystemExit(f"catalog/gpus → HTTP {status}: {str(payload)[:300]}")
    rows = payload.get("gpus") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise SystemExit(f"catalog/gpus returned no rows: {str(payload)[:300]}")
    return rows


def datacenter_rows(key: str) -> dict:
    status, payload = call("/v2/catalog/datacenters", key)
    rows = payload.get("dataCenters") if isinstance(payload, dict) else payload
    if status != 200 or not isinstance(rows, list):
        raise SystemExit(f"catalog/datacenters → HTTP {status}: {str(payload)[:300]}")
    return {str(row.get("id")): row for row in rows if isinstance(row, dict)}


def price_of(row: dict) -> float | None:
    prices = row.get("price") or {}
    for tier in ACCEPTED_TIERS:
        value = prices.get(tier.lower())
        if value:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def compatible(row: dict, *, min_vram: int, max_price: float) -> bool:
    memory = row.get("memory") or row.get("vramGb") or 0
    try:
        vram = int(memory)
    except (TypeError, ValueError):
        return False
    price = price_of(row)
    return vram >= min_vram and price is not None and price <= max_price


def per_datacenter(
    rows: list[dict], *, min_vram: int, max_price: float
) -> dict[str, list[dict]]:
    """Compatible, bookable cards grouped by datacenter, cheapest first.

    The catalogue carries a *per-datacenter* ``dataCenters`` list (``{id, availability}``) next to
    an overall ``availability``; only the per-datacenter value decides whether a card can actually
    be booked in a given place, which is exactly the question a second placement asks.
    """
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        if not compatible(row, min_vram=min_vram, max_price=max_price):
            continue
        price = price_of(row)
        for center in row.get("dataCenters") or []:
            if not isinstance(center, dict) or not center.get("id"):
                continue
            stock = str(center.get("availability") or "NONE").upper()
            grouped.setdefault(str(center["id"]), []).append(
                {
                    "gpu": row.get("id") or row.get("name"),
                    "name": row.get("name") or row.get("id"),
                    "vram_gb": int(row.get("memory") or 0),
                    "price_per_hour": price,
                    "availability": stock,
                    "bookable": stock in USABLE_STOCK,
                }
            )
    for cards in grouped.values():
        cards.sort(
            key=lambda card: (not card["bookable"], card["price_per_hour"], card["gpu"])
        )
    return grouped


def rank(grouped: dict[str, list[dict]], centers: dict, *, exclude: str) -> list[dict]:
    """The decision order: a bookable card wins over a cheaper one nobody can book today."""
    ranking = []
    for datacenter, cards in grouped.items():
        if datacenter == exclude:
            continue
        center = centers.get(datacenter) or {}
        volumes = center.get("networkVolumeTypes") or []
        bookable = [card for card in cards if card["bookable"]]
        ranking.append(
            {
                "datacenter": datacenter,
                "network_volume_types": volumes,
                "supports_network_volume": bool(volumes),
                "cards": cards,
                "bookable_cards": len(bookable),
                "cheapest_bookable": bookable[0]["price_per_hour"]
                if bookable
                else None,
                "cheapest_any": min(card["price_per_hour"] for card in cards)
                if cards
                else None,
            }
        )
    ranking.sort(
        key=lambda item: (
            not item["supports_network_volume"],
            item["bookable_cards"] == 0,
            item["cheapest_bookable"] if item["cheapest_bookable"] is not None else 1e9,
            -(item["bookable_cards"]),
            item["datacenter"],
        )
    )
    return ranking


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-vram", type=int, default=48, help="the model's VRAM floor"
    )
    parser.add_argument(
        "--max-price", type=float, default=2.00, help="the operator's own $/hour"
    )
    parser.add_argument("--exclude", default="US-TX-3", help="the primary datacenter")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    key = key_or_exit()
    rows = gpu_rows(key)
    centers = datacenter_rows(key)
    grouped = per_datacenter(rows, min_vram=args.min_vram, max_price=args.max_price)
    ranking = rank(grouped, centers, exclude=args.exclude)

    if args.json:
        print(
            json.dumps(
                {"ranking": ranking, "centers": len(centers)},
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0

    print(f"catalogue rows: {len(rows)} · datacenters: {len(centers)}")
    print(
        f"filter: SECURE Cloud, VRAM >= {args.min_vram} GB, price <= ${args.max_price:.2f}/h"
    )
    print(f"primary (excluded from ranking): {args.exclude}")
    for item in ranking:
        mark = (
            "CANDIDATE"
            if item["bookable_cards"] and item["supports_network_volume"]
            else "-"
        )
        print(
            f"\n[{mark}] {item['datacenter']} · volume types={item['network_volume_types']} · "
            f"bookable={item['bookable_cards']} · cheapest bookable="
            f"{item['cheapest_bookable'] if item['cheapest_bookable'] is not None else '—'}"
        )
        for card in item["cards"][:6]:
            print(
                f"    {card['name'][:34]:34} {card['vram_gb']:>3} GB "
                f"${card['price_per_hour']:.2f}/h  stock={card['availability']}"
                f"{'  BOOKABLE' if card['bookable'] else ''}"
            )
    useful = [
        item
        for item in ranking
        if item["bookable_cards"] and item["supports_network_volume"]
    ]
    print("\n=== best secondary datacenter")
    if not useful:
        print(
            "NONE — no datacenter other than the primary has a compatible, bookable card"
        )
        return 3
    best = useful[0]
    print(json.dumps(best, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
