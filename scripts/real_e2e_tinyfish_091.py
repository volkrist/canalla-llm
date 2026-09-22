"""Alex LLM 0.9.1 GPU: TinyFish Agent/Browser routing. One L40S Pod, hard $0.25."""

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

spec = importlib.util.spec_from_file_location("real_e2e_08", SCRIPTS / "real_e2e_08.py")
if spec is None or spec.loader is None:
    raise SystemExit("cannot load the real_e2e_08.py harness")
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)

# The harness is loaded by path, so its overrides go through the module namespace.
vars(e2e)["SESSION_BUDGET"] = 0.25
vars(e2e)["GPU_COST_STOP"] = 0.22
vars(e2e)["GPU_WALL"] = 12 * 60
vars(e2e)["MAX_HOURLY"] = 1.10
vars(e2e)["CATALOG_WAIT"] = 25 * 60

PROMPT_MATH = "Сколько будет 2+2?"
PROMPT_LOOKUP = "Какая актуальная версия Python?"
PROMPT_BROWSER = (
    "Открой официальный сайт Python, посмотри страницу в браузере, "
    "перейди в документацию и кратко скажи, что находится на второй странице."
)
PROMPT_AGENT = (
    "Исследуй официальный сайт Python: найди три раздела документации, "
    "сравни их назначение и верни ссылки."
)
PROMPT_TOR = "Через Tor найди официальный onion-сервис Tor Project."
PROMPT_FORM = "Отправь форму на внешнем сайте"
PROMPT_LOCAL = "Создай на моём рабочем столе тестовую папку"
PAID = {"web_agent", "web_browser", "browser_start", "browser_read", "browser_write", "web_agent_read"}


def wait_l40s_only(api, prefs):
    deadline = time.time() + e2e.CATALOG_WAIT
    last = []
    while time.time() < deadline:
        try:
            response = api.client.get("/compute/options", headers=api.headers())
        except Exception as error:
            e2e.log("gpu catalog transport=%s" % type(error).__name__)
            time.sleep(20)
            continue
        if response.status_code != 200:
            e2e.log("gpu catalog http=%s" % response.status_code)
            time.sleep(20)
            continue
        options = response.json().get("options") or []
        if options:
            last = options
        l40s = next((item for item in options if item.get("id") == "NVIDIA L40S"), None)
        compatible = e2e.compatible_gpus(options)
        e2e.REPORT["gpu_options"] = [
            {
                "id": item.get("id"),
                "vram": item.get("vram_gb"),
                "price": item.get("hourly_rate"),
                "availability": item.get("availability"),
                "selectable": item.get("selectable"),
            }
            for item in options
            if item.get("id") in {"NVIDIA L40S", "NVIDIA A40", "NVIDIA RTX 6000 Ada"}
        ]
        e2e.log(
            "gpu catalog wait l40s=%s selectable_compatible=%s"
            % ((l40s or {}).get("availability"), [item.get("id") for item in compatible])
        )
        if l40s and l40s in compatible:
            prefs["gpu_id"] = "NVIDIA L40S"
            api.client.put("/compute/preferences", headers=api.headers(), json=prefs)
            return prefs, options, "l40s"
        time.sleep(20)
    raise RuntimeError(
        "no_l40s_after_wait:%s"
        % [{"id": item.get("id"), "availability": item.get("availability")} for item in last]
    )


vars(e2e)["wait_for_selectable_gpu"] = wait_l40s_only


def dummy_discovered():
    return {
        "ahmia_onion": None,
        "torproject_onion": None,
        "search_providers": [
            {
                "name": "ahmia-clearnet",
                "url_template": "https://ahmia.fi/search/?q=__QUERY__",
                "form_url": "https://ahmia.fi/",
            }
        ],
        "official_mapping": [],
        "sources": [{"page": "dummy", "status": 0, "onion_count": 0}],
    }


def compute_running(api):
    try:
        status = api.client.get("/compute/status", headers=api.headers()).json()
    except Exception as error:
        return {"error": type(error).__name__}
    session = status.get("session") or {}
    state = status.get("state")
    running = state not in {None, "stopped", "idle", "not_configured", "error"} and bool(
        session.get("pod_id")
    )
    return {
        "state": state,
        "pod_id": session.get("pod_id"),
        "managed": session.get("managed"),
        "running_gpu": 1 if running else 0,
    }


def sources_for(api, chat_id):
    messages = api.client.get(f"/chats/{chat_id}/messages", headers=api.headers()).json()
    last = [row for row in messages if row.get("role") == "assistant"]
    if not last:
        return [], ""
    snaps = api.client.get(f"/messages/{last[-1]['id']}/web-sources", headers=api.headers()).json()
    return snaps, last[-1].get("content") or ""


def summarize_sources(snaps):
    rows = []
    for row in snaps:
        details = row.get("details") or {}
        rows.append(
            {
                "label": row.get("label"),
                "kind": row.get("kind"),
                "url": row.get("final_url") or row.get("url"),
                "title": (row.get("title") or "")[:160],
                "provider": details.get("provider") or row.get("provider"),
                "retrieval": details.get("retrieval"),
            }
        )
    return rows


def summarize_runs(runs):
    rows = []
    for run in runs:
        meta = run.get("result_metadata") or {}
        summary = run.get("input_summary") or {}
        rows.append(
            {
                "name": run.get("tool_name"),
                "origin": run.get("origin"),
                "status": run.get("status"),
                "error": run.get("error_code"),
                "operation": summary.get("operation"),
                "url": summary.get("url"),
                "estimated_cost": run.get("estimated_cost") or meta.get("estimated_provider_cost"),
                "steps": meta.get("completed_steps") or meta.get("num_of_steps"),
                "retrieval": meta.get("retrieval"),
                "duration_s": meta.get("duration_seconds") or meta.get("elapsed_seconds"),
            }
        )
    return rows


def paid_runs(runs):
    return [row for row in runs if row.get("tool_name") in PAID]


def wait_model(api, started):
    for _ in range(120):
        e2e.gpu_guard(started)
        status = api.client.get("/compute/status", headers=api.headers()).json()
        llm = api.client.get("/llm/status", headers=api.headers()).json()
        if status.get("state") in {"ready", "generating"} and llm.get("state") == "ready":
            return {"status": status, "llm": llm}
        time.sleep(2)
    raise RuntimeError("model_not_ready_after_pod")


def set_web_prefs(api, **extra):
    body = {
        "search_enabled": True,
        "fetch_enabled": True,
        "default_mode": "on",
        "agent_mode": "auto",
        "browser_mode": "auto",
        "agent_enabled": True,
        "browser_enabled": True,
        "agent_max_runtime": 90,
        "agent_max_steps": 8,
        "agent_max_runs": 1,
        "agent_run_budget": 0.16,
        "tinyfish_paid_task_budget_usd": 0.35,
        "browser_max_sessions": 1,
        "browser_max_minutes": 5,
        "tor_mode": "auto",
        "tor_enabled": True,
        "tor_browser_mode": "off",
        "computer_mode": "off",
        "workspace_roots": [],
        "device_display_name": "TinyFish GPU E2E",
    }
    body.update(extra)
    api.client.put("/tools/preferences", headers=api.headers(), json=body)


def run_prompt(api, title, prompt, web_mode="on", tor_mode="auto", computer_mode="off", timeout=180):
    chat = api.client.post("/chats", headers=api.headers(), json={"title": title}).json()
    stream = e2e.collect_stream(
        api,
        chat["id"],
        prompt,
        web_mode=web_mode,
        computer_mode=computer_mode,
        tor_mode=tor_mode,
        read_timeout=timeout,
    )
    runs = e2e.latest_runs(api, chat["id"])
    snaps, answer = sources_for(api, chat["id"])
    paid = paid_runs(runs)
    return {
        "chat_id": chat["id"],
        "prompt": prompt,
        "chars": stream["chars"],
        "answer_prefix": (answer or stream.get("text") or "")[:400],
        "events": [item["event"] for item in stream["events"]],
        "tools": summarize_runs(runs),
        "paid_tools": [row.get("tool_name") for row in paid],
        "paid_origins": sorted({(row.get("tool_name"), row.get("origin")) for row in paid}),
        "agent_started": any(row.get("tool_name") == "web_agent" for row in paid),
        "browser_started": any(row.get("tool_name") == "web_browser" for row in paid),
        "sources": summarize_sources(snaps),
        "read_timeout": stream.get("read_timeout"),
    }


def wallet_snapshot():
    sys.path.insert(0, str(e2e.BACKEND))
    cwd = os.getcwd()
    os.chdir(e2e.BACKEND)
    try:
        from app.tools.tinyfish.client import WALLET, TinyFishClient

        client = TinyFishClient()

        async def _read():
            value = await client.request("GET", WALLET, retry=False, timeout=20)
            if value is None:
                raise RuntimeError("tinyfish_wallet_returned_no_payload")
            reload_value = value.get("auto_reload")
            if isinstance(reload_value, dict):
                state = str(reload_value.get("state") or "").lower()
                auto_reload = state in {"on", "enabled", "active", "true"} or bool(
                    reload_value.get("enabled")
                )
            else:
                auto_reload = bool(reload_value)
            return {
                "available_balance": value.get("available_balance"),
                "currency": value.get("currency"),
                "auto_reload": auto_reload,
                "auto_reload_state": (reload_value or {}).get("state")
                if isinstance(reload_value, dict)
                else reload_value,
            }

        import asyncio

        return asyncio.run(_read())
    finally:
        os.chdir(cwd)


def main():
    stamp = e2e.utcnow().strftime("%Y%m%dT%H%M%S")
    work = Path(os.environ["TEMP"]) / "alex-llm-real-e2e" / f"tinyfish-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    evidence = work / "evidence.json"
    report = e2e.REPORT
    report.update(
        {
            "version": "0.9.1-tinyfish",
            "workspace": str(work),
            "cursor_executed_task": False,
            "mock": False,
        }
    )
    log = e2e.log
    backend_proc = None
    backend_log = None
    api = e2e.Api()
    gpu_started = None

    try:
        values = e2e.env_file_map()
        key = os.environ.get("TINYFISH_API_KEY") or ""
        report["config"] = {
            "llm_provider_env": values.get("LLM_PROVIDER"),
            "runpod_key": e2e.secret_present(values, "RUNPOD_API_KEY"),
            "llm_key": e2e.secret_present(values, "LLM_API_KEY"),
            "tinyfish_key": bool(key) or e2e.secret_present(values, "TINYFISH_API_KEY"),
            "tinyfish_key_length": len(key) if key else 0,
            "tinyfish_secret_printed": False,
            "jwt": e2e.secret_present(values, "JWT_SECRET"),
        }
        if not report["config"]["runpod_key"] or not report["config"]["llm_key"]:
            raise SystemExit("missing compute credentials")
        if not report["config"]["tinyfish_key"]:
            raise SystemExit("missing tinyfish key")

        discovered = dummy_discovered()
        try:
            discovered = e2e.discover_official()
        except Exception as error:
            log("official onion discovery skipped: %s" % type(error).__name__)

        log("restart backend with llamacpp")
        os.environ["DATABASE_URL"] = values.get("DATABASE_URL") or "sqlite:///./alex.db"
        e2e.kill_port_8000()
        time.sleep(1)
        os.environ["COMPUTE_BACKGROUND_ENABLED"] = "true"
        os.environ["ALLOW_USER_COMPUTE_START"] = "true"
        os.environ["LLM_PROVIDER"] = "llamacpp"
        backend_proc, backend_log, _ = e2e.start_backend(discovered, 9050, session_budget="0.25")
        health = e2e.wait_health()
        report["backend_health"] = health
        if health.get("provider") != "llamacpp":
            raise RuntimeError("backend_not_llamacpp")

        email = f"tinyfish-e2e-{stamp}@example.com"
        password = "e2e-password-" + hashlib.sha256(stamp.encode()).hexdigest()[:12]
        api.register(email, password)
        api.client.post("/compute/search/cancel", headers=api.headers())
        live = compute_running(api)
        report["compute_before"] = live
        if live.get("running_gpu"):
            raise RuntimeError("existing_gpu_must_not_start_second_pod:%s" % live)
        try:
            e2e.stop_compute(api)
        except Exception:
            pass
        set_web_prefs(api)
        report["tools_status"] = {
            key: value
            for key, value in api.client.get("/tools/status", headers=api.headers()).json().items()
            if key
            not in {
                "limits",
            }
        }
        report["tinyfish_configured"] = bool(report["tools_status"].get("configured"))
        try:
            report["wallet_before"] = wallet_snapshot()
        except Exception as error:
            report["wallet_before"] = {"error": type(error).__name__}

        gpu_started = e2e.launch_managed_pod(api)
        e2e.record_gpu("pod_ready")
        e2e.gpu_guard(gpu_started)
        wait_model(api, gpu_started)
        e2e.record_gpu("model_ready")

        log("sanity 2+2 web off")
        sanity = run_prompt(api, "tf-sanity", PROMPT_MATH, web_mode="off", timeout=90)
        usage = (e2e.usage_rows(1) or [{}])[0]
        sanity["provider"] = usage.get("provider")
        sanity["usage_status"] = usage.get("status")
        sanity["mock"] = usage.get("provider") == "mock"
        report["orcarouter_basic"] = sanity
        e2e.record_gpu("sanity")
        e2e.gpu_guard(gpu_started)
        if sanity["mock"] or sanity["chars"] < 1:
            raise RuntimeError("orcarouter_sanity_failed")

        log("browser routing")
        report["test_browser"] = run_prompt(api, "tf-browser", PROMPT_BROWSER, timeout=150)
        e2e.record_gpu("browser")
        e2e.gpu_guard(gpu_started)

        log("agent routing")
        report["test_agent"] = run_prompt(api, "tf-agent", PROMPT_AGENT, timeout=200)
        e2e.record_gpu("agent")
        e2e.gpu_guard(gpu_started)

        log("math with web on")
        report["test_math"] = run_prompt(api, "tf-math", PROMPT_MATH, timeout=90)
        e2e.record_gpu("math")
        e2e.gpu_guard(gpu_started)

        log("simple lookup")
        report["test_lookup"] = run_prompt(api, "tf-lookup", PROMPT_LOOKUP, timeout=150)
        e2e.record_gpu("lookup")
        e2e.gpu_guard(gpu_started)

        log("side-effect form")
        report["test_form"] = run_prompt(api, "tf-form", PROMPT_FORM, timeout=90)
        e2e.record_gpu("form")
        e2e.gpu_guard(gpu_started)

        log("tor isolation")
        report["test_tor"] = run_prompt(api, "tf-tor", PROMPT_TOR, timeout=120)
        e2e.record_gpu("tor")
        e2e.gpu_guard(gpu_started)

        log("local computer isolation")
        report["test_local"] = run_prompt(
            api, "tf-local", PROMPT_LOCAL, computer_mode="off", timeout=90
        )
        e2e.record_gpu("local")

        try:
            report["wallet_after"] = wallet_snapshot()
        except Exception as error:
            report["wallet_after"] = {"error": type(error).__name__}

        report["verdict"] = {
            "mock": False,
            "browser_selected": report["test_browser"]["browser_started"],
            "browser_origin": report["test_browser"]["paid_origins"],
            "agent_selected": report["test_agent"]["agent_started"],
            "agent_origin": report["test_agent"]["paid_origins"],
            "math_paid": report["test_math"]["paid_tools"],
            "lookup_agent": report["test_lookup"]["agent_started"],
            "form_agent": report["test_form"]["agent_started"],
            "tor_paid": report["test_tor"]["paid_tools"],
            "local_paid": report["test_local"]["paid_tools"],
            "cursor_executed_task": False,
        }
        log("VERDICT %s" % report["verdict"])
    finally:
        log("cleanup gpu")
        if gpu_started:
            try:
                report["compute_stop"] = e2e.stop_compute(api)
            except Exception as error:
                report["compute_stop"] = {"error": type(error).__name__}
            time.sleep(3)
            row = e2e.session_row()
            live = compute_running(api) if api.token else {}
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
                "compute_state": live.get("state"),
            }
        else:
            report["compute_stop"] = {"skipped": True, "reason": "gpu_not_started"}
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
        path = Path(os.environ.get("TEMP", ".")) / "alex-llm-real-e2e" / f"fatal-tinyfish-{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(e2e.REPORT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"FATAL {type(error).__name__}:{error} evidence={path}", flush=True)
        raise
