"""Alex LLM 0.8.6 GPU: automatic tor_fetch → Tor Browser fallback. One Pod, hard $0.25."""

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
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)

from real_onion_js_086 import (  # noqa: E402
    RENDERED_MARKER,
    SECOND_MARKER,
    list_tor_pids,
    run_local,
)

e2e.SESSION_BUDGET = 0.25
e2e.GPU_COST_STOP = 0.22
e2e.GPU_WALL = 20 * 60
e2e.MAX_HOURLY = 1.10


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
                "reason": item.get("reason"),
            }
            for item in options
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


e2e.wait_for_selectable_gpu = wait_l40s_only


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
                "channel": row.get("channel"),
                "authority": row.get("authority"),
                "url": row.get("final_url") or row.get("url"),
                "title": (row.get("title") or "")[:180],
                "excerpt": (row.get("excerpt") or "")[:240],
                "transport": details.get("transport"),
                "retrieval": details.get("retrieval"),
                "rendered": details.get("rendered"),
                "needs_browser": details.get("needs_browser"),
            }
        )
    return rows


def cost_ok(limit):
    return e2e.estimated_cost() < limit


def controlled_prompt(onion_url):
    return (
        "Через Tor проверь эту тестовую onion-страницу:\n\n"
        f"{onion_url}\n\n"
        "Расскажи, какой текст реально отображается на странице после полной загрузки, "
        "и затем перейди по найденной безопасной ссылке на вторую страницу."
    )


def main():
    os.environ["TOR_BROWSER_AUTOMATION_ENABLED"] = "true"
    stamp = e2e.utcnow().strftime("%Y%m%dT%H%M%S")
    work = Path(os.environ["TEMP"]) / "alex-llm-real-e2e" / f"onion-js-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    evidence = work / "evidence.json"
    report = e2e.REPORT
    report.update(
        {
            "version": "0.8.6-candidate",
            "workspace": str(work),
            "ui_automation": "no",
            "cursor_injected_browser": False,
        }
    )

    log = e2e.log
    service = None
    local_report = {}
    backend_proc = None
    backend_log = None
    api = e2e.Api()
    gpu_started = None
    try:
        log("LOCAL double verification before GPU")
        service, local_report = run_local(keep=True)
        report["local"] = local_report
        if not local_report.get("rendered_marker") or not local_report.get("external_pass"):
            raise RuntimeError("local_double_verification_failed")
        if not (local_report.get("local_integration") or {}).get("pass"):
            raise RuntimeError("local_integration_failed")
        onion_url = local_report["onion_url"]
        prompt = controlled_prompt(onion_url)
        if any(
            token in prompt.lower()
            for token in ("tor browser", "tor_browser", "javascript rendering", "use browser")
        ):
            raise RuntimeError("prompt_mentions_browser")

        values = e2e.env_file_map()
        report["config"] = {
            "llm_provider_env": values.get("LLM_PROVIDER"),
            "runpod_key": e2e.secret_present(values, "RUNPOD_API_KEY"),
            "llm_key": e2e.secret_present(values, "LLM_API_KEY"),
            "jwt": e2e.secret_present(values, "JWT_SECRET"),
        }
        if not report["config"]["runpod_key"] or not report["config"]["llm_key"]:
            raise SystemExit("missing compute credentials")

        log("refresh official mapping for GPU backend")
        discovered = e2e.discover_official()
        socks = e2e.start_tor_browser(e2e.probe_socks())
        report["tor_socks"] = {key: socks.get(key) for key in ("9150", "9050", "port", "host", "started_by_test", "error")}
        if not socks.get("port"):
            raise RuntimeError("tor_socks_unavailable")
        if int(socks.get("port") or 0) != 9050:
            log("warning expected user SOCKS 9050, using %s" % socks.get("port"))

        log("restart backend with llamacpp + tor browser enabled")
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

        email = f"onion-js-e2e-{stamp}@example.com"
        password = "e2e-password-" + hashlib.sha256(stamp.encode()).hexdigest()[:12]
        api.register(email, password)
        tools_status = api.client.get("/tools/status", headers=api.headers()).json()
        report["tor_status"] = tools_status.get("tor_status")
        report["tor_browser"] = tools_status.get("tor_browser")
        if not (tools_status.get("tor_browser") or {}).get("installed"):
            raise RuntimeError("tor_browser_not_detected")
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
                "tor_browser_mode": "auto",
                "computer_mode": "off",
                "workspace_roots": [],
                "device_display_name": "Onion JS GPU",
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

        log("orcarouter sanity")
        chat = api.client.post("/chats", headers=api.headers(), json={"title": "onion-sanity"}).json()
        basic = e2e.collect_stream(
            api,
            chat["id"],
            "Ответь одним коротким предложением: сколько будет 2+2?",
            web_mode="off",
            computer_mode="off",
            tor_mode="auto",
            read_timeout=90,
        )
        usage = (e2e.usage_rows(1) or [{}])[0]
        report["orcarouter_basic"] = {
            "chars": basic["chars"],
            "text": (basic.get("text") or "")[:240],
            "provider": usage.get("provider"),
            "usage_status": usage.get("status"),
            "mock": usage.get("provider") == "mock",
        }
        e2e.record_gpu("sanity")
        e2e.gpu_guard(gpu_started)
        if report["orcarouter_basic"]["mock"] or report["orcarouter_basic"]["chars"] < 1:
            raise RuntimeError("orcarouter_sanity_failed")

        log("model-driven automatic fallback on controlled onion")
        report["user_tor_before_gpu"] = list_tor_pids()
        chat_a = api.client.post("/chats", headers=api.headers(), json={"title": "onion-auto"}).json()
        result_a = e2e.collect_stream(
            api,
            chat_a["id"],
            prompt,
            web_mode="off",
            computer_mode="off",
            tor_mode="auto",
            read_timeout=360,
        )
        runs_a = e2e.latest_runs(api, chat_a["id"])
        snaps_a, answer_a = sources_for(api, chat_a["id"])
        fetch_rows = [row for row in runs_a if row.get("tool_name") == "tor_fetch"]
        browser_rows = [row for row in runs_a if row.get("tool_name") == "tor_browser"]
        fetch_excerpts = " ".join(
            str((row.get("result_metadata") or {})) + str(row.get("input_summary") or "") for row in fetch_rows
        )
        source_text = " ".join((row.get("excerpt") or "") + " " + (row.get("title") or "") for row in snaps_a)
        raw_sources = [row for row in snaps_a if (row.get("details") or {}).get("retrieval") == "http"]
        raw_text = " ".join((row.get("excerpt") or "") for row in raw_sources)
        report["automatic_fallback"] = {
            "prompt": prompt,
            "natural_prompt": True,
            "cursor_injected_browser": False,
            "answer_len": result_a["chars"],
            "answer_prefix": answer_a[:800],
            "tools": e2e.tool_entries(runs_a),
            "origins": sorted({(row.get("tool_name"), row.get("origin")) for row in runs_a}),
            "tor_fetch": bool(fetch_rows),
            "raw_fetch_insufficient": RENDERED_MARKER not in raw_text,
            "raw_excerpt": raw_text[:240],
            "browser_runs": len(browser_rows),
            "browser_origin": sorted({row.get("origin") for row in browser_rows}),
            "browser_automatic": any(row.get("origin") == "server_policy" for row in browser_rows),
            "model_called_browser": any(row.get("origin") == "model" for row in browser_rows),
            "rendered_marker": RENDERED_MARKER in source_text or RENDERED_MARKER in answer_a,
            "second_marker": SECOND_MARKER in source_text or SECOND_MARKER in answer_a,
            "browser_t_source": any((row.get("details") or {}).get("retrieval") == "browser" for row in snaps_a),
            "sources": summarize_sources(snaps_a),
            "fetch_meta": fetch_excerpts[:400],
        }
        auto = report["automatic_fallback"]
        auto["real_pass"] = bool(
            not report["orcarouter_basic"]["mock"]
            and auto["tor_fetch"]
            and auto["raw_fetch_insufficient"]
            and auto["browser_automatic"]
            and not auto["cursor_injected_browser"]
            and auto["rendered_marker"]
            and auto["second_marker"]
            and auto["browser_t_source"]
            and RENDERED_MARKER in answer_a
        )
        e2e.record_gpu("automatic_fallback")
        e2e.gpu_guard(gpu_started)

        report["external_gpu"] = {"skipped": True, "reason": "controlled_priority_or_budget"}
        if auto["real_pass"] and cost_ok(0.15):
            discovered_host = None
            for item in discovered.get("official_mapping") or []:
                onion = item.get("onion") or ""
                if "torproject" in (item.get("name") or "").lower() or "2gzyxa5" in onion:
                    discovered_host = onion
                    break
            if discovered_host:
                ext_prompt = (
                    f"Через Tor Browser открой этот подтверждённый официальный onion и кратко скажи, "
                    f"какая страница загрузилась: {discovered_host}"
                )
                log("optional short external onion GPU check")
                chat_e = api.client.post("/chats", headers=api.headers(), json={"title": "onion-ext"}).json()
                result_e = e2e.collect_stream(
                    api,
                    chat_e["id"],
                    ext_prompt,
                    web_mode="off",
                    computer_mode="off",
                    tor_mode="auto",
                    read_timeout=240,
                )
                runs_e = e2e.latest_runs(api, chat_e["id"])
                snaps_e, answer_e = sources_for(api, chat_e["id"])
                report["external_gpu"] = {
                    "skipped": False,
                    "prompt": ext_prompt,
                    "answer_prefix": answer_e[:400],
                    "tools": e2e.tool_entries(runs_e),
                    "browser": any(row.get("tool_name") == "tor_browser" for row in runs_e),
                    "sources": summarize_sources(snaps_e),
                    "chars": result_e["chars"],
                }
                e2e.record_gpu("external_gpu")

        all_runs = e2e.latest_runs(api)
        report["tinyfish"] = {
            "search": sum(1 for row in all_runs if row.get("tool_name") == "web_search"),
            "fetch": sum(1 for row in all_runs if row.get("tool_name") == "web_fetch"),
            "agent_calls": sum(1 for row in all_runs if "agent" in (row.get("tool_name") or "")),
            "browser_calls": sum(
                1 for row in all_runs if str(row.get("tool_name") or "").startswith("browser")
            ),
        }
        report["user_tor_final"] = list_tor_pids()
    finally:
        log("cleanup gpu")
        if gpu_started:
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
        log("cleanup temporary hidden service")
        try:
            hs_pid = (local_report or {}).get("hs_pid")
            if service:
                service.stop(wipe_keys=True)
            report["cleanup"] = {
                "temp_http_stopped": True,
                "temp_hs_stopped": True,
                "user_tor_alive": hs_pid not in list_tor_pids() if hs_pid else True,
                "user_tor_pids": list_tor_pids(),
                "keys_removed": True,
            }
        except Exception as error:
            report["cleanup"] = {"error": f"{type(error).__name__}:{error}"}
        evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        log(f"evidence={evidence}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        e2e.REPORT["fatal"] = f"{type(error).__name__}:{error}"
        try:
            from onion_js_service import OnionJsService

            latest = Path(os.environ.get("TEMP", ".")) / "alex-onion-js-e2e" / "latest.json"
            if latest.is_file():
                state = json.loads(latest.read_text(encoding="utf-8"))
                service = OnionJsService(Path(state["root"]))
                # HTTP/HS handles live only in this process; best-effort PID kill is in local service.stop
        except Exception:
            pass
        stamp = e2e.utcnow().strftime("%Y%m%dT%H%M%S")
        path = Path(os.environ.get("TEMP", ".")) / "alex-llm-real-e2e" / f"fatal-onion-js-{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(e2e.REPORT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"FATAL {type(error).__name__} evidence={path}", flush=True)
        raise
