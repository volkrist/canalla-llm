"""Read-only: the exact availability the allocator sees for the cards that fit."""

import json
import os
import urllib.parse
import urllib.request

KEY = os.environ["RUNPOD_API_KEY"].strip()
BASE = "https://api.runpod.io/v2/catalog/gpus"
QUERY = {
    "include": "AVAILABILITY",
    "product": "POD",
    "count": 1,
    "minCudaVersion": "12.1",
}
request = urllib.request.Request(
    BASE + "?" + urllib.parse.urlencode(QUERY),
    headers={"Authorization": "Bearer " + KEY},
)
with urllib.request.urlopen(request, timeout=30) as response:
    data = json.load(response)

for gpu in data.get("gpus") or []:
    if gpu.get("manufacturer") != "NVIDIA":
        continue
    try:
        memory = int(gpu.get("memory") or 0)
    except (TypeError, ValueError):
        continue
    if memory < 48:
        continue
    print(
        json.dumps(
            {
                "id": gpu.get("id"),
                "name": gpu.get("name"),
                "vram_gb": memory,
                "secure": gpu.get("secure"),
                "community": gpu.get("community"),
                "price": gpu.get("price"),
                "dataCenters": gpu.get("dataCenters"),
            },
            ensure_ascii=False,
        )
    )
