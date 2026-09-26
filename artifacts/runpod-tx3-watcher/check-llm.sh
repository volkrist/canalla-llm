#!/usr/bin/env bash

echo "=== GPU ==="
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader

echo
echo "=== LLM ==="

if curl -sf http://127.0.0.1:8080/v1/models; then
    echo
    echo "STATUS: READY"
else
    echo "STATUS: NOT RUNNING"
fi
