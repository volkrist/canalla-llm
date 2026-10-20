set -a
. /etc/alex-gateway/runpod.env
set +a
/opt/alex-gateway/venv/bin/python - <<'PY'
import json
import os
import urllib.request

key = os.environ["RUNPOD_API_KEY"].strip()
query = '{"query":"query { myself { clientBalance currentSpendPerHr pods { id name desiredStatus } } }"}'
request = urllib.request.Request(
    "https://api.runpod.io/graphql",
    data=query.encode("utf-8"),
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=20) as response:
    data = json.load(response).get("data", {}).get("myself", {})
print("balance_usd", data.get("clientBalance"))
print("current_spend_per_hr", data.get("currentSpendPerHr"))
print("pods", [(pod.get("id"), pod.get("name"), pod.get("desiredStatus")) for pod in data.get("pods") or []])
PY
