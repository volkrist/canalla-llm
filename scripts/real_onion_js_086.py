"""Alex LLM 0.8.6 local proofs: controlled JS onion + official external onion."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "apps" / "backend"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SCRIPTS))

from onion_js_service import (  # noqa: E402
    RENDERED_MARKER,
    SECOND_MARKER,
    OnionJsService,
    list_tor_pids,
    tor_browser_tor_exe,
)


def utcnow():
    return datetime.now(timezone.utc)


def log(message):
    print(f"[{utcnow().strftime('%H:%M:%S')}] {message}", flush=True)


def load_e2e():
    spec = importlib.util.spec_from_file_location("real_e2e_08", SCRIPTS / "real_e2e_08.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configure_env():
    os.environ["TOR_BROWSER_AUTOMATION_ENABLED"] = "true"
    os.environ["COMPUTE_BACKGROUND_ENABLED"] = "false"
    os.environ["ALLOW_USER_COMPUTE_START"] = "false"
    os.environ["TOR_SOCKS_HOST"] = "127.0.0.1"
    os.environ["TOR_SOCKS_PORT"] = "9050"
    os.environ["TINYFISH_API_KEY"] = ""
    os.environ.setdefault("LLM_PROVIDER", "mock")
    from app.config import get_settings

    get_settings.cache_clear()
    return get_settings()


def socks_open(port=9050, timeout=0.4):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout):
            return True
    except OSError:
        return False


def local_get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def summarize_source(source):
    details = source.get("details") or {}
    return {
        "url": source.get("final_url") or source.get("url"),
        "title": (source.get("title") or "")[:180],
        "excerpt": (source.get("excerpt") or "")[:240],
        "authority": source.get("authority"),
        "transport": source.get("transport") or details.get("transport"),
        "retrieval": source.get("retrieval") or details.get("retrieval"),
        "rendered": source.get("rendered") if "rendered" in source else details.get("rendered"),
        "needs_browser": source.get("needs_browser") if "needs_browser" in source else details.get("needs_browser"),
        "links": len(source.get("links") or details.get("links") or []),
        "link_ids": [item.get("id") for item in (source.get("links") or details.get("links") or [])[:6]],
    }


class ToolContext:
    def __init__(self, run_id):
        self.run_id = run_id
        self.settings = SimpleNamespace(tor_browser_mode="auto")
        self.secrets = ()
        self.tor_browser_loopback = False


async def production_fetch(url):
    from app.tools.tor.provider import TorFetchArgs, TorFetchProvider

    result = await TorFetchProvider().execute(TorFetchArgs(urls=[url], fresh=True), ToolContext("fetch"))
    return result


async def production_browser(url, *, click=True, wait_ms=5000):
    from app.tools.tor.browser import TorBrowserArgs, TorBrowserProvider

    provider = TorBrowserProvider()
    context = ToolContext("browser")
    opened = None
    clicked = None
    try:
        opened = await provider.execute(
            TorBrowserArgs(operation="open", url=url, wait_ms=wait_ms), context
        )
        text = (opened.sources or [{}])[0].get("excerpt") or opened.text or ""
        if RENDERED_MARKER not in text and SECOND_MARKER not in text:
            opened = await provider.execute(TorBrowserArgs(operation="wait", wait_ms=4000), context)
            text = (opened.sources or [{}])[0].get("excerpt") or opened.text or ""
        if click:
            links = (opened.sources or [{}])[0].get("links") or []
            token = next(
                (str(item.get("id") or "") for item in links if str(item.get("id") or "").upper().startswith("L")),
                "",
            )
            if token:
                clicked = await provider.execute(
                    TorBrowserArgs(operation="click", link_id=token, wait_ms=2500), context
                )
        return opened, clicked
    finally:
        await provider.close_all()


def write_mapping(discovered):
    work = Path(os.environ["TEMP"]) / "alex-llm-real-e2e"
    work.mkdir(parents=True, exist_ok=True)
    mapping_file = work / "tor-official-mapping-086.json"
    mapping_file.write_text(json.dumps(discovered.get("official_mapping") or []), encoding="utf-8")
    os.environ["TOR_OFFICIAL_MAPPING_FILE"] = str(mapping_file)
    from app.config import get_settings

    get_settings.cache_clear()
    return mapping_file


def start_isolated_backend(port, database, mapping_file=None):
    python = BACKEND / ".venv" / "Scripts" / "python.exe"
    env = os.environ.copy()
    secret = env.get("JWT_SECRET") or ("local-onion-js-e2e-secret-not-for-deployxxxx")
    env.update(
        {
            "LLM_PROVIDER": "mock",
            "MOCK_DELAY": "0",
            "DATABASE_URL": "sqlite:///" + str(database).replace("\\", "/"),
            "JWT_SECRET": secret if len(secret) >= 32 else secret + "x" * (32 - len(secret)),
            "COMPUTE_BACKGROUND_ENABLED": "false",
            "ALLOW_USER_COMPUTE_START": "false",
            "RUNPOD_API_KEY": "",
            "TINYFISH_API_KEY": "",
            "TOR_SOCKS_HOST": "127.0.0.1",
            "TOR_SOCKS_PORT": "9050",
            "TOR_BROWSER_AUTOMATION_ENABLED": "true",
            "TOR_SEARCH_PROVIDERS": "[]",
        }
    )
    env.pop("TOR_SEARCH_PROVIDERS_FILE", None)
    if mapping_file:
        env["TOR_OFFICIAL_MAPPING_FILE"] = str(mapping_file)
    upgrade = subprocess.run(
        [str(python), "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND),
        env=env,
        capture_output=True,
        text=True,
    )
    if upgrade.returncode != 0:
        raise RuntimeError("alembic_failed:" + (upgrade.stderr or upgrade.stdout)[-400:])
    log_path = database.parent / "backend-8011.log"
    handle = open(log_path, "ab")
    process = subprocess.Popen(
        [
            str(python),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "1",
        ],
        cwd=str(BACKEND),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    return process, handle, log_path


def wait_http(url, timeout=40):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return response.status
        except Exception as error:
            last = error
            time.sleep(0.4)
    raise RuntimeError(f"backend_not_ready:{last}")


def local_integration(onion_url, mapping_file, work):
    import httpx

    port = 8011
    database = work / "onion-integration.db"
    process = handle = None
    report = {"port": port, "prompt": f"Через Tor проверь {onion_url}"}
    try:
        process, handle, log_path = start_isolated_backend(port, database, mapping_file)
        report["backend_log"] = str(log_path)
        wait_http(f"http://127.0.0.1:{port}/health")
        stamp = utcnow().strftime("%Y%m%dT%H%M%S")
        email = f"onion-int-{stamp}@example.com"
        password = "e2e-password-" + hashlib.sha256(stamp.encode()).hexdigest()[:12]
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=httpx.Timeout(180.0)) as client:
            register = client.post("/auth/register", json={"email": email, "password": password})
            register.raise_for_status()
            token = register.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            client.put(
                "/tools/preferences",
                headers=headers,
                json={
                    "search_enabled": False,
                    "fetch_enabled": False,
                    "default_mode": "off",
                    "agent_enabled": False,
                    "browser_enabled": False,
                    "tor_mode": "auto",
                    "tor_enabled": True,
                    "tor_browser_mode": "auto",
                    "computer_mode": "off",
                    "workspace_roots": [],
                    "device_display_name": "Onion JS local",
                },
            )
            chat = client.post("/chats", headers=headers, json={"title": "onion-js-local"}).json()
            with client.stream(
                "POST",
                f"/chats/{chat['id']}/stream",
                headers=headers,
                json={"content": report["prompt"], "web_mode": "off", "computer_mode": "off", "tor_mode": "auto"},
            ) as response:
                body = "".join(part.decode("utf-8", "replace") if isinstance(part, bytes) else part for part in response.iter_text())
            runs = client.get("/tools/runs", headers=headers, params={"chat_id": chat["id"]}).json()
            messages = client.get(f"/chats/{chat['id']}/messages", headers=headers).json()
            last = [row for row in messages if row.get("role") == "assistant"]
            sources = []
            if last:
                sources = client.get(f"/messages/{last[-1]['id']}/web-sources", headers=headers).json()
        names = [row.get("tool_name") for row in runs]
        origins = [(row.get("tool_name"), row.get("origin")) for row in runs]
        excerpts = " ".join((row.get("excerpt") or "") for row in sources)
        report.update(
            {
                "stream_has_done": "event: done" in body,
                "tools": names,
                "origins": origins,
                "tor_fetch": "tor_fetch" in names,
                "tor_browser": "tor_browser" in names,
                "browser_origin": [origin for name, origin in origins if name == "tor_browser"],
                "rendered_marker": RENDERED_MARKER in excerpts or RENDERED_MARKER in body,
                "second_marker": SECOND_MARKER in excerpts or SECOND_MARKER in body,
                "sources": [
                    {
                        "label": row.get("label"),
                        "url": row.get("final_url") or row.get("url"),
                        "authority": row.get("authority"),
                        "retrieval": (row.get("details") or {}).get("retrieval"),
                        "rendered": (row.get("details") or {}).get("rendered"),
                        "transport": (row.get("details") or {}).get("transport"),
                    }
                    for row in sources
                ],
                "answer_prefix": (last[-1].get("content") if last else "")[:300],
            }
        )
        report["pass"] = bool(
            report["tor_fetch"]
            and report["tor_browser"]
            and "server_policy" in report["browser_origin"]
            and report["rendered_marker"]
        )
        return report
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=8)
            except Exception:
                process.kill()
        if handle:
            handle.close()


def run_local(*, keep=True):
    configure_env()
    from app.tools.tor.browser import browser_status, existing_tor_browser
    from app.tools.tor.snapshot import fetch_needs_browser, visible_text

    stamp = utcnow().strftime("%Y%m%dT%H%M%S")
    work = Path(os.environ["TEMP"]) / "alex-onion-js-e2e" / stamp
    work.mkdir(parents=True, exist_ok=True)
    report = {
        "version": "0.8.6-local",
        "workspace": str(work),
        "user_tor_before": list_tor_pids(),
        "socks9050": socks_open(9050),
        "tor_exe": str(tor_browser_tor_exe() or ""),
        "tor_browser": existing_tor_browser() and str(existing_tor_browser()),
        "browser_status": browser_status(),
        "keep": keep,
    }
    if not report["socks9050"]:
        raise RuntimeError("user_tor_socks_9050_missing")
    if not report["tor_exe"]:
        raise RuntimeError("bundled_tor_exe_missing")
    if not (report["browser_status"] or {}).get("installed"):
        raise RuntimeError("tor_browser_not_detected")

    service = OnionJsService(work)
    service.write_pages()
    service.start_http()
    report["http_port"] = service.http_port
    report["http_bind"] = "127.0.0.1"
    raw_local = local_get(f"http://127.0.0.1:{service.http_port}/")
    report["local_raw"] = {
        "chars": len(raw_local),
        "visible": visible_text(raw_local)[:200],
        "scripts": "<script" in raw_local.lower(),
        "rendered_marker_in_visible": RENDERED_MARKER in visible_text(raw_local),
        "rendered_marker_in_html": RENDERED_MARKER in raw_local,
        "needs_browser": fetch_needs_browser(raw_local),
    }
    if report["local_raw"]["rendered_marker_in_visible"]:
        raise RuntimeError("local_raw_already_rendered")
    if not report["local_raw"]["needs_browser"]:
        raise RuntimeError("local_shell_not_browser_required")

    log("start temporary hidden service")
    onion = service.start_hidden_service(timeout=150)
    bootstrapped = service.wait_bootstrap(timeout=90)
    report["onion"] = onion
    report["onion_url"] = service.onion_url
    report["onion_v3"] = onion.endswith(".onion") and len(onion.split(".")[0]) == 56
    report["hs_pid"] = service.tor_job.pid if service.tor_job else None
    report["bootstrapped"] = bootstrapped
    report["user_tor_after_hs"] = list_tor_pids()
    if report["hs_pid"] in report["user_tor_before"]:
        raise RuntimeError("hs_pid_collided_with_user_tor")
    log("wait until generated onion is reachable via user SOCKS 9050")
    service.wait_reachable(9050, timeout=150)
    service.save_state()
    latest = Path(os.environ["TEMP"]) / "alex-onion-js-e2e" / "latest.json"
    latest.write_text(json.dumps(service.state(), indent=2), encoding="utf-8")

    log("production tor_fetch generated onion")
    fetched = asyncio.run(production_fetch(service.onion_url))
    fetch_source = (fetched.sources or [{}])[0]
    socks_meta = (fetched.metadata or {}).get("socks") or {}
    visible = visible_text(fetch_source.get("excerpt") or "")
    report["raw_fetch"] = {
        "source": summarize_source(fetch_source),
        "socks": socks_meta,
        "visible": visible[:240],
        "visible_len": len(visible),
        "marker_absent": RENDERED_MARKER not in (fetch_source.get("excerpt") or "")
        and RENDERED_MARKER not in visible,
        "needs_browser": bool(fetch_source.get("needs_browser")),
        "retrieval": fetch_source.get("retrieval"),
        "rendered": fetch_source.get("rendered"),
        "authority": fetch_source.get("authority"),
        "errors": fetched.errors,
        "local_dns": socks_meta.get("local_dns"),
        "atyp": socks_meta.get("atyp"),
        "direct_fallback": False,
    }
    if not report["raw_fetch"]["needs_browser"]:
        raise RuntimeError("raw_fetch_not_browser_required")
    if not report["raw_fetch"]["marker_absent"]:
        raise RuntimeError("raw_fetch_saw_rendered_marker")
    if socks_meta.get("local_dns") is True:
        raise RuntimeError("local_onion_dns")
    if socks_meta.get("atyp") not in {0x03, 3, None}:
        raise RuntimeError("socks_atyp_not_hostname")

    log("actual Tor Browser on generated onion")
    opened, clicked = asyncio.run(production_browser(service.onion_url, click=True, wait_ms=5000))
    open_source = (opened.sources or [{}])[0] if opened else {}
    click_source = (clicked.sources or [{}])[0] if clicked else {}
    report["browser_home"] = summarize_source(open_source)
    report["browser_second"] = summarize_source(click_source) if clicked else None
    report["rendered_marker"] = RENDERED_MARKER in (open_source.get("excerpt") or "")
    report["second_marker"] = SECOND_MARKER in (click_source.get("excerpt") or "")
    report["browser_started_by_alex"] = bool((opened.metadata or {}).get("started_by_alex")) if opened else False
    report["browser_socks"] = (opened.metadata or {}).get("socks") if opened else {}
    if not report["rendered_marker"]:
        raise RuntimeError("browser_missing_rendered_marker")
    if not report["second_marker"]:
        raise RuntimeError("browser_missing_second_page")

    e2e = load_e2e()
    log("discover official onion from Tor Project pages")
    discovered = e2e.discover_official()
    mapping_file = write_mapping(discovered)
    official_host = discovered.get("torproject_onion")
    official_url = f"http://{official_host}/" if official_host else None
    report["external_discovery"] = {
        "sources": discovered.get("sources"),
        "torproject_prefix": (official_host or "")[:12],
        "provenance_pages": [row.get("page") for row in discovered.get("sources") or []],
        "official_publisher": bool(official_host),
        "mapping_file": str(mapping_file),
    }
    if not official_url:
        raise RuntimeError("official_onion_not_discovered")

    log("actual Tor Browser on official external onion")
    ext_open, ext_click = asyncio.run(production_browser(official_url, click=True, wait_ms=5000))
    ext_home = (ext_open.sources or [{}])[0] if ext_open else {}
    ext_next = (ext_click.sources or [{}])[0] if ext_click else {}
    report["external_browser"] = {
        "url_prefix": (ext_home.get("final_url") or ext_home.get("url") or official_url)[:28],
        "title": (ext_home.get("title") or "")[:180],
        "excerpt": (ext_home.get("excerpt") or "")[:300],
        "authority": ext_home.get("authority"),
        "retrieval": ext_home.get("retrieval"),
        "rendered": ext_home.get("rendered"),
        "transport": ext_home.get("transport"),
        "links": len(ext_home.get("links") or []),
        "followed": bool(ext_click),
        "follow_title": (ext_next.get("title") or "")[:180] if ext_click else None,
        "follow_url_prefix": ((ext_next.get("final_url") or ext_next.get("url") or "")[:28] if ext_click else None),
        "follow_authority": ext_next.get("authority") if ext_click else None,
    }
    report["external_pass"] = bool(
        ext_home.get("retrieval") == "browser"
        and ext_home.get("rendered") is True
        and (ext_home.get("excerpt") or ext_home.get("title"))
    )

    log("local automatic fallback integration with silent/mock planner")
    report["local_integration"] = local_integration(service.onion_url, mapping_file, work)
    report["user_tor_after_local"] = list_tor_pids()
    report["user_tor_survived"] = service.user_tor_survived()
    evidence = work / "evidence" / "local-proof.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    report["evidence"] = str(evidence)
    log(f"local evidence={evidence}")
    if not keep:
        service.stop(wipe_keys=True)
        service = None
    return service, report


def main():
    keep = "--keep" in sys.argv or "--no-keep" not in sys.argv
    if "--no-keep" in sys.argv:
        keep = False
    service, report = run_local(keep=keep)
    print(json.dumps({k: report[k] for k in report if k not in {"local_raw"}}, default=str)[:2000], flush=True)
    return service, report


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        stamp = utcnow().strftime("%Y%m%dT%H%M%S")
        path = Path(os.environ.get("TEMP", ".")) / "alex-onion-js-e2e" / f"fatal-local-{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fatal": f"{type(error).__name__}:{error}"}, indent=2), encoding="utf-8")
        print(f"FATAL {type(error).__name__}:{error} evidence={path}", flush=True)
        raise
