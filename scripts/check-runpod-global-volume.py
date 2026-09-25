"""Is a RunPod Global Volume reachable from a *supported* automation surface yet?

This is the gate for one architecture decision, so it is a script and not a paragraph: the day the
provider exposes global volumes to the public API, this fails differently and the answer changes.

It answers, from the live contract rather than from our own client:

* does `GET https://api.runpod.io/v2/openapi.json` mention a global volume anywhere (path, schema,
  field, enum value)? It reads the whole document, so a new endpoint or a new mount kind is seen
  without us having to know where to look;
* what does the live `Mounts` schema actually allow (today: `persistent` XOR `network`, one of each
  at most), and what does `VolumeType` allow (today: STANDARD / HIGH_PERFORMANCE);
* what does `TemplateMounts` allow — a template that cannot carry storage cannot carry a volume of
  any kind, which is what closes the `templateId` bridge;
* is GraphQL introspection available (it is not), and does the CLI reference have a global-volume
  command (it does not).

Exit code 0 means "a supported automation path exists — re-read the report before building on it",
1 means "not exposed yet", 2 means "could not read the live contract". No credentials are used,
none are read, and nothing is created: the only network call is the public OpenAPI document.

    python scripts/check-runpod-global-volume.py
    python scripts/check-runpod-global-volume.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

OPENAPI = "https://api.runpod.io/v2/openapi.json"
GRAPHQL = "https://api.runpod.io/graphql"
CLI_DOC = "https://docs.runpod.io/runpodctl/reference/runpodctl-network-volume.md"
# The provider sits behind a CDN that refuses a bare library user agent (HTTP 403, `error code:
# 1010`), so the fetches identify themselves. No cookie, token or credential is ever sent.
USER_AGENT = "canalla-llm/1.2.0 (global-volume capability check)"
# The words a newly exposed global volume would have to use somewhere in the contract, in either
# spelling. A single match is enough to send a human back to the schema.
MARKERS = (
    "globalvolume",
    "global-volume",
    "global_volume",
    "storagevolume",
    "storage-volume",
)


def fetch(url: str, *, data: bytes | None = None) -> tuple[int, bytes]:
    headers = {"User-Agent": USER_AGENT}
    if data:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return 0, str(error).encode()


def properties(schemas: dict, name: str) -> dict:
    """Every property of a schema, including the ones an ``allOf`` composition brings in."""
    schema = schemas.get(name) or {}
    found = dict(schema.get("properties") or {})
    for part in schema.get("allOf") or []:
        ref = part.get("$ref")
        if ref:
            found.update(
                schemas.get(ref.rsplit("/", 1)[-1], {}).get("properties") or {}
            )
        found.update(part.get("properties") or {})
    return found


def inspect(spec: dict, introspection: str, cli: str) -> dict:
    schemas = (spec.get("components") or {}).get("schemas") or {}
    paths = spec.get("paths") or {}
    raw = json.dumps(spec).lower()
    markers = {marker: raw.count(marker) for marker in MARKERS}
    mounts = properties(schemas, "Mounts")
    template_mounts = properties(schemas, "TemplateMounts")
    volume_types = ((schemas.get("VolumeType") or {}).get("enum")) or []
    pod_paths = sorted(path for path in paths if path.endswith("/pods"))
    return {
        "openapi": spec.get("openapi"),
        "api_version": (spec.get("info") or {}).get("version"),
        "path_count": len(paths),
        "volume_paths": sorted(
            path
            for path in paths
            if "volume" in path.lower() or "storage" in path.lower()
        ),
        "pod_paths": pod_paths,
        "markers": markers,
        "mount_kinds": sorted(mounts),
        "mount_network_max_items": (
            (mounts.get("network") or {}).get("items", {}).get("maxItems")
            or (mounts.get("network") or {}).get("maxItems")
        ),
        "template_mount_kinds": sorted(template_mounts),
        "template_rejects_network": "network" not in template_mounts,
        "volume_types": volume_types,
        "global_volume_enum": any(
            "GLOBAL" in str(value).upper() for value in volume_types
        ),
        "graphql_introspection_blocked": "INTROSPECTION_DISABLED" in introspection
        or "not allowed" in introspection.lower(),
        "cli_has_global_volume_command": bool(
            re.search(r"global[-_ ]?volume", cli, re.IGNORECASE)
        ),
    }


def verdict(report: dict) -> tuple[bool, str]:
    """Whether a *supported* automation path exists, with the sentence that says why."""
    if (
        any(count for count in report["markers"].values())
        or report["global_volume_enum"]
    ):
        return (
            True,
            "the live contract mentions a global volume — inspect it before building",
        )
    if "network" in report["template_mount_kinds"]:
        return (
            True,
            "templates accept a network mount now — the templateId bridge needs a retest",
        )
    missing = [
        "REST v2 has no global-volume path, schema or enum value",
        "no global-volume CLI command",
    ]
    detail = "; ".join(missing)
    if report["template_rejects_network"]:
        detail += "; templates reject a network mount (so they cannot carry any volume)"
    return False, f"not exposed yet: {detail}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="the raw findings, for a report"
    )
    args = parser.parse_args(argv)

    status, body = fetch(OPENAPI)
    if status != 200:
        print(
            f"could not read {OPENAPI}: HTTP {status} {body[:200]!r}", file=sys.stderr
        )
        return 2
    spec = json.loads(body)
    _, introspection = fetch(
        GRAPHQL, data=b'{"query":"{ __schema { queryType { name } } }"}'
    )
    _, cli = fetch(CLI_DOC)

    report = inspect(
        spec, introspection.decode(errors="replace"), cli.decode(errors="replace")
    )
    supported, sentence = verdict(report)

    if args.json:
        print(
            json.dumps({**report, "supported": supported}, ensure_ascii=False, indent=2)
        )
    else:
        print(
            f"contract: {report['openapi']} · api v{report['api_version']} · {report['path_count']} paths"
        )
        print(f"volume paths: {report['volume_paths']}")
        print(
            f"mount kinds: {report['mount_kinds']} · template mounts: {report['template_mount_kinds']}"
        )
        print(f"volume types: {report['volume_types']}")
        print(f"global-volume markers: {report['markers']}")
        print(
            f"graphql introspection blocked: {report['graphql_introspection_blocked']}"
        )
        print(f"CLI global-volume command: {report['cli_has_global_volume_command']}")
        print(f"SUPPORTED AUTOMATION: {'YES' if supported else 'NO'} — {sentence}")
    return 0 if supported else 1


if __name__ == "__main__":
    raise SystemExit(main())
