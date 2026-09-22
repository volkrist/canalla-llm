"""Standalone Pod entrypoint: existing model/scripts + authenticated streaming gateway.

Only the gateway port is published through RunPod HTTPS. The llama.cpp port is
not published. This file uses the container's Python standard library only.
"""

import hmac
import http.client
import json
import os
import select
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def gateway_server(key, llm_port, port):
    if len(key) < 32:
        raise ValueError("Gateway key is required")

    class Gateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            pass  # Never log Authorization, prompts, paths or upstream bodies.

        def forward(self):
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), ("Bearer " + key).encode()):
                self.send_error(401, "Unauthorized")
                return
            allowed = {("GET", "/v1/models"), ("POST", "/v1/chat/completions")}
            if (self.command, self.path) not in allowed:
                self.send_error(404)
                return
            if self.headers.get("Transfer-Encoding"):
                self.send_error(400)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400)
                return
            if not 0 <= length <= 1_048_576:
                self.send_error(413)
                return
            self.connection.settimeout(120)
            body = self.rfile.read(length)
            upstream = http.client.HTTPConnection("127.0.0.1", llm_port, timeout=120)
            finished = threading.Event()
            sent = False
            upstream_socket = None
            response = None

            def disconnected():
                while not finished.wait(0.2):
                    try:
                        readable, _, _ = select.select([self.connection], [], [], 0)
                        if readable and self.connection.recv(1, socket.MSG_PEEK) == b"":
                            if upstream_socket:
                                upstream_socket.shutdown(socket.SHUT_RDWR)
                            upstream.close()
                            return
                    except OSError:
                        upstream.close()
                        return

            try:
                upstream.request(
                    self.command, self.path, body=body or None, headers={"Content-Type": "application/json"}
                )
                upstream_socket = upstream.sock
                threading.Thread(target=disconnected, daemon=True).start()
                response = upstream.getresponse()
                self.send_response(response.status)
                self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()
                sent = True
                while chunk := response.read1(4096):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, http.client.HTTPException):
                if not sent:
                    self.send_error(503, "Model unavailable")
            finally:
                finished.set()
                if response:
                    response.close()
                upstream.close()
                self.close_connection = True

        do_GET = forward
        do_POST = forward

    return ThreadingHTTPServer(("0.0.0.0", port), Gateway)


def model_state(port, model):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", "/v1/models")
        response = connection.getresponse()
        if response.status == 503:
            return "loading"
        if response.status != 200:
            return "unavailable"
        data = json.loads(response.read(262144))
        return "ready" if any(row.get("id") == model for row in data.get("data", [])) else "wrong_model"
    except ConnectionRefusedError:
        return "absent"
    except (OSError, ValueError, TypeError, AttributeError, http.client.HTTPException):
        return "unavailable"
    finally:
        connection.close()


def main():
    import fcntl  # Linux container only; local gateway tests do not execute main.

    def phase(value):
        print("ALEX_LLM_PHASE=" + value, flush=True)

    key = os.environ["ALEX_GATEWAY_KEY"]
    port = int(os.environ.get("ALEX_LLM_PORT", "8080"))
    gateway_port = int(os.environ.get("ALEX_GATEWAY_PORT", "9000"))
    model = os.environ["ALEX_LLM_MODEL"]
    timeout = int(os.environ.get("ALEX_STARTUP_TIMEOUT", "900"))
    with open("/tmp/alex-llm-runtime.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # Another wrapper owns startup; never start a second model process.
        phase("mounting_storage")
        for path in (
            "/workspace/start-llm.sh",
            "/workspace/check-llm.sh",
            "/workspace/models/orcarouter-qwen38/orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf",
        ):
            if not Path(path).is_file():
                phase("error")
                return
        server = gateway_server(key, port, gateway_port)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        if model_state(port, model) == "absent":
            phase("starting_llm")
            environment = {name: value for name, value in os.environ.items() if name != "ALEX_GATEWAY_KEY"}
            with open("/tmp/alex-llm-start.log", "ab") as log:
                subprocess.Popen(["bash", "/workspace/start-llm.sh"], stdout=log, stderr=log, env=environment)
        phase("loading_model")
        deadline = time.monotonic() + timeout
        ready = False
        try:
            while True:
                state = model_state(port, model)
                if state == "ready":
                    if not ready:
                        try:
                            with open("/tmp/alex-llm-check.log", "ab") as log:
                                checked = subprocess.run(
                                    ["bash", "/workspace/check-llm.sh"],
                                    stdout=log,
                                    stderr=log,
                                    timeout=20,
                                    env={
                                        name: value
                                        for name, value in os.environ.items()
                                        if name != "ALEX_GATEWAY_KEY"
                                    },
                                )
                            if checked.returncode != 0:
                                phase("error")
                                return
                        except subprocess.TimeoutExpired:
                            phase("error")
                            return
                    ready = True
                    phase("ready")
                elif ready or state == "wrong_model" or time.monotonic() >= deadline:
                    phase("error")
                    return
                time.sleep(3)
        finally:
            server.shutdown()


if __name__ == "__main__":
    main()
