"""Deterministic OpenAI-compatible inference for the free LEVEL 7 resume E2E.

Only inference is fake. The backend, its SQLite, the task controller, the tools, the checkpoint and
the verifier are the real product. The planner counter lives in a file, so it survives the backend
death the test performs on purpose.

    GET  /v1/models            -> 200 (health)
    POST /v1/chat/completions  -> non-stream: the next tool call; stream: a short final answer
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOCK = threading.Lock()
STATE: Path | None = None
WORKSPACE: Path | None = None

APP = "def add(a, b):\n    return a + b\n"
TEST = "from app import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def scenario() -> list[tuple[str, dict] | None]:
    """A real code change and a real test run, in the order the controller checks.

    `verification.tests` only becomes `passed` through `run_python` whose argv contains `-m pytest`
    and whose exit code is 0; every later write marks it stale again. So the test run is the last
    work step before the final independent check.
    """
    ws = str(WORKSPACE or ".")
    return [
        ("write_file", {"path": "app.py", "content": APP}),
        ("write_file", {"path": "test_smoke.py", "content": TEST}),
        ("write_file", {"path": "part3.txt", "content": "three\n"}),
        ("write_file", {"path": "final.txt", "content": "done\n"}),
        (
            "run_python",
            {
                "argv": ["-m", "pytest", "-q"],
                "cwd": ws,
                "purpose": "verify project tests",
                "timeout_seconds": 120,
            },
        ),
        (
            "verify_result",
            {
                "checks": [
                    {"kind": "file_exists", "target": f"{ws}/app.py"},
                    {"kind": "file_exists", "target": f"{ws}/test_smoke.py"},
                ],
                "purpose": "level 7 resume e2e",
            },
        ),
        None,
    ]


def next_step(request: dict) -> tuple[int, "tuple[str, dict] | None"]:
    """Counter for the write phase, transcript for everything after the test run.

    The counter drifted by one call after a restart and replayed a write after pytest had passed,
    which correctly marked `verification.tests` stale. Once pytest's own output is in the
    transcript, no further write is ever proposed: only the independent check remains.
    """
    assert STATE is not None
    text = json.dumps(request, ensure_ascii=False)
    steps = scenario()
    verify = next(item for item in steps if item and item[0] == "verify_result")
    with LOCK:
        stored = json.loads(STATE.read_text(encoding="utf-8")) if STATE.is_file() else {}
        count = int(stored.get("planner", 0)) + 1
        asked = bool(stored.get("verification_asked"))
        if "test session starts" in text and not asked:
            asked = True
        STATE.write_text(
            json.dumps({"planner": count, "verification_asked": asked}), encoding="utf-8"
        )
    if "test session starts" in text:
        return count, (None if asked and count > 0 else verify)
    index = min(count - 1, len(steps) - 1)
    return count, steps[index]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003 - quiet server
        return

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") in {"/v1/models", "/models"}:
            self._json({"object": "list", "data": [{"id": "fake-qwen", "object": "model"}]})
            return
        self._json({"error": "not_found"}, 404)

    def do_POST(self):  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json({"error": "not_found"}, 404)
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            request = json.loads(raw or b"{}")
        except ValueError:
            self._json({"error": "bad_json"}, 400)
            return
        count, step = next_step(request)
        if not request.get("stream"):
            # A deliberate gap between the planner's answer and the next call: the test kills the
            # backend as soon as the pytest run is recorded, and without this gap the final check
            # could already be in flight, leaving no work for the resumed process to prove.
            time.sleep(1.2)
        if request.get("stream"):
            body = (
                'data: {"choices":[{"delta":{"content":"resume complete"},"index":0}]}\n\n'
                "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if step is None:
            message = {"role": "assistant", "content": "resume complete", "tool_calls": []}
            finish = "stop"
        else:
            tool, arguments = step
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call_{count}",
                        "type": "function",
                        "function": {"name": tool, "arguments": json.dumps(arguments)},
                    }
                ],
            }
            finish = "tool_calls"
        self._json(
            {
                "id": f"chatcmpl-{count}",
                "object": "chat.completion",
                "model": "fake-qwen",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )


def main() -> int:
    global STATE, WORKSPACE
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18080
    state_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
    state_dir.mkdir(parents=True, exist_ok=True)
    STATE = state_dir / "fake-llm-state.json"
    if len(sys.argv) > 3:
        WORKSPACE = Path(sys.argv[3])
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(json.dumps({"fake_llm": "ready", "port": port, "state": str(STATE)}), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
