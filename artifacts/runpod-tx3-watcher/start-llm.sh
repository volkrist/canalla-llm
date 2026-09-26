#!/usr/bin/env bash
set -e

SERVER="/workspace/llama.cpp/build/bin/llama-server"
MODEL="/workspace/models/orcarouter-qwen38/orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf"
LOG="/workspace/llama-server.log"

if curl -sf http://127.0.0.1:8080/v1/models >/dev/null 2>&1; then
    echo "LLM уже запущена."
    exit 0
fi

echo "Запускаю OrcaRouter..."

nohup "$SERVER" \
  --model "$MODEL" \
  --alias orcarouter-qwen38-27b-q5km \
  --n-gpu-layers all \
  --ctx-size 32768 \
  --parallel 1 \
  --flash-attn on \
  --jinja \
  --host 127.0.0.1 \
  --port 8080 \
  > "$LOG" 2>&1 &

echo $! > /workspace/llama-server.pid

echo "Ожидаю загрузку модели..."

for i in $(seq 1 120); do
    if curl -sf http://127.0.0.1:8080/v1/models >/dev/null 2>&1; then
        echo "LLM готова: http://127.0.0.1:8080"
        exit 0
    fi
    sleep 2
done

echo "LLM не запустилась вовремя."
tail -n 50 "$LOG"
exit 1
