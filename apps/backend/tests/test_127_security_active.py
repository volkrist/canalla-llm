"""Authorized active security assessment: DETECT → CONFIRM → FIX → RETEST.

The service under test is a real HTTP server on loopback with a known, deliberately unsafe
behaviour. Nothing is mocked: the scanner opens sockets, reads headers and bodies, and a finding
only becomes FIXED when the same probe stops reproducing it.

The negative case is part of the acceptance: a target outside the confirmed scope must be refused
before any request is made.
"""

from __future__ import annotations

import asyncio
import html
import http.server
import json
import threading

from sqlalchemy import select

from app.agent import security as active_security
from app.database import SessionLocal
from app.tools.executor import ExecutionContext, ToolExecutor
from app.tools.models import LocalTask
from app.tools.policy import ToolLimits, WebSettings
from app.tools.registry import make_registry

SECURE_HEADERS = {
    "Content-Security-Policy": "default-src 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def _handler(vulnerable: bool):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "AcceptanceSrv"

        def do_GET(self):  # noqa: N802 - stdlib signature
            path, _, query = self.path.partition("?")
            if path == "/debug":
                if vulnerable:
                    body = b"DEBUG build=dev secret-ish diagnostics"
                    self.send_response(200)
                else:
                    body = b"not found"
                    self.send_response(404)
            else:
                value = ""
                if "q=" in query:
                    from urllib.parse import parse_qs, unquote

                    value = unquote(parse_qs(query).get("q", [""])[0])
                rendered = value if vulnerable else html.escape(value)
                body = f"<html><body><p>echo: {rendered}</p></body></html>".encode("utf-8")
                self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if not vulnerable:
                for key, value in SECURE_HEADERS.items():
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    return Handler


class Server:
    def __init__(self, vulnerable: bool):
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), _handler(vulnerable))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.httpd.shutdown()
        self.httpd.server_close()


def _context(client, auth, tmp_path) -> ExecutionContext:
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    with SessionLocal() as db:
        row = LocalTask(
            user_id=user_id,
            chat_id=chat_id,
            title="security acceptance",
            original_user_request="Проверь безопасность локального приложения",
            status="EXECUTING",
            current_phase="EXECUTING",
        )
        db.add(row)
        db.commit()
        task_id = row.id

    async def emit(event, value):
        return None

    context = ExecutionContext(
        user_id,
        chat_id,
        None,
        ToolLimits(max_calls=20, hard_max_calls=30),
        emit,
        mode="off",
        computer_mode="trusted",
        settings=WebSettings(computer_mode="trusted", workspace_roots=[str(tmp_path)], default_mode="off"),
        user_prompt="Проверь безопасность локального приложения",
        host_online=True,
    )
    context.task_id = task_id
    return context


def test_active_scan_confirms_real_findings_then_retest_fixes_them(client, auth, tmp_path):
    context = _context(client, auth, tmp_path)
    executor = ToolExecutor(make_registry())

    with Server(vulnerable=True) as server:
        target = f"http://127.0.0.1:{server.port}"
        result = asyncio.run(
            executor.execute(
                "security_active",
                {"action": "scan", "target": target, "allowed_targets": [target], "authorized": True},
                context,
            )
        )
        payload = json.loads(result.text)
        assert payload["ok"] is True, payload
        assert payload["open"] >= 5, payload["findings"]
        titles = {item["title"] for item in payload["findings"]}
        assert any("content-security-policy" in title.lower() for title in titles)
        assert any("reflected" in title.lower() for title in titles)
        assert any("diagnostic endpoint" in title.lower() for title in titles)
        confirmed = [item for item in payload["findings"] if item["status"] == "CONFIRMED"]
        assert len(confirmed) >= 5
        # Every confirmed finding carries the evidence that reproduced it.
        assert all(item["evidence"] for item in confirmed)

    with Server(vulnerable=False) as server:
        target = f"http://127.0.0.1:{server.port}"
        result = asyncio.run(
            executor.execute(
                "security_active",
                {"action": "retest", "target": target, "allowed_targets": [target], "authorized": True},
                context,
            )
        )
        payload = json.loads(result.text)
        assert payload["ok"] is True, payload
        assert payload["open"] == 0, payload["findings"]
        assert payload["fixed"] >= 5

    with SessionLocal() as db:
        row = db.scalar(select(LocalTask).where(LocalTask.id == context.task_id))
        stored = (row.verification or {}).get("security") or {}
        assert stored.get("open") == 0
        statuses = {item["status"] for item in stored["findings"].values()}
        assert statuses <= {"FIXED", "FALSE_POSITIVE"}, statuses


def test_a_target_outside_the_scope_is_refused_before_any_request(client, auth, tmp_path):
    context = _context(client, auth, tmp_path)
    executor = ToolExecutor(make_registry())
    result = asyncio.run(
        executor.execute(
            "security_active",
            {
                "action": "scan",
                "target": "http://192.0.2.10:8080",
                "allowed_targets": ["http://127.0.0.1:8000"],
                "authorized": True,
            },
            context,
        )
    )
    payload = json.loads(result.text)
    assert payload["ok"] is False
    assert payload["error"] == "scope_denied"

    result = asyncio.run(
        executor.execute(
            "security_active",
            {
                "action": "scan",
                "target": "http://127.0.0.1:8000",
                "allowed_targets": ["http://127.0.0.1:8000"],
                "authorized": False,
            },
            context,
        )
    )
    assert json.loads(result.text)["error"] == "scope_not_authorized"


def test_findings_never_become_fixed_without_a_retest():
    confirmed = {
        "header:content-security-policy:127.0.0.1:1": {
            "status": active_security.CONFIRMED,
            "id": "x",
            "title": "t",
        }
    }
    # A scan that still reproduces the issue keeps it CONFIRMED.
    again = active_security.merge(confirmed, dict(confirmed))
    assert again["header:content-security-policy:127.0.0.1:1"]["status"] == "CONFIRMED"
    # Only a retest that no longer produces it marks it FIXED.
    fixed = active_security.merge(confirmed, {})
    assert fixed["header:content-security-policy:127.0.0.1:1"]["status"] == "FIXED"
    # And a later regression reopens it.
    reopened = active_security.merge(fixed, dict(confirmed))
    assert reopened["header:content-security-policy:127.0.0.1:1"]["status"] == "REOPENED"
