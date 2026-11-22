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
WORKSPACE: Path | None = None

# (tool, arguments) in order. Only `write_file` is used: its schema is path+content, so the fake
# cannot invent an argument shape the product does not have.
def _collect_paths(node, out: list[str]) -> None:
    """Absolute paths from the transcript, so the verifier checks real files, not the cwd."""
    if isinstance(node, dict):
        for value in node.values():
            _collect_paths(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_paths(value, out)
    elif isinstance(node, str) and (node.endswith("part1.txt") or node.endswith("final.txt")):
        out.append(node)


SCENARIO: list[tuple[str, dict] | None] = [
    ("write_file", {"path": "part1.txt", "content": "one\n"}),
    ("write_file", {"path": "part2.txt", "content": "two\n"}),
    ("write_file", {"path": "part3.txt", "content": "three\n"}),
    ("write_file", {"path": "final.txt", "content": "done\n"}),
    None,  # no more tools: the turn finishes with an answer
]


def next_step(request: dict) -> tuple[int, tuple[str, dict] | None]:
    """Decide from the conversation itself, so the scenario survives any number of restarts.

    A counter alone drifted: an extra planning request shifted every later step. The transcript is
    the durable state — once it contains a final.txt result the turn must verify, and once
    verify_result was already asked the turn must finish.
    """
    assert STATE is not None
    with LOCK:
        stored = json.loads(STATE.read_text(encoding="utf-8")) if STATE.is_file() else {}
        count = int(stored.get("planner", 0)) + 1
        asked = bool(stored.get("verification_asked"))
        text = json.dumps(request)
        if asked:
            STATE.write_text(json.dumps({"planner": count, "verification_asked": True}), encoding="utf-8")
            return count, None
        if "final.txt" in text:
            STATE.write_text(json.dumps({"planner": count, "verification_asked": True}), encoding="utf-8")
            # The runner names the real workspace, so the checks are absolute by construction. The
            # transcript only carries the model's relative call arguments, and resolving those
            # against the backend's cwd is exactly what produced CHECK_REFUSED.
            if WORKSPACE is not None:
                chosen = [str(WORKSPACE / "part1.txt"), str(WORKSPACE / "final.txt")]
            else:
                targets: list[str] = []
                _collect_paths(request, targets)
                chosen = [item for item in sorted(set(targets)) if item.endswith(("part1.txt", "final.txt"))]
                if not chosen:
                    chosen = ["part1.txt", "final.txt"]
            return count, (
                "verify_result",
                {
                    "checks": [{"kind": "file_exists", "target": item} for item in chosen],
                    "purpose": "level 7 resume e2e",
                },
            )
        STATE.write_text(json.dumps({"planner": count}), encoding="utf-8")
    if False:
        return count, (
            "verify_result",
            {
                "checks": [
                    {"kind": "file_exists", "target": "part1.txt"},
                    {"kind": "file_exists", "target": "final.txt"},
                ],
                "purpose": "level 7 resume e2e",
            },
        )
    index = min(count - 1, 3)
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
        count, step = next_step(request)
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
    if len(sys.argv) > 3:
        WORKSPACE = Path(sys.argv[3])
    state_dir.mkdir(parents=True, exist_ok=True)
    STATE = state_dir / "fake-llm-state.json"
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(json.dumps({"fake_llm": "ready", "port": port, "state": str(STATE)}), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
