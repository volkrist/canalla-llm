"""Optional Tor Browser fallback. Not used on the automatic SOCKS path."""

import os
from pathlib import Path

from ..contracts import ToolError, ToolProvider


def existing_tor_browser() -> Path | None:
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
    candidates = [
        home / "Desktop" / "Tor Browser" / "Browser" / "firefox.exe",
        home / "Desktop" / "Tor Browser" / "Browser" / "firefox",
        Path(os.environ.get("TOR_BROWSER_EXE") or ""),
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    return None


class TorBrowserProvider(ToolProvider):
    """Isolated-profile GUI fallback. Automatic research stays on SOCKS5h HTTP."""

    async def execute(self, args, context):
        if os.environ.get("ALEX_TOR_BROWSER_FALLBACK") != "1":
            raise ToolError("tor_browser_fallback_disabled")
        if not existing_tor_browser():
            raise ToolError("tor_browser_not_installed")
        raise ToolError("tor_browser_not_ready")


def browser_status():
    path = existing_tor_browser()
    return {
        "installed": bool(path),
        "path": str(path) if path else None,
        "automatic": False,
        "enabled": os.environ.get("ALEX_TOR_BROWSER_FALLBACK") == "1",
    }
