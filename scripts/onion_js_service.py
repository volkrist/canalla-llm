"""Disposable loopback HTTP + temporary Tor v3 hidden service. Not a production provider."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SHELL_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Alex onion JS shell</title></head>
<body>
<div id="root">Loading...</div>
<script>
setTimeout(function () {
  var root = document.getElementById("root");
  root.innerHTML =
    "<p>ALEX_ONION_JS_RENDERED_OK</p>" +
    "<p>This content exists only after JavaScript rendering.</p>" +
    '<a href="/second.html">Second onion test page</a>';
}, 1200);
</script>
</body>
</html>
"""

SECOND_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Alex onion second page</title></head>
<body>
<p>ALEX_ONION_SECOND_PAGE_OK</p>
<p>Second onion test page.</p>
</body>
</html>
"""

RENDERED_MARKER = "ALEX_ONION_JS_RENDERED_OK"
SECOND_MARKER = "ALEX_ONION_SECOND_PAGE_OK"
STATE_NAME = "service-state.json"


def tor_browser_tor_exe() -> Path | None:
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
    candidates = [
        home / "Desktop" / "Tor Browser" / "Browser" / "TorBrowser" / "Tor" / "tor.exe",
        home / "OneDrive" / "Desktop" / "Tor Browser" / "Browser" / "TorBrowser" / "Tor" / "tor.exe",
        Path(os.environ.get("LOCALAPPDATA") or "") / "Tor Browser" / "Browser" / "TorBrowser" / "Tor" / "tor.exe",
    ]
    env = os.environ.get("TOR_DAEMON_EXE")
    if env:
        candidates.append(Path(env))
    for path in candidates:
        if path.is_file() and path.name.lower() == "tor.exe":
            return path.resolve()
    try:
        from app.tools.tor.browser import existing_tor_browser

        firefox = existing_tor_browser()
    except Exception:
        firefox = None
    if firefox:
        bundled = firefox.parent / "TorBrowser" / "Tor" / "tor.exe"
        if bundled.is_file():
            return bundled.resolve()
    return None


def list_tor_pids() -> list[int]:
    try:
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


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _posix(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def _torrc_path(path: Path) -> str:
    value = _posix(path)
    if " " in value:
        return '"' + value + '"'
    return value


class OnionJsService:
    def __init__(self, root: Path, *, http_port: int | None = None):
        self.root = Path(root)
        self.www = self.root / "www"
        self.tor_data = self.root / "tor-data"
        self.hidden = self.root / "hidden-service"
        self.evidence = self.root / "evidence"
        self.torrc = self.root / "torrc"
        self.log_path = self.root / "tor-notice.log"
        self.http_port = int(http_port or 0)
        self.httpd = None
        self.http_thread = None
        self.tor_job = None
        self.onion = None
        self.started_by_test = True
        self.user_tor_before = list_tor_pids()

    def write_pages(self):
        self.www.mkdir(parents=True, exist_ok=True)
        self.tor_data.mkdir(parents=True, exist_ok=True)
        self.hidden.mkdir(parents=True, exist_ok=True)
        self.evidence.mkdir(parents=True, exist_ok=True)
        (self.www / "index.html").write_text(SHELL_HTML, encoding="utf-8")
        (self.www / "second.html").write_text(SECOND_HTML, encoding="utf-8")

    def start_http(self):
        if not self.http_port:
            self.http_port = 8088
            try:
                with socket.create_connection(("127.0.0.1", self.http_port), 0.2):
                    self.http_port = free_loopback_port()
            except OSError:
                pass
        www = self.www

        class BoundHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(www), **kwargs)

            def log_message(self, format, *_args):
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.http_port), BoundHandler)
        self.http_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.http_thread.start()

    def write_torrc(self):
        exe = tor_browser_tor_exe()
        geoip = (exe.parent.parent / "Data" / "Tor" / "geoip") if exe else None
        geoip6 = (exe.parent.parent / "Data" / "Tor" / "geoip6") if exe else None
        lines = [
            f"DataDirectory {_torrc_path(self.tor_data)}",
            "SocksPort 0",
            f"HiddenServiceDir {_torrc_path(self.hidden)}",
            "HiddenServiceVersion 3",
            f"HiddenServicePort 80 127.0.0.1:{int(self.http_port)}",
            f"Log notice file {_torrc_path(self.log_path)}",
        ]
        if geoip is not None and geoip.is_file():
            lines.append(f"GeoIPFile {_torrc_path(geoip)}")
        if geoip6 is not None and geoip6.is_file():
            lines.append(f"GeoIPv6File {_torrc_path(geoip6)}")
        lines.extend(["SafeLogging 1", ""])
        self.torrc.write_text("\n".join(lines), encoding="utf-8")

    def start_hidden_service(self, timeout=120):
        exe = tor_browser_tor_exe()
        if not exe:
            raise RuntimeError("tor_daemon_not_found")
        self.write_torrc()
        from app.tools.tor.winjob import JobProcess

        self.tor_job = JobProcess([str(exe), "-f", str(self.torrc)], cwd=str(exe.parent))
        deadline = time.time() + timeout
        hostname = self.hidden / "hostname"
        while time.time() < deadline:
            if self.tor_job.poll() is not None:
                log_text = (
                    self.log_path.read_text(encoding="utf-8", errors="replace")[-800:]
                    if self.log_path.is_file()
                    else ""
                )
                raise RuntimeError("hidden_service_tor_exited:" + log_text)
            if hostname.is_file():
                onion = hostname.read_text(encoding="utf-8").strip().split()[0].lower()
                if onion.endswith(".onion") and len(onion.split(".")[0]) == 56:
                    self.onion = onion
                    return onion
            time.sleep(0.5)
        raise RuntimeError("hidden_service_hostname_timeout")

    def wait_bootstrap(self, timeout=90):
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = self.log_path.read_text(encoding="utf-8", errors="replace") if self.log_path.is_file() else ""
            if "Bootstrapped 100%" in text:
                return True
            if self.tor_job and self.tor_job.poll() is not None:
                raise RuntimeError("hidden_service_tor_exited")
            time.sleep(0.5)
        return False

    def wait_reachable(self, socks_port=9050, timeout=120):
        if not self.onion:
            raise RuntimeError("hidden_service_not_ready")
        host = self.onion.encode("ascii")
        deadline = time.time() + timeout
        last = "not_tried"
        while time.time() < deadline:
            sock = None
            try:
                sock = socket.create_connection(("127.0.0.1", int(socks_port)), 5)
                sock.settimeout(12)
                sock.sendall(b"\x05\x01\x00")
                greeting = sock.recv(2)
                if greeting != b"\x05\x00":
                    last = f"greeting={greeting!r}"
                    sock.close()
                    time.sleep(2)
                    continue
                sock.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + (80).to_bytes(2, "big"))
                reply = sock.recv(4)
                if reply and reply[0] == 5 and reply[1] == 0:
                    sock.close()
                    return True
                last = f"socks_reply={reply!r}"
            except Exception as error:
                last = f"{type(error).__name__}:{error}"
            finally:
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass
            time.sleep(2)
        raise RuntimeError("onion_not_reachable:" + str(last))

    @property
    def onion_url(self) -> str:
        if not self.onion:
            raise RuntimeError("hidden_service_not_ready")
        return "http://" + self.onion + "/"

    def state(self) -> dict:
        return {
            "root": str(self.root),
            "http_port": self.http_port,
            "onion": self.onion,
            "onion_url": self.onion_url if self.onion else None,
            "hs_pid": self.tor_job.pid if self.tor_job else None,
            "started_by_test": self.started_by_test,
            "user_tor_before": self.user_tor_before,
            "user_tor_now": list_tor_pids(),
        }

    def save_state(self):
        path = self.root / STATE_NAME
        path.write_text(json.dumps(self.state(), indent=2), encoding="utf-8")
        return path

    def stop(self, *, wipe_keys=True):
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None
        if self.tor_job:
            self.tor_job.close()
            self.tor_job = None
        if wipe_keys:
            for name in ("hs_ed25519_secret_key", "hs_ed25519_public_key", "hostname"):
                path = self.hidden / name
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    pass
            import shutil

            shutil.rmtree(self.tor_data, ignore_errors=True)
            shutil.rmtree(self.hidden, ignore_errors=True)

    def user_tor_survived(self) -> bool:
        now = set(list_tor_pids())
        return all(pid in now for pid in self.user_tor_before)
