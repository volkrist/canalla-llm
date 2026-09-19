"""0.9.3 GPU TEST A retest: TinyFish Browser inject. Sequential after GPU=0. Hard remaining $0.22."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("real_e2e_093", SCRIPTS / "real_e2e_093.py")
gpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpu)
e2e = gpu.e2e

e2e.SESSION_BUDGET = 0.22
e2e.GPU_COST_STOP = 0.20
e2e.GPU_WALL = 10 * 60


def main():
    stamp = e2e.utcnow().strftime("%Y%m%dT%H%M%S")
    work = Path(os.environ["TEMP"]) / "alex-llm-real-e2e" / f"gpu-093-browser-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    evidence = work / "evidence.json"
    report = e2e.REPORT
    report.update(
        {
            "version": "0.9.3-browser-retest",
            "workspace": str(work),
            "mock": False,
            "cursor_executed_task": False,
            "prior_pod": "j2qzj29p7d8zlq",
        }
    )
    log = e2e.log
    backend_proc = None
    backend_log = None
    api = e2e.Api()
    gpu_started = None
    try:
        values = e2e.env_file_map()
        report["config"] = {
            "runpod_key": e2e.secret_present(values, "RUNPOD_API_KEY"),
            "llm_key": e2e.secret_present(values, "LLM_API_KEY"),
            "tinyfish_key": e2e.secret_present(values, "TINYFISH_API_KEY")
            or bool(os.environ.get("TINYFISH_API_KEY")),
            "jwt": e2e.secret_present(values, "JWT_SECRET"),
        }
        if not report["config"]["runpod_key"] or not report["config"]["llm_key"]:
            raise SystemExit("missing compute credentials")
        if not report["config"]["tinyfish_key"]:
            raise SystemExit("missing tinyfish key")
        os.environ["DATABASE_URL"] = values.get("DATABASE_URL") or "sqlite:///./alex.db"
        e2e.kill_port_8000()
        time.sleep(1)
        os.environ["COMPUTE_BACKGROUND_ENABLED"] = "true"
        os.environ["ALLOW_USER_COMPUTE_START"] = "true"
        os.environ["LLM_PROVIDER"] = "llamacpp"
        backend_proc, backend_log, _ = e2e.start_backend(gpu.dummy_discovered(), 9050, session_budget="0.22")
        health = e2e.wait_health()
        report["backend_health"] = health
        if health.get("provider") != "llamacpp":
            raise RuntimeError("backend_not_llamacpp")
        email = f"gpu-093-browser-{stamp}@example.com"
        password = "e2e-password-" + hashlib.sha256(stamp.encode()).hexdigest()[:12]
        api.register(email, password)
        api.client.post("/compute/search/cancel", headers=api.headers())
        live = gpu.compute_running(api)
        report["compute_before"] = live
        if live.get("running_gpu"):
            raise RuntimeError("existing_gpu_must_not_start_second_pod:%s" % live)
        try:
            e2e.stop_compute(api)
        except Exception:
            pass
        api.client.put(
            "/tools/preferences",
            headers=api.headers(),
            json={
                "search_enabled": True,
                "fetch_enabled": True,
                "default_mode": "on",
                "agent_mode": "auto",
                "browser_mode": "auto",
                "agent_enabled": True,
                "browser_enabled": True,
                "browser_max_sessions": 1,
                "browser_max_minutes": 4,
                "tinyfish_paid_task_budget_usd": 0.20,
                "tor_enabled": False,
                "computer_mode": "off",
            },
        )
        try:
            report["wallet_before"] = gpu.wallet_snapshot()
        except Exception as error:
            report["wallet_before"] = {"error": type(error).__name__}

        gpu_started = e2e.launch_managed_pod(api)
        e2e.record_gpu("pod_ready")
        gpu.wait_model(api, gpu_started)
        e2e.record_gpu("model_ready")

        sanity = gpu.run_prompt(api, "093-sanity", gpu.PROMPT_MATH, web_mode="off", computer_mode="off", timeout=90)
        usage = (e2e.usage_rows(1) or [{}])[0]
        sanity["provider"] = usage.get("provider")
        sanity["mock"] = usage.get("provider") == "mock"
        report["orcarouter_basic"] = {
            "chars": sanity["chars"],
            "provider": sanity["provider"],
            "mock": sanity["mock"],
        }
        if sanity["mock"] or sanity["chars"] < 1:
            raise RuntimeError("orcarouter_sanity_failed")
        e2e.gpu_guard(gpu_started)

        log("GPU TEST A browser retest")
        report["test_browser"] = gpu.run_prompt(
            api, "093-browser-retest", gpu.PROMPT_BROWSER, web_mode="on", computer_mode="off", timeout=180
        )
        e2e.record_gpu("browser")
        browser = report["test_browser"]
        report["verdict"] = {
            "mock": False,
            "browser_selected": "web_browser" in (browser.get("paid_tools") or []),
            "browser_search_instead": bool({"web_search", "web_fetch"} & set(browser.get("tool_names") or []))
            and "web_browser" not in (browser.get("paid_tools") or []),
            "origins": browser.get("origins"),
            "sources": browser.get("sources"),
            "cursor_executed_task": False,
        }
        log("VERDICT %s" % report["verdict"])
        try:
            report["wallet_after"] = gpu.wallet_snapshot()
        except Exception as error:
            report["wallet_after"] = {"error": type(error).__name__}
    finally:
        log("cleanup gpu")
        if gpu_started:
            try:
                report["compute_stop"] = e2e.stop_compute(api)
            except Exception as error:
                report["compute_stop"] = {"error": type(error).__name__}
            time.sleep(3)
            row = e2e.session_row()
            live = gpu.compute_running(api) if api.token else {}
            report["runpod_final"] = {
                "status": row.get("status"),
                "pod_id": row.get("pod_id"),
                "gpu": row.get("gpu_type"),
                "datacenter": row.get("datacenter"),
                "price_hour": row.get("hourly_rate"),
                "billable_seconds": row.get("billable_seconds"),
                "estimated_cost": row.get("estimated_cost"),
                "stop_reason": row.get("stop_reason"),
                "volume": row.get("network_volume_id"),
                "running_gpu": live.get("running_gpu"),
            }
        else:
            report["runpod_final"] = {"skipped": True}
        if backend_proc:
            backend_proc.terminate()
            try:
                backend_proc.wait(timeout=8)
            except Exception:
                backend_proc.kill()
        if backend_log:
            backend_log.close()
        evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        log(f"evidence={evidence}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        e2e.REPORT["fatal"] = f"{type(error).__name__}:{error}"
        stamp = e2e.utcnow().strftime("%Y%m%dT%H%M%S")
        path = Path(os.environ.get("TEMP", ".")) / "alex-llm-real-e2e" / f"fatal-093-browser-{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(e2e.REPORT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"FATAL {type(error).__name__}:{error} evidence={path}", flush=True)
        raise
