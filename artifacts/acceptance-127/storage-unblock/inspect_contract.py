"""What the live RunPod contract says about storage, read from the public OpenAPI document.

Free and credential-free: the only network call is ``GET /v2/openapi.json``. The output is the
evidence behind ``runpod-storage-capabilities.json`` — paths, mount kinds, the Pod-create storage
fields and the global-volume endpoints, quoted rather than summarised.

    python artifacts/acceptance-127/storage-unblock/inspect_contract.py
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OPENAPI = "https://api.runpod.io/v2/openapi.json"
USER_AGENT = "canalla-llm/1.2.7 (storage contract inspection)"
OUT = Path(__file__).resolve().parent


def fetch() -> dict:
    request = urllib.request.Request(OPENAPI, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read())


def resolve(document: dict, node) -> dict:
    """One dereferenced schema object (one level of ``$ref`` is enough for these)."""
    if isinstance(node, dict) and "$ref" in node:
        name = str(node["$ref"]).rsplit("/", 1)[-1]
        return document.get("components", {}).get("schemas", {}).get(name, {})
    return node if isinstance(node, dict) else {}


def mount_schema(document: dict) -> dict:
    pods = document.get("paths", {}).get("/v2/pods", {})
    post = pods.get("post") or {}
    body = resolve(
        document,
        (post.get("requestBody") or {})
        .get("content", {})
        .get("application/json", {})
        .get("schema"),
    )
    mounts = resolve(document, body.get("properties", {}).get("mounts"))
    return {
        "pod_create_accepts_fields": sorted(body.get("properties", {})),
        "mounts_schema": sorted(mounts.get("properties", {})),
        "mounts_raw": mounts,
        "pod_models": sorted(
            {
                str(item.get("$ref")).rsplit("/", 1)[-1]
                for item in (body.get("oneOf") or body.get("anyOf") or [])
                if isinstance(item, dict) and item.get("$ref")
            }
        ),
        "data_centers_field": "dataCenterIds" in body.get("properties", {}),
    }


def global_volume_paths(document: dict) -> dict:
    paths = {}
    for path, methods in document.get("paths", {}).items():
        if "global-volumes" not in path:
            continue
        paths[path] = {
            method: {
                "summary": str(spec.get("summary") or spec.get("description") or "")[
                    :200
                ],
                "parameters": [
                    p.get("name")
                    for p in spec.get("parameters") or []
                    if isinstance(p, dict)
                ],
                "request_schema": sorted(
                    spec.get("requestBody")
                    or {}.get("content", {})
                    .get("application/json", {})
                    .get("schema", {})
                    .get("properties", {})
                ),
            }
            for method, spec in methods.items()
            if method in {"get", "post", "delete", "put", "patch"}
        }
    return paths


def mount_kind_schema(document: dict) -> dict:
    """The ``Mounts`` schema and whatever the global mount object requires."""
    schemas = document.get("components", {}).get("schemas", {})
    out = {}
    for name in ("Mounts", "GlobalMount", "NetworkMount", "PersistentMount"):
        if name in schemas:
            out[name] = schemas[name]
    return out


def main() -> int:
    document = fetch()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": OPENAPI,
        "read_only": True,
        "openapi": (document.get("openapi") or document.get("info", {}).get("version")),
        "title": document.get("info", {}).get("title"),
        "path_count": len(document.get("paths", {})),
        "volume_paths": sorted(p for p in document.get("paths", {}) if "volume" in p),
        "pod_create": mount_schema(document),
        "global_volume_endpoints": global_volume_paths(document),
        "mount_schemas": mount_kind_schema(document),
    }
    (OUT / "runpod-storage-capabilities.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=1)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
