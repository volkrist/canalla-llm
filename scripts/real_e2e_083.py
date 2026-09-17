"""Alex LLM Tor-only GPU retest. One Pod, hard session budget $0.40."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("real_e2e_08", Path(__file__).with_name("real_e2e_08.py"))
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)

e2e.SESSION_BUDGET = 0.40
e2e.GPU_COST_STOP = 0.35
e2e.GPU_WALL = 12 * 60
NATURAL = (
    "Через Tor найди официальный onion-сервис Tor Project. "
    "Проверь его по официальному источнику. "
    "Затем открой найденный onion через Tor. "
    "Если на странице есть релевантные внутренние ссылки, "
    "перейди по одной из них и кратко расскажи, что удалось подтвердить."
)
FOLLOW = "Продолжи поиск по найденным onion-ссылкам и проверь ещё два источника."


def sources_for(api, chat_id):
    messages = api.client.get(f"/chats/{chat_id}/messages", headers=api.headers()).json()
    last = [row for row in messages if row.get("role") == "assistant"]
    if not last:
        return [], ""
    snaps = api.client.get(f"/messages/{last[-1]['id']}/web-sources", headers=api.headers()).json()
    return snaps, last[-1].get("content") or ""


def summarize_tools(runs):
    return e2e.tool_entries(runs)


def main():
    stamp = e2e.utcnow().strftime("%Y%m%dT%H%M%S")
    work = Path(os.environ["TEMP"]) / "alex-llm-real-e2e" / f"tor-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    evidence = work / "evidence.json"
    report = e2e.REPORT
    report.update(
        {
            "version": "0.8.3-candidate",
            "workspace": str(work),
            "ui_automation": "no",
            "native_host": "skipped",
        }
    )
    values = e2e.env_file_map()
    report["config"] = {
        "llm_provider_env": values.get("LLM_PROVIDER"),
        "runpod_key": e2e.secret_present(values, "RUNPOD_API_KEY"),
        "llm_key": e2e.secret_present(values, "LLM_API_KEY"),
        "jwt": e2e.secret_present(values, "JWT_SECRET"),
    }
    if not report["config"]["runpod_key"] or not report["config"]["llm_key"]:
        raise SystemExit("missing compute credentials")

    e2e.log("discover official onion endpoints")
    discovered = e2e.discover_official()
    report["tor_discovery"] = {
        "ahmia_prefix": (discovered["ahmia_onion"] or "")[:12],
        "torproject_prefix": (discovered["torproject_onion"] or "")[:12],
        "sources": discovered["sources"],
    }
    e2e.log("tor socks preflight")
    socks = e2e.start_tor_browser(e2e.probe_socks())
    report["tor_socks"] = {key: socks.get(key) for key in ("9150", "9050", "port", "host", "started_by_test", "error")}
    if not socks.get("port"):
        raise RuntimeError("tor_socks_unavailable")

    backend_proc = None
    backend_log = None
    api = e2e.Api()
    gpu_started = None
    try:
        e2e.log("restart backend with llamacpp + tor providers")
        e2e.kill_port_8000()
        time.sleep(1)
        backend_proc, backend_log, _ = e2e.start_backend(discovered, socks.get("port"), session_budget="0.40")
        health = e2e.wait_health()
        report["backend_health"] = health
        if health.get("provider") != "llamacpp":
            raise RuntimeError("backend_not_llamacpp")

        email = f"tor-e2e-{stamp}@example.com"
        password = "e2e-password-" + hashlib.sha256(stamp.encode()).hexdigest()[:12]
        api.register(email, password)
        tools_status = api.client.get("/tools/status", headers=api.headers()).json()
        report["tor_search_configured"] = tools_status.get("tor_search_configured")
        report["tor_status"] = tools_status.get("tor_status")
        report["tor_browser"] = tools_status.get("tor_browser")
        if not tools_status.get("tor_search_configured"):
            raise RuntimeError("tor_search_not_configured")
        api.client.post("/compute/search/cancel", headers=api.headers())
        api.client.put(
            "/tools/preferences",
            headers=api.headers(),
            json={
                "search_enabled": True,
                "fetch_enabled": True,
                "default_mode": "off",
                "agent_enabled": False,
                "browser_enabled": False,
                "tor_mode": "auto",
                "tor_enabled": True,
                "computer_mode": "off",
                "workspace_roots": [],
                "device_display_name": "Tor E2E",
            },
        )

        gpu_started = e2e.launch_managed_pod(api)
        e2e.record_gpu("pod_ready")
        e2e.gpu_guard(gpu_started)
        for _ in range(90):
            status = api.client.get("/compute/status", headers=api.headers()).json()
            llm = api.client.get("/llm/status", headers=api.headers()).json()
            if status.get("state") in {"ready", "generating"} and llm.get("state") == "ready":
                break
            time.sleep(2)
        else:
            raise RuntimeError("model_not_ready_after_pod")
        e2e.record_gpu("model_ready")
        e2e.gpu_guard(gpu_started)

        e2e.log("TEST 1 orcarouter sanity")
        chat = api.client.post("/chats", headers=api.headers(), json={"title": "tor-sanity"}).json()
        basic = e2e.collect_stream(
            api,
            chat["id"],
            "Ответь одним коротким предложением: сколько будет 2+2?",
            web_mode="off",
            computer_mode="off",
            tor_mode="auto",
        )
        usage = (e2e.usage_rows(1) or [{}])[0]
        report["orcarouter_basic"] = {
            "chars": basic["chars"],
            "events": [item["event"] for item in basic["events"]],
            "provider": usage.get("provider"),
            "usage_status": usage.get("status"),
            "mock": usage.get("provider") == "mock",
            "tools": summarize_tools(e2e.latest_runs(api, chat["id"])),
        }
        e2e.record_gpu("sanity")
        e2e.gpu_guard(gpu_started)

        e2e.log("TEST 2 model-driven Tor natural prompt")
        tor_chat = api.client.post("/chats", headers=api.headers(), json={"title": "tor-model"}).json()
        tor_model = e2e.collect_stream(
            api,
            tor_chat["id"],
            NATURAL,
            web_mode="off",
            computer_mode="off",
            tor_mode="auto",
            read_timeout=300,
        )
        runs = e2e.latest_runs(api, tor_chat["id"])
        snaps, answer = sources_for(api, tor_chat["id"])
        report["tor_model"] = {
            "prompt": NATURAL,
            "answer_len": tor_model["chars"],
            "answer_prefix": answer[:400],
            "tools": summarize_tools(runs),
            "origins": sorted({(row.get("tool_name"), row.get("origin")) for row in runs}),
            "search_completed": any(
                row.get("tool_name") == "tor_search" and row.get("status") == "completed" for row in runs
            ),
            "fetch_completed": any(
                row.get("tool_name") == "tor_fetch" and row.get("status") == "completed" for row in runs
            ),
            "model_called_search": any(
                row.get("tool_name") == "tor_search" and row.get("origin") == "model" for row in runs
            ),
            "server_policy_search": any(
                row.get("tool_name") == "tor_search" and row.get("origin") == "server_policy" for row in runs
            ),
            "sources": [
                {
                    "label": row.get("label"),
                    "channel": row.get("channel"),
                    "authority": row.get("authority"),
                    "kind": row.get("kind"),
                    "url": row.get("final_url") or row.get("url"),
                    "depth": (row.get("details") or {}).get("depth"),
                    "transport": (row.get("details") or {}).get("transport"),
                    "links": len((row.get("details") or {}).get("links") or []),
                }
                for row in snaps
            ],
        }
        e2e.record_gpu("tor_model")
        e2e.gpu_guard(gpu_started)

        e2e.log("TEST 3 continue research")
        follow = e2e.collect_stream(
            api,
            tor_chat["id"],
            FOLLOW,
            web_mode="off",
            computer_mode="off",
            tor_mode="auto",
            read_timeout=240,
        )
        follow_runs = e2e.latest_runs(api, tor_chat["id"])
        first_ids = {item.get("id") for item in runs}
        new_runs = [row for row in follow_runs if row.get("id") not in first_ids]
        follow_snaps, follow_answer = sources_for(api, tor_chat["id"])
        report["tor_follow"] = {
            "answer_len": follow["chars"],
            "answer_prefix": follow_answer[:400],
            "new_tools": summarize_tools(new_runs),
            "new_fetch": any(row.get("tool_name") == "tor_fetch" for row in new_runs),
            "sources": [
                {
                    "label": row.get("label"),
                    "channel": row.get("channel"),
                    "authority": row.get("authority"),
                    "kind": row.get("kind"),
                    "url": row.get("final_url") or row.get("url"),
                }
                for row in follow_snaps
            ],
        }
        e2e.record_gpu("tor_follow")

        if not report["tor_model"]["model_called_search"]:
            report["tor_server_policy"] = {
                "needed": True,
                "origin_server_policy": report["tor_model"]["server_policy_search"],
            }
        else:
            report["tor_server_policy"] = {
                "needed": False,
                "covered_by_local_test": True,
                "gpu_model_origin": True,
            }

        all_runs = e2e.latest_runs(api)
        report["tinyfish"] = {
            "search": sum(1 for row in all_runs if row.get("tool_name") == "web_search"),
            "fetch": sum(1 for row in all_runs if row.get("tool_name") == "web_fetch"),
            "agent_calls": sum(1 for row in all_runs if "agent" in (row.get("tool_name") or "")),
            "browser_calls": sum(1 for row in all_runs if str(row.get("tool_name") or "").startswith("browser")),
        }
        report["usage"] = [
            {
                "provider": row["provider"],
                "status": row["status"],
                "input": row["input_tokens"],
                "output": row["output_tokens"],
                "total": row["total_tokens"],
            }
            for row in e2e.usage_rows()
        ]
    finally:
        e2e.log("cleanup gpu")
        try:
            report["compute_stop"] = e2e.stop_compute(api)
        except Exception as error:
            report["compute_stop"] = {"error": type(error).__name__}
        time.sleep(3)
        row = e2e.session_row()
        report["runpod_final"] = {
            "status": row.get("status"),
            "pod_id": row.get("pod_id"),
            "billable_seconds": row.get("billable_seconds"),
            "estimated_cost": row.get("estimated_cost"),
            "stop_reason": row.get("stop_reason"),
            "volume": row.get("network_volume_id"),
            "gpu_type": row.get("gpu_type"),
            "datacenter": row.get("datacenter"),
        }
        if backend_proc:
            backend_proc.terminate()
            try:
                backend_proc.wait(timeout=8)
            except Exception:
                backend_proc.kill()
        if backend_log:
            backend_log.close()
        evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        e2e.log(f"evidence={evidence}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        e2e.REPORT["fatal"] = f"{type(error).__name__}:{error}"
        stamp = e2e.utcnow().strftime("%Y%m%dT%H%M%S")
        path = Path(os.environ.get("TEMP", ".")) / "alex-llm-real-e2e" / f"fatal-tor-{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(e2e.REPORT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"FATAL {type(error).__name__} evidence={path}", flush=True)
        raise
