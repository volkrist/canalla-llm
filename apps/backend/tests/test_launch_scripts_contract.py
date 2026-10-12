"""The launch contract the reserve volume must satisfy is the product's own, not a new one.

``artifacts/runpod-tx3-watcher/start-llm.sh`` and ``check-llm.sh`` are the measured runtime scripts.
This test pins the parts that must agree with the product's own code (the model path the runtime
refuses to start without) and with the documented serving contract (context window, alias, port), so
a future edit to either side fails loudly instead of silently diverging.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ARTIFACTS = REPO / "artifacts" / "runpod-tx3-watcher"
START = (ARTIFACTS / "start-llm.sh").read_text(encoding="utf-8")
CHECK = (ARTIFACTS / "check-llm.sh").read_text(encoding="utf-8")
RUNTIME = (REPO / "apps" / "backend" / "app" / "compute" / "remote_runtime.py").read_text(
    encoding="utf-8"
)

MODEL_PATH = "/workspace/models/orcarouter-qwen38/orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf"
SERVER_PATH = "/workspace/llama.cpp/build/bin/llama-server"
ALIAS = "orcarouter-qwen38-27b-q5km"


def test_the_scripts_exist_where_the_repo_keeps_them():
    assert (ARTIFACTS / "start-llm.sh").is_file()
    assert (ARTIFACTS / "check-llm.sh").is_file()


def test_the_model_path_is_the_one_the_runtime_refuses_to_start_without():
    assert MODEL_PATH in START
    assert MODEL_PATH in RUNTIME


def test_the_cli_contract_matches_the_documented_serving_contract():
    assert SERVER_PATH in START
    assert f"--alias {ALIAS}" in START
    assert "--ctx-size 32768" in START  # == LLM_CONTEXT_WINDOW default
    assert "--parallel 1" in START
    assert "--host 127.0.0.1" in START
    assert "--port 8080" in START


def test_the_health_probe_is_the_alias_endpoint_on_the_internal_port():
    assert "127.0.0.1:8080/v1/models" in START
    assert "127.0.0.1:8080/v1/models" in CHECK
