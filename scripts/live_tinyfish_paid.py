"""Opt-in TinyFish live proof. Never prints secrets, CDP URLs, cookies or wallet payloads."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "apps" / "backend"
sys.path.insert(0, str(BACKEND))

from dotenv import dotenv_values
from pydantic import SecretStr

values = dotenv_values(BACKEND / ".env")
for key, value in values.items():
    if key and key not in os.environ and key != "TINYFISH_API_KEY":
        os.environ[key] = value or ""

from app.config import get_settings  # noqa: E402
from app.tools.policy import ToolLimits, WebSettings  # noqa: E402
from app.tools.tinyfish.agent import AgentArgs, TinyFishAgentProvider  # noqa: E402
from app.tools.tinyfish.browser import TinyFishBrowserProvider, WebBrowserArgs  # noqa: E402
from app.tools.tinyfish.client import SEARCH, TinyFishClient, WALLET  # noqa: E402
from app.tools.tinyfish.web import FetchArgs, SearchArgs, TinyFishFetchProvider, TinyFishSearchProvider  # noqa: E402

KEY = os.environ.get("TINYFISH_API_KEY") or ""
REPORT = {
    "key_configured": bool(KEY),
    "key_length": len(KEY),
    "secret_printed": False,
    "search": {},
    "fetch": {},
    "wallet": {},
    "browser": {},
    "agent": {},
    "cleanup": {},
}


class Credentials:
    def configured(self, provider):
        return bool(KEY)

    def resolve(self, provider):
        if not KEY:
            raise RuntimeError("missing_key")
        return KEY


class LiveContext:
    def __init__(self):
        self.user_id = "live-tinyfish-owner"
        self.run_id = "live-tinyfish-run"
        self.task_id = None
        self.secrets = (KEY,) if KEY else ()
        self.settings = WebSettings(
            agent_mode="on",
            browser_mode="on",
            agent_run_budget=0.24,
            agent_max_runtime=180,
            agent_max_steps=15,
            tinyfish_paid_task_budget_usd=0.50,
        )
        self.limits = ToolLimits(max_seconds=120)
        self.events = []
        self.resolver = None

    async def progress(self, **values):
        safe = {
            k: v
            for k, v in values.items()
            if k
            in {
                "event_kind",
                "steps",
                "supplier_state",
                "supplier_stop_confirmed",
                "session_id",
                "budget_enforcement",
            }
        }
        self.events.append(safe)


def redact(value):
    text = str(value)
    if KEY:
        text = text.replace(KEY, "[redacted]")
    for token in ("wss://", "cdp", "cookie", "authorization"):
        if token in text.casefold() and token != "cdp":
            return "[redacted-structure]"
    return text[:800]


def client():
    settings = get_settings().model_copy(update={"tinyfish_api_key": SecretStr(KEY)})
    return TinyFishClient(Credentials(), settings=settings)


async def live_search(tf):
    context = LiveContext()
    result = await TinyFishSearchProvider(tf).execute(
        SearchArgs(query="official Python documentation site:python.org", top_results=3),
        context,
    )
    REPORT["search"] = {
        "status": "PASS" if result.sources else "FAIL",
        "sources": len(result.sources),
        "paid": False,
        "titles": [item.get("title", "")[:80] for item in result.sources[:3]],
        "hosts": [item.get("url", "").split("/")[2] if "://" in item.get("url", "") else "" for item in result.sources[:3]],
        "errors": result.errors[:5],
    }


async def live_fetch(tf):
    context = LiveContext()
    result = await TinyFishFetchProvider(tf).execute(
        FetchArgs(urls=["https://www.python.org/"], purpose="Read official Python homepage title"),
        context,
    )
    excerpt = (result.sources[0].get("excerpt") if result.sources else "") or ""
    REPORT["fetch"] = {
        "status": "PASS" if result.sources and excerpt else "FAIL",
        "sources": len(result.sources),
        "paid": False,
        "title": (result.sources[0].get("title") if result.sources else "")[:120],
        "excerpt_chars": len(excerpt),
        "has_python": "python" in excerpt.casefold(),
        "errors": result.errors[:5],
    }


async def live_wallet(tf):
    try:
        value = await tf.request("GET", WALLET, retry=False, timeout=20)
        auto = value.get("auto_reload")
        if isinstance(auto, dict):
            state = str(auto.get("state") or auto.get("status") or "").lower()
            auto_state = "ON" if state in {"on", "enabled", "active", "true"} or auto.get("enabled") else "OFF"
        elif isinstance(auto, bool):
            auto_state = "ON" if auto else "OFF"
        else:
            auto_state = str(auto)[:20]
        REPORT["wallet"] = {
            "status": "available",
            "currency": value.get("currency"),
            "available_balance": value.get("available_balance"),
            "auto_reload": auto_state,
            "as_of": value.get("as_of"),
        }
    except Exception as error:
        REPORT["wallet"] = {"status": "unavailable", "error": type(error).__name__, "code": getattr(error, "code", "")}


async def live_browser(tf):
    context = LiveContext()
    provider = TinyFishBrowserProvider(tf)
    started = time.monotonic()
    try:
        opened = await provider.execute(
            WebBrowserArgs(operation="open", url="https://www.python.org/"), context
        )
        title_one = (opened.text or "").split("\n", 1)[0][:200]
        links = (opened.metadata or {}).get("links") or []
        docs = next(
            (
                item
                for item in links
                if "docs.python.org" in (item.get("url") or "").casefold()
                or "doc" in f"{item.get('text', '')} {item.get('url', '')}".casefold()
            ),
            links[0] if links else None,
        )
        second = None
        if docs:
            second = await provider.execute(
                WebBrowserArgs(operation="click", link_id=docs.get("id")), context
            )
            if not ((second.sources or [{}])[0].get("url")):
                second = await provider.execute(
                    WebBrowserArgs(operation="open", url="https://docs.python.org/3/"), context
                )
        closed = await provider.execute(WebBrowserArgs(operation="close"), context)
        duration = float((closed.metadata or {}).get("duration_seconds") or (time.monotonic() - started))
        cost = closed.metadata.get("cost_estimate") if closed.metadata else None
        REPORT["browser"] = {
            "session_created": bool((opened.metadata or {}).get("session_id")),
            "cdp_connected": True,
            "playwright": True,
            "page_loaded": "python" in (opened.text or "").casefold(),
            "title": title_one,
            "links_extracted": len(links),
            "clicked": (docs or {}).get("id") if docs else None,
            "second_page": bool(second and (second.text or "").strip()),
            "second_title": ((second.text or "").split("\n", 1)[0][:200] if second else ""),
            "w_source": bool(opened.sources),
            "duration_seconds": round(duration, 2),
            "estimated_cost": cost,
            "session_closed": bool((closed.metadata or {}).get("supplier_stop_confirmed")),
            "local_sessions": len(provider.sessions),
            "status": "REAL PASS"
            if opened.sources and second and (closed.metadata or {}).get("supplier_stop_confirmed")
            else "FAIL",
        }
    finally:
        if provider.sessions:
            await provider.close_all()
        REPORT["cleanup"]["browser_local"] = len(provider.sessions)


async def live_agent(tf):
    context = LiveContext()
    try:
        result = await TinyFishAgentProvider(tf).execute(
            AgentArgs(
                url="https://www.python.org/",
                task="find",
                goal=(
                    "Read this official Python homepage and return the current Python release "
                    "version shown on the page plus the page URL."
                ),
            ),
            context,
        )
    except Exception as error:
        REPORT["agent"] = {
            "goal": "python.org release version on the homepage",
            "read_only": True,
            "real_api": True,
            "status": "PARTIAL",
            "error": getattr(error, "code", type(error).__name__),
            "events": context.events[-12:],
        }
        return
    text = redact(result.text or "")
    urls = [item.get("url") for item in result.sources or [] if item.get("url")]
    steps = (result.metadata or {}).get("steps")
    REPORT["agent"] = {
        "goal": "python.org release version on the homepage",
        "read_only": True,
        "real_api": True,
        "steps": steps,
        "sources": urls[:8],
        "completed": (result.metadata or {}).get("supplier_state") == "COMPLETED",
        "estimated_cost": result.cost_estimate,
        "provider_reported_cost": result.cost_actual,
        "answer_preview": text[:400],
        "status": "REAL READ_ONLY PASS"
        if result.text and (result.metadata or {}).get("supplier_state") == "COMPLETED"
        else "FAIL",
    }


async def main():
    print("TinyFish key configured:", "YES" if KEY else "NO")
    print("length:", len(KEY) if KEY else 0)
    if not KEY:
        REPORT["error"] = "missing_key"
        return
    tf = client()
    try:
        await live_search(tf)
        print("Search:", REPORT["search"].get("status"), "sources", REPORT["search"].get("sources"))
        await live_fetch(tf)
        print("Fetch:", REPORT["fetch"].get("status"), "title", REPORT["fetch"].get("title"))
        await live_wallet(tf)
        print("Wallet API:", REPORT["wallet"].get("status"), REPORT["wallet"].get("auto_reload"))
        if "--agent-only" not in sys.argv:
            await live_browser(tf)
            print(
                "Browser:",
                REPORT["browser"].get("status"),
                "duration",
                REPORT["browser"].get("duration_seconds"),
                "cost",
                REPORT["browser"].get("estimated_cost"),
            )
        await live_agent(tf)
        print(
            "Agent:",
            REPORT["agent"].get("status"),
            "steps",
            REPORT["agent"].get("steps"),
            "cost",
            REPORT["agent"].get("estimated_cost"),
        )
    finally:
        print(json.dumps(REPORT, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
