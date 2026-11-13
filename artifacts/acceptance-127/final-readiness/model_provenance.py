"""Provenance matrix + an honest answer to "is a real Qwen available for free?".

Two independent things are recorded here:

* the validator's discrimination, proven by positive and negative tests against the product's own
  recorder (a synthetic trace is never reported as LIVE);
* whether a real local model exists on this machine at all. Ollama is installed but has no models,
  and there is no GGUF or llama.cpp server in the usual roots, so a free real-model run is NOT
  AVAILABLE. Nothing is downloaded.

Run:  python artifacts/acceptance-127/final-readiness/model_provenance.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "apps" / "backend"
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
OUT = Path(__file__).resolve().parent

TESTS = [
    "tests/test_127_provenance.py::test_a_live_chain_with_verification_is_confirmed",
    "tests/test_127_provenance.py::test_a_mock_turn_is_never_confirmed",
    "tests/test_127_provenance.py::test_tools_without_a_model_response_are_a_scripted_planner",
    "tests/test_127_provenance.py::test_an_origin_model_run_without_a_model_response_is_invalid",
    "tests/test_127_provenance.py::test_a_missing_tool_result_is_incomplete",
    "tests/test_127_provenance.py::test_a_tool_call_not_declared_by_the_response_is_invalid",
    "tests/test_127_provenance.py::test_a_tampered_record_fails_the_seal",
    "tests/test_127_provenance.py::test_a_tool_result_without_a_call_is_invalid",
    "tests/test_127_provenance.py::test_the_ledger_is_cross_checked_against_the_journal",
    "tests/test_127_provenance.py::test_the_orchestrator_records_the_chain_for_a_declared_live_adapter",
    "tests/test_127_provenance.py::test_the_same_recording_path_never_confirms_an_unrecognised_adapter",
]


def run_test(node: str) -> dict:
    completed = subprocess.run(
        [str(PYTHON), "-m", "pytest", node, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return {
        "node": node,
        "ok": completed.returncode == 0,
        "summary": " | ".join([line for line in completed.stdout.splitlines() if line.strip()][-2:])[:300],
    }


def local_model_inventory() -> dict:
    inventory: dict = {"ollama_binary": None, "ollama_models": [], "gguf_files": [], "llama_server": []}
    ollama = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    if ollama.is_file():
        inventory["ollama_binary"] = str(ollama)
        try:
            completed = subprocess.run(
                [str(ollama), "list"], capture_output=True, text=True, timeout=60
            )
            inventory["ollama_models"] = [
                line for line in completed.stdout.splitlines()[1:] if line.strip()
            ]
        except (OSError, subprocess.TimeoutExpired):
            inventory["ollama_models"] = []
    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")),
        Path.home() / "Documents",
        Path.home() / ".ollama",
        Path("C:/models"),
    ]
    for root in roots:
        if not root or not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).relative_to(root).parts)
            if depth > 3:
                dirnames[:] = []
                continue
            for name in filenames:
                lower = name.lower()
                if lower.endswith(".gguf"):
                    inventory["gguf_files"].append(str(Path(dirpath) / name))
                if lower in {"llama-server.exe", "llama-cli.exe"}:
                    inventory["llama_server"].append(str(Path(dirpath) / name))
            if len(inventory["gguf_files"]) > 10:
                break
    inventory["available"] = bool(inventory["ollama_models"] or inventory["gguf_files"])
    return inventory


def main() -> int:
    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "classifier_states": [
            "REAL_MODEL_CONFIRMED",
            "MOCK_MODEL",
            "SCRIPTED_PLANNER",
            "INCOMPLETE_TRACE",
            "INVALID_PROVENANCE",
        ],
        "recording_rules": {
            "seal": "HMAC-SHA256 over the canonical payload with the deployment secret",
            "live_requires": "provider in {llamacpp, canalla-cloud} AND runtime in {llama.cpp, gateway}",
            "result_requires": "executed=true, non-empty result digest, matching arguments digest",
            "verification_requires": "VERIFY_PASSED with evidence_ok=true",
            "mock_requires": "provider in MOCK_PROVIDERS or live=false",
        },
    }
    results = [run_test(node) for node in TESTS]
    report["tests"] = results
    report["tests_ok"] = sum(1 for item in results if item["ok"])
    report["tests_total"] = len(results)

    inventory = local_model_inventory()
    report["free_real_model"] = {
        "status": "AVAILABLE" if inventory["available"] else "NOT AVAILABLE",
        "inventory": inventory,
        "note": (
            "No real local Qwen exists on this machine, and downloading multi-gigabyte weights is "
            "out of scope. This is not a project failure: the remote Qwen path is ready and the "
            "LIVE run needs owner approval."
        ),
    }

    report["live_verdict"] = {
        "status": "NOT YET RUN",
        "reason": (
            "No real model turn has been recorded. Every synthetic trace in the tests above is a "
            "validator check, not a LIVE pass, and FULL LIVE AUTONOMY remains NOT VERIFIED."
        ),
    }
    report["verdict"] = "PASS" if report["tests_ok"] == report["tests_total"] else "FAIL"
    (OUT / "model-provenance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "tests": f"{report['tests_ok']}/{report['tests_total']}",
                "free_real_model": report["free_real_model"]["status"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
