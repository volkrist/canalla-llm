"""Alex LLM 0.8.5 Tor Browser GPU verification. One Pod, hard session budget $0.30."""

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

e2e.SESSION_BUDGET = 0.30
e2e.GPU_COST_STOP = 0.25
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

PROMPT_A = (
    "Через Tor Browser открой официальный сайт проверки Tor, "
    "дождись полной загрузки страницы, посмотри отображаемый текст, "
    "затем перейди по одной безопасной ссылке на официальный сайт Tor Project "
    "и расскажи, что увидел."
)
PROMPT_C = (
    "Продолжи исследование по релевантным ссылкам, которые ты увидел в Tor Browser. "
    "Проверь ещё одну безопасную страницу."
)
JS_CANDIDATES = (
    "https://forum.torproject.org/",
    "https://gitlab.torproject.org/",
    "https://metrics.torproject.org/",
    "https://community.torproject.org/",
    "https://donate.torproject.org/",
    "https://blog.torproject.org/",
)


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
                "kind": row.get("kind"),
                "url": row.get("final_url") or row.get("url"),
                "title": (row.get("title") or "")[:180],
                "excerpt": (row.get("excerpt") or "")[:240],
                "transport": details.get("transport"),
                "retrieval": details.get("retrieval"),
                "rendered": details.get("rendered"),
                "needs_browser": details.get("needs_browser"),
                "browser_session_id": details.get("browser_session_id"),
                "links": len(details.get("links") or []),
                "started_by_alex": details.get("started_by_alex"),
            }
        )
    return rows


def tor_pid():
    try:
        import subprocess

        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-Process tor -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id",
            ],
            text=True,
        )
        return sorted({int(part) for part in out.split() if part.strip().isdigit()})
    except Exception:
        return []


def firefox_pids():
    try:
        import subprocess

        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-Process firefox -ErrorAction SilentlyContinue | Select-Object Id,Path | ConvertTo-Json",
            ],
            text=True,
        )
        return (out or "").strip()[:800]
    except Exception:
        return ""


def probe_js_targets(socks_port):
    import asyncio
    import sys

    sys.path.insert(0, str(e2e.BACKEND))
    from app.tools.tor.snapshot import fetch_needs_browser, visible_text
    from app.tools.tor.socks import TorTransportProvider

    async def run():
        transport = TorTransportProvider(host="127.0.0.1", port=int(socks_port))
        found = []
        for url in JS_CANDIDATES:
            item = {"url": url, "error": None, "needs_browser": False, "chars": 0, "title": ""}
            try:
                page = await transport.fetch(url, timeout=18)
                html = page.get("text") or ""
                item["chars"] = len(visible_text(html))
                item["needs_browser"] = fetch_needs_browser(html)
                item["final_url"] = page.get("url") or url
                item["excerpt"] = visible_text(html)[:220]
            except Exception as error:
                item["error"] = f"{type(error).__name__}:{error}"[:200]
            found.append(item)
            e2e.log("js probe %s needs=%s chars=%s err=%s" % (url, item["needs_browser"], item["chars"], item["error"]))
        return found

    return asyncio.run(run())


def cost_ok(limit):
    return e2e.estimated_cost() < limit


def main():
    stamp = e2e.utcnow().strftime("%Y%m%dT%H%M%S")
    work = Path(os.environ["TEMP"]) / "alex-llm-real-e2e" / f"browser-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    evidence = work / "evidence.json"
    report = e2e.REPORT
    report.update(
        {
            "version": "0.8.5-candidate",
            "workspace": str(work),
            "ui_automation": "no",
            "native_host": "skipped",
            "user_tor_before": tor_pid(),
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

    os.environ["TOR_BROWSER_AUTOMATION_ENABLED"] = "true"

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
        e2e.log("restart backend with llamacpp + tor browser enabled")
        e2e.kill_port_8000()
        time.sleep(1)
        backend_proc, backend_log, _ = e2e.start_backend(discovered, socks.get("port"), session_budget="0.30")
        health = e2e.wait_health()
        report["backend_health"] = health
        if health.get("provider") != "llamacpp":
            raise RuntimeError("backend_not_llamacpp")

        email = f"browser-e2e-{stamp}@example.com"
        password = "e2e-password-" + hashlib.sha256(stamp.encode()).hexdigest()[:12]
        api.register(email, password)
        tools_status = api.client.get("/tools/status", headers=api.headers()).json()
        report["tor_search_configured"] = tools_status.get("tor_search_configured")
        report["tor_status"] = tools_status.get("tor_status")
        report["tor_browser"] = tools_status.get("tor_browser")
        if not tools_status.get("tor_search_configured"):
            raise RuntimeError("tor_search_not_configured")
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
                "device_display_name": "Tor Browser E2E",
            },
        )

        e2e.log("probe JS-shell candidates through Tor SOCKS before GPU")
        report["js_probe"] = probe_js_targets(socks.get("port") or 9050)
        js_target = next((item for item in report["js_probe"] if item.get("needs_browser") and not item.get("error")), None)
        report["js_target"] = js_target

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

        e2e.log("TEST 0 orcarouter sanity")
        chat = api.client.post("/chats", headers=api.headers(), json={"title": "browser-sanity"}).json()
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
            "events": [item["event"] for item in basic["events"]],
            "provider": usage.get("provider"),
            "usage_status": usage.get("status"),
            "mock": usage.get("provider") == "mock",
        }
        e2e.record_gpu("sanity")
        e2e.gpu_guard(gpu_started)
        if report["orcarouter_basic"]["mock"] or report["orcarouter_basic"]["chars"] < 1:
            raise RuntimeError("orcarouter_sanity_failed")

        e2e.log("TEST A explicit model-driven Tor Browser")
        report["user_tor_before_a"] = tor_pid()
        report["firefox_before_a"] = firefox_pids()
        chat_a = api.client.post("/chats", headers=api.headers(), json={"title": "browser-a"}).json()
        result_a = e2e.collect_stream(
            api,
            chat_a["id"],
            PROMPT_A,
            web_mode="off",
            computer_mode="off",
            tor_mode="auto",
            read_timeout=360,
        )
        runs_a = e2e.latest_runs(api, chat_a["id"])
        snaps_a, answer_a = sources_for(api, chat_a["id"])
        browser_runs = [row for row in runs_a if row.get("tool_name") == "tor_browser"]
        report["test_a"] = {
            "prompt": PROMPT_A,
            "answer_len": result_a["chars"],
            "answer_prefix": answer_a[:500],
            "tools": e2e.tool_entries(runs_a),
            "origins": sorted({(row.get("tool_name"), row.get("origin")) for row in runs_a}),
            "model_called_browser": any(
                row.get("tool_name") == "tor_browser" and row.get("origin") == "model" for row in runs_a
            ),
            "server_policy_browser": any(
                row.get("tool_name") == "tor_browser" and row.get("origin") == "server_policy" for row in runs_a
            ),
            "browser_completed": any(row.get("status") == "completed" for row in browser_runs),
            "click_or_navigate": any(
                (row.get("input_summary") or {}).get("operation") in {"click", "navigate"}
                or "Перешёл" in str((row.get("input_summary") or {}).get("action_detail") or "")
                for row in browser_runs
            ),
            "metadata": [
                {
                    "origin": row.get("origin"),
                    "status": row.get("status"),
                    "error": row.get("error_code"),
                    "operation": (row.get("input_summary") or {}).get("operation"),
                    "url": (row.get("input_summary") or {}).get("url"),
                    "link_id": (row.get("input_summary") or {}).get("link_id"),
                    "retrieval": (row.get("result_metadata") or {}).get("retrieval"),
                    "rendered": (row.get("result_metadata") or {}).get("rendered"),
                    "started_by_alex": (row.get("result_metadata") or {}).get("started_by_alex"),
                    "session_id": (row.get("result_metadata") or {}).get("session_id"),
                    "process_ids": (row.get("result_metadata") or {}).get("process_ids"),
                    "socks": (row.get("result_metadata") or {}).get("socks"),
                }
                for row in browser_runs
            ],
            "sources": summarize_sources(snaps_a),
            "user_tor_after": tor_pid(),
            "firefox_after": firefox_pids(),
        }
        e2e.record_gpu("test_a")
        e2e.gpu_guard(gpu_started)

        report["test_c"] = {"skipped": True, "reason": "budget_or_test_a"}
        if cost_ok(0.20) and report["test_a"]["browser_completed"]:
            e2e.log("TEST C follow-up from browser sources")
            first_ids = {item.get("id") for item in runs_a}
            result_c = e2e.collect_stream(
                api,
                chat_a["id"],
                PROMPT_C,
                web_mode="off",
                computer_mode="off",
                tor_mode="auto",
                read_timeout=300,
            )
            runs_all = e2e.latest_runs(api, chat_a["id"])
            new_runs = [row for row in runs_all if row.get("id") not in first_ids]
            snaps_c, answer_c = sources_for(api, chat_a["id"])
            report["test_c"] = {
                "skipped": False,
                "prompt": PROMPT_C,
                "answer_len": result_c["chars"],
                "answer_prefix": answer_c[:500],
                "new_tools": e2e.tool_entries(new_runs),
                "new_browser_or_fetch": any(
                    row.get("tool_name") in {"tor_browser", "tor_fetch"} for row in new_runs
                ),
                "sources": summarize_sources(snaps_c),
            }
            e2e.record_gpu("test_c")
            e2e.gpu_guard(gpu_started)

        report["test_b"] = {"skipped": True, "reason": "no_js_shell_or_budget"}
        if js_target and cost_ok(0.22):
            e2e.log("TEST B automatic fetch to browser fallback")
            prompt_b = (
                f"Через Tor открой {js_target['url']} и кратко расскажи, что видно на странице. "
                "Не используй обычный интернет напрямую."
            )
            chat_b = api.client.post("/chats", headers=api.headers(), json={"title": "browser-b"}).json()
            result_b = e2e.collect_stream(
                api,
                chat_b["id"],
                prompt_b,
                web_mode="off",
                computer_mode="off",
                tor_mode="auto",
                read_timeout=360,
            )
            runs_b = e2e.latest_runs(api, chat_b["id"])
            snaps_b, answer_b = sources_for(api, chat_b["id"])
            fetch_rows = [row for row in runs_b if row.get("tool_name") == "tor_fetch"]
            browser_b = [row for row in runs_b if row.get("tool_name") == "tor_browser"]
            report["test_b"] = {
                "skipped": False,
                "prompt": prompt_b,
                "target": js_target,
                "answer_len": result_b["chars"],
                "answer_prefix": answer_b[:500],
                "tools": e2e.tool_entries(runs_b),
                "fetch_completed": any(row.get("status") == "completed" for row in fetch_rows),
                "browser_completed": any(row.get("status") == "completed" for row in browser_b),
                "browser_origin": sorted({row.get("origin") for row in browser_b}),
                "sources": summarize_sources(snaps_b),
            }
            e2e.record_gpu("test_b")

        all_runs = e2e.latest_runs(api)
        report["tinyfish"] = {
            "search": sum(1 for row in all_runs if row.get("tool_name") == "web_search"),
            "fetch": sum(1 for row in all_runs if row.get("tool_name") == "web_fetch"),
            "agent_calls": sum(1 for row in all_runs if "agent" in (row.get("tool_name") or "")),
            "browser_calls": sum(
                1 for row in all_runs if str(row.get("tool_name") or "").startswith("browser")
            ),
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
        report["user_tor_final"] = tor_pid()
        report["firefox_final"] = firefox_pids()
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
        path = Path(os.environ.get("TEMP", ".")) / "alex-llm-real-e2e" / f"fatal-browser-{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(e2e.REPORT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"FATAL {type(error).__name__} evidence={path}", flush=True)
        raise
