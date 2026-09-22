"""Local Gateway fixture for ``apps/desktop/e2e/cloud-smoke.mjs``.

The installed-GUI cloud smoke needs two things the repository never had a fixture for: a
Gateway reachable on ``http://127.0.0.1:9011`` with a *disposable* database, and one
unredeemed activation code from that Gateway's operator CLI. This script provides both, runs
the smoke against them, and always tears the Gateway down again.

It is test support only. It never touches the deployed Gateway
(``https://gateway.12testers.store``), the real RunPod API, the real data root
(``%LOCALAPPDATA%\\Alex LLM``), the real credentials or the Network Volume.

WHAT IT STARTS

* a temporary directory (``alex-cloud-smoke-*`` under the OS temp dir) holding the throwaway
  ``gateway.db`` and the Gateway's own log;
* the real Gateway code from ``apps/gateway`` — ``alembic upgrade head`` on that database,
  one activation code through ``python -m gateway.cli create-code`` (a fixed code, that
  database only), then ``uvicorn gateway.main:app`` on ``127.0.0.1:9011`` exactly as the
  deployment runbook starts it;
* a loopback stand-in for the provider's read-only balance query, bound to an ephemeral port
  and used as ``RUNPOD_GRAPHQL_URL``. The Gateway is configured with a clearly fake
  ``RUNPOD_API_KEY``, so it reports ``provider_configured=true`` and the smoke can assert the
  *shared* balance without a real account. The stand-in answers that one query only, refuses
  any other credential, and listens on loopback only. The RunPod REST base
  (``https://api.runpod.io/v2``) is hardcoded in the product but nothing on this smoke's path
  calls it (no ``compute/ensure``, no ``compute/stop``), and the Gateway process additionally
  gets a dead loopback ``HTTPS_PROXY`` so an accidental provider call could not leave the
  machine.

HOW TO RUN (repository root, Git Bash or PowerShell — the smoke itself is Windows-only)

    apps/backend/.venv/Scripts/python.exe scripts/cloud-smoke-local.py

    --self-test     prove the fixture alone: Gateway up, code redeemed, token minted, shared
                    balance read — no GUI, no installed app, no provider traffic
    --keep          keep the temporary directory (database + Gateway log) for diagnosis
    --port 9011     Gateway port (the smoke drives the installed app to this exact URL)

The exit code is the smoke's own (0 = PASS). ``Ctrl-C`` stops the smoke and the Gateway and
removes the temporary directory. A hard interrupt can outrun the smoke's own cleanup: if it
leaves ``alex-llm.exe`` running, close it before the next run, because the smoke refuses to
start while another instance is alive.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DESKTOP = REPO / "apps" / "desktop"
GATEWAY_ROOT = REPO / "apps" / "gateway"
BACKEND_ROOT = REPO / "apps" / "backend"
VENV_PYTHON = BACKEND_ROOT / ".venv" / "Scripts" / "python.exe"
SMOKE = DESKTOP / "e2e" / "cloud-smoke.mjs"

GATEWAY_HOST = "127.0.0.1"
DEFAULT_PORT = 9011
# Throwaway values. The provider key is deliberately not a key: the stand-in below accepts
# exactly this string, so a run can never fall back to a real credential.
FAKE_PROVIDER_KEY = "smoke-local-not-a-runpod-key"
ACTIVATION_CODE = "SMOKE-LOCAL-ACTIVATION-0001"
BALANCE_USD = "12.34"
SPEND_PER_HR = "0.79"
# Outbound HTTPS from the Gateway process is routed to this closed loopback port.
DEAD_PROXY = "http://127.0.0.1:9"
NO_PROXY = "127.0.0.1,localhost,::1"

PROGRAM_DIRS = ("Canalla LLM", "Alex LLM")
COMPUTE_OPERATIONS = ("ensure", "stop", "creating", "starting_pod")


# ---------------------------------------------------------------------------------- provider


class ProviderStub:
    """Loopback stand-in for the read-only RunPod GraphQL balance query.

    It is not a provider double: it answers one query with one account and refuses every other
    path and credential, so a misconfigured run fails loudly instead of quietly reaching out.
    """

    def __init__(self) -> None:
        self.hits = 0
        self.rejected = 0
        self.port = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                if self.path != "/graphql":
                    stub.rejected += 1
                    self._send(404, {"errors": [{"message": "not found"}]})
                    return
                if self.headers.get("Authorization") != "Bearer " + FAKE_PROVIDER_KEY:
                    stub.rejected += 1
                    self._send(401, {"errors": [{"message": "unauthorized"}]})
                    return
                stub.hits += 1
                self._send(
                    200,
                    {
                        "data": {
                            "myself": {
                                "id": "smoke-local-account",
                                "clientBalance": BALANCE_USD,
                                "currentSpendPerHr": SPEND_PER_HR,
                            }
                        }
                    },
                )

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                stub.rejected += 1
                self._send(404, {"errors": [{"message": "not found"}]})

            def _send(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - http.server signature
                pass

        self._server = ThreadingHTTPServer((GATEWAY_HOST, 0), Handler)
        self._server.daemon_threads = True
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


# ------------------------------------------------------------------------------------ helpers


def gateway_env(database_url: str, jwt_secret: str, graphql_url: str) -> dict:
    """The Gateway's own environment. Never inherited by the app the smoke launches."""
    return {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "JWT_SECRET": jwt_secret,
        "RUNPOD_API_KEY": FAKE_PROVIDER_KEY,
        "RUNPOD_GRAPHQL_URL": graphql_url,
        # The Gateway reuses the backend's provider core as a library.
        "ALEX_BACKEND_LIB_DIR": str(BACKEND_ROOT),
        # Safety net, scoped to this process: the product's REST base is
        # https://api.runpod.io/v2 and is not configurable, so an accidental provider call
        # would have to leave the machine. httpx honours the environment proxy, and the
        # loopback no_proxy entry keeps the balance query direct.
        "HTTPS_PROXY": DEAD_PROXY,
        "NO_PROXY": NO_PROXY,
        "no_proxy": NO_PROXY,
    }


def run_checked(command: list[str], *, cwd: Path, env: dict, timeout: float = 300) -> str:
    result = subprocess.run(
        command, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(command)} failed with exit {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


def http_json(
    url: str, *, method: str = "GET", payload: dict | None = None, token: str = "", timeout: float = 5.0
) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def tail(path: Path, lines: int = 40) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return f"(no log at {path})"
    return "\n".join(content[-lines:])


def wait_for_health(port: int, child: subprocess.Popen, log_path: Path, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    url = f"http://{GATEWAY_HOST}:{port}/health"
    while time.time() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f"the Gateway exited with code {child.returncode}\n{tail(log_path)}")
        try:
            body = http_json(url)
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.5)
            continue
        if body.get("ready") is True and body.get("product") == "alex-llm-gateway":
            return body
        time.sleep(0.5)
    raise RuntimeError(f"the Gateway never became ready on {url}\n{tail(log_path)}")


def stop_process(child: subprocess.Popen | None, timeout: float = 20.0) -> None:
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        child.kill()
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((GATEWAY_HOST, port))
        except OSError:
            return False
    return True


def running_image(image: str) -> bool:
    listing = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image}"], capture_output=True, text=True
    ).stdout
    return image.lower() in listing.lower()


def installed_app() -> Path | None:
    local = os.environ.get("LOCALAPPDATA", "")
    for name in PROGRAM_DIRS:
        candidate = Path(local) / "Programs" / name / "alex-llm.exe"
        if candidate.is_file():
            return candidate
    return None


def node_binary() -> str | None:
    for name in ("node.exe", "node"):
        found = shutil.which(name)
        if found:
            return found
    return None


def check(condition: bool, name: str, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}{f' — {detail}' if detail else ''}")
    return bool(condition)


def preflight(port: int, gui: bool) -> str:
    """Fail fast, with the facts the smoke would otherwise discover much later."""
    if os.name != "nt":
        raise RuntimeError("the cloud smoke drives a Windows install (Credential Manager, WebView2 CDP)")
    if not VENV_PYTHON.is_file():
        raise RuntimeError(f"backend virtualenv python missing: {VENV_PYTHON}")
    if not (DESKTOP / "node_modules" / "playwright").is_dir():
        raise RuntimeError("playwright is not installed in apps/desktop (npm install)")
    node = node_binary()
    if node is None:
        raise RuntimeError("node is not on PATH")
    if not port_is_free(port):
        raise RuntimeError(
            f"port {port} is already in use (a leftover Gateway?) — stop it or pass --port"
        )
    if not gui:
        # The self-test never touches the installed app, so a running app is not a problem.
        return node
    if not SMOKE.is_file():
        raise RuntimeError(f"smoke missing: {SMOKE}")
    if installed_app() is None:
        raise RuntimeError(
            "the installed app was not found under %LOCALAPPDATA%\\Programs — the smoke drives "
            "the REAL installed build, so install Canalla Cloud 1.1.0 first"
        )
    if running_image("alex-llm.exe"):
        raise RuntimeError(
            "an alex-llm.exe process is already running; close the app first — the smoke drives "
            "the installed app through its own WebView2 debug port and cannot share it with a "
            "running instance (use --self-test to exercise the Gateway alone)"
        )
    return node


# --------------------------------------------------------------------------------- self-test


def self_test(base: str, database: Path) -> bool:
    """The endpoint contract the smoke depends on, without the GUI and without the app."""
    health = http_json(f"{base}/health")
    ok = check(
        health.get("ready") is True and health.get("gateway_protocol_version") == 1,
        "S1 /health is ready with protocol 1",
        f"provider_configured={health.get('provider_configured')} database={health.get('database')}",
    )
    ok &= check(
        health.get("provider_configured") is True,
        "S2 the Gateway reports a configured provider (the loopback stand-in)",
    )

    enrollment = http_json(
        f"{base}/enroll",
        method="POST",
        payload={"activation_code": ACTIVATION_CODE, "name": "fixture self-test", "platform": "windows"},
    )
    ok &= check(
        bool(enrollment.get("installation_id") and enrollment.get("installation_secret")),
        "S3 the fixed activation code enrolls one installation",
    )
    token = http_json(
        f"{base}/auth/token",
        method="POST",
        payload={
            "installation_id": enrollment["installation_id"],
            "installation_secret": enrollment["installation_secret"],
        },
    ).get("access_token", "")
    ok &= check(bool(token), "S4 the enrollment mints a short-lived token")

    balance = http_json(f"{base}/balance", token=token)
    ok &= check(
        balance.get("balance_usd") == BALANCE_USD and balance.get("shared_account") is True,
        "S5 the shared balance comes from the loopback stand-in",
        f"balance_usd={balance.get('balance_usd')} configured={balance.get('configured')}",
    )

    status = http_json(f"{base}/compute/status", token=token)
    ok &= check(
        status.get("state") == "offline" and status.get("global") is True,
        "S6 compute is offline and global, nothing was created",
        f"state={status.get('state')}",
    )

    ok &= check(
        read_compute(database) == {"state": "offline", "sessions": 0, "operations": []},
        "S7 the throwaway database holds no session and no compute operation",
    )
    return bool(ok)


def read_compute(database: Path) -> dict:
    """Read-only view of the Gateway's own database, the same way the smoke does it."""
    import sqlite3

    db = sqlite3.connect(str(database))
    try:
        control = db.execute("SELECT state, error_code, active_session_id FROM gateway_compute").fetchall()
        sessions = db.execute("SELECT COUNT(*) FROM gateway_sessions").fetchone()[0]
        operations = [
            row[0]
            for row in db.execute("SELECT operation FROM audit_events ORDER BY created_at").fetchall()
            if row[0] in COMPUTE_OPERATIONS
        ]
    finally:
        db.close()
    state = control[0][0] if control else "offline"
    return {"state": state, "sessions": sessions, "operations": operations}


def remove_workdir(workdir: Path, attempts: int = 10, delay: float = 0.3) -> str | None:
    """Delete the temporary directory. Windows releases a killed process' file mappings
    asynchronously, so the first delete can still hit a sharing violation."""
    for attempt in range(attempts):
        try:
            shutil.rmtree(workdir)
            return None
        except FileNotFoundError:
            return None
        except OSError as error:
            if attempt == attempts - 1:
                return str(error)
            time.sleep(delay)
    return "unknown"


# -------------------------------------------------------------------------------------- main


def main() -> int:
    # The Windows console renders UTF-8, but piping switches Python to the legacy code page and
    # would mangle every dash in this file's output. Line buffering keeps the fixture's lines in
    # order in a piped log (the smoke writes directly to the same pipe).
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    parser = argparse.ArgumentParser(description="local Gateway fixture for the cloud smoke")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--keep", action="store_true", help="keep the temporary directory")
    parser.add_argument("--self-test", action="store_true", help="fixture only, no GUI")
    args = parser.parse_args()

    try:
        node = preflight(args.port, gui=not args.self_test)
    except RuntimeError as error:
        print(f"cloud-smoke-local: {error}", file=sys.stderr)
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="alex-cloud-smoke-"))
    database = workdir / "gateway.db"
    log_path = workdir / "gateway.log"
    base = f"http://{GATEWAY_HOST}:{args.port}"
    stub = ProviderStub()
    gateway: subprocess.Popen | None = None
    smoke: subprocess.Popen | None = None
    log_handle = None
    status = 2

    print("Canalla Cloud smoke fixture — throwaway Gateway, no production service")
    print(f"  database    {database}")
    print(f"  gateway     {base}   (the smoke enrolls the installed app against this URL)")
    print("  provider    loopback stand-in, fake key, no api.runpod.io traffic")
    try:
        stub_port = stub.start()
        # A throwaway signing key: never printed and confined to this process and its children.
        env = gateway_env(
            "sqlite:///" + database.as_posix(),
            "smoke-local-jwt-" + secrets.token_urlsafe(32),
            f"http://{GATEWAY_HOST}:{stub_port}/graphql",
        )
        run_checked(
            [str(VENV_PYTHON), "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            cwd=GATEWAY_ROOT,
            env=env,
        )
        print("  [ok] migrations applied to the throwaway database")
        # The raw code is fixed for this fixture and printed by the CLI only; it is never a
        # real credential and it is unredeemed for exactly one run.
        run_checked(
            [
                str(VENV_PYTHON), "-m", "gateway.cli", "create-code",
                "--code", ACTIVATION_CODE, "--label", "cloud-smoke-local",
            ],
            cwd=GATEWAY_ROOT,
            env=env,
        )
        print("  [ok] one activation code created in that database")
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        log_handle = log_path.open("wb")
        gateway = subprocess.Popen(
            [
                str(VENV_PYTHON), "-m", "uvicorn", "gateway.main:app",
                "--host", GATEWAY_HOST, "--port", str(args.port),
            ],
            cwd=str(GATEWAY_ROOT),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        health = wait_for_health(args.port, gateway, log_path)
        print(
            f"  [ok] Gateway ready on {base} — provider_configured={health['provider_configured']} "
            f"database={health['database']}"
        )

        if args.self_test:
            print("Self-test — fixture only, no GUI, no installed app")
            status = 0 if self_test(base, database) else 1
            print(f"  [info] provider stand-in hits={stub.hits} rejected={stub.rejected}")
        else:
            print("Running: node e2e/cloud-smoke.mjs")
            smoke = subprocess.Popen(
                [node, str(SMOKE)],
                cwd=str(DESKTOP),
                env={
                    **os.environ,
                    "ALEX_SMOKE_ACTIVATION_CODE": ACTIVATION_CODE,
                    "ALEX_SMOKE_GATEWAY_URL": base,
                    "ALEX_SMOKE_GATEWAY_DB": str(database),
                },
            )
            try:
                status = smoke.wait()
            except KeyboardInterrupt:
                print("\ncloud-smoke-local: interrupted — stopping the smoke and the Gateway")
                stop_process(smoke, timeout=30)
                status = 130
            print(f"  [info] provider stand-in hits={stub.hits} rejected={stub.rejected}")
            print(f"  [info] compute rows in the throwaway database: {read_compute(database)}")
    except KeyboardInterrupt:
        print("\ncloud-smoke-local: interrupted")
        status = 130
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"cloud-smoke-local: {error}", file=sys.stderr)
        if gateway is not None and gateway.poll() is not None:
            print(tail(log_path), file=sys.stderr)
        status = 2
    finally:
        stop_process(gateway)
        if log_handle is not None:
            log_handle.close()
        stub.stop()
        if args.keep:
            print(f"  [keep] temporary directory left in place: {workdir}")
        else:
            failure = remove_workdir(workdir)
            if failure is not None:
                print(
                    f"  [warn] the temporary directory could not be removed ({failure}): {workdir}",
                    file=sys.stderr,
                )
        if running_image("alex-llm.exe"):
            print(
                "cloud-smoke-local: an alex-llm.exe process is still running — close it before "
                "the next run (the smoke refuses to start while another instance is alive)"
            )

    return status


if __name__ == "__main__":
    sys.exit(main())
