import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.compute.remote_runtime import gateway_server


@pytest.fixture
def gateway():
    calls = []
    interrupted = threading.Event()

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append(self.path)
            payload = json.dumps({"data": [{"id": "test-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b'data: {"choices":[{"delta":{"content":"token"}}]}\n\n')
                    self.wfile.flush()
                    time.sleep(0.03)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                interrupted.set()

    model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    proxy = gateway_server("test-key-" + "a" * 40, model.server_port, 0)
    for server in (model, proxy):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    yield (
        f"http://127.0.0.1:{proxy.server_port}",
        {"Authorization": "Bearer test-key-" + "a" * 40},
        calls,
        interrupted,
    )
    for server in (proxy, model):
        server.shutdown()
        server.server_close()


def test_gateway_authentication_and_route_allowlist(gateway):
    base, headers, calls, _ = gateway
    assert httpx.get(base + "/v1/models").status_code == 401
    assert httpx.get(base + "/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert not calls
    assert httpx.get(base + "/admin", headers=headers).status_code == 404
    assert not calls
    assert httpx.get(base + "/v1/models", headers=headers).json()["data"][0]["id"] == "test-model"
    assert calls == ["/v1/models"]


def test_gateway_disconnect_closes_model_stream(gateway):
    base, headers, _, interrupted = gateway
    with httpx.stream(
        "POST", base + "/v1/chat/completions", headers=headers, json={"stream": True}
    ) as response:
        assert response.status_code == 200
        assert "token" in next(response.iter_lines())
    assert interrupted.wait(2), "Gateway kept consuming tokens after its client disconnected"


def test_gateway_refuses_empty_key():
    with pytest.raises(ValueError):
        gateway_server("", 8080, 0)
