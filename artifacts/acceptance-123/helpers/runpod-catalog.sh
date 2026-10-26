set -a
. /etc/alex-gateway/runpod.env
set +a
/opt/alex-gateway/venv/bin/python - <<'PY'
import json
import os
import urllib.parse
import urllib.request

key = os.environ["RUNPOD_API_KEY"].strip()
query = {"include": "AVAILABILITY", "product": "POD", "count": 1, "minCudaVersion": "12.1"}
request = urllib.request.Request(
    "https://api.runpod.io/v2/catalog/gpus?" + urllib.parse.urlencode(query),
    headers={"Authorization": "Bearer " + key},
)
data = json.load(urllib.request.urlopen(request, timeout=30))
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
PY
