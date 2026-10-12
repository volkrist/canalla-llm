"""The launch contract the reserve volume must satisfy is the product's own, not a new one.

``artifacts/runpod-tx3-watcher/start-llm.sh`` and ``check-llm.sh`` are the measured runtime scripts.
This test pins the parts that must agree with the product's own code (the model path the runtime
refuses to start without) and with the documented serving contract (context window, alias, port), so
a future edit to either side fails loudly instead of silently diverging.
"""

import json
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[3]
ARTIFACTS = REPO / "artifacts" / "runpod-tx3-watcher"
START = (ARTIFACTS / "start-llm.sh").read_text(encoding="utf-8")
CHECK = (ARTIFACTS / "check-llm.sh").read_text(encoding="utf-8")
RUNTIME = (REPO / "apps" / "backend" / "app" / "compute" / "remote_runtime.py").read_text(encoding="utf-8")
RUNTIME_PIN = json.loads((REPO / "scripts" / "llama-runtime.json").read_text(encoding="utf-8"))

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


def test_the_runtime_pin_stages_the_launcher_where_the_scripts_call_it():
    """The reserve runtime is staged exactly where the measured start-llm.sh looks for it."""
    assert RUNTIME_PIN["server_path"] == SERVER_PATH
    assert RUNTIME_PIN["target_dir"] == str(PurePosixPath(SERVER_PATH).parent)


def test_the_runtime_pin_repeats_the_scripts_cli_contract():
    """One contract, two files: the pin and the script must describe the same launch."""
    contract = RUNTIME_PIN["contract"]
    assert contract["model"] == MODEL_PATH
    assert MODEL_PATH in START
    assert contract["alias"] == ALIAS
    assert f"--alias {ALIAS}" in START
    assert f"--ctx-size {contract['ctx_size']}" in START
    assert f"--parallel {contract['parallel']}" in START
    assert f"--flash-attn {contract['flash_attn']}" in START
    assert f"--port {contract['port']}" in START
    assert f"--host {contract['host']}" in START
    for flag in contract["required_flags"]:
        assert flag in START, flag


def test_the_runtime_pin_matches_the_gpu_image_it_will_run_in():
    """The pin must be the build the container image is: a mismatch would not load at all."""
    assert RUNTIME_PIN["platform"] == "linux-x86_64"
    # runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404 with runpod_min_cuda_version 12.8
    assert RUNTIME_PIN["cuda"] == "12.8"
    assert RUNTIME_PIN["contract"]["jinja"] is True
    assert "--jinja" in START


def test_the_runtime_pin_is_content_addressed():
    """Every asset is pinned by an official url, a byte count and a full SHA256 digest."""
    assert RUNTIME_PIN["tag"]
    assert len(RUNTIME_PIN["commit"]) == 40
    assert RUNTIME_PIN["assets"]
    for key, entry in RUNTIME_PIN["assets"].items():
        assert str(entry["url"]).startswith("https://github.com/ggml-org/llama.cpp/releases/download/"), key
        assert int(entry["bytes"]) > 0, key
        assert len(str(entry["sha256"])) == 64, key
        assert entry["archive_root"], key
    assert RUNTIME_PIN["assets"]["bin"]["archive_root"] == f"llama-{RUNTIME_PIN['tag']}"
