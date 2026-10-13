#!/bin/bash
# One short, real GPU acceptance for the reserve runtime, run *inside* the GPU Pod.
#
# It uses nothing but the image and the mounted reserve volume: the production launch script starts
# llama.cpp from /workspace, the alias is awaited on the internal port, one short completion is
# requested and the answer is printed. Every step reports a CANALLA_G_* marker so the operator can
# read the outcome from the log stream, and the Pod is deleted by the caller afterwards.
set -u

echo CANALLA_G_START
echo "CANALLA_G_KERNEL $(uname -srm)"

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader || true
else
  echo CANALLA_G_NO_NVSMI
fi

for path in /workspace/llama.cpp/build/bin/llama-server /workspace/start-llm.sh /workspace/check-llm.sh; do
  if [ -f "$path" ]; then
    echo "CANALLA_G_PRESENT $path"
  else
    echo "CANALLA_G_MISSING $path"
  fi
done

# The production launch script probes readiness with curl, so the image must have it; install the
# official package if it somehow does not, and say so either way.
if ! command -v curl >/dev/null 2>&1; then
  echo CANALLA_G_NO_CURL
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq >/dev/null 2>&1 || true
    apt-get install -y -qq curl ca-certificates >/dev/null 2>&1 || true
  fi
  if command -v curl >/dev/null 2>&1; then echo CANALLA_G_CURL_INSTALLED; else echo CANALLA_G_CURL_UNAVAILABLE; fi
fi

started=$(date -u +%s)
nohup bash /workspace/start-llm.sh > /tmp/canalla-start.log 2>&1 &
echo CANALLA_G_STARTED

ready=0
i=0
while [ "$i" -lt 90 ]; do
  i=$((i + 1))
  if curl -sf http://127.0.0.1:8080/v1/models >/dev/null 2>&1; then
    ready=1
    echo "CANALLA_G_READY_SECONDS $(( $(date -u +%s) - started ))"
    break
  fi
  sleep 4
done

if [ "$ready" -ne 1 ]; then
  echo CANALLA_G_NOT_READY
  echo "--- start-llm.log ---"; tail -n 40 /tmp/canalla-start.log 2>/dev/null || true
  echo "--- llama-server.log ---"; tail -n 40 /workspace/llama-server.log 2>/dev/null || true
  echo CANALLA_G_DONE
  exit 1
fi

echo "CANALLA_G_MODELS $(curl -s http://127.0.0.1:8080/v1/models | tr -d '\n' | head -c 400)"

python3 - <<'PY'
import json
import time
import urllib.request

payload = {
    "model": "orcarouter-qwen38-27b-q5km",
    "messages": [{"role": "user", "content": "Ответь одним коротким предложением: кто ты?"}],
    "max_tokens": 64,
    "temperature": 0.2,
}
started = time.time()
request = urllib.request.Request(
    "http://127.0.0.1:8080/v1/chat/completions",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(request, timeout=240) as response:
        data = json.load(response)
    text = (data["choices"][0]["message"]["content"] or "").strip().replace("\n", " ")
    print("CANALLA_G_ANSWER " + text[:400])
    print("CANALLA_G_GEN_SECONDS %.1f" % (time.time() - started))
except Exception as error:  # noqa: BLE001 - the marker is the report
    print("CANALLA_G_GEN_FAILED %s: %s" % (type(error).__name__, str(error)[:200]))
PY

if command -v nvidia-smi >/dev/null 2>&1; then
  echo "CANALLA_G_VRAM $(nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader || true)"
fi
echo "--- llama-server.log (tail) ---"
tail -n 6 /workspace/llama-server.log 2>/dev/null || true
echo CANALLA_G_DONE
