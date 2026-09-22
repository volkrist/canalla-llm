"""Local REAL verification of Tor Browser automation. No RunPod. No TinyFish."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))
os.chdir(ROOT / "apps" / "backend")
os.environ.setdefault("JWT_SECRET", "test-only-secret-not-for-deployment-" + "a" * 32)
os.environ.setdefault("COMPUTE_BACKGROUND_ENABLED", "false")
os.environ.setdefault("LLM_PROVIDER", "mock")

from app.tools.contracts import ToolError  # noqa: E402
from app.tools.tor.browser import (  # noqa: E402
    TorBrowserController,
    browser_install_info,
    existing_tor_browser,
    prove_socks5,
    socks_listening,
)
from app.tools.tor.snapshot import visible_text  # noqa: E402

MARKER = "TOR_BROWSER_JS_OK"
JS_PAGE = f"""<!doctype html>
<html><head><title>JS shell</title></head>
<body>
<div id="root">Loading...</div>
<script>
setTimeout(function() {{
  document.getElementById('root').innerHTML =
    '<p>{MARKER}</p><a href="/second">Second page</a>';
}}, 400);
</script>
</body></html>
"""
SECOND_PAGE = "<!doctype html><html><head><title>Second</title></head><body><p>SECOND_PAGE_OK</p></body></html>"


class Pages(BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):
        body = SECOND_PAGE if self.path.startswith("/second") else JS_PAGE
        Pages.hits.append((self.client_address[0], self.path, time.time()))
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        return


def serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def tor_pids():
    import subprocess

    raw = subprocess.check_output(
        ["powershell", "-NoProfile", "-Command", "Get-Process tor -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id"],
        text=True,
    )
    return {int(item) for item in raw.split() if item.strip().isdigit()}


async def main():
    report = {
        "version": "0.8.4-candidate",
        "gpu": 0,
        "tinyfish_agent": 0,
        "tinyfish_browser": 0,
    }
    exe = existing_tor_browser()
    info = browser_install_info()
    report["detection"] = {
        "found": bool(exe),
        "version": info.get("version"),
        "engine_version": info.get("engine_version"),
        "mechanism": "marionette",
        "executable_tor_browser": bool(info.get("executable")),
    }
    report["socks_9050"] = socks_listening("127.0.0.1", 9050)
    if not report["socks_9050"]:
        report["error"] = "tor_unavailable"
        print(json.dumps(report, indent=2))
        return 2
    await prove_socks5("127.0.0.1", 9050)
    before = tor_pids()
    report["user_tor_pids_before"] = sorted(before)

    server = serve()
    local_url = f"http://127.0.0.1:{server.server_address[1]}/"
    controller = TorBrowserController(allow_loopback=True)
    try:
        raw_initial = JS_PAGE
        try:
            snap = await controller.open(local_url, wait_ms=1200)
        except ToolError as error:
            last = getattr(getattr(controller.session, "marionette", None), "last_error", None)
            report["local_js"] = {"error": error.code, "marionette": last}
            raise
        session = controller.session
        if session is None:  # open() returned a snapshot, so the session must exist
            raise RuntimeError("tor_browser_session_missing_after_open")
        report["local_js"] = {
            "url": snap.url,
            "title": snap.title,
            "marker": MARKER in (snap.visible_text or ""),
            "raw_had_marker": MARKER in visible_text(raw_initial),
            "links": len(snap.links),
            "profile_temp": "alex-tor-browser-" in str(session.profile),
            "started_by_alex": True,
            "pids": session.pids(),
        }
        if snap.links:
            followed = await controller.click("L1", wait_ms=400)
            report["local_follow"] = {
                "yes": "SECOND_PAGE_OK" in (followed.visible_text or ""),
                "url": followed.url,
            }
        else:
            report["local_follow"] = {"yes": False}
    except ToolError as error:
        last = getattr(getattr(controller.session, "marionette", None), "last_error", None)
        report["local_js"] = report.get("local_js") or {"error": error.code, "marionette": last}
        report["local_follow"] = {"yes": False}
    finally:
        await controller.close()
        server.shutdown()
    fail = TorBrowserController(socks_host="127.0.0.1", socks_port=19999, allow_loopback=False)
    try:
        await fail.open("https://check.torproject.org/")
        report["fail_closed"] = "UNEXPECTED_SUCCESS"
    except ToolError as error:
        report["fail_closed"] = error.code

    routed = TorBrowserController(allow_loopback=False)
    try:
        proof = await routed.open("https://check.torproject.org/", wait_ms=2500)
        text = (proof.visible_text or "") + " " + (proof.html or "")
        report["tor_check"] = {
            "opened": True,
            "congratulations": "congratulations" in text.lower(),
            "not_using_tor": "not using tor" in text.lower(),
            "title": proof.title,
            "links": len(proof.links),
            "transport": "tor",
            "ip_redacted": True,
        }
        if proof.links:
            nxt = await routed.click("L1", wait_ms=1500)
            report["tor_follow"] = {"yes": True, "title": nxt.title, "url_host_only": True}
        else:
            report["tor_follow"] = {"yes": False}
    except ToolError as error:
        report["tor_check"] = {"opened": False, "error": error.code}
        report["tor_follow"] = {"yes": False}
    finally:
        close_meta = await routed.close()
        report["close"] = close_meta

    after = tor_pids()
    report["user_tor_pids_after"] = sorted(after)
    report["user_tor_survived"] = before <= after and bool(before)
    report["direct_fallback"] = False
    print(json.dumps(report, indent=2))
    local_ok = report.get("local_js", {}).get("marker") and report.get("local_follow", {}).get("yes")
    fail_ok = report.get("fail_closed") in {"tor_unavailable", "tor_not_configured", "tor_browser_not_ready"}
    if local_ok and fail_ok and report["user_tor_survived"]:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
