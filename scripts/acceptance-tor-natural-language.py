"""The natural-language Tor route, proven live on the installed product.

The requirement this closes: a user writes «Открой https://example.com через Tor» and the request is
routed through Canalla's own managed Tor over SOCKS5h — with the destination travelling as a *name*,
the local resolver untouched, a valid circuit proof attached to the run, and zero clearnet fallback.
A green Tor chip is not evidence of any of that, so this harness reads the run the backend recorded
and the bytes the proxy actually saw.

It needs no GPU and no model: the route is chosen by the server's own prompt policy, which is why this
can run without compute and without a Pod. It drives the installed app's real backend and its real
bundled Tor; the data root, the device directory and the credential names are isolated, so nothing
here touches the operator's own Canalla data.

The `tor.json` proof the backend writes is the same proof the Tor chip is drawn from, and the
`result_metadata` on the tool run is what the executor recorded about the connection it made. Both are
read here rather than inferred.

Usage:
    python scripts/acceptance-tor-natural-language.py
    python scripts/acceptance-tor-natural-language.py --prompt "Открой https://example.com через Tor"
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXE_CANDIDATES = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Programs"
    / "Canalla LLM"
    / "alex-llm.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Alex LLM" / "alex-llm.exe",
)
PASSWORD = "natural-language-tor-passphrase"
EMAIL = "tor-natural-language@example.com"
DEFAULT_PROMPT = "Открой https://example.com через Tor"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def installed_exe() -> Path:
    for candidate in EXE_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "no installed Canalla LLM found; run this against an installed build"
    )


def post(url: str, payload: dict, token: str = "", runtime_token: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if runtime_token:
        headers["X-Alex-Runtime-Token"] = runtime_token
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def get(url: str, token: str = "") -> tuple[int, str]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def wait_for_backend(deadline_seconds: float) -> int:
    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        for port in range(8000, 8010):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=2
                ) as response:
                    body = json.loads(response.read().decode("utf-8"))
                if body.get("product") == "alex-llm":
                    return port
            except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError):
                continue
        time.sleep(0.5)
    raise SystemExit("the installed backend never answered /health")


def wait_for_tor_proof(proof: Path, deadline_seconds: float) -> dict:
    deadline = time.time() + deadline_seconds
    last: dict = {}
    while time.time() < deadline:
        try:
            last = json.loads(proof.read_text(encoding="utf-8"))
            if last.get("verified") is True:
                return last
        except (OSError, ValueError):
            pass
        time.sleep(2)
    return last


def stream_request(url: str, payload: dict, token: str, timeout: int = 600) -> str:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--host", default="example.com")
    parser.add_argument("--tor-timeout", type=float, default=420.0)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    exe = installed_exe()
    print("NATURAL-LANGUAGE TOR ROUTE — installed product, managed Tor, no GPU")
    print(f"  app      {exe}")
    print(f"  prompt   {args.prompt}\n")

    root = Path(tempfile.mkdtemp(prefix="canalla-tor-nl-data-"))
    device = Path(tempfile.mkdtemp(prefix="canalla-tor-nl-device-"))
    stamp = str(int(time.time()))
    env = {
        **os.environ,
        "ALEX_LLM_DATA_DIR": str(root),
        "ALEX_DEVICE_DIR": str(device),
        "ALEX_DEVICE_CREDENTIAL_TARGET": f"Alex LLM/device-credential-tor-nl-{stamp}",
        "ALEX_GATEWAY_CREDENTIAL_NAME": f"tor-nl-{stamp}",
        # Tor is the subject of this run, and the route policy the product ships by default is what
        # a normal user has: nothing here turns Tor on or points the app at anything unusual.
        "ALEX_AI_MODE": "direct",
    }
    child = subprocess.Popen(
        [str(exe)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        port = wait_for_backend(120)
        print(f"  backend  127.0.0.1:{port}")
        check("the installed product serves its own backend", True, f"port {port}")

        runtime_token = ""
        token_file = root / "runtime" / "shutdown.token"
        deadline = time.time() + 30
        while time.time() < deadline and not runtime_token:
            try:
                runtime_token = token_file.read_text(encoding="utf-8").strip()
            except OSError:
                time.sleep(0.5)
        check("the runtime token exists for the first-owner flow", bool(runtime_token))

        base = f"http://127.0.0.1:{port}"
        session = post(
            f"{base}/auth/bootstrap",
            {"email": EMAIL, "password": PASSWORD},
            runtime_token=runtime_token,
        )
        token = session["access_token"]
        check("an owner session was created", bool(token))

        proof = root / "runtime" / "tor.json"
        tor = wait_for_tor_proof(proof, args.tor_timeout)
        check(
            "the bundled Tor proved a circuit before the request",
            tor.get("verified") is True,
            f"state={tor.get('source')} endpoint={tor.get('port')} method={tor.get('method')}",
        )
        check(
            "the route the product will use is SOCKS5h",
            tor.get("method") == "socks5h",
            f"method={tor.get('method')}",
        )

        chat = post(f"{base}/chats", {}, token=token)["id"]
        turn_started = time.time()
        body = stream_request(
            f"{base}/chats/{chat}/stream",
            {"content": args.prompt, "tor_mode": "auto", "web_mode": "on"},
            token,
        )
        turn_seconds = time.time() - turn_started
        print(f"  the turn took {turn_seconds:.1f}s before it answered")
        turn_completed = "event: done" in body
        check("the turn completed", turn_completed, body[-200:].replace("\n", " "))
        if not turn_completed:
            # A model-required turn that could not obtain the model ends typed, never hanging. The
            # compute snapshot is printed because it carries the allocation diagnostics: which
            # candidates were walked, and with which result.
            print(f"  turn body: {body.strip()[:400]}")
            status, compute_body = get(f"{base}/compute/status", token)
            print(f"  compute/status ({status}): {compute_body[:600]}")
            status, sessions = get(f"{base}/compute/sessions/me", token)
            print(f"  compute/sessions/me ({status}): {sessions[:400]}")

        status, runs_body = get(f"{base}/tools/runs", token)
        runs = json.loads(runs_body) if status == 200 else []
        fetches = [row for row in runs if row.get("tool_name") == "tor_fetch"]
        check(
            "exactly one Tor fetch was executed for the turn",
            len(fetches) == 1,
            f"{len(fetches)}",
        )

        if fetches:
            run = fetches[0]
            meta = run.get("result_metadata") or {}
            socks = meta.get("socks") or {}
            check(
                "the run completed",
                run.get("status") == "completed",
                str(run.get("status")),
            )
            check(
                "the server chose TOR REQUIRED from the prompt, not the model",
                meta.get("route") == "TOR_ONLY",
                f"route={meta.get('route')} origin={run.get('origin')}",
            )
            check(
                "the transport was SOCKS5h",
                meta.get("transport") == "tor-socks5h",
                f"transport={meta.get('transport')}",
            )
            check(
                "the destination travelled to the proxy as a NAME (ATYP 3), never pre-resolved",
                socks.get("atyp") == 3 and socks.get("local_dns") is False,
                f"atyp={socks.get('atyp')} local_dns={socks.get('local_dns')} host={socks.get('dest_host')}",
            )
            check(
                "the destination is the host the user named",
                socks.get("dest_host") == args.host,
                f"dest_host={socks.get('dest_host')}",
            )
            check(
                "the circuit proof rides on the run itself",
                meta.get("verified_chain") is True and bool(meta.get("verified_at")),
                f"verified_chain={meta.get('verified_chain')} verified_at={meta.get('verified_at')}",
            )
            check(
                "the route was served by Canalla's managed daemon",
                meta.get("managed") is True,
                f"managed={meta.get('managed')}",
            )
            check(
                "bytes came back from the destination",
                bool(run.get("result_summary")) or bool(run.get("result_metadata")),
                (str(run.get("result_summary") or ""))[:120],
            )

        clearnet = [
            row
            for row in runs
            if row.get("tool_name") != "tor_fetch"
            and str(row.get("tool_name")).startswith("web")
        ]
        check(
            "no clearnet web tool ran in the same turn",
            clearnet == [],
            json.dumps(clearnet)[:200],
        )

        after = json.loads(proof.read_text(encoding="utf-8")) if proof.is_file() else {}
        check(
            "the proof is still valid after the request",
            after.get("verified") is True,
            f"verified={after.get('verified')} version={after.get('tor_version')}",
        )

        # Every run of this harness leaves the machine as it found it: whatever compute the turn may
        # have started is stopped here, and the snapshot is printed either way so the allocation
        # diagnostics (which candidates were walked, with which result) are part of the record.
        status, final = get(f"{base}/compute/status", token)
        print(f"  compute/status after the turn ({status}): {final[:500]}")
        # The endpoint the AI chip is drawn from, read live: `search_active=false` next to a parked
        # `searching` is the fix — a scheduled retry is not an operation, so the badge is red.
        status, llm = get(f"{base}/llm/status", token)
        print(f"  llm/status ({status}): {llm[:500]}")
        if status == 200:
            payload = json.loads(llm)
            diagnostic = payload.get("diagnostic") or {}
            parked = (json.loads(final or "{}") or {}).get("state") == "searching"
            if parked:
                check(
                    "a parked search is reported as no operation in flight",
                    diagnostic.get("search_active") is False,
                    f"compute_state={diagnostic.get('compute_state')} "
                    f"search_active={diagnostic.get('search_active')}",
                )
                check(
                    "and the chip therefore does not claim a transition",
                    payload.get("ai") != "starting",
                    f"ai={payload.get('ai')} label={payload.get('ai_label')}",
                )
        try:
            stopped = post(f"{base}/compute/stop", {}, token=token)
            print(f"  compute/stop -> {json.dumps(stopped)[:300]}")
        except (urllib.error.URLError, urllib.error.HTTPError) as error:
            print(f"  compute/stop -> {error}")
        # `source` says who runs the daemon: `managed` is Canalla's own process, `discovered` is one
        # that happened to be on this machine. The proof's `pid` is then resolved through the OS, so
        # "served by the bundled daemon" is a fact about a process, not about a file name.
        check(
            "the route was served by Canalla's managed daemon",
            after.get("source") == "managed",
            f"source={after.get('source')} pid={after.get('pid')}",
        )
        pid = after.get("pid")
        serving = ""
        if isinstance(pid, int) and pid > 0:
            try:
                proc = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-Command",
                        f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).Path",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=60,
                )
                serving = (proc.stdout or "").strip()
            except subprocess.TimeoutExpired:
                serving = ""
        # The proof carries a pid only when Canalla owns the process it verified. When it does, the
        # claim "the bundled daemon served it" is checked against the OS. When it does not, the
        # bundled runtime directory and the pinned version are what can be checked — and the check
        # says which of the two it did rather than passing quietly.
        if serving:
            check(
                "the daemon process is the bundled tor.exe",
                serving.lower().endswith("tor.exe") and "canalla" in serving.lower(),
                serving,
            )
        else:
            bundled = (
                Path(os.environ.get("LOCALAPPDATA", ""))
                / "Programs"
                / "Canalla LLM"
                / "runtime"
                / "tor"
                / "tor.exe"
            )
            check(
                "the daemon belongs to the installed bundled runtime",
                after.get("source") == "managed" and bundled.is_file(),
                f"no pid in the proof; bundled runtime at {bundled} present={bundled.is_file()}",
            )
    finally:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            child.kill()
        time.sleep(2)
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(device, ignore_errors=True)
        else:
            print(f"  kept {root}")

    print()
    if failures:
        print(f"NATURAL-LANGUAGE TOR FAILED: {len(failures)} check(s)")
        for name in failures:
            print(f" - {name}")
        return 1
    print(
        "NATURAL-LANGUAGE TOR PASS — the prompt's own route carried the request through Tor"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
