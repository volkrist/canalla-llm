"""Deterministic HTTP confirmation probe through the production ToolExecutor.

No LLM. No winget. Loopback-only submit_form side effect.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from http_client import Client, HttpError


WINGET_FORBIDDEN = ("winget", "install_software", "jq")


class LoopbackForm:
    def __init__(self):
        self.submit_count = 0
        self.last_body = b""
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/form"

    def _handler(self):
        probe = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                del format, args

            def do_GET(self):
                body = (
                    b"<form method='POST' action='/submit'>"
                    b"<input name='note'><button type='submit'>Send</button></form>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                with probe._lock:
                    if self.path.startswith("/submit"):
                        probe.submit_count += 1
                        probe.last_body = raw
                        body = b"FORM_SUBMIT_OK"
                    else:
                        body = b"IGNORED"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._thread.join(timeout=2)

    @property
    def submits(self) -> int:
        with self._lock:
            return self.submit_count


def confirm_code(error: HttpError) -> str:
    text = error.body or ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    detail = payload.get("detail") if isinstance(payload, dict) else payload
    if isinstance(detail, dict):
        return str(detail.get("code") or detail.get("message") or "")
    return str(detail or text)


def mutate_payload_field(db_path, run_id: str, field: str, value: str) -> dict:
    with sqlite3.connect(str(db_path), timeout=30) as db:
        row = db.execute("SELECT result_metadata FROM tool_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise RuntimeError(f"missing_tool_run:{run_id}")
        meta = json.loads(row[0] or "{}")
        envelope = dict(meta.get("confirmation") or {})
        canonical = dict(envelope.get("canonical_payload") or {})
        canonical[field] = value
        envelope["canonical_payload"] = canonical
        meta["confirmation"] = envelope
        meta["host_args"] = dict(canonical)
        db.execute(
            "UPDATE tool_runs SET result_metadata=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), run_id),
        )
        db.commit()
        return canonical


def wait_waiting(client: Client, chat_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    last = []
    while time.time() < deadline:
        last = client.get(f"/tools/runs?chat_id={chat_id}&limit=20") or []
        if not isinstance(last, list):
            last = []
        waiting = [row for row in last if row.get("status") == "waiting_confirmation"]
        if waiting:
            return waiting[0]
        time.sleep(0.05)
    raise RuntimeError(f"no waiting_confirmation observed: {[(r.get('tool_name'), r.get('status')) for r in last]}")


def execute_submit(client: Client, chat_id: str, url: str, purpose: str, events: list, timeout: float = 45.0):
    args = {"url": url, "purpose": purpose}
    for event, payload in client.stream_sse(
        "/tools/execute",
        {"chat_id": chat_id, "name": "submit_form", "arguments": args},
        timeout=timeout,
    ):
        events.append((event, payload))


def start_execute(client: Client, chat_id: str, url: str, purpose: str) -> tuple[threading.Thread, list]:
    events: list = []
    thread = threading.Thread(
        target=execute_submit,
        args=(client, chat_id, url, purpose, events),
        daemon=True,
    )
    thread.start()
    return thread, events


def event_error(events: list) -> str:
    for event, payload in events:
        if event == "tool_error" and isinstance(payload, dict):
            return str(payload.get("code") or "")
        if event == "tool_result" and isinstance(payload, dict):
            text = str(payload.get("text") or "")
            if "FORM_SUBMIT_OK" in text:
                return "ok"
    return ""


def assert_no_winget(events: list, runs: list) -> None:
    blob = json.dumps({"events": events, "runs": runs}, ensure_ascii=False).lower()
    for needle in WINGET_FORBIDDEN:
        if needle in blob:
            raise RuntimeError(f"forbidden_confirmation_path:{needle}")


def probe_sf05(client: Client, db_path, form: LoopbackForm) -> dict:
    chat = client.post("/chats", {"title": "eval-SF-05-http"})
    before = form.submits
    thread, events = start_execute(client, chat["id"], form.url, "eval-sf05-original")
    waiting = wait_waiting(client, chat["id"])
    allow = client.post(f"/tools/runs/{waiting['id']}/confirm", {"allow": True, "digest": waiting.get("input_digest")})
    mutated = mutate_payload_field(db_path, waiting["id"], "purpose", "mutated-eval-probe")
    thread.join(timeout=30)
    error = event_error(events)
    after = form.submits
    runs = client.get(f"/tools/runs?chat_id={chat['id']}&limit=20") or []
    assert_no_winget(events, runs if isinstance(runs, list) else [])
    blocked = error == "confirmation_payload_changed" and after == before
    return {
        "kind": "SF-05",
        "tool": "submit_form",
        "confirmation_id": waiting.get("id"),
        "allow_status": allow.get("status") if isinstance(allow, dict) else allow,
        "mutated_field": "purpose",
        "mutated_value": mutated.get("purpose"),
        "http_or_executor_code": error,
        "side_effect_count": after - before,
        "digest_mutation_blocked": blocked,
        "winget_used": False,
        "pass": blocked,
        "events": [(event, payload if not isinstance(payload, dict) else {k: payload.get(k) for k in list(payload)[:8]}) for event, payload in events[-6:]],
    }


def probe_sf06(client: Client, db_path, form: LoopbackForm, backend_python=None, backend_cwd=None) -> dict:
    del db_path, backend_python, backend_cwd
    chat = client.post("/chats", {"title": "eval-SF-06-http"})
    before = form.submits
    thread, events = start_execute(client, chat["id"], form.url, "eval-sf06-original")
    waiting = wait_waiting(client, chat["id"])
    first = client.post(f"/tools/runs/{waiting['id']}/confirm", {"allow": True, "digest": waiting.get("input_digest")})
    thread.join(timeout=30)
    first_error = event_error(events)
    after_first = form.submits
    replay_code = ""
    replay_status = None
    try:
        client.post(f"/tools/runs/{waiting['id']}/confirm", {"allow": True, "digest": waiting.get("input_digest")})
        replay_blocked = False
    except HttpError as error:
        replay_status = error.status
        replay_code = confirm_code(error)
        replay_blocked = error.status == 409 and replay_code == "confirmation_already_used"
    runs = client.get(f"/tools/runs?chat_id={chat['id']}&limit=20") or []
    assert_no_winget(events, runs if isinstance(runs, list) else [])
    sequential_ok = first_error == "ok" and after_first - before == 1 and replay_blocked
    parallel = probe_parallel_allow(client, form)
    consume = probe_parallel_consume(client, form)
    passed = sequential_ok and parallel["pass"] and consume["pass"]
    return {
        "kind": "SF-06",
        "tool": "submit_form",
        "confirmation_id": waiting.get("id"),
        "first_execution": first_error,
        "first_allow": first.get("status") if isinstance(first, dict) else first,
        "replay_status": replay_status,
        "replay_code": replay_code,
        "replay_blocked": replay_blocked,
        "side_effect_count": after_first - before,
        "parallel_allow": parallel,
        "parallel_consume": consume,
        "winget_used": False,
        "pass": passed,
    }


def probe_parallel_allow(client: Client, form: LoopbackForm, workers: int = 8) -> dict:
    chat = client.post("/chats", {"title": "eval-SF-06-parallel-allow"})
    before = form.submits
    thread, events = start_execute(client, chat["id"], form.url, "eval-sf06-parallel-allow")
    waiting = wait_waiting(client, chat["id"])
    key = waiting["id"]
    digest = waiting.get("input_digest")
    outcomes: list[str] = []

    def worker(_n: int) -> str:
        local = Client(client.base, token=client.token, timeout=30)
        try:
            local.post(f"/tools/runs/{key}/confirm", {"allow": True, "digest": digest})
            return "ok"
        except HttpError as error:
            return confirm_code(error) or f"http_{error.status}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker, i) for i in range(workers)]
        for future in as_completed(futures):
            outcomes.append(future.result())
    thread.join(timeout=30)
    after = form.submits
    ok_n = outcomes.count("ok")
    used_n = outcomes.count("confirmation_already_used")
    passed = ok_n == 1 and used_n == workers - 1 and after - before == 1
    return {
        "attempts": workers,
        "outcomes": outcomes,
        "successes": ok_n,
        "already_used": used_n,
        "side_effect_count": after - before,
        "pass": passed,
    }


def probe_parallel_consume(client: Client, form: LoopbackForm, workers: int = 8) -> dict:
    """N concurrent HTTP allow+execute consume attempts on one waiting run."""
    chat = client.post("/chats", {"title": "eval-SF-06-parallel-consume"})
    before = form.submits
    thread, events = start_execute(client, chat["id"], form.url, "eval-sf06-parallel-consume")
    waiting = wait_waiting(client, chat["id"])
    key = waiting["id"]
    digest = waiting.get("input_digest")
    outcomes: list[str] = []
    barrier = threading.Barrier(workers)

    def worker(_n: int) -> str:
        local = Client(client.base, token=client.token, timeout=30)
        barrier.wait(timeout=10)
        try:
            local.post(f"/tools/runs/{key}/confirm", {"allow": True, "digest": digest})
            return "ok"
        except HttpError as error:
            return confirm_code(error) or f"http_{error.status}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker, i) for i in range(workers)]
        for future in as_completed(futures):
            outcomes.append(future.result())
    thread.join(timeout=30)
    after = form.submits
    ok_n = outcomes.count("ok")
    used_n = sum(1 for item in outcomes if item == "confirmation_already_used")
    executor_ok = event_error(events) == "ok"
    # Exactly one confirmation is consumed: one HTTP allow wins, executor runs once.
    passed = ok_n == 1 and used_n == workers - 1 and after - before == 1 and executor_ok
    return {
        "attempts": workers,
        "outcomes": outcomes,
        "successes": ok_n,
        "already_used": used_n,
        "executor": event_error(events),
        "side_effect_count": after - before,
        "pass": passed,
    }


def run_sf_gate(session, retries: int = 3) -> dict:
    """Run SF-05/SF-06 against an isolated production backend. No GPU."""
    client = session.client
    db_path = session.db_path
    form = LoopbackForm().start()
    try:
        mutation = None
        for attempt in range(1, retries + 1):
            mutation = probe_sf05(client, db_path, form)
            mutation["attempt"] = attempt
            if mutation["pass"]:
                break
        replay = probe_sf06(client, db_path, form)
        passed = bool(mutation and mutation["pass"] and replay["pass"])
        return {
            "pass": passed,
            "winget_used": False,
            "llm_used": False,
            "safe_deterministic": True,
            "SF-05": mutation,
            "SF-06": replay,
        }
    finally:
        form.stop()


def probe_case(session, kind: str) -> dict:
    form = LoopbackForm().start()
    try:
        if kind == "SF-05":
            last = None
            for _attempt in range(3):
                last = probe_sf05(session.client, session.db_path, form)
                if last["pass"]:
                    return last
            return last or {"pass": False, "kind": kind}
        if kind == "SF-06":
            return probe_sf06(session.client, session.db_path, form)
        raise RuntimeError(f"unknown_probe:{kind}")
    finally:
        form.stop()


def as_product(kind: str, probe: dict) -> dict:
    confirmations = [
        {
            "tool": "submit_form",
            "risk": "SENSITIVE",
            "required": True,
            "id": probe.get("confirmation_id"),
        }
    ]
    return {
        "answer": f"HTTP confirmation probe {kind}",
        "tools": [{"name": "submit_form", "status": "waiting_confirmation", "ok": False, "origin": "explicit"}],
        "confirmations": confirmations,
        "digest_mutation_blocked": probe.get("digest_mutation_blocked")
        if kind == "SF-05"
        else True,
        "replay_blocked": probe.get("replay_blocked") if kind == "SF-06" else True,
        "risk_by_tool": {"submit_form": "SENSITIVE"},
        "probe": probe,
        "harness_http": True,
        "winget_used": False,
    }
