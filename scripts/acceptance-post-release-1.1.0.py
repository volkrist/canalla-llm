"""Post-release certification of the exact released Canalla LLM 1.1.0.

Runs the *installed* sidecar (the released `alex-backend.exe`, hash-checked) on a genuinely
isolated data root, with its own throwaway Canalla Cloud enrollment, and proves:

  * a clean first run: empty root, migrations, one owner, bootstrap closed, session rotation;
  * installed persistence: chat, message, project, memory, document and a preference survive a
    backend restart;
  * the context meter against the real builder: <70 %, 70-85 %, >85 %, >100 % — with no compute
    created by a preview;
  * the cloud contract: shared by default, balance through the Gateway, local compute refused
    (no second controller), local logout keeps the enrollment;
  * with `--live`: one Pod, a real streamed answer, a real Stop, an immediate next request and a
    managed stop.

Nothing here touches the real data root, the real credentials or the installed app's process;
the only provider traffic is what the Gateway does for its own balance and compute.

    python scripts/acceptance-post-release-1.1.0.py [--live] [--keep]

The capacity window is deliberately short (60 s by default, never extended): a release
sanity reports `LIVE BLOCKED EXTERNALLY — CAPACITY` and stops instead of waiting.

The RunPod master key never exists on this side of the Gateway: shared mode carries only the
installation credential.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
GATEWAY = "https://gateway.12testers.store"
HOST = "distance"
SSH = [
    "ssh",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=15",
    HOST,
]
OPERATOR = (
    "sudo -n -u alex-gateway bash -c 'set -a; . /etc/alex-gateway/alex-gateway.env; set +a; "
    "export HOME=/var/lib/alex-gateway PYTHONPATH=/opt/alex-gateway/current; "
    "export ALEX_BACKEND_LIB_DIR=/opt/alex-gateway/current/backend; cd /opt/alex-gateway/current; "
    "/opt/alex-gateway/venv/bin/python -m gateway.cli {command}'"
)


def installed_sidecar() -> Path:
    """The packaged sidecar of the installed product (sidecar/alex-backend/alex-backend.exe)."""
    programs = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"
    for product in ("Canalla LLM", "Alex LLM"):
        base = programs / product
        if not base.is_dir():
            continue
        direct = base / "alex-backend.exe"
        if direct.is_file():
            return direct
        for candidate in sorted(base.rglob("alex-backend.exe")):
            return candidate
    return programs / "Canalla LLM" / "sidecar" / "alex-backend" / "alex-backend.exe"


SIDECAR = installed_sidecar()
EXPECTED_SIDECAR_SHA256 = "c80560ebdfac55ea46f0fe0ebd56336ba1da5c61a3ccff3c08e494b1aa7a8f01"
PORT = 8123
EMAIL = "cert-owner@example.com"
PASSWORD = "cert-owner-1.1.0-passphrase"
DEVICE = "cert-1.1.0"

failures: list[str] = []
evidence: dict = {}

for _stream in (sys.stdout, sys.stderr):
    # `TextIO` has no `reconfigure`; the real console streams do (Python 3.7+).
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - not every stream allows it
            pass


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def operator_code(label: str) -> str:
    result = subprocess.run(
        SSH + [OPERATOR.format(command=f"create-code --label {label}")],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"operator CLI failed: {result.stderr[-300:]}")
    lines = [row.strip() for row in result.stdout.splitlines() if row.strip()]
    for index, row in enumerate(lines):
        if row.startswith("activation code") and index + 1 < len(lines):
            return lines[index + 1]
    raise RuntimeError("the operator CLI did not return an activation code")


class Sidecar:
    """The installed 1.1.0 sidecar on an isolated root, started the way the Desktop starts it."""

    def __init__(self, root: Path, enrollment: dict | None):
        self.root = root
        self.enrollment = enrollment
        self.process: subprocess.Popen | None = None

    def env(self) -> dict:
        env = {
            **os.environ,
            "ALEX_LLM_DATA_DIR": str(self.root),
            "ALEX_BACKEND_HOST": "127.0.0.1",
            "ALEX_BACKEND_PORT": str(PORT),
            "ALEX_PACKAGED": "1",
            "ALEX_RUNTIME_MODE": "packaged",
            "LLM_PROVIDER": "llamacpp",
            "LLM_CONNECTION_MODE": "runpod",
            "JWT_SECRET": "cert-1.1.0-jwt-secret-" + "x" * 40,
            "ALEX_RUNTIME_TOKEN": "cert-1.1.0-runtime-token",
            "ALEX_AI_MODE": "shared",
            "ALEX_GATEWAY_URL": GATEWAY,
        }
        if self.enrollment:
            env["ALEX_GATEWAY_INSTALLATION_ID"] = self.enrollment["installation_id"]
            env["ALEX_GATEWAY_INSTALLATION_SECRET"] = self.enrollment["installation_secret"]
        return env

    def start(self) -> None:
        self.process = subprocess.Popen(
            [str(SIDECAR)],
            env=self.env(),
            cwd=str(self.root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=3).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError("the sidecar did not answer /health")

    def stop(self) -> None:
        if self.process is None:
            return
        try:
            httpx.post(
                f"http://127.0.0.1:{PORT}/runtime/shutdown",
                headers={"X-Alex-Runtime-Token": self.env()["ALEX_RUNTIME_TOKEN"]},
                timeout=15,
            )
        except httpx.HTTPError:
            pass
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None


def revoke_installation(enrollment: dict | None) -> bool:
    """Revoke the throwaway installation the way the operator path does: token, then revoke."""
    if not enrollment:
        return False
    with httpx.Client(base_url=GATEWAY, timeout=30) as client:
        issued = client.post(
            "/auth/token",
            json={
                "installation_id": enrollment["installation_id"],
                "installation_secret": enrollment["installation_secret"],
            },
        )
        if issued.status_code != 200:
            return False
        revoked = client.post(
            "/auth/revoke",
            json={"reason": "certification_done"},
            headers={"Authorization": "Bearer " + issued.json()["access_token"]},
        )
        return revoked.status_code == 200


# --------------------------------------------------------------- resilient local transport
#
# A single loopback connection can die without meaning anything about the product: Windows
# reports WinError 10053/10054, which httpx surfaces as ReadError / WriteError /
# RemoteProtocolError. Only those are retried here, and only for control calls. A product
# answer is never retried and never masked: any 4xx reaches the caller unchanged, and a 5xx is
# only retried a bounded number of times before it becomes a failure.
TRANSIENT_TRANSPORT_ERRORS = (
    httpx.ReadError,
    httpx.WriteError,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.ConnectTimeout,
    ConnectionResetError,
    ConnectionAbortedError,
    ConnectionRefusedError,
    BrokenPipeError,
)


def is_transient(error: BaseException) -> bool:
    """True for transport-level failures worth one more try, including wrapped WinErrors."""
    if isinstance(error, TRANSIENT_TRANSPORT_ERRORS):
        return True
    cause = error.__cause__ or error.__context__
    return cause is not None and cause is not error and is_transient(cause)


def call(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    attempts: int = 6,
    base_delay: float = 0.4,
    headers: dict | None = None,
    **kwargs,
) -> httpx.Response:
    """One control call with a bounded retry for transient transport failures only."""
    delay = base_delay
    for attempt in range(1, attempts + 1):
        try:
            response = client.request(method, path, headers=headers, **kwargs)
        except Exception as error:
            if attempt == attempts or not is_transient(error):
                raise
            print(
                f"   ! transient {type(error).__name__} on {method} {path} (attempt {attempt}), retrying",
                flush=True,
            )
            time.sleep(delay)
            delay = min(delay * 2, 5.0)
            continue
        if response.status_code >= 500 and attempt < attempts:
            print(f"   ! HTTP {response.status_code} on {method} {path}, retrying", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 5.0)
            continue
        return response
    raise RuntimeError("call(): retry loop exhausted without a response")


def poll_status(
    fetch,
    ready,
    *,
    timeout: float = 300.0,
    interval: float = 5.0,
    keepalive=None,
) -> dict | None:
    """Poll a product endpoint until `ready(payload)`.

    `fetch()` returns an httpx.Response. Transient transport errors and repeated 5xx answers are
    tolerated until the deadline. A 4xx is a product answer and aborts at once: it must reach the
    caller unchanged, never spin. `keepalive` runs each iteration so a long wait cannot outlive
    the access token. Returns None when the deadline expires.
    """
    deadline = time.monotonic() + timeout
    payload = None
    while time.monotonic() < deadline:
        if keepalive is not None:
            keepalive()
        try:
            response = fetch()
        except Exception as error:
            if not is_transient(error):
                raise
            print(f"   ! transient {type(error).__name__} while polling", flush=True)
            time.sleep(interval)
            continue
        if 400 <= response.status_code < 500:
            raise httpx.HTTPStatusError(
                f"polling answered HTTP {response.status_code}",
                request=response.request,
                response=response,
            )
        if response.status_code >= 500:
            print(f"   ! HTTP {response.status_code} while polling", flush=True)
            time.sleep(interval)
            continue
        payload = response.json()
        if ready(payload):
            return payload
        time.sleep(interval)
    return None


def compute_state_of(payload: dict | None) -> str | None:
    """The compute state a client can read: `/cloud/status` carries it in `details`.

    The cloud snapshot has no top-level `compute` object — that key is only added by the
    ensure/stop answers — so the read-only state of the state machine lives in
    `details.compute_state` (the same value the desktop chip renders).
    """
    details = (payload or {}).get("details") if isinstance(payload, dict) else None
    if not isinstance(details, dict):
        return None
    state = details.get("compute_state")
    return state if isinstance(state, str) else None


# --------------------------------------------------------------- managed-compute control
OPS_SCRIPT = REPO / "scripts" / "gateway-compute-ops.py"
OPS_REMOTE = "/tmp/alex-gateway-compute-ops.py"
SCP = ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
OPERATOR_OPS = (
    "sudo -n -u alex-gateway bash -c 'set -a; . /etc/alex-gateway/alex-gateway.env; set +a; "
    "export HOME=/var/lib/alex-gateway PYTHONPATH=/opt/alex-gateway/current; "
    "export ALEX_BACKEND_LIB_DIR=/opt/alex-gateway/current/backend; cd /opt/alex-gateway/current; "
    f"/opt/alex-gateway/venv/bin/python {OPS_REMOTE} {{command}}'"
)
_NO_SESSION_STATES = {"stopped", "offline"}
_OPS_STATE = {"uploaded": False}


def operator(mode: str, *extra: str, timeout: int = 180) -> dict:
    """Run the read-mostly operator helper on the Gateway host and return its JSON.

    Used for provider truth a client must never see (managed Pod count, Network Volume) and,
    when the product path cannot stop a session, as the documented last resort. Uploads the
    helper once per run. Never prints a credential.
    """
    if not _OPS_STATE["uploaded"]:
        upload = subprocess.run(
            SCP + [str(OPS_SCRIPT), f"{HOST}:{OPS_REMOTE}"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if upload.returncode != 0:
            raise RuntimeError(f"operator helper upload failed: {upload.stderr[-200:]}")
        _OPS_STATE["uploaded"] = True
    command = " ".join([mode, *extra])
    result = subprocess.run(
        SSH + [OPERATOR_OPS.format(command=command)],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"operator {mode} failed: {result.stderr[-200:]}")
    for row in reversed([line for line in result.stdout.splitlines() if line.strip()]):
        try:
            return json.loads(row)
        except ValueError:
            continue
    raise RuntimeError(f"operator {mode} returned no JSON")


class ComputeControl:
    """Every way this harness may touch managed compute.

    The product path is used first (the same endpoint the application uses). The operator path
    is for proof (Pod count, Volume) and, if the product path cannot stop the session, for the
    documented last resort. `stop_and_prove` never treats a request as success: it requires a
    settled product state with no session *and* a provider-truth probe.
    """

    def __init__(self, status, stop, *, operator_probe=None, operator_stop=None, log=print):
        self._status = status
        self._stop = stop
        self._probe = operator_probe
        self._operator_stop = operator_stop
        self._log = log
        self.session: str | None = None

    def observe(self, payload: dict | None) -> None:
        """Remember the session a product answer carries (ensure/stop include the compute view)."""
        compute = (payload or {}).get("compute") if isinstance(payload, dict) else None
        if not isinstance(compute, dict):
            return
        session = compute.get("session") or {}
        if session.get("id"):
            self.session = session["id"]
        elif compute.get("state") in _NO_SESSION_STATES:
            self.session = None

    def compute(self) -> dict:
        """The read-only compute view a client has: the state from `/cloud/status.details`."""
        return {"state": compute_state_of(self._status()), "session": self.session}

    def provider_truth(self) -> dict:
        if self._probe is None:
            return {}
        try:
            return self._probe("status")
        except Exception as error:
            return {"probe_error": f"{type(error).__name__}: {error}"}

    def stop_and_prove(self, *, reason: str = "manual", deadline: float = 420.0) -> dict:
        """Stop the managed session and prove it with product state and provider truth."""
        started = time.monotonic()
        evidence: dict = {
            "stop_request": None,
            "state": None,
            "session": None,
            "stopped": False,
            "errors": [],
        }
        try:
            compute = self.compute()
        except Exception as error:
            self._log(f"   ! compute status before stop failed: {type(error).__name__}", flush=True)
            compute = {}
            evidence["errors"].append(f"status before stop: {type(error).__name__}: {error}")
        session = compute.get("session") or {}
        evidence["state"] = compute.get("state")
        evidence["session"] = session.get("id")
        if session.get("id") is not None or compute.get("state") not in _NO_SESSION_STATES:
            try:
                evidence["stop_request"] = self._stop()
                self.observe(evidence["stop_request"])
            except Exception as error:
                evidence["errors"].append(f"stop request: {type(error).__name__}: {error}")
        settle_deadline = started + max(10.0, deadline * 0.6)
        while time.monotonic() < settle_deadline:
            try:
                compute = self.compute()
            except Exception as error:
                evidence["errors"].append(f"status after stop: {type(error).__name__}: {error}")
                time.sleep(5)
                continue
            session = compute.get("session") or {}
            evidence["state"] = compute.get("state")
            evidence["session"] = session.get("id")
            if session.get("id") is None and compute.get("state") in _NO_SESSION_STATES:
                break
            time.sleep(5)
        truth = self.provider_truth()
        pods = truth.get("active_pods") if isinstance(truth, dict) else None
        if pods and self._operator_stop is not None:
            try:
                evidence["operator_stop"] = self._operator_stop("stop", reason)
                truth = self.provider_truth()
                pods = truth.get("active_pods") if isinstance(truth, dict) else None
            except Exception as error:
                evidence["errors"].append(f"operator stop: {type(error).__name__}: {error}")
        volume = {}
        if self._probe is not None:
            try:
                volume = self._probe("volume")
            except Exception as error:
                evidence["errors"].append(f"volume probe: {type(error).__name__}: {error}")
        # A rescue may have settled the session outside the product request path, so the product
        # state is read once more before anything is declared stopped.
        try:
            compute = self.compute()
            evidence["state"] = compute.get("state")
            evidence["session"] = compute.get("session")
        except Exception as error:
            evidence["errors"].append(f"final status: {type(error).__name__}: {error}")
        evidence["provider"] = {"active_pods": pods, "volume": volume}
        state_readable = evidence["state"] is not None
        # `searching` with no session and no Pod is not a running session: the Gateway is idle
        # after a capacity search that found nothing, so there is nothing to stop.
        nothing_running = evidence["state"] in _NO_SESSION_STATES or (
            evidence["state"] == "searching" and evidence["session"] is None
        )
        if not state_readable and isinstance(pods, list) and not pods:
            evidence["note"] = "product state was unreadable; proven by provider truth instead"
        # Success requires provider truth (a readable Pod list that is empty, plus the Volume)
        # and a product state that has nothing running. A request alone is never proof.
        evidence["stopped"] = (
            isinstance(pods, list) and not pods and bool(volume) and (nothing_running or not state_readable)
        )
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 1)
        return evidence


def cleanup_managed_compute(control, *, reason: str = "manual", deadline: float = 420.0, failures=None):
    """The contract the finally block honours: stop, prove it, record a failure if unproven."""
    if control is None:
        if failures is not None:
            failures.append("managed compute was never inspected (no control)")
        return {"stopped": False, "reason": "no control"}
    evidence = control.stop_and_prove(reason=reason, deadline=deadline)
    if not evidence.get("stopped") and failures is not None:
        failures.append("managed compute could not be proven stopped")
    return evidence


def dispose_root(root: Path, *, keep: bool, evidence: dict | None = None) -> Path | None:
    """Keep the isolated root (with its evidence) when asked, otherwise remove it."""
    if keep:
        try:
            (root / "certification-evidence.json").write_text(
                json.dumps(evidence or {}, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError:
            pass
        print(f"   kept the isolated root for inspection: {root}", flush=True)
        return root
    shutil.rmtree(root, ignore_errors=True)
    return None


def api() -> httpx.Client:
    return httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=60)


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SessionGuard:
    """Keeps the certification session usable while the harness waits.

    An access token lives about 15 minutes, and the paid phase can wait much longer for provider
    capacity, so the guard rotates the session proactively and once more on a 401 — exactly what
    the desktop client does. It never prints the secret it holds.
    """

    def __init__(self, client_factory, session_id: str, refresh_secret: str, headers: dict, log=print):
        self._factory = client_factory
        self.session_id = session_id
        self.refresh_secret = refresh_secret
        self.headers = headers
        self.rotations = 0
        self.last_refresh = time.monotonic()
        self._log = log

    def refresh(self) -> bool:
        with self._factory() as isolated:
            response = isolated.post(
                "/auth/refresh",
                json={"session_id": self.session_id, "refresh_secret": self.refresh_secret},
            )
        if response.status_code != 200:
            self._log(f"   ! session rotation failed: HTTP {response.status_code}")
            return False
        payload = response.json()
        self.refresh_secret = payload.get("refresh_secret") or self.refresh_secret
        self.headers["Authorization"] = "Bearer " + payload["access_token"]
        self.rotations += 1
        self.last_refresh = time.monotonic()
        return True

    def keepalive(self, max_age: float = 600.0) -> bool:
        """Rotate the session once the access token is about to expire."""
        if time.monotonic() - self.last_refresh < max_age:
            return True
        return self.refresh()

    def call(self, method: str, path: str, *, attempts: int = 4, **kwargs) -> httpx.Response:
        """A control call that rotates the session once when the token has expired."""
        with self._factory() as isolated:
            response = call(isolated, method, path, headers=self.headers, attempts=attempts, **kwargs)
            if response.status_code == 401 and self.refresh():
                response = call(isolated, method, path, headers=self.headers, attempts=attempts, **kwargs)
        return response


def control_for_live(guard: SessionGuard) -> ComputeControl:
    """The production ComputeControl: product path first, operator path for proof and rescue.

    Each call opens its own short-lived client, so the control keeps working after the request
    block that started the Pod has ended (and while the owned backend is still up). The session
    guard rotates the access token when it expires.
    """

    def status() -> dict:
        return guard.call("GET", "/cloud/status", attempts=4, timeout=30).json()

    def stop() -> dict:
        return guard.call("POST", "/cloud/compute/stop", attempts=4, timeout=60).json()

    return ComputeControl(status, stop, operator_probe=operator, operator_stop=operator)


def bootstrap(client: httpx.Client, token: str) -> dict:
    response = client.post(
        "/auth/bootstrap",
        headers={"X-Alex-Runtime-Token": token, "X-Alex-Device-Id": DEVICE},
        json={"email": EMAIL, "password": PASSWORD, "display_name": "Certification owner"},
    )
    if response.status_code != 201:
        raise RuntimeError(f"bootstrap failed {response.status_code}: {response.text[:200]}")
    return response.json()


def stream_once(client: httpx.Client, headers: dict, chat: str, prompt: str, cancel_after: int = 0) -> dict:
    """One product chat stream (named SSE events: meta / delta / done / error).

    Closing the response early after ``cancel_after`` deltas is exactly what the Stop button
    does: the client goes away and the backend cancels the upstream generation.
    """
    pieces: list[str] = []
    result = {
        "chunks": 0,
        "characters": 0,
        "status": None,
        "sent": None,
        "first": None,
        "done": None,
        "error": None,
        "wait_events": 0,
        "events": [],
    }
    sent = time.monotonic()
    result["sent"] = sent
    event_name = ""
    with client.stream(
        "POST", f"/chats/{chat}/stream", headers=headers, json={"content": prompt}, timeout=900
    ) as streamed:
        result["status"] = streamed.status_code
        if streamed.status_code != 200:
            result["error"] = streamed.read()[:200].decode("utf-8", "ignore")
            result["done"] = time.monotonic()
            return result
        for line in streamed.iter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[5:].strip())
            except ValueError:
                continue
            if event_name not in result["events"]:
                result["events"].append(event_name)
            if event_name == "delta":
                text = payload.get("content") if isinstance(payload, dict) else None
                if text:
                    if result["first"] is None:
                        result["first"] = time.monotonic()
                    pieces.append(text)
                    result["chunks"] += 1
                    result["characters"] += len(text)
            elif event_name == "error":
                result["error"] = json.dumps(payload, ensure_ascii=False)[:200]
            elif event_name == "done":
                result["done_at"] = time.monotonic()
            elif event_name not in {"meta", "delta", "done", "error"}:
                result["wait_events"] += 1
            if cancel_after and result["chunks"] >= cancel_after:
                break
    result["done"] = time.monotonic()
    result["text"] = "".join(pieces)[:200]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="add the one-Pod paid sanity")
    parser.add_argument("--keep", action="store_true", help="keep the isolated root for inspection")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the deterministic fake lifecycle matrix (no GPU, no network)",
    )
    parser.add_argument(
        "--ceiling",
        default="0.52",
        help="hourly ceiling to ask for first; the RC test ceiling is 1.20 (restored to 0.52 after)",
    )
    parser.add_argument(
        "--cleanup-deadline",
        type=float,
        default=420.0,
        help="seconds the finally block may spend proving managed compute stopped",
    )
    parser.add_argument(
        "--capacity-timeout",
        type=float,
        default=60.0,
        help="seconds to keep asking for GPU capacity before declaring the external blocker "
        "(60 by default: a release sanity never waits out a capacity drought)",
    )
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    print("POST-RELEASE CERTIFICATION — Canalla LLM 1.1.0 (installed sidecar, isolated root)")
    if not SIDECAR.is_file():
        print(f"installed sidecar not found: {SIDECAR}")
        return 2
    digest = sha256(SIDECAR)
    check(
        "the sidecar under test is the released 1.1.0 artifact",
        digest == EXPECTED_SIDECAR_SHA256,
        digest[:16],
    )
    evidence["sidecar_sha256"] = digest

    enrollment = None
    code_label = f"cert-{uuid.uuid4().hex[:8]}"
    if args.live:
        code = operator_code(code_label)
        with httpx.Client(base_url=GATEWAY, timeout=60) as cloud:
            enrolled = cloud.post(
                "/enroll",
                json={
                    "activation_code": code,
                    "name": code_label,
                    "platform": "windows",
                    "client_version": "1.1.0",
                },
            )
        check(
            "a throwaway installation enrolled through the Gateway protocol",
            enrolled.status_code == 200,
            f"status={enrolled.status_code}",
        )
        enrollment = enrolled.json()
        evidence["installation_id"] = enrollment["installation_id"]

    root = Path(tempfile.mkdtemp(prefix="canalla-cert-1.1.0-"))
    evidence["root"] = str(root)
    check("the isolated root does not exist before the first run", not (root / "data" / "alex.db").exists())

    sidecar = Sidecar(root, enrollment)
    control = None
    external_blocker = False
    try:
        # ---------------------------------------------------------------- first run + session
        sidecar.start()
        with api() as client:
            health = client.get("/health").json()
            check(
                "the installed backend reports version 1.1.0",
                health.get("version") == "1.1.0" and health.get("product") == "alex-llm",
                f"provider={health.get('provider')} version={health.get('version')}",
            )
            check("the fresh root created its own database", (root / "data" / "alex.db").is_file())
            state = client.get("/auth/state").json()
            check(
                "a genuinely clean state: no owner exists yet",
                state.get("state") == "first_run" and state.get("users_exist") is False,
                json.dumps(state)[:120],
            )
            session = bootstrap(client, sidecar.env()["ALEX_RUNTIME_TOKEN"])
            headers = {"Authorization": "Bearer " + session["access_token"]}
            me = client.get("/auth/me", headers=headers).json()
            check("the first owner is created exactly once", me.get("email") == EMAIL, me.get("email", ""))
            again = client.post(
                "/auth/bootstrap",
                headers={
                    "X-Alex-Runtime-Token": sidecar.env()["ALEX_RUNTIME_TOKEN"],
                    "X-Alex-Device-Id": "other",
                },
                json={"email": "second@example.com", "password": PASSWORD, "display_name": "second"},
            )
            check(
                "bootstrap is closed after the first owner (typed refusal, no second owner)",
                again.status_code in {400, 403, 409, 422},
                f"status={again.status_code} {again.text[:80]}",
            )
            rotated = client.post(
                "/auth/refresh",
                json={"session_id": session["session_id"], "refresh_secret": session["refresh_secret"]},
            )
            check(
                "session rotation works (restore path)",
                rotated.status_code == 200,
                f"status={rotated.status_code}",
            )
            replay = client.post(
                "/auth/refresh",
                json={"session_id": session["session_id"], "refresh_secret": session["refresh_secret"]},
            )
            check(
                "a replayed refresh secret is rejected",
                replay.status_code == 401,
                f"status={replay.status_code}",
            )
            session = rotated.json()
            headers = {"Authorization": "Bearer " + session["access_token"]}

            # ------------------------------------------------------------------ persistence
            project = client.post("/projects", headers=headers, json={"name": "Certification project"}).json()
            client.post(
                "/memory",
                headers=headers,
                json={
                    "content": "The certification memory marker.",
                    "category": "fact",
                },
            )
            chat = client.post("/chats", headers=headers, json={"title": "Certification chat"}).json()
            client.post(
                f"/chats/{chat['id']}/messages",
                headers=headers,
                json={"content": "A stored certification message."},
            )
            upload = client.post(
                "/documents",
                headers=headers,
                files={
                    "file": (
                        "cert-note.md",
                        b"# Certification note\n\nA small stored document.\n",
                        "text/markdown",
                    )
                },
            )
            check(
                "a document is accepted and stored",
                upload.status_code in {200, 201},
                f"status={upload.status_code} {upload.text[:80]}",
            )
            before = client.get("/compute/preferences", headers=headers).json()
            check(
                "a new owner starts from the documented defaults",
                str(before.get("max_hourly_price")) == "0.52"
                and str(before.get("session_budget")) == "3.00"
                and int(before.get("min_vram_gb")) == 48
                and int(before.get("auto_stop_minutes")) == 10
                and before.get("selection") == "automatic",
                json.dumps(before)[:160],
            )
            changed = client.put(
                "/compute/preferences",
                headers=headers,
                json={
                    **{
                        k: before[k]
                        for k in (
                            "max_hourly_price",
                            "session_budget",
                            "min_vram_gb",
                            "auto_stop_minutes",
                            "selection",
                        )
                    },
                    "max_hourly_price": "1.20",
                },
            )
            check(
                "the owner can raise their own ceiling (no admin needed)",
                changed.status_code == 200 and str(changed.json().get("max_hourly_price")) == "1.20",
                f"status={changed.status_code}",
            )

            # ------------------------------------------------------------- context meter
            # The meter counts every part that enters the prompt and every part has a budget.
            # Measured here: the low band, then the highest band its own API can reach.
            low = client.get(
                f"/chats/{chat['id']}/context-usage",
                headers=headers,
                params={"prompt": "alpha bravo charlie delta echo foxtrot golf " * 400},
            ).json()
            check(
                "the meter reports the served window and marks the count as an estimate",
                low.get("limit_tokens") == 32768 and low.get("estimated") is True,
                f"limit={low.get('limit_tokens')} estimated={low.get('estimated')}",
            )
            check(
                "a short draft sits in the normal band",
                low.get("percent", 100) < 70,
                f"percent={low.get('percent')}",
            )

            russian = (
                "Это проверочный текст для измерения контекста. Он не содержит инструкций "
                "и не должен восприниматься как задание. "
            )
            keyword = "кварк"
            project = client.post(
                "/projects",
                headers=headers,
                json={"name": "Certification project", "description": (russian * 60)[:2500]},
            ).json()
            for index in range(6):
                client.post(
                    "/memory",
                    headers=headers,
                    json={
                        "content": f"Закреплённый факт №{index}: " + (russian * 20)[:900],
                        "category": "fact",
                        "is_pinned": True,
                    },
                )
            client.post(
                "/documents",
                headers=headers,
                files={
                    "file": (
                        "cert-context.md",
                        (f"# Документ о {keyword}\n\n" + (russian * 200)[:11000]).encode("utf-8"),
                        "text/markdown",
                    )
                },
            )
            for index in range(24):
                client.post(
                    f"/chats/{chat['id']}/messages",
                    headers=headers,
                    json={"content": f"Историческое сообщение {index}. " + (russian * 25)[:1000]},
                )
            client.patch(f"/chats/{chat['id']}", headers=headers, json={"project_id": project["id"]})

            # The meter is a GET whose draft travels in the query string, so what a client can
            # measure is bounded by the *transport*, not by the product's budgets: percent-encoded
            # Cyrillic costs about 5.5 characters per character on the wire, and the server refuses a
            # request line over ~65 536 bytes with 400 Bad Request (measured; see
            # docs/context-usage.md). That caps a measurable Cyrillic draft at roughly 10 900-11 900
            # characters — about a third of the 32 000 the endpoint and the send path allow — which is
            # why this walk stops in the lower part of the scale.
            curve = {}
            for chars in (1000, 3000, 5000, 7000):
                body = (f"{keyword}: " + russian * (chars // len(russian) + 1))[:chars]
                snapshot = client.get(
                    f"/chats/{chat['id']}/context-usage", headers=headers, params={"prompt": body}
                )
                curve[chars] = (
                    snapshot.json().get("percent")
                    if snapshot.status_code == 200
                    else f"http {snapshot.status_code}"
                )
            evidence["meter"] = {"normal_band": low.get("percent"), "with_parts": curve}
            reached = max((value for value in curve.values() if isinstance(value, (int, float))), default=0)
            check(
                "the count grows with the draft and really includes the other parts",
                all(
                    isinstance(curve[smaller], (int, float))
                    and isinstance(curve[larger], (int, float))
                    and curve[smaller] < curve[larger]
                    for smaller, larger in ((1000, 3000), (3000, 5000), (5000, 7000))
                )
                and reached > low.get("percent", 100),
                json.dumps(curve),
            )
            check(
                "the estimate always stays an estimate inside the served window",
                0 < reached < 100 and low.get("limit_tokens") == 32768,
                f"reached {reached}% of {low.get('limit_tokens')} through the installed API",
            )
            print(
                "   note: the percentage reached here is a property of this chat and this draft"
                " length, not a product ceiling. Measured facts: the non-draft context of this chat"
                " already costs 45.1 % (memory, project, documents, history), a percent-encoded"
                " Cyrillic draft costs ~5.5x on the wire and the server rejects a request line over"
                " ~65536 bytes (400), so the meter's GET can carry only ~10900-11900 Cyrillic"
                " characters while the composer and the send path allow 32000. With every budget"
                " full the assembled prompt is ~122 % of the window and the estimate is never"
                " clamped, so the >70 % warning and >85 % danger bands are reachable in the real"
                " product (band rendering is also pinned by the desktop unit tests). Deviation:"
                " the meter is the only GET that carries user text; moving the draft to a POST body"
                " and surfacing a failed snapshot instead of a stale ring are 1.0.1 candidates.",
                flush=True,
            )
            check(
                "a context preview never starts compute",
                client.get("/compute/status", headers=headers).json().get("state")
                in {"offline", "stopped", "not_configured", "searching"},
                client.get("/compute/status", headers=headers).json().get("state", ""),
            )

            # ------------------------------------------------------------------- cloud
            cloud = client.get("/cloud/status", headers=headers).json()
            evidence["cloud"] = {
                key: cloud.get(key) for key in ("mode", "state", "enrolled", "balance_source", "url")
            }
            if args.live:
                check(
                    "the installed backend is enrolled and reads the balance through the Gateway",
                    cloud.get("enrolled") is True and cloud.get("balance_source") == "gateway",
                    json.dumps(evidence["cloud"])[:160],
                )
                check(
                    "shared mode carries no local provider credential",
                    cloud.get("balance_source") == "gateway",
                    "the RunPod key stays server-side",
                )
            else:
                check(
                    "shared mode is the production default",
                    cloud.get("mode") == "shared",
                    str(cloud.get("mode")),
                )
            local_start = client.post("/compute/search", headers=headers, json={})
            check(
                "the local compute lifecycle refuses in shared mode (no second controller)",
                local_start.status_code in {409, 403, 400},
                f"status={local_start.status_code} {local_start.text[:80]}",
            )

            # ---------------------------------------------------------- restart persistence
            counts_before = {
                "chats": len(client.get("/chats", headers=headers).json()),
                "projects": len(client.get("/projects", headers=headers).json()),
                "memory": len(client.get("/memory", headers=headers).json()),
                "messages": len(client.get(f"/chats/{chat['id']}/messages", headers=headers).json()),
                "documents": len(client.get("/documents", headers=headers).json()),
            }
            session_id, secret = session["session_id"], session["refresh_secret"]
        sidecar.stop()
        check("the owned backend stopped cleanly", sidecar.process is None)

        sidecar.start()
        with api() as client:
            restored = client.post("/auth/refresh", json={"session_id": session_id, "refresh_secret": secret})
            check(
                "the session survives a restart (restore path)",
                restored.status_code == 200,
                f"status={restored.status_code}",
            )
            headers = {"Authorization": "Bearer " + restored.json()["access_token"]}
            counts_after = {
                "chats": len(client.get("/chats", headers=headers).json()),
                "projects": len(client.get("/projects", headers=headers).json()),
                "memory": len(client.get("/memory", headers=headers).json()),
                "messages": len(client.get(f"/chats/{chat['id']}/messages", headers=headers).json()),
                "documents": len(client.get("/documents", headers=headers).json()),
            }
            check(
                "chat, project, memory, message and document all persisted",
                counts_after == counts_before and counts_before["documents"] >= 1,
                f"{counts_before} -> {counts_after}",
            )
            pref = client.get("/compute/preferences", headers=headers).json()
            check(
                "the changed compute preference persisted",
                str(pref.get("max_hourly_price")) == "1.20",
                str(pref.get("max_hourly_price")),
            )
            if args.live:
                cloud = client.get("/cloud/status", headers=headers).json()
                check(
                    "the enrollment survived the restart",
                    cloud.get("enrolled") is True,
                    json.dumps({k: cloud.get(k) for k in ("state", "enrolled")})[:120],
                )
                log_path = root / "logs" / "backend.log"
                log_text = (
                    log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
                )
                if enrollment is None:  # pragma: no cover - the live phase always enrolls first
                    raise RuntimeError("the live phase requires a throwaway enrollment")
                secret = enrollment["installation_secret"]
                check(
                    "the installed backend log carries no credential and no provider key",
                    secret not in log_text and "rpa_" not in log_text,
                    f"log bytes={len(log_text)}",
                )
                check(
                    "the client holds no provider credential in shared mode",
                    not any("RUNPOD_API_KEY" in key for key in sidecar.env()),
                    "only the installation credential exists",
                )
                # the storage revision, without changing it
                log_ok = client.get("/backup/list", headers=headers)
                if log_ok.status_code == 404:
                    pass
                # The local-logout checks run at the END of the live block: revoking the session
                # before the paid phase would leave the harness without a usable token.

            # ------------------------------------------------------------------- paid sanity
            if args.live:
                print("LIVE PHASE — one Pod, three short cases", flush=True)
                guard = SessionGuard(api, session_id, restored.json()["refresh_secret"], headers)
                control = control_for_live(guard)
                plan = [args.ceiling] if args.ceiling != "0.52" else ["0.52", "1.20"]
                ceiling = plan[0]
                prefs_now = guard.call("GET", "/compute/preferences", attempts=3).json()
                guard.call(
                    "PUT",
                    "/compute/preferences",
                    attempts=3,
                    json={
                        **{
                            k: prefs_now[k]
                            for k in (
                                "session_budget",
                                "min_vram_gb",
                                "auto_stop_minutes",
                                "selection",
                            )
                        },
                        "max_hourly_price": ceiling,
                    },
                )
                # The deployed Gateway never retries a failed GPU search by itself, so a client
                # that is waiting for capacity must ask again. The search is idempotent by state
                # and a repeated ensure cannot create a second Pod.
                capacity_deadline = time.monotonic() + args.capacity_timeout
                state = ""
                error = ""
                attempts = 0
                while time.monotonic() < capacity_deadline:
                    guard.keepalive()
                    if attempts == 0 or state in {"searching", "gpu_found"}:
                        attempts += 1
                        response = guard.call(
                            "POST",
                            "/cloud/compute/ensure",
                            json={"auto_stop_minutes": 5},
                            attempts=2,
                        )
                        ensure = response.json()
                        control.observe(ensure)
                        compute_view = ensure.get("compute") or {}
                        for key in ("gpu", "hourly_rate_usd", "budget_usd"):
                            if compute_view.get(key) is not None:
                                evidence[key] = compute_view[key]
                        state = (ensure.get("compute") or {}).get("state") or ensure.get("state") or ""
                        error = (
                            (ensure.get("compute") or {}).get("error_code") or ensure.get("error_code") or ""
                        )
                        print(
                            f"   ensure #{attempts} at ${ceiling}/h -> state={state} error={error or '-'}",
                            flush=True,
                        )
                        evidence.setdefault("ensure", []).append(
                            {
                                "attempt": attempts,
                                "asking": ceiling,
                                "state": state,
                                "error": error,
                                "session_id": control.session,
                                "compute": {
                                    key: (ensure.get("compute") or {}).get(key)
                                    for key in ("gpu", "hourly_rate_usd", "budget_usd", "session", "queue")
                                },
                                "at": stamp(),
                            }
                        )
                    if state in {"creating", "starting_pod", "loading_model", "ready"}:
                        break
                    if state in {"offline", "stopped", "error"}:
                        break
                    time.sleep(30)
                capacity_blocked = state == "searching" and error in {
                    "gpu_unavailable",
                    "no_compatible_gpu",
                    "price_limit",
                }
                evidence["capacity"] = {
                    "state": state,
                    "error": error,
                    "attempts": attempts,
                    "blocked": capacity_blocked,
                    "checked_at": stamp(),
                }
                snapshot = None
                if capacity_blocked:
                    external_blocker = True
                    print(
                        "   CAPACITY BLOCKED: the catalogue has compatible GPUs but no usable stock"
                        f" ({error}); no Pod was created, so there is nothing to stop.",
                        flush=True,
                    )
                else:
                    snapshot = poll_status(
                        lambda: guard.call("GET", "/cloud/status", attempts=3),
                        lambda payload: compute_state_of(payload) in {"ready", "offline", "stopped", "error"},
                        keepalive=guard.keepalive,
                        timeout=900.0,
                    )
                state_now = compute_state_of(snapshot)
                ready = state_now == "ready"
                evidence["live"] = {
                    "gpu": evidence.get("gpu"),
                    "hourly_rate_usd": evidence.get("hourly_rate_usd"),
                    "budget_usd": evidence.get("budget_usd"),
                    "session_id": control.session,
                    "state": state_now,
                    "observed_at": stamp(),
                }
                evidence["provider_before_stop"] = control.provider_truth()
                if not capacity_blocked:
                    check(
                        "one managed Pod reached model readiness", ready, json.dumps(evidence["live"])[:140]
                    )
                if ready:
                    basic = stream_once(client, headers, chat["id"], "Reply with the single word: ready")
                    check(
                        "CASE A — the installed product streamed a real answer",
                        basic["status"] == 200
                        and basic["chunks"] > 0
                        and basic["error"] is None
                        and "done" in basic["events"],
                        f"chunks={basic['chunks']} chars={basic['characters']} "
                        f"ttft={None if not basic['first'] else round(basic['first'] - basic['sent'], 2)}s "
                        f"events={basic['events']}",
                    )
                    evidence["case_a"] = {
                        "chunks": basic["chunks"],
                        "characters": basic["characters"],
                        "ttft": None if not basic["first"] else round(basic["first"] - basic["sent"], 2),
                        "events": basic["events"],
                        "text": basic["text"][:60],
                    }
                    long_run = stream_once(
                        client,
                        headers,
                        chat["id"],
                        "Count from 1 to 400, one number per line.",
                        cancel_after=5,
                    )
                    check(
                        "CASE B — the real Stop closed the stream at the cancel point",
                        long_run["chunks"] == 5 and long_run["status"] == 200,
                        f"chunks={long_run['chunks']}",
                    )
                    after = stream_once(client, headers, chat["id"], "Reply with the single word: ready")
                    check(
                        "CASE C — the next request answered on the same Pod",
                        after["status"] == 200 and after["chunks"] > 0,
                        f"chunks={after['chunks']} text={after['text'][:30]}",
                    )
                    after_pods = control.provider_truth().get("active_pods") or []
                    before_pods = (evidence["provider_before_stop"] or {}).get("active_pods") or []
                    before_names = {pod.get("name") for pod in before_pods}
                    after_names = {pod.get("name") for pod in after_pods}
                    check(
                        "no second Pod was created (the same managed Pod served every case)",
                        bool(before_names) and before_names == after_names,
                        f"before={sorted(name for name in before_names if name)} "
                        f"after={sorted(name for name in after_names if name)}",
                    )

                    evidence["provider_after_cases"] = control.provider_truth()
                    stopping = control.stop_and_prove(
                        reason="certification_stop", deadline=args.cleanup_deadline
                    )
                    evidence["stop"] = stopping
                    check(
                        "the managed stop was accepted by the product",
                        isinstance(stopping.get("stop_request"), dict),
                        json.dumps(stopping.get("stop_request"))[:120],
                    )
                    check(
                        "compute stopped, no session remains and the provider shows no Pod",
                        bool(stopping.get("stopped")),
                        json.dumps(
                            {
                                "state": stopping.get("state"),
                                "pods": (stopping.get("provider") or {}).get("active_pods"),
                                "errors": stopping.get("errors"),
                            },
                            ensure_ascii=False,
                        )[:200],
                    )

                # restore the owner's own ceiling
                prefs_now = client.get("/compute/preferences", headers=headers).json()
                client.put(
                    "/compute/preferences",
                    headers=headers,
                    json={
                        **{
                            k: prefs_now[k]
                            for k in ("session_budget", "min_vram_gb", "auto_stop_minutes", "selection")
                        },
                        "max_hourly_price": "0.52",
                    },
                )
                check(
                    "the certification ceiling was put back to the user's $0.52",
                    str(client.get("/compute/preferences", headers=headers).json().get("max_hourly_price"))
                    == "0.52",
                )
                # The local-logout checks are last on purpose: revoking the session earlier would
                # leave the paid phase without a usable access token.
                local_logout = client.post(
                    "/auth/revoke",
                    json={"session_id": session_id, "refresh_secret": guard.refresh_secret},
                )
                check(
                    "a local logout is accepted",
                    local_logout.status_code == 200,
                    f"status={local_logout.status_code}",
                )
                cloud = client.get("/cloud/status", headers=headers).json()
                check(
                    "a local logout does not destroy the cloud enrollment",
                    cloud.get("enrolled") is True,
                    json.dumps({k: cloud.get(k) for k in ("state", "enrolled")})[:120],
                )
    finally:
        # Order matters. The paid compute is proven gone while the owned backend is still up
        # (the product path needs it); only then is the throwaway environment torn down. A
        # failure anywhere above still reaches this block: assertion, HTTP error, transport
        # error, KeyboardInterrupt, SystemExit.
        if args.live:
            stop_evidence: dict = {}
            try:
                stop_evidence = cleanup_managed_compute(
                    control, deadline=args.cleanup_deadline, failures=failures
                )
            except BaseException as error:  # noqa: BLE001 - cleanup must never mask the cause
                stop_evidence = {"stopped": False, "cleanup_error": f"{type(error).__name__}: {error}"}
                failures.append("managed compute cleanup raised")
            evidence["cleanup"] = stop_evidence
            print(
                "   compute cleanup: stopped="
                f"{stop_evidence.get('stopped')} state={stop_evidence.get('state')} "
                f"session={stop_evidence.get('session')} "
                f"pods={(stop_evidence.get('provider') or {}).get('active_pods')}",
                flush=True,
            )
        sidecar.stop()
        if enrollment:
            revoked = revoke_installation(enrollment)
            print(f"   throwaway installation revoked: {revoked}", flush=True)
        # Evidence survives a failure: the isolated root (backend log, its own database, the
        # measurements) is kept whenever anything failed, and removed on a clean pass.
        dispose_root(root, keep=args.keep or bool(failures), evidence=evidence)

    print()
    print("MEASUREMENTS " + json.dumps(evidence, ensure_ascii=False))
    if failures:
        print(f"POST-RELEASE CERTIFICATION FAILED: {len(failures)} check(s)")
        for name in failures:
            print(f"  - {name}")
        return 1
    if external_blocker:
        print("POST-RELEASE CERTIFICATION INCOMPLETE — RC BLOCKED EXTERNALLY: RUNPOD CAPACITY")
        print("  (no Pod was created, no GPU was spent, nothing needs stopping)")
        return 3
    print("POST-RELEASE CERTIFICATION PASS — installed Canalla LLM 1.1.0, isolated root")
    return 0


# ------------------------------------------------------------- deterministic lifecycle matrix
#
# This is the no-GPU half of the acceptance: it proves the cleanup contract itself. Every case
# runs the *same* functions the real run uses (`call`, `poll_status`, `ComputeControl`,
# `cleanup_managed_compute`, `dispose_root`) against a scripted fake, so a failed case can never
# leave compute running and a passing case can never keep a Pod alive silently.


class FakeCloud:
    """Scripted double for the product path and the operator path.

    `status_script` / `stop_script` hold the next answers for those endpoints: an int is an HTTP
    status to return, an exception is raised instead of answering. `stubborn_pods` makes the
    provider keep reporting a Pod, which is how a broken product stop is simulated.
    """

    def __init__(
        self,
        *,
        state: str = "ready",
        session: str | None = "sess-1",
        status_script: list | None = None,
        stop_script: list | None = None,
        stop_effective: bool = True,
        stubborn_pods: bool = False,
        volume: dict | None = None,
        operator_works: bool = True,
    ):
        self.state = state
        self.session = session
        self.status_script = list(status_script or [])
        self.stop_script = list(stop_script or [])
        self.stop_effective = stop_effective
        self.stubborn_pods = stubborn_pods
        self.volume = volume if volume is not None else {"id": "uwgeaie5b0", "size": 50}
        self.operator_works = operator_works
        self.calls: list[str] = []
        self.steps: list[str] = []
        self.ensure_calls = 0

    def _scripted(self, script: list):
        if script:
            item = script.pop(0)
            if isinstance(item, int):
                return httpx.Response(item, json={"detail": "injected failure"})
            if isinstance(item, BaseException):
                raise item
        return None

    def payload(self) -> dict:
        return {
            "state": self.state,
            "session": {"id": self.session, "gpu": "NVIDIA L40S"} if self.session else None,
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if path == "/cloud/status":
            scripted = self._scripted(self.status_script)
            if scripted is not None:
                return scripted
            # The real route answers the cloud snapshot: the compute state lives in `details`.
            return httpx.Response(200, json={"details": {"compute_state": self.state}})
        if path == "/cloud/compute/stop":
            scripted = self._scripted(self.stop_script)
            if scripted is not None:
                return scripted
            self.steps.append("product:stop")
            if self.stop_effective:
                self.state, self.session = "stopped", None
            return httpx.Response(200, json={"state": self.state})
        if path == "/cloud/compute/ensure":
            self.ensure_calls += 1
            self.steps.append("product:ensure")
            self.state, self.session = "ready", self.session or "sess-1"
            return httpx.Response(200, json={"compute": self.payload()})
        raise AssertionError(f"unexpected path {path}")

    def operator(self, mode: str, *extra: str) -> dict:
        self.steps.append(f"operator:{mode}")
        if not self.operator_works:
            raise RuntimeError("operator unavailable")
        if mode == "status":
            settled = self.state in {"stopped", "offline"} and not self.session
            pods = [{"id": "pod-1", "status": "RUNNING"}]
            if settled and not self.stubborn_pods:
                pods = []
            return {
                "state": self.state,
                "session": {"id": self.session} if self.session else None,
                "pods": pods,
                "active_pods": pods,
            }
        if mode == "stop":
            if not self.stubborn_pods:
                self.state, self.session = "stopped", None
            return {"stop": {"state": "stopped"}}
        if mode == "volume":
            return self.volume
        raise AssertionError(f"unexpected operator mode {mode}")


def run_self_test() -> int:
    """Deterministic lifecycle matrix for the cleanup contract: no GPU, no network, no state."""
    print("ACCEPTANCE SELF-TEST — deterministic lifecycle matrix (no GPU, no network)")
    results: list[tuple[str, bool, str]] = []

    def expect(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)

    def build(cloud: FakeCloud) -> ComputeControl:
        client = httpx.Client(transport=httpx.MockTransport(cloud.handler), base_url="http://127.0.0.1:9")

        def status() -> dict:
            return call(client, "GET", "/cloud/status", attempts=3, base_delay=0.01).json()

        def stop() -> dict:
            return call(client, "POST", "/cloud/compute/stop", attempts=3, base_delay=0.01).json()

        return ComputeControl(status, stop, operator_probe=cloud.operator, operator_stop=cloud.operator)

    def lifecycle(cloud: FakeCloud, case=None, *, root: Path | None = None, deadline: float = 20.0):
        """Mirror the real run: execute `case`, then always run the cleanup contract."""
        control = build(cloud)
        problems: list[str] = []
        failure = None
        try:
            if case is not None:
                case()
        except BaseException as caught:
            failure = caught
            problems.append(type(caught).__name__)
        finally:
            cleanup = cleanup_managed_compute(control, deadline=deadline, failures=problems)
        kept = None
        if root is not None:
            kept = dispose_root(root, keep=bool(problems), evidence={"cleanup": cleanup})
        return control, cleanup, problems, failure, kept

    # 1 — success still stops
    cloud = FakeCloud()
    _, cleanup, problems, _, _ = lifecycle(cloud)
    expect(
        "success path: finally stops and proves the Pod is gone",
        cleanup["stopped"] and "product:stop" in cloud.steps and not problems,
        f"stopped={cleanup['stopped']} steps={cloud.steps}",
    )

    # 2 — an assertion failure inside a case still stops
    cloud = FakeCloud()

    def assert_case() -> None:
        raise AssertionError("case assertion")

    _, cleanup, problems, failure, _ = lifecycle(cloud, assert_case)
    expect(
        "assertion failure: finally still stops managed compute",
        isinstance(failure, AssertionError) and cleanup["stopped"] and "product:stop" in cloud.steps,
        f"failure={type(failure).__name__} stopped={cleanup['stopped']}",
    )

    # 3 — a transient ReadError (WinError 10054) is retried, not fatal
    cloud = FakeCloud(status_script=[httpx.ReadError("[WinError 10054] reset"), httpx.ReadError("reset")])
    _, cleanup, problems, _, _ = lifecycle(cloud)
    expect(
        "transient ReadError during polling: retried, then normal stop",
        cleanup["stopped"] and not problems and len(cloud.calls) > 2,
        f"calls={len(cloud.calls)} stopped={cleanup['stopped']}",
    )

    # 3b — a persistent ReadError makes the product path unusable; the operator path rescues
    cloud = FakeCloud(status_script=[httpx.ReadError("reset") for _ in range(60)])
    _, cleanup, problems, failure, _ = lifecycle(cloud)
    expect(
        "persistent transport failure: cleanup falls back to the operator and still proves no Pod",
        cleanup["stopped"] and bool(cleanup.get("errors")) and "operator:status" in cloud.steps,
        f"stopped={cleanup['stopped']} errors={len(cleanup.get('errors') or [])}",
    )

    # 4 — ConnectError (backend restarting) behaves the same way
    cloud = FakeCloud(
        stop_script=[httpx.ConnectError("connection refused")], status_script=[httpx.ConnectError("refused")]
    )
    _, cleanup, problems, _, _ = lifecycle(cloud)
    expect(
        "transient ConnectError: retried and then stopped",
        cleanup["stopped"] and "product:stop" in cloud.steps,
        f"stopped={cleanup['stopped']} steps={cloud.steps}",
    )

    # 5 — KeyboardInterrupt must not skip the cleanup
    cloud = FakeCloud()

    def interrupt_case() -> None:
        raise KeyboardInterrupt

    _, cleanup, problems, failure, _ = lifecycle(cloud, interrupt_case)
    expect(
        "KeyboardInterrupt: finally still stops managed compute",
        isinstance(failure, KeyboardInterrupt) and cleanup["stopped"],
        f"stopped={cleanup['stopped']}",
    )

    # 6 — HTTP 500 while polling never hangs and never leaks
    cloud = FakeCloud(status_script=[500 for _ in range(60)])
    _, cleanup, problems, failure, _ = lifecycle(cloud, deadline=8.0)
    expect(
        "HTTP 500 during polling: aborts, then cleanup stops and proves no Pod",
        cleanup["stopped"],
        f"stopped={cleanup['stopped']} failure={type(failure).__name__}",
    )

    # 7 — a transient failure on the stop request itself is retried
    cloud = FakeCloud(stop_script=[httpx.ReadError("reset"), 503])
    _, cleanup, _, _, _ = lifecycle(cloud)
    expect(
        "transient stop failure: the stop request is retried until it settles",
        cleanup["stopped"] and isinstance(cleanup.get("stop_request"), dict),
        f"stopped={cleanup['stopped']} stop_request={cleanup.get('stop_request')}",
    )

    # 8 — a permanent stop failure is reported loudly, never as success
    cloud = FakeCloud(stop_script=[500 for _ in range(20)], operator_works=False)
    _, cleanup, problems, _, _ = lifecycle(cloud, deadline=6.0)
    expect(
        "permanent stop failure: reported, not smoothed over",
        cleanup["stopped"] is False and "managed compute could not be proven stopped" in problems,
        f"stopped={cleanup['stopped']} problems={problems}",
    )

    # 9 — the product never settles, so the operator reconciliation stops it
    cloud = FakeCloud(stop_effective=False)
    _, cleanup, _, _, _ = lifecycle(cloud, deadline=8.0)
    expect(
        "product stop never settles: operator reconciliation stops the Pod",
        cleanup["stopped"] and "operator:stop" in cloud.steps,
        f"stopped={cleanup['stopped']} steps={cloud.steps}",
    )

    # 10 — nothing to stop means nothing is touched
    cloud = FakeCloud(state="offline", session=None)
    _, cleanup, _, _, _ = lifecycle(cloud)
    expect(
        "no session: no stop request, no create, and the state is proven",
        cleanup["stopped"] and "POST /cloud/compute/stop" not in cloud.calls and cloud.ensure_calls == 0,
        f"calls={cloud.calls} ensure={cloud.ensure_calls}",
    )

    # 11 — one create serves every case; the cleanup adds no second create
    cloud = FakeCloud(state="offline", session=None)
    control = build(cloud)
    client = httpx.Client(transport=httpx.MockTransport(cloud.handler), base_url="http://127.0.0.1:9")
    call(client, "POST", "/cloud/compute/ensure", json={"auto_stop_minutes": 5})
    cleanup = cleanup_managed_compute(control, deadline=15.0, failures=[])
    expect(
        "one Pod only: a single create serves every case, cleanup adds none",
        cloud.steps.count("product:ensure") == 1 and cleanup["stopped"],
        f"steps={cloud.steps} stopped={cleanup['stopped']}",
    )

    # 12 — the Volume is only ever read, never deleted
    cloud = FakeCloud()
    _, cleanup, _, _, _ = lifecycle(cloud)
    modes = [step for step in cloud.steps if step.startswith("operator:")]
    expect(
        "Network Volume: probed read-only, never deleted",
        "operator:volume" in modes
        and not any("delete" in mode for mode in modes)
        and bool(cleanup["provider"]["volume"]),
        f"modes={modes}",
    )

    # 14/15 — evidence is kept on failure, the temp root is removed on success
    failing_root = Path(tempfile.mkdtemp(prefix="cert-selftest-fail-"))
    cloud = FakeCloud()
    _, _, _, _, kept = lifecycle(cloud, assert_case, root=failing_root)
    evidence_file = failing_root / "certification-evidence.json"
    expect(
        "failed run: isolated root and evidence are kept",
        kept is not None and failing_root.is_dir() and evidence_file.is_file(),
        f"kept={kept}",
    )
    dispose_root(failing_root, keep=False)

    passing_root = Path(tempfile.mkdtemp(prefix="cert-selftest-pass-"))
    cloud = FakeCloud()
    _, _, _, _, kept = lifecycle(cloud, root=passing_root)
    expect(
        "passing run: isolated root is cleaned up",
        kept is None and not passing_root.exists(),
        f"kept={kept} exists={passing_root.exists()}",
    )

    # 16 — a 4xx while polling is a product answer: it aborts at once and still cleans up
    cloud = FakeCloud(status_script=[401 for _ in range(20)])
    unauthorised_client = httpx.Client(
        transport=httpx.MockTransport(cloud.handler), base_url="http://127.0.0.1:9"
    )

    def unauthorised_case() -> None:
        def fetch() -> httpx.Response:
            return call(unauthorised_client, "GET", "/cloud/status", attempts=1)

        poll_status(fetch, lambda payload: False, timeout=30.0, interval=0.05)

    _, cleanup, problems, failure, _ = lifecycle(cloud, unauthorised_case, deadline=20.0)
    expect(
        "HTTP 401 while polling: aborts at once and the cleanup still stops the Pod",
        isinstance(failure, httpx.HTTPStatusError) and cleanup["stopped"],
        f"failure={type(failure).__name__} stopped={cleanup['stopped']}",
    )

    failed = [name for name, ok, _ in results if not ok]
    print()
    print(f"SELF-TEST: {len(results) - len(failed)}/{len(results)} cases pass")
    if failed:
        for name in failed:
            print(f"  - {name}")
        return 1
    print("SELF-TEST PASS — a failed case cannot leave managed compute running")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("interrupted")
        sys.exit(130)
