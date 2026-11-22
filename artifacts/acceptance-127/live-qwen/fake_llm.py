"""Deterministic OpenAI-compatible inference for the free LEVEL 7 resume E2E.

Only inference is fake. The backend, its SQLite, the task controller, the tools, the checkpoint and
the verifier are the real product. The scenario counter lives in a file, so it survives the backend
death the test performs on purpose.

    GET  /v1/models            -> 200 (health)
    POST /v1/chat/completions  -> non-stream: the next tool call; stream: a short final answer
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOCK = threading.Lock()
STATE: Path | None = None

# (tool, arguments) in order. Only `write_file` is used: its schema is path+content, so the fake
# cannot invent an argument shape the product does not have.
SCENARIO: list[tuple[str, dict] | None] = [
    ("write_file", {"path": "part1.txt", "content": "one\n"}),
    ("write_file", {"path": "part2.txt", "content": "two\n"}),
    ("write_file", {"path": "part3.txt", "content": "three\n"}),
    ("write_file", {"path": "final.txt", "content": "done\n"}),
    None,  # no more tools: the turn finishes with an answer
]


def next_step() -> tuple[int, tuple[str, dict] | None]:
    assert STATE is not None
    with LOCK:
        count = int(json.loads(STATE.read_text(encoding="utf-8")).get("planner", 0)) if STATE.is_file() else 0
        count += 1
        STATE.write_text(json.dumps({"planner": count}), encoding="utf-8")
    index = min(count - 1, len(SCENARIO) - 1)
    return count, SCENARIO[index]


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
        count, step = next_step()
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
    global STATE
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18080
    state_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
    state_dir.mkdir(parents=True, exist_ok=True)
    STATE = state_dir / "fake-llm-state.json"
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(json.dumps({"fake_llm": "ready", "port": port, "state": str(STATE)}), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
