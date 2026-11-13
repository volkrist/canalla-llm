"""The model/tool contract, read out of the live application objects.

This is an inspection, not a model run: it imports the product, asks the real registry and the real
provider classes what they offer, and records the invariants a LIVE acceptance depends on. It fails
if a contract that was true at the last release silently changes (a mock marked live, a model
without tool schemas, a completion rule that no longer requires verification, a context window the
frontend could invent).

Run:  python artifacts/acceptance-127/final-readiness/model_contract.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "apps" / "backend"
OUT = Path(__file__).resolve().parent

work = Path(tempfile.mkdtemp(prefix="canalla-contract-"))
os.environ.update(
    {
        "DATABASE_URL": "sqlite:///" + (work / "contract.db").as_posix(),
        "JWT_SECRET": "contract-inspection-secret-" + "c" * 32,
        "APP_ENV": "test",
        "LLM_PROVIDER": "mock",
        "MOCK_DELAY": "0",
        "RUNPOD_API_KEY": "",
        "TINYFISH_API_KEY": "",
        "COMPUTE_BACKGROUND_ENABLED": "false",
        "ADMIN_EMAILS": "[]",
        "ALLOW_USER_COMPUTE_START": "false",
        "DOCUMENT_STORAGE_DIR": str(work / "documents"),
        "ALEX_LLM_DATA_DIR": str(work / "app-data"),
    }
)
sys.path.insert(0, str(BACKEND))


def main() -> int:
    from app.agent import provenance
    from app.cloud.provider import GatewayProvider
    from app.config import get_settings
    from app.providers import LlamaCppProvider, MockLLMProvider, make_provider
    from app.tools.local import machine
    from app.tools.local.journal import KINDS
    from app.tools.local.task import LocalTaskController
    from app.tools.registry import make_registry

    settings = get_settings()
    registry = make_registry()
    definitions = registry.definitions()
    names = sorted(definition.name for definition in definitions)
    schemas = [definition.llm_schema() for definition in definitions]

    checks: list[dict] = []

    def check(name: str, ok: bool, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:400]})

    check("production_model_is_qwen", settings.llm_model == "orcarouter-qwen38-27b-q5km", settings.llm_model)
    check("context_window_is_server_side", settings.llm_context_window == 32768, settings.llm_context_window)
    check("real_provider_declares_live", LlamaCppProvider.live_model is True)
    check("real_provider_name", LlamaCppProvider.provenance_name == "llamacpp")
    check("gateway_provider_declares_live", GatewayProvider.live_model is True)
    check("gateway_provider_name", GatewayProvider.provenance_name == "canalla-cloud")
    check("mock_is_not_live", MockLLMProvider.live_model is False and MockLLMProvider.provenance_name == "mock")
    check("mock_factory_is_explicit", isinstance(make_provider(settings), MockLLMProvider))

    check("provider_supports_tools", LlamaCppProvider.supports_tools is True)
    check("gateway_supports_tools", GatewayProvider.supports_tools is True)
    check("tool_schemas_are_openai_shape", all("function" in item and item["function"].get("name") for item in schemas))
    check("tool_count_at_least_the_agent_core", len(names) >= 20, len(names))
    for required in ("write_file", "read_file", "run_process", "run_python", "window_inspect", "window_act",
                     "verify_result", "deep_research", "browser_gui", "security_active", "agent_queue"):
        check(f"tool_registered_{required}", required in names)

    controller = LocalTaskController()
    check("controller_owns_completion", hasattr(controller, "conclude") and hasattr(controller, "next_forced_action"))
    check("controller_requires_verification", hasattr(controller, "checkpoint"))
    states = sorted(
        value
        for name, value in vars(machine).items()
        if name.isupper() and isinstance(value, str)
    )
    for required in (
        "PLANNING", "EXECUTING", "WAITING_DEVICE", "WAITING_CONFIRMATION", "WAITING_LLM",
        "INTERRUPTED", "VERIFYING", "COMPLETED", "FAILED",
    ):
        check(f"state_{required}", required in states)

    for kind in (provenance.MODEL_RESPONSE, provenance.MODEL_TOOL_CALL, provenance.TOOL_RESULT):
        check(f"journal_kind_{kind}", kind in KINDS)
    check("real_providers_are_named", provenance.REAL_PROVIDERS == {"llamacpp", "canalla-cloud"}, provenance.REAL_PROVIDERS)
    check("real_runtimes_are_named", provenance.REAL_RUNTIMES == {"llama.cpp", "gateway"}, provenance.REAL_RUNTIMES)
    check("mock_providers_are_named", "mock" in provenance.MOCK_PROVIDERS)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "inspection_of_the_real_application_objects",
        "note": (
            "No model was contacted. The LIVE verdict can only be produced by classifying a real "
            "task journal after a real provider turn."
        ),
        "model": {
            "provider_default": settings.llm_provider,
            "model": settings.llm_model,
            "context_window": settings.llm_context_window,
            "task_max_calls": settings.tools_task_max_calls,
            "task_hard_calls": settings.tools_task_hard_calls,
            "task_max_runtime_seconds": settings.tools_task_max_runtime,
            "task_hard_runtime_seconds": settings.tools_task_hard_runtime,
            "planner_max_tokens": 1200,
        },
        "providers": {
            "llamacpp": {"live": LlamaCppProvider.live_model, "supports_tools": LlamaCppProvider.supports_tools},
            "canalla-cloud": {"live": GatewayProvider.live_model, "supports_tools": GatewayProvider.supports_tools},
            "mock": {"live": MockLLMProvider.live_model, "supports_tools": MockLLMProvider.supports_tools},
        },
        "tools": {"count": len(names), "names": names},
        "task_states": states,
        "provenance_kinds": sorted(provenance.PROVENANCE_KINDS),
        "evidence_chain": [
            "model -> plan_tools(messages, tools, usage)",
            "decision -> MODEL_RESPONSE (sealed: provider, model, response_id, tool_call_ids)",
            "tool_call -> MODEL_TOOL_CALL (sealed: tool_call_id, tool, arguments_digest)",
            "ToolExecutor.execute -> ToolRun(origin=model, input_digest, result_digest)",
            "result -> TOOL_RESULT (sealed: executed, result_digest, tool_run_id)",
            "verify_result -> VERIFY_PASSED/VERIFY_FAILED + task.verification.evidence_ok",
            "classify(journal) -> REAL_MODEL_CONFIRMED only for a named live provider + runtime",
        ],
        "tests": {
            "model_loop": "apps/backend/tests/test_127_autonomous_e2e.py",
            "provenance": "apps/backend/tests/test_127_provenance.py",
            "recovery": "apps/backend/tests/test_127_recovery_e2e.py",
            "long_task": "apps/backend/tests/test_long_autonomy.py",
        },
        "checks": checks,
    }
    report["verdict"] = "PASS" if all(item["ok"] for item in checks) else "FAIL"
    (OUT / "model-contract.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps({"verdict": report["verdict"], "tools": len(names)}, ensure_ascii=False))
    import shutil

    shutil.rmtree(work, ignore_errors=True)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
