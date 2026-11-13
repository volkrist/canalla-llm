"""Produce the evidence files for the three 1.2.7 acceptances that can be captured offline."""

from __future__ import annotations

import html
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1] / "apps" / "backend"
sys.path.insert(0, str(BACKEND))

from app.agent import security as active_security  # noqa: E402

HEADERS = {
    "Content-Security-Policy": "default-src 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def _handler(vulnerable: bool):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "AcceptanceSrv"

        def do_GET(self):  # noqa: N802
            path, _, query = self.path.partition("?")
            if path == "/debug":
                body = b"DEBUG build=dev diagnostics" if vulnerable else b"not found"
                self.send_response(200 if vulnerable else 404)
            else:
                value = ""
                if "q=" in query:
                    from urllib.parse import parse_qs, unquote

                    value = unquote(parse_qs(query).get("q", [""])[0])
                body = (
                    f"<html><body><p>echo: {value if vulnerable else html.escape(value)}</p></body></html>"
                ).encode()
                self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if not vulnerable:
                for key, value in HEADERS.items():
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    return Handler


def _serve(vulnerable: bool):
    server = http.server.HTTPServer(("127.0.0.1", 0), _handler(vulnerable))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def capture_security() -> dict:
    out = HERE / "security"
    out.mkdir(parents=True, exist_ok=True)
    vulnerable = _serve(True)
    base = f"http://127.0.0.1:{vulnerable.server_address[1]}"
    scope = active_security.ActiveScope("local-stand", [base], authorized=True)
    assessment = active_security.ActiveAssessment(scope)
    findings = assessment.probe_http(base)
    findings.update(assessment.probe_debug_endpoint(base))
    confirmed = {key: value.public() for key, value in findings.items()}
    (out / "before.json").write_text(
        json.dumps({"scope": scope.public(), "findings": confirmed}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    vulnerable.shutdown()
    vulnerable.server_close()

    fixed = _serve(False)
    base = f"http://127.0.0.1:{fixed.server_address[1]}"
    scope2 = active_security.ActiveScope("local-stand", [base], authorized=True)
    again = active_security.ActiveAssessment(scope2)
    current = again.probe_http(base)
    current.update(again.probe_debug_endpoint(base))
    merged = active_security.merge(
        confirmed, {key: value.public() for key, value in current.items()}
    )
    (out / "after.json").write_text(
        json.dumps(
            {
                "scope": scope2.public(),
                "findings": merged,
                "open": len(active_security.open_findings(merged)),
                "fixed": sum(1 for item in merged.values() if item["status"] == "FIXED"),
                "adapters": active_security.adapters(),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    fixed.shutdown()
    fixed.server_close()
    return {
        "confirmed": len(confirmed),
        "open_after_fix": len(active_security.open_findings(merged)),
        "fixed": sum(1 for item in merged.values() if item["status"] == "FIXED"),
    }


def capture_recovery() -> dict:
    out = HERE / "recovery"
    out.mkdir(parents=True, exist_ok=True)
    root = out / "workspace"
    root.mkdir(exist_ok=True)
    for stale in root.glob("*"):
        stale.unlink()
    database = out / "recovery.db"
    if database.exists():
        database.unlink()
    env = dict(os.environ, DATABASE_URL="sqlite:///" + database.as_posix())
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND), env=env, check=True, capture_output=True,
    )
    worker = BACKEND / "tests" / "helpers" / "recovery_worker.py"
    first = subprocess.run(
        [sys.executable, str(worker), "--phase", "interrupt", "--root", str(root)],
        cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=240,
    )
    import sqlite3

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "select id, status, finished_at from local_tasks order by started_at desc limit 1"
    ).fetchone()
    task_id = row["id"]
    second = subprocess.run(
        [sys.executable, str(worker), "--phase", "resume", "--root", str(root),
         "--task-id", task_id],
        cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=240,
    )
    row = connection.execute(
        "select id, status, last_error, completion_summary, verification from local_tasks where id=?",
        (task_id,),
    ).fetchone()
    events = [
        item["kind"]
        for item in connection.execute(
            "select kind from task_events where task_id=? order by created_at", (task_id,)
        )
    ]
    connection.close()
    payload = {
        "interrupt_exit_code": first.returncode,
        "resume_exit_code": second.returncode,
        "task_id": task_id,
        "status_after_resume": row["status"],
        "completion_summary": row["completion_summary"],
        "events": events,
        "content": (root / "steps.txt").read_text(encoding="utf-8"),
        "files": sorted(path.name for path in root.iterdir()),
    }
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload


if __name__ == "__main__":
    result = {"security": capture_security(), "recovery": capture_recovery()}
    print(json.dumps(result, ensure_ascii=False, indent=1))
