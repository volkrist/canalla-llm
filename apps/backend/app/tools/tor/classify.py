"""Official onion mappings are provenance hints only — never a search index."""

from urllib.parse import urlsplit

from .urls import is_onion

OFFICIAL_AND_REACHABLE = "OFFICIAL_AND_REACHABLE"
REACHABLE_UNVERIFIED = "REACHABLE_UNVERIFIED"
OFFICIAL_UNREACHABLE = "OFFICIAL_UNREACHABLE"


def official_pairs(settings):
    rows = []
    for item in getattr(settings, "tor_official_mapping", None) or []:
        if isinstance(item, dict) and item.get("onion") and item.get("clearnet"):
            rows.append(
                {
                    "name": str(item.get("name") or ""),
                    "onion": urlsplit(item["onion"]).hostname or item["onion"],
                    "clearnet": urlsplit(item["clearnet"]).hostname or item["clearnet"],
                }
            )
    return rows


def classify_authority(url: str, reachable: bool, settings) -> str:
    host = (urlsplit(url).hostname or "").rstrip(".").lower()
    official = False
    for pair in official_pairs(settings):
        if host in {pair["onion"].lower(), pair["clearnet"].lower()}:
            official = True
            break
    if official and reachable:
        return OFFICIAL_AND_REACHABLE
    if official and not reachable:
        return OFFICIAL_UNREACHABLE
    if reachable:
        return REACHABLE_UNVERIFIED
    return OFFICIAL_UNREACHABLE if is_onion(host) and official else REACHABLE_UNVERIFIED
