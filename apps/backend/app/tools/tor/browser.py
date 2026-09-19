"""Tor Browser automation: isolated profile, Marionette, Job Object, Tor-only."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from ...config import get_settings
from ..contracts import ToolError, ToolProvider, ToolResult
from .classify import classify_authority
from .marionette import MarionetteClient
from .router import blocked_link, normalize_http_url
from .snapshot import fetch_needs_browser, resolve_link_id, snapshot_from_html
from .socks import Socks5hConnector
from .urls import is_onion, validate_tor_url

BROWSER_OPS = ("open", "content", "links", "click", "navigate", "back", "wait", "close")
UNSAFE_SCHEMES = {"file", "javascript", "data", "blob", "chrome", "about", "view-source"}
TOR_BROWSER_REQUEST = (
    r"(?i)(tor browser|открой в tor browser|open( it)? in tor browser|"
    r"через tor browser|use tor browser)"
)
NO_TOR_BROWSER = re.compile(
    r"(?i)(не используй.{0,24}tor browser|don't use tor browser|do not use tor browser|"
    r"без tor browser|without tor browser)"
)
PROMPT_URL = re.compile(r"https?://[^\s<>\"')\]]+")
CHECK_TOR_URL = "https://check.torproject.org/"
TOR_PROJECT_URL = "https://www.torproject.org/"


class TorBrowserArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    operation: str = Field(default="open", max_length=20)
    url: str | None = Field(default=None, max_length=2048)
    link_id: str | None = Field(default=None, max_length=8)
    wait_ms: int | None = Field(default=None, ge=0, le=30000)
    purpose: str | None = Field(default=None, max_length=2000)


def candidate_browser_paths() -> list[Path]:
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
    names = [
        home / "Desktop" / "Tor Browser" / "Browser" / "firefox.exe",
        home / "OneDrive" / "Desktop" / "Tor Browser" / "Browser" / "firefox.exe",
        home / "AppData" / "Local" / "Tor Browser" / "Browser" / "firefox.exe",
        Path(os.environ.get("LOCALAPPDATA") or "") / "Tor Browser" / "Browser" / "firefox.exe",
        Path(os.environ.get("ProgramFiles") or r"C:\Program Files")
        / "Tor Browser"
        / "Browser"
        / "firefox.exe",
        Path(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)")
        / "Tor Browser"
        / "Browser"
        / "firefox.exe",
        Path(os.environ.get("TOR_BROWSER_EXE") or ""),
    ]
    found, seen = [], set()
    for path in names:
        if not path or not str(path):
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen or not resolved.is_file():
            continue
        if resolved.name.lower() not in {"firefox.exe", "firefox"}:
            continue
        seen.add(key)
        found.append(resolved)
    return found


def existing_tor_browser() -> Path | None:
    paths = candidate_browser_paths()
    return paths[0] if paths else None


def _ini_value(text: str, key: str) -> str:
    for line in (text or "").splitlines():
        if line.strip().startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return ""


def browser_install_info(path: Path | None = None) -> dict:
    exe = path or existing_tor_browser()
    if not exe:
        return {
            "installed": False,
            "executable": False,
            "version": None,
            "engine_version": None,
            "mechanism": None,
        }
    version = None
    engine = None
    tbb = exe.parent / "tbb_version.json"
    ini = exe.parent / "application.ini"
    platform = exe.parent / "platform.ini"
    try:
        if tbb.is_file():
            payload = json.loads(tbb.read_text(encoding="utf-8"))
            version = str(payload.get("version") or "") or None
    except (OSError, json.JSONDecodeError):
        version = None
    try:
        if ini.is_file():
            text = ini.read_text(encoding="utf-8", errors="replace")
            engine = _ini_value(text, "Version") or engine
            if (
                _ini_value(text, "RemotingName") != "Tor Browser"
                and _ini_value(text, "CodeName") != "Tor Browser"
            ):
                if "Tor Browser" not in text and "tor-browser" not in text.lower():
                    return {
                        "installed": False,
                        "executable": False,
                        "version": None,
                        "engine_version": None,
                        "mechanism": None,
                    }
        if platform.is_file() and not engine:
            engine = _ini_value(platform.read_text(encoding="utf-8", errors="replace"), "Milestone")
    except OSError:
        pass
    return {
        "installed": True,
        "executable": True,
        "version": version,
        "engine_version": engine,
        "mechanism": "marionette",
        "vendor": "Tor Project",
    }


def socks_listening(host: str, port: int, timeout=0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


async def prove_socks5(host="127.0.0.1", port=9050, timeout=2.0):
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ToolError("unsafe_url")
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError) as error:
        raise ToolError("tor_unavailable") from error
    try:
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        greeting = await asyncio.wait_for(reader.readexactly(2), timeout)
        if greeting != b"\x05\x00":
            raise ToolError("tor_unavailable")
        return {"proxy_host": host, "proxy_port": port, "socks": "socks5", "atyp_ready": True}
    except ToolError:
        raise
    except Exception as error:
        raise ToolError("tor_unavailable") from error
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def looks_like_tor_browser(prompt: str) -> bool:
    return bool(re.search(TOR_BROWSER_REQUEST, prompt or "")) and not looks_like_no_tor_browser(prompt)


def looks_like_no_tor_browser(prompt: str) -> bool:
    return bool(NO_TOR_BROWSER.search(prompt or ""))


def browser_target_from_prompt(prompt: str) -> str:
    text = prompt or ""
    for raw in PROMPT_URL.findall(text):
        url = raw.rstrip(").,];")
        try:
            return validate_browser_url(url)
        except ToolError:
            continue
    if re.search(r"(?i)(проверк\w*.{0,24}tor|tor check|check\.torproject)", text):
        return CHECK_TOR_URL
    if re.search(r"(?i)(сайт tor project|torproject\.org|tor project home)", text):
        return TOR_PROJECT_URL
    return ""


def automation_enabled(settings=None, prefs=None) -> bool:
    cfg = settings or get_settings()
    if not getattr(cfg, "tor_browser_automation_enabled", False):
        return False
    mode = getattr(prefs, "tor_browser_mode", None) if prefs is not None else "auto"
    if mode == "off":
        return False
    return True


def automation_ready(settings=None, prefs=None) -> bool:
    return automation_enabled(settings, prefs) and existing_tor_browser() is not None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_isolated_profile(
    root: Path, *, socks_host: str, socks_port: int, marionette_port: int, allow_loopback=False
):
    root.mkdir(parents=True, exist_ok=True)
    (root / "downloads").mkdir(exist_ok=True)
    no_proxy = "localhost, 127.0.0.1" if allow_loopback else ""
    hijack = "false" if allow_loopback else "true"
    prefs = f"""
user_pref("network.proxy.type", 1);
user_pref("network.proxy.socks", "{socks_host}");
user_pref("network.proxy.socks_port", {int(socks_port)});
user_pref("network.proxy.socks_version", 5);
user_pref("network.proxy.socks_remote_dns", true);
user_pref("network.proxy.no_proxies_on", "{no_proxy}");
user_pref("network.proxy.allow_hijacking_localhost", {hijack});
user_pref("network.proxy.share_proxy_settings", false);
user_pref("network.dns.blockDotOnion", false);
user_pref("network.dns.disablePrefetch", true);
user_pref("extensions.torlauncher.start_tor", false);
user_pref("extensions.torlauncher.prompt_at_startup", false);
user_pref("marionette.enabled", true);
user_pref("marionette.port", {int(marionette_port)});
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.page", 0);
user_pref("browser.startup.homepage", "about:blank");
user_pref("startup.homepage_welcome_url", "");
user_pref("startup.homepage_welcome_url.additional", "");
user_pref("toolkit.telemetry.enabled", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("app.update.enabled", false);
user_pref("app.update.auto", false);
user_pref("extensions.update.enabled", false);
user_pref("browser.download.folderList", 2);
user_pref("browser.download.dir", "{str(root / "downloads").replace("\\\\", "/").replace(chr(92), "/")}");
user_pref("browser.download.useDownloadDir", true);
user_pref("browser.download.alwaysOpenPanel", false);
user_pref("browser.helperApps.neverAsk.saveToDisk", "");
user_pref("browser.tabs.warnOnClose", false);
user_pref("browser.warnOnQuit", false);
user_pref("privacy.spoof_english", 2);
user_pref("intl.locale.requested", "en-US");
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("toolkit.startup.max_resumed_crashes", -1);
user_pref("browser.crashReports.unsubmittedCheck.enabled", false);
user_pref("privacy.clearOnShutdown.cookies", true);
user_pref("privacy.clearOnShutdown.sessions", true);
"""
    (root / "user.js").write_text(prefs, encoding="utf-8")
    return root


def _isolated_env(profile: Path, socks_host: str, socks_port: int) -> dict[str, str]:
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    temp = profile / "tmp"
    temp.mkdir(exist_ok=True)
    return {
        "SystemRoot": system_root,
        "SystemDrive": os.environ.get("SystemDrive") or "C:",
        "windir": os.environ.get("windir") or system_root,
        "OS": os.environ.get("OS") or "Windows_NT",
        "NUMBER_OF_PROCESSORS": os.environ.get("NUMBER_OF_PROCESSORS") or "4",
        "PROCESSOR_ARCHITECTURE": os.environ.get("PROCESSOR_ARCHITECTURE") or "AMD64",
        "PATH": rf"{system_root}\System32",
        "TEMP": str(temp),
        "TMP": str(temp),
        "USERPROFILE": str(profile),
        "HOME": str(profile),
        "APPDATA": str(profile / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(profile / "AppData" / "Local"),
        "TOR_SKIP_LAUNCH": "1",
        "TOR_SOCKS_HOST": socks_host,
        "TOR_SOCKS_PORT": str(socks_port),
        "MOZ_MARIONETTE": "1",
        "MOZ_DISABLE_AUTO_RESTART": "1",
    }


def validate_browser_url(url: str, *, allow_loopback=False):
    value = (url or "").strip()
    parsed = urlsplit(value)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").rstrip(".").lower()
    if parsed.username or parsed.password:
        raise ToolError("unsafe_url")
    if scheme in UNSAFE_SCHEMES or scheme not in {"http", "https"}:
        raise ToolError("unsafe_url")
    if blocked_link(value):
        raise ToolError("download_blocked")
    if host in {"localhost", "127.0.0.1", "::1"}:
        if not allow_loopback:
            raise ToolError("unsafe_url")
        return value
    if "\\" in value or host.endswith((".local", ".internal", ".lan", ".home")):
        raise ToolError("unsafe_url")
    return value


class TorBrowserSession:
    def __init__(self, session_id, tool_run_id, profile, job, marionette, socks, allow_loopback=False):
        self.session_id = session_id
        self.tool_run_id = tool_run_id
        self.profile = profile
        self.job = job
        self.marionette = marionette
        self.socks = socks
        self.allow_loopback = allow_loopback
        self.started_by_alex = True
        self.snapshot = None
        self.visited = set()
        self.depth = 0
        self.parent_source = None

    def pids(self):
        return self.job.pids() if self.job else []

    def downloads(self) -> list[Path]:
        folder = Path(self.profile) / "downloads"
        if not folder.is_dir():
            return []
        return [item for item in folder.iterdir() if item.is_file()]


class TorBrowserController:
    def __init__(self, *, exe=None, socks_host=None, socks_port=None, allow_loopback=False):
        settings = get_settings()
        self.exe = Path(exe) if exe else existing_tor_browser()
        self.socks_host = socks_host or settings.tor_socks_host
        self.socks_port = int(socks_port or settings.tor_socks_port)
        self.allow_loopback = allow_loopback
        self.session: TorBrowserSession | None = None

    async def start_session(self, *, tool_run_id=None):
        if self.session:
            return self.session
        if not self.exe or not Path(self.exe).is_file():
            raise ToolError("tor_browser_not_installed")
        info = browser_install_info(Path(self.exe))
        if not info.get("installed"):
            raise ToolError("tor_browser_not_installed")
        if self.socks_host not in {"127.0.0.1", "::1", "localhost"}:
            raise ToolError("unsafe_url")
        await prove_socks5(self.socks_host, self.socks_port)
        from .winjob import JobProcess

        profile = Path(tempfile.mkdtemp(prefix="alex-tor-browser-"))
        marionette_port = _free_port()
        write_isolated_profile(
            profile,
            socks_host=self.socks_host,
            socks_port=self.socks_port,
            marionette_port=marionette_port,
            allow_loopback=self.allow_loopback,
        )
        env = _isolated_env(profile, self.socks_host, self.socks_port)
        argv = [
            str(self.exe),
            "-no-remote",
            "-marionette",
            "-profile",
            str(profile),
        ]
        job = JobProcess(argv, cwd=str(Path(self.exe).parent), env=env)
        client = MarionetteClient("127.0.0.1", marionette_port, timeout=45)
        try:
            await self._wait_marionette(client, job)
            await client.set_timeouts(45000)
        except Exception:
            job.close()
            shutil.rmtree(profile, ignore_errors=True)
            raise
        session = TorBrowserSession(
            uuid.uuid4().hex[:16],
            tool_run_id,
            profile,
            job,
            client,
            {"host": self.socks_host, "port": self.socks_port},
            allow_loopback=self.allow_loopback,
        )
        self.session = session
        return session

    async def _wait_marionette(self, client: MarionetteClient, job, timeout=50):
        deadline = asyncio.get_running_loop().time() + timeout
        last = None
        while asyncio.get_running_loop().time() < deadline:
            if job.poll() is not None:
                raise ToolError("tor_browser_not_ready")
            try:
                await client.connect()
                return
            except ToolError as error:
                last = error
                await asyncio.sleep(0.4)
        raise last or ToolError("tor_browser_not_ready")

    async def open(self, url: str, *, wait_ms=0, tool_run_id=None):
        await self.start_session(tool_run_id=tool_run_id)
        return await self.navigate(url, wait_ms=wait_ms)

    async def navigate(self, url: str, *, wait_ms=0, follow=False):
        session = self.session
        if not session:
            raise ToolError("tor_browser_not_ready")
        target = validate_browser_url(url, allow_loopback=session.allow_loopback)
        if not session.allow_loopback:
            await validate_tor_url(target)
        key = normalize_http_url(target) or target
        if key in session.visited and follow:
            raise ToolError("tool_limit")
        parent = session.snapshot.url if session.snapshot else None
        await session.marionette.navigate(target)
        delay = wait_ms or 0
        if is_onion((urlsplit(target).hostname or "").rstrip(".").lower()):
            delay = max(delay, 5000)
        if delay:
            await asyncio.sleep(min(delay, 30000) / 1000)
        snapshot = await self._capture(parent_source=parent if follow else None, follow=follow)
        session.visited.add(normalize_http_url(snapshot.url) or snapshot.url)
        if snapshot.downloads_blocked if hasattr(snapshot, "downloads_blocked") else False:
            pass
        if session.downloads():
            for item in session.downloads():
                try:
                    item.unlink()
                except OSError:
                    pass
            raise ToolError("download_blocked")
        return snapshot

    async def click(self, link_id: str, *, wait_ms=0):
        session = self.session
        if not session or not session.snapshot:
            raise ToolError("tor_browser_not_ready")
        url = resolve_link_id(session.snapshot, link_id)
        if not url:
            raise ToolError("unsafe_url")
        return await self.navigate(url, wait_ms=wait_ms, follow=True)

    async def back(self):
        session = self.session
        if not session:
            raise ToolError("tor_browser_not_ready")
        await session.marionette.back()
        return await self._capture()

    async def wait(self, wait_ms: int):
        await asyncio.sleep(min(max(wait_ms or 0, 0), 30000) / 1000)
        return await self.get_content()

    async def get_content(self):
        return await self._capture()

    async def get_links(self):
        snapshot = await self._capture()
        return snapshot.links

    async def current_url(self) -> str:
        if not self.session:
            return ""
        return await self.session.marionette.current_url()

    async def page_title(self) -> str:
        if not self.session:
            return ""
        return await self.session.marionette.title()

    async def rendered_html(self) -> str:
        snapshot = await self._capture()
        return snapshot.html

    async def rendered_text(self) -> str:
        snapshot = await self._capture()
        return snapshot.visible_text

    async def extract_links(self):
        return await self.get_links()

    async def _capture(self, parent_source=None, follow=False):
        session = self.session
        if not session:
            raise ToolError("tor_browser_not_ready")
        url = await session.marionette.current_url()
        title = await session.marionette.title()
        html = await session.marionette.page_source()
        text = await session.marionette.inner_text()
        depth = session.depth + (1 if follow else 0)
        if follow:
            session.depth = depth
        snapshot = snapshot_from_html(
            url,
            html,
            title=title,
            text=text,
            depth=max(depth, 1),
            parent_source=parent_source or session.parent_source,
            transport="tor",
        )
        session.snapshot = snapshot
        session.parent_source = parent_source or session.parent_source
        return snapshot

    async def close(self):
        session, self.session = self.session, None
        if not session:
            return {"closed": True, "started_by_alex": True}
        pids = session.pids()
        try:
            await session.marionette.close()
        except Exception:
            pass
        try:
            session.job.close()
        except Exception:
            pass
        shutil.rmtree(session.profile, ignore_errors=True)
        return {
            "closed": True,
            "started_by_alex": True,
            "session_id": session.session_id,
            "tool_run_id": session.tool_run_id,
            "process_ids": pids,
        }


def _source_from_snapshot(snapshot, settings, *, session_id, pids):
    url = snapshot.url
    reachable = bool(snapshot.visible_text or snapshot.links)
    return {
        "url": url,
        "final_url": url,
        "title": snapshot.title,
        "excerpt": snapshot.visible_text[:1500] or snapshot.title or url,
        "authority": classify_authority(url, reachable, settings),
        "links": snapshot.links,
        "reachable": reachable,
        "transport": "tor",
        "retrieval": "browser",
        "rendered": True,
        "browser_session_id": session_id,
        "depth": snapshot.depth,
        "parent_source": snapshot.parent_source,
        "details": {
            "transport": "tor",
            "retrieval": "browser",
            "rendered": True,
            "browser_session_id": session_id,
            "process_ids": pids,
            "started_by_alex": True,
            "links": snapshot.links[:20],
            "forms_present": snapshot.forms_present,
        },
    }


class TorBrowserProvider(ToolProvider):
    def __init__(self, controller=None):
        self.controller = controller
        self._own = controller is None

    def _controller(self, context):
        if self.controller:
            return self.controller
        settings = get_settings()
        loopback = bool(getattr(context, "tor_browser_loopback", False))
        self.controller = TorBrowserController(
            socks_host=settings.tor_socks_host,
            socks_port=settings.tor_socks_port,
            allow_loopback=loopback,
        )
        return self.controller

    async def execute(self, args: TorBrowserArgs, context):
        settings = get_settings()
        prefs = getattr(context, "settings", None)
        if not automation_enabled(settings, prefs):
            raise ToolError("tor_browser_disabled")
        operation = (args.operation or "open").strip().lower()
        if operation not in BROWSER_OPS:
            raise ToolError("unknown_tool")
        controller = self._controller(context)
        snapshot = None
        if operation == "close":
            meta = await controller.close()
            return ToolResult(text="Tor Browser session closed.", metadata=meta)
        if operation == "open":
            if not args.url:
                raise ToolError("unsafe_url")
            snapshot = await controller.open(
                args.url, wait_ms=args.wait_ms or 0, tool_run_id=getattr(context, "run_id", None)
            )
        elif operation == "navigate":
            if not args.url:
                raise ToolError("unsafe_url")
            snapshot = await controller.navigate(args.url, wait_ms=args.wait_ms or 0)
        elif operation == "click":
            snapshot = await controller.click(args.link_id or "", wait_ms=args.wait_ms or 0)
        elif operation == "back":
            snapshot = await controller.back()
        elif operation == "wait":
            snapshot = await controller.wait(args.wait_ms or 1000)
        elif operation in {"content", "links"}:
            snapshot = await controller.get_content()
        session = controller.session
        if not snapshot or not session:
            raise ToolError("tor_browser_not_ready")
        source = _source_from_snapshot(snapshot, settings, session_id=session.session_id, pids=session.pids())
        public = snapshot.public()
        if operation == "links":
            text = json.dumps({"links": public["links"]}, ensure_ascii=False)
        else:
            text = json.dumps(public, ensure_ascii=False)
        return ToolResult(
            text=text,
            sources=[source],
            metadata={
                "transport": "tor",
                "retrieval": "browser",
                "rendered": True,
                "session_id": session.session_id,
                "tool_run_id": session.tool_run_id,
                "process_ids": session.pids(),
                "started_by_alex": True,
                "network": "tor",
                "socks": session.socks,
                "local_dns": False,
            },
        )

    async def close_all(self):
        if self.controller:
            await self.controller.close()
            if self._own:
                self.controller = None


class TorRoutedBrowserProvider(ToolProvider):
    """Not Tor Browser. A future SOCKS-routed engine must keep this name."""

    async def execute(self, args, context):
        raise ToolError("tor_routed_browser_not_implemented")


def browser_status():
    settings = get_settings()
    info = browser_install_info()
    enabled = bool(getattr(settings, "tor_browser_automation_enabled", False))
    installed = bool(info.get("installed"))
    if not installed:
        automation = "unsupported"
    elif not enabled:
        automation = "disabled"
    else:
        automation = "available"
    return {
        "installed": installed,
        "detected": installed,
        "version": info.get("version"),
        "engine_version": info.get("engine_version"),
        "mechanism": info.get("mechanism") if installed else None,
        "executable_tor_browser": bool(info.get("executable")),
        "automation": automation,
        "automatic": enabled and installed,
        "enabled": enabled,
        "routed_provider": "not_implemented",
    }


async def socks_dest_probe(host: str, port: int, dest_host: str, dest_port=80, timeout=8):
    connector = Socks5hConnector(host, port, timeout=timeout)
    reader, writer = await connector.open(dest_host, dest_port, timeout=timeout)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return connector.last_handshake


needs_browser = fetch_needs_browser
