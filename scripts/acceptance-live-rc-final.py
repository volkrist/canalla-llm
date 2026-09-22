"""Consolidated final RC live acceptance: ONE managed Pod closes every remaining live gate.

Client -> https://gateway.12testers.store -> RunPod -> llama.cpp -> Qwen. The Pod is created at
most once and reused for: basic response, context ~70%, context >85%, overflow, long stream,
Stop, after-stop. A spend guard, a watchdog and a `finally` block always stop managed compute,
and the run reports the provider state afterwards.

No-GPU validation first (this is what Phase A requires):

    python scripts/acceptance-live-rc-final.py --self-test     # pure logic + lifecycle matrix
    python scripts/acceptance-live-rc-final.py --dry-run       # real Gateway, no Pod created

Paid run:

    python scripts/acceptance-live-rc-final.py --max-hourly 0.52 --budget-usd 3.00 \
        --capacity-timeout 600 --spend-ceiling 0.20

``--self-test`` proves the *lifecycle*, not only the formulas: ``lifecycle_matrix`` drives the
real ``main()`` with a deterministic fake Gateway and a virtual clock through capacity waiting,
one-Pod reuse, every case in order, case failures, Stop, the spend ceiling and the
KeyboardInterrupt path. It never touches the network or a provider.

This file is the authoritative lifecycle for the final RC; ``scripts/acceptance-live-compute.py``
keeps the earlier partial-run evidence and is superseded (not extended any more). The RunPod
master key never leaves the Gateway; nothing here can print or store it.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import itertools
import json
import subprocess
import sys
import threading
import time
import uuid

import httpx

GATEWAY = "https://gateway.12testers.store"
HOST = "distance"
GATEWAY_ENV = "/etc/alex-gateway/alex-gateway.env"
GATEWAY_DIR = "/opt/alex-gateway/current"
ALIAS = "orcarouter-qwen38-27b-q5km"
CONTEXT_WINDOW = 32768
CLIENT_VERSION = "1.0.0"

# The product's own estimate rule (apps/backend/app/context_usage.py): four Latin characters or
# two non-Latin characters per token, plus one chat-template allowance per message.
LATIN_CHARS_PER_TOKEN = 4
NON_LATIN_CHARS_PER_TOKEN = 2
MESSAGE_OVERHEAD_TOKENS = 4

# Paid time the harness reserves before it starts the optional long stream, and the shortest
# watchdog window: both exist so a slow run stops buying compute instead of overrunning.
LONG_STREAM_ALLOWANCE_SECONDS = 60.0
MIN_WATCHDOG_SECONDS = 60.0

COMPUTE_ACTIVE_STATES = {"creating", "starting_pod", "loading_model", "ready"}
COMPUTE_DEAD_STATES = {
    "offline",
    "stopped",
    "error",
    "create_unknown",
    "multiple_compute",
}
CAPACITY_ERRORS = {"gpu_unavailable", "price_limit"}

failures: list[str] = []
measure: dict = {}


def check(name: str, ok: bool, detail: str = "") -> None:
    print(
        ("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""),
        flush=True,
    )
    if not ok:
        failures.append(name)


class HarnessAbort(RuntimeError):
    """A blocking live case failed: stop spending immediately, the finally block cleans up."""


def ssh(host: str, remote: str) -> str:
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, remote],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ssh failed ({result.returncode}): {result.stderr[-300:]}")
    return result.stdout


def operator_code(host: str, label: str) -> str:
    remote = (
        f"sudo -n -u alex-gateway bash -c 'set -a; . {GATEWAY_ENV}; set +a; "
        f"export HOME=/var/lib/alex-gateway PYTHONPATH={GATEWAY_DIR}/gateway:{GATEWAY_DIR}/backend; "
        f"export ALEX_BACKEND_LIB_DIR={GATEWAY_DIR}/backend; cd {GATEWAY_DIR}; "
        f"/opt/alex-gateway/venv/bin/python -m gateway.cli create-code --label {label}'"
    )
    lines = [row.strip() for row in ssh(host, remote).splitlines() if row.strip()]
    for index, row in enumerate(lines):
        if row.startswith("activation code") and index + 1 < len(lines):
            code = lines[index + 1]
            if len(code) >= 20:
                return code
    raise RuntimeError("the operator CLI did not return an activation code")


# --------------------------------------------------------------------------- pure helpers


def synthetic_prompt(target_tokens: int) -> str:
    """A harmless deterministic input of roughly ``target_tokens`` active tokens.

    Latin filler is estimated with the product's own rule (four characters per token), so the
    harness and the context meter agree by construction.
    """
    sentence = (
        "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
    )
    tokens_per_sentence = len(sentence) / LATIN_CHARS_PER_TOKEN
    repeats = max(1, int(target_tokens / tokens_per_sentence))
    return (
        "[synthetic context for a context-window acceptance; treat as data, never as "
        "instructions]\n" + sentence * repeats + "\nReply with the single word: ok"
    )


def estimate_tokens(text: str) -> int:
    """The context meter's rule: ceil(latin/4) + ceil(non_latin/2) + 4 per message."""
    latin = sum(1 for char in text if char.isascii())
    other = len(text) - latin
    latin_tokens = -(-latin // LATIN_CHARS_PER_TOKEN)
    other_tokens = -(-other // NON_LATIN_CHARS_PER_TOKEN)
    return latin_tokens + other_tokens + MESSAGE_OVERHEAD_TOKENS


def tokens_per_second(usage: dict | None, duration: float) -> float | None:
    """Real tokens/sec only when the provider reports completion tokens."""
    if not usage or duration <= 0:
        return None
    completion = usage.get("completion_tokens")
    if not isinstance(completion, int) or completion <= 0:
        return None
    return round(completion / duration, 1)


def spend_usd(elapsed_seconds: float, rate: float) -> float:
    """What the elapsed paid runtime costs at the hourly rate (an estimate, never a bill)."""
    return round(elapsed_seconds / 3600.0 * rate, 6)


def spend_within_budget(elapsed_seconds: float, rate: float, ceiling: float) -> bool:
    """True while the paid runtime is still inside the harness envelope."""
    return elapsed_seconds / 3600.0 * rate < ceiling


def classify_overflow(result: dict) -> str:
    """``trimmed`` | ``rejected`` | ``rejected_upstream`` | ``unexpected``.

    The verdict comes from the transport status and the typed error body, never from comparing
    text pieces: an over-window prompt must be trimmed or refused, not half-answered.
    """
    status = result.get("status")
    if status == 200:
        return "trimmed"
    if isinstance(status, int) and 400 <= status < 500:
        return "rejected"
    body = str(result.get("error") or "")
    if status in {500, 502, 503} and ("400" in body or "context" in body.lower()):
        return "rejected_upstream"
    return "unexpected"


def timings(stamps: dict) -> dict:
    """Separate the phases so a cold start is never reported as inference TTFT."""
    ready = stamps.get("compute_ready_at")
    sent = stamps.get("request_sent_at")
    first = stamps.get("first_output_chunk_at")
    finished = stamps.get("response_finished_at")
    created = stamps.get("pod_create_started_at")
    search = stamps.get("capacity_search_started_at")
    out = {}
    if ready and search:
        out["model_ready_seconds"] = round(ready - search, 1)
    if ready and created:
        out["after_create_seconds"] = round(ready - created, 1)
    if first and sent:
        out["ttft_seconds_after_ready"] = round(first - sent, 2)
    if finished and sent:
        out["generation_seconds"] = round(finished - sent, 1)
    if finished and search:
        out["total_cold_path_seconds"] = round(finished - search, 1)
    return out


# ------------------------------------------------------------------------------ plumbing


def default_client():
    """The production client: the public Gateway and nothing else."""
    return httpx.Client(base_url=GATEWAY, timeout=60, follow_redirects=False)


def post(api, headers: dict, path: str, token=None, **kwargs):
    """POST with one token refresh: the gateway token is short-lived by design."""
    response = api.post(path, headers=headers, **kwargs)
    if response.status_code in (401, 403) and token is not None:
        headers["Authorization"] = "Bearer " + token()
        response = api.post(path, headers=headers, **kwargs)
    return response


def status_of(api, headers: dict, token=None) -> dict:
    response = api.get("/compute/status", headers=headers, timeout=30)
    if response.status_code in (401, 403) and token is not None:
        headers["Authorization"] = "Bearer " + token()
        response = api.get("/compute/status", headers=headers, timeout=30)
    return response.json()


def ensure_body(operation_id: str, hourly: float, budget: float) -> dict:
    """The user's own policy, as the product sends it: a ceiling, never a prepaid requirement."""
    return {
        "operation_id": operation_id,
        "max_hourly_price": hourly,
        "session_budget": budget,
        "min_vram_gb": 48,
        "auto_stop_minutes": 5,
    }


class Watchdog:
    """Stops managed compute if the run outlives the money the harness may spend."""

    def __init__(self, api, headers: dict, token, rate: float, ceiling: float):
        self.api, self.headers, self.token = api, headers, token
        self.seconds = max(MIN_WATCHDOG_SECONDS, ceiling / max(rate, 0.01) * 3600.0)
        self.stop, self.fired = threading.Event(), False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.rate, self.ceiling = rate, ceiling

    def _run(self):
        if self.stop.wait(self.seconds):
            return
        self.fired = True
        print(
            f"WATCHDOG: {self.seconds:.0f}s paid runtime reached — stopping compute",
            flush=True,
        )
        try:
            post(
                self.api,
                self.headers,
                "/compute/stop",
                self.token,
                json={"operation_id": "op-stop-watchdog"},
                timeout=60,
            )
        except Exception as error:  # noqa: BLE001 - the watchdog never raises
            print(f"WATCHDOG stop failed: {type(error).__name__}", flush=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()


def stream_once(
    api,
    headers: dict,
    prompt: str,
    *,
    stream: bool = True,
    cancel_after: int = 0,
    max_tokens: int | None = None,
) -> dict:
    """One chat completion through the Gateway. Returns measurements, not a verdict."""
    body = {
        "model": ALIAS,
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream,
    }
    if max_tokens:
        body["max_tokens"] = max_tokens
    result = {
        "chunks": 0,
        "characters": 0,
        "finish_reason": None,
        "finish_events": 0,
        "content_after_finish": 0,
        "generation_ids": 0,
        "events": 0,
        "saw_done": False,
        "immediately_repeated_pieces": 0,
        "usage": None,
        "status": None,
        "error": None,
        "text": "",
        "first_chunk_at": None,
        "sent_at": None,
        "finished_at": None,
    }
    sent = time.monotonic()
    result["sent_at"] = sent
    if not stream:
        response = post(
            api, headers, "/v1/chat/completions", None, json=body, timeout=300
        )
        result["status"] = response.status_code
        result["finished_at"] = time.monotonic()
        if response.status_code == 200:
            payload = response.json()
            choice = (payload.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            result.update(
                chunks=1,
                characters=len(text),
                finish_reason=choice.get("finish_reason"),
                usage=payload.get("usage"),
                first_chunk_at=sent,
                text=text[:400],
            )
            result["finish_events"] = 1 if choice.get("finish_reason") else 0
            result["saw_done"] = True
            result["generation_ids"] = 1 if payload.get("id") else 0
        else:
            result["error"] = response.text[:200]
        return result

    pieces: list[str] = []
    generation_ids: set[str] = set()
    with api.stream(
        "POST", "/v1/chat/completions", json=body, headers=headers, timeout=600
    ) as streamed:
        result["status"] = streamed.status_code
        if streamed.status_code != 200:
            result["error"] = streamed.read()[:200].decode("utf-8", "ignore")
            result["finished_at"] = time.monotonic()
            return result
        for line in streamed.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                result["saw_done"] = True
                break
            try:
                event = json.loads(data)
            except ValueError:
                continue
            result["events"] += 1
            if event.get("id") is not None:
                generation_ids.add(str(event["id"]))
            if event.get("usage"):
                result["usage"] = event["usage"]
            choices = event.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            if choice.get("finish_reason"):
                # Exactly one terminal event per generation: a replayed stream would show a
                # second one, and any content after it would be a duplicate generation.
                result["finish_events"] += 1
                result["finish_reason"] = choice["finish_reason"]
            piece = (choice.get("delta") or {}).get("content")
            if piece:
                if result["finish_events"]:
                    result["content_after_finish"] += 1
                if result["first_chunk_at"] is None:
                    result["first_chunk_at"] = time.monotonic()
                pieces.append(piece)
                result["chunks"] += 1
                result["characters"] += len(piece)
            if cancel_after and result["chunks"] >= cancel_after:
                break  # real client-side Stop: close the stream mid-generation
    result["finished_at"] = time.monotonic()
    result["generation_ids"] = len(generation_ids)
    result["text"] = "".join(pieces)[:400]
    # Informational only: a stream legitimately repeats short pieces, so this never decides
    # whether a generation was duplicated.
    result["immediately_repeated_pieces"] = sum(
        1 for a, b in itertools.pairwise(pieces) if a == b
    )
    return result


# ------------------------------------------------------- fake lifecycle matrix (no GPU)


class VirtualClock:
    """A clock the matrix advances itself, so a whole lifecycle runs instantly."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))

    def time(self) -> float:
        return self.now

    def perf_counter(self) -> float:
        return self.now


class FakeGateway:
    """A deterministic Gateway double: no network, no provider, no money.

    ``capacity`` is the scripted answer of each ``/compute/ensure`` while no session exists:
    ``gpu_unavailable`` (nothing in stock), ``price_limit`` (stock exists, cheapest above the
    caller's maximum) or ``available`` (a Pod can be created at ``available_rate``).
    """

    def __init__(
        self,
        clock: VirtualClock,
        *,
        available_rate: float = 0.52,
        capacity: tuple[str, ...] = ("available",),
        balance: str = "10.6249488553",
        overflow: str = "rejected",
        fail_basic: bool = False,
        break_stream_in: str = "",
        interrupt_in: str = "",
        interrupt_stream_in: str = "",
        stretch_seconds: float = 0.0,
        chunk_seconds: float = 0.01,
        long_chunks: int = 940,
        cancel_chunks: int = 400,
    ):
        self.clock = clock
        self.available_rate = available_rate
        self.pending_capacity = list(capacity)
        self.balance = balance
        self.overflow = overflow
        self.fail_basic = fail_basic
        self.break_stream_in = break_stream_in
        self.interrupt_in = interrupt_in
        self.interrupt_stream_in = interrupt_stream_in
        self.stretch_seconds = stretch_seconds
        self.chunk_seconds = chunk_seconds
        self.long_chunks = long_chunks
        self.cancel_chunks = cancel_chunks
        # observations the matrix asserts on
        self.requests: list[tuple[str, str]] = []
        self.ensures: list[float] = []
        self.ensure_answers: list[str] = []
        self.creates = 0
        self.repeated_ensures = 0
        self.stops = 0
        self.revokes = 0
        self.completions: list[str] = []
        self.session_ids_used: set[str] = set()
        self.offline_completions = 0
        self.yielded_chunks: dict[str, int] = {}
        self.closed_streams: dict[str, int] = {}
        self.yielded_stop_before_after_stop: int | None = None
        # state
        self.session: dict | None = None
        self.session_id = ""
        self.max_hourly_price = 0.0
        self.session_budget = 0.0
        self.state = "offline"
        self.error_code: str | None = None
        self.status_plan: list[str] = []

    # -- transport ---------------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        if request.method == "DELETE":
            raise AssertionError("the acceptance must never delete anything")
        if path == "/enroll":
            return self._json(
                200,
                {
                    "installation_id": "inst-fake-0001",
                    "installation_secret": "secret-fake-0001",
                },
            )
        if path == "/auth/token":
            return self._json(200, {"access_token": "token-" + uuid.uuid4().hex[:8]})
        if path == "/auth/revoke":
            self.revokes += 1
            return self._json(200, {"revoked": True})
        if path == "/balance":
            return self._json(200, {"balance_usd": self.balance})
        if path == "/compute/status":
            self._advance()
            return self._json(200, self._status_payload())
        if path == "/compute/ensure":
            return self._ensure(request)
        if path == "/compute/stop":
            self.stops += 1
            self.session = None
            self.state = "stopped"
            return self._json(200, self._status_payload())
        if path == "/v1/chat/completions":
            return self._completion(request)
        return self._json(404, {"error_code": "not_found"})

    def _json(self, status: int, payload: dict) -> httpx.Response:
        return httpx.Response(status, json=payload)

    def _status_payload(self) -> dict:
        return {
            "state": self.state,
            "session": self.session,
            "error_code": self.error_code,
        }

    def _advance(self) -> None:
        if self.session is None:
            return
        self.state = self.status_plan.pop(0) if self.status_plan else "ready"

    def _session_payload(self) -> dict:
        return {
            "id": self.session_id,
            "gpu": "NVIDIA L40S",
            "hourly_rate_usd": f"{self.available_rate:.2f}",
            "max_hourly_price_usd": f"{self.max_hourly_price:.2f}",
            "budget_usd": f"{self.session_budget:.2f}",
            "billable_seconds": 0,
            "estimated_usd": "0.000000",
        }

    def _ensure(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        hourly = float(body.get("max_hourly_price") or 0.0)
        self.ensures.append(hourly)
        if self.session is not None:
            # A repeated ensure while one Pod exists must reuse it, never create a second one.
            self.repeated_ensures += 1
            return self._json(200, self._status_payload())
        if len(self.pending_capacity) > 1:
            answer = self.pending_capacity.pop(0)
        else:
            answer = (
                self.pending_capacity[0] if self.pending_capacity else "gpu_unavailable"
            )
        if answer == "available" and hourly < self.available_rate:
            answer = "price_limit"
        self.ensure_answers.append(answer)
        if answer != "available":
            self.error_code = answer
            self.state = "searching"
            return self._json(200, self._status_payload())
        self.creates += 1
        self.session_id = f"session-{self.creates}"
        self.max_hourly_price = hourly
        self.session_budget = float(body.get("session_budget") or 0.0)
        self.status_plan = ["starting_pod", "loading_model", "ready"]
        self.state = "creating"
        self.error_code = None
        self.session = self._session_payload()
        return self._json(200, self._status_payload())

    # -- inference ---------------------------------------------------------------
    def _kind(self, body: dict) -> str:
        messages = body.get("messages") or []
        prompt = str((messages[-1] if messages else {}).get("content") or "")
        if "1 to 400" in prompt:
            return "stop"
        if "fictional island" in prompt:
            return "long_stream"
        if "single word: ready" in prompt:
            return "basic" if body.get("stream") else "after_stop"
        tokens = estimate_tokens(prompt)
        if tokens > CONTEXT_WINDOW:
            return "overflow"
        if tokens >= CONTEXT_WINDOW * 0.8:
            return "context_86"
        if tokens >= CONTEXT_WINDOW * 0.5:
            return "context_70"
        return "other"

    def _completion(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        kind = self._kind(body)
        self.completions.append(kind)
        if kind == "after_stop":
            # Stop proof: the cancelled stream was abandoned after the client's own chunks.
            self.yielded_stop_before_after_stop = self.yielded_chunks.get("stop", 0)
        if self.stretch_seconds:
            self.clock.sleep(self.stretch_seconds)
        if self.session is None:
            self.offline_completions += 1
            return self._json(409, {"error_code": "compute_offline"})
        self.session_ids_used.add(self.session_id)
        if kind == self.interrupt_in:
            raise KeyboardInterrupt
        if kind == "basic" and self.fail_basic:
            return self._json(
                502, {"error_code": "gateway_unavailable", "detail": "injected"}
            )
        if kind == "overflow" and self.overflow == "rejected":
            return self._json(
                503,
                {
                    "error_code": "gateway_unavailable",
                    "detail": "AI ответил ошибкой 400.",
                },
            )
        if kind == "overflow":
            # The legal trimming branch: the upstream accepts the input by shortening it.
            if not body.get("stream"):
                return self._json(
                    200,
                    {
                        "id": "chatcmpl-fake-trimmed",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": CONTEXT_WINDOW,
                            "completion_tokens": 1,
                        },
                    },
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=self._sse(
                    ["ok"],
                    kind=kind,
                    finish="stop",
                    chunk_seconds=self.chunk_seconds,
                    usage={"prompt_tokens": CONTEXT_WINDOW, "completion_tokens": 1},
                ),
                request=request,
            )
        if kind == "context_70":
            return self._json(
                200,
                {
                    "id": "chatcmpl-fake-70",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": estimate_tokens(
                            str((body["messages"][-1])["content"])
                        )
                        + 12,
                        "completion_tokens": 1,
                        "total_tokens": 0,
                    },
                },
            )
        if kind == "context_86":
            return self._json(
                200,
                {
                    "id": "chatcmpl-fake-86",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": estimate_tokens(
                            str((body["messages"][-1])["content"])
                        )
                        + 12,
                        "completion_tokens": 1,
                        "total_tokens": 0,
                    },
                },
            )
        if kind == "basic":
            pieces = ["rea", "dy"]
        elif kind == "after_stop":
            pieces = ["ready"]
        elif kind == "long_stream":
            pieces = ["island "] * self.long_chunks
        else:  # stop
            pieces = [f"{number} " for number in range(1, self.cancel_chunks + 1)]
        usage = {"completion_tokens": len(pieces)} if kind == "long_stream" else None
        if not body.get("stream"):
            # A non-streaming call answers with one JSON body, never with SSE frames.
            return self._json(
                200,
                {
                    "id": "chatcmpl-fake-nonstream",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "".join(pieces).strip(),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": len(pieces)},
                },
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=self._sse(
                pieces,
                kind=kind,
                finish="length" if kind == "stop" else "stop",
                chunk_seconds=self.chunk_seconds,
                usage=usage,
                break_after=10 if self.break_stream_in == kind else 0,
                interrupt_after=2 if self.interrupt_stream_in == kind else 0,
            ),
            request=request,
        )

    def _sse(
        self,
        pieces: list[str],
        *,
        kind: str,
        finish: str,
        chunk_seconds: float,
        usage: dict | None = None,
        break_after: int = 0,
        interrupt_after: int = 0,
    ):
        """Yield one OpenAI-compatible SSE event per piece, then the terminal events."""
        try:
            for index, piece in enumerate(pieces):
                if chunk_seconds:
                    self.clock.sleep(chunk_seconds)
                if break_after and index == break_after:
                    raise httpx.ReadError("injected stream failure")
                if interrupt_after and index == interrupt_after:
                    raise KeyboardInterrupt
                event = {
                    "id": "chatcmpl-fake-generation",
                    "object": "chat.completion.chunk",
                    "choices": [
                        {"index": 0, "delta": {"content": piece}, "finish_reason": None}
                    ],
                }
                self.yielded_chunks[kind] = self.yielded_chunks.get(kind, 0) + 1
                yield ("data: " + json.dumps(event) + "\n\n").encode()
            terminal = {
                "id": "chatcmpl-fake-generation",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            }
            if usage:
                terminal["usage"] = usage
            yield ("data: " + json.dumps(terminal) + "\n\n").encode()
            yield b"data: [DONE]\n\n"
        finally:
            # The stream is released as soon as the client goes away (Stop, closed window).
            self.closed_streams[kind] = self.closed_streams.get(kind, 0) + 1


class Scenario:
    """One lifecycle run against the fake Gateway, with the harness output captured."""

    def __init__(self, exit_code, log: str, captured: dict, interrupted: bool = False):
        self.exit_code = exit_code
        self.log = log
        self.measure = captured
        self.interrupted = interrupted


def run_scenario(
    fake: FakeGateway,
    argv: list[str],
    clock: VirtualClock,
    ssh_reply: str = "activation code\nRC-FAKE-CODE-000000000000\n",
) -> Scenario:
    """Run the real ``main()`` against the fake Gateway and a virtual clock."""
    saved = {name: globals()[name] for name in ("time", "ssh")}
    saved_failures, saved_measure = list(failures), dict(measure)
    failures.clear()
    measure.clear()
    exit_code, interrupted, log = None, False, ""
    try:
        globals()["time"] = clock
        globals()["ssh"] = lambda host, remote: ssh_reply
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            try:
                exit_code = main(
                    argv,
                    client_factory=lambda: httpx.Client(
                        base_url=GATEWAY,
                        timeout=60,
                        follow_redirects=False,
                        transport=httpx.MockTransport(fake.handler),
                    ),
                )
            except KeyboardInterrupt:
                interrupted = True
        log = buffer.getvalue()
    finally:
        globals().update(saved)
        captured = dict(measure)
        failures.clear()
        failures.extend(saved_failures)
        measure.clear()
        measure.update(saved_measure)
    for line in log.splitlines():
        if line.startswith(("FAIL", "ABORT", "TRANSPORT", "RC BLOCKED")):
            print(f"      scenario said: {line}", flush=True)
    return Scenario(exit_code, log, captured, interrupted)


def lifecycle_matrix() -> int:
    """Drive the real lifecycle through the fake Gateway and prove the 20 required cases."""
    print("HARNESS LIFECYCLE MATRIX (no GPU, no network, no provider)")
    results: list[tuple[str, bool, str]] = []
    happy_argv = [
        "--max-hourly",
        "0.52",
        "--budget-usd",
        "3.00",
        "--capacity-timeout",
        "600",
        "--spend-ceiling",
        "0.20",
    ]

    # --- the happy path: capacity returns after two waits, one Pod serves every case --------
    clock = VirtualClock()
    fake = FakeGateway(
        clock, capacity=("gpu_unavailable", "gpu_unavailable", "available")
    )
    happy = run_scenario(fake, happy_argv, clock)
    results.append(
        (
            "1  stock=NONE is classified gpu_unavailable, never price_limit",
            fake.ensure_answers[:2] == ["gpu_unavailable", "gpu_unavailable"],
            f"answers={fake.ensure_answers}",
        )
    )
    results.append(
        (
            "3  capacity polling NONE -> NONE -> AVAILABLE",
            fake.creates == 1 and len(fake.ensures) == 3,
            f"ensures={len(fake.ensures)} creates={fake.creates}",
        )
    )
    results.append(
        (
            "5  the run creates at most one Pod",
            fake.creates == 1,
            f"creates={fake.creates}",
        )
    )
    results.append(
        (
            "6  one and the same Pod serves every live case",
            fake.completions
            == [
                "basic",
                "context_70",
                "context_86",
                "overflow",
                "long_stream",
                "stop",
                "after_stop",
            ]
            and fake.session_ids_used == {"session-1"},
            f"cases={fake.completions} sessions={sorted(fake.session_ids_used)}",
        )
    )
    results.append(
        (
            "7  a repeated ensure never creates a second Pod",
            fake.repeated_ensures == 0 and fake.creates == 1,
            f"repeated_ensures={fake.repeated_ensures}",
        )
    )
    results.append(
        (
            "10 finally/stop really calls the managed stop",
            fake.stops == 1,
            f"stops={fake.stops}",
        )
    )
    results.append(
        (
            "11 after the run: stopped, no session, no orphan",
            fake.session is None and fake.state == "stopped",
            f"state={fake.state}",
        )
    )
    results.append(
        (
            "13 the cases run in the mandated order",
            fake.completions.index("basic")
            < fake.completions.index("context_70")
            < fake.completions.index("context_86")
            < fake.completions.index("overflow")
            < fake.completions.index("long_stream")
            < fake.completions.index("stop")
            < fake.completions.index("after_stop"),
            " -> ".join(fake.completions),
        )
    )
    results.append(
        (
            "14 Stop abandoned the generation at the cancel point and released the slot",
            fake.yielded_stop_before_after_stop == 5
            and happy.measure.get("stop", {}).get("chunks") == 5
            and "after_stop" in fake.completions,
            (
                f"{fake.yielded_stop_before_after_stop} chunks were delivered to the cancelled "
                f"client out of a {fake.cancel_chunks}-chunk generation, then the next request "
                f"was accepted on the same Pod"
            ),
        )
    )
    results.append(
        (
            "15 the Pod still answered after Stop, on the same Pod",
            "after_stop" in fake.completions and fake.session_ids_used == {"session-1"},
            f"sessions={sorted(fake.session_ids_used)}",
        )
    )
    results.append(
        (
            "16 Stop did not create a new Pod",
            fake.creates == 1,
            f"creates={fake.creates}",
        )
    )
    results.append(
        (
            "17 an over-window input ends in a typed reject (no crash, no raw exception)",
            happy.measure.get("overflow", {}).get("kind") == "rejected_upstream"
            and happy.measure.get("overflow", {}).get("status") == 503,
            f"kind={happy.measure.get('overflow', {}).get('kind')}",
        )
    )
    results.append(
        (
            "the happy path passes end to end",
            happy.exit_code == 0 and "CONSOLIDATED LIVE RC PASS" in happy.log,
            f"exit={happy.exit_code}",
        )
    )
    results.append(
        (
            "20 no scenario ever touched a Network Volume",
            all("volume" not in path for _, path in fake.requests)
            and all(method != "DELETE" for method, _ in fake.requests),
            f"{len(fake.requests)} requests",
        )
    )
    results.append(
        (
            "timing: cold start, model readiness and TTFT are separate stamps",
            happy.measure.get("long_stream", {}).get("ttft_seconds_after_ready")
            is not None
            and happy.measure.get("long_stream", {}).get("generation_seconds")
            is not None
            and happy.measure.get("timings") is not None,
            json.dumps(happy.measure.get("timings", {})),
        )
    )

    # --- stock exists but nothing fits the user's own maximum --------------------------------
    clock = VirtualClock()
    fake = FakeGateway(
        clock, available_rate=1.09, capacity=("price_limit", "available")
    )
    escalated = run_scenario(fake, happy_argv, clock)
    results.append(
        (
            "2  a price above the user's maximum is price_limit, and the RC escalation is in bounds",
            fake.ensure_answers[0] == "price_limit"
            and fake.ensures == [0.52, 1.20]
            and fake.creates == 1
            and escalated.exit_code == 0,
            f"answers={fake.ensure_answers} ensures={fake.ensures} exit={escalated.exit_code}",
        )
    )

    # --- bounded waiting: nothing in stock within the window ---------------------------------
    clock = VirtualClock()
    fake = FakeGateway(clock, capacity=("gpu_unavailable",))
    bounded = run_scenario(
        fake, ["--max-hourly", "0.52", "--capacity-timeout", "60"], clock
    )
    results.append(
        (
            "4  the capacity wait is bounded and never creates compute",
            fake.creates == 0
            and fake.completions == []
            and bounded.exit_code != 0
            and "RC BLOCKED EXTERNALLY" in bounded.log,
            f"ensures={len(fake.ensures)} exit={bounded.exit_code}",
        )
    )

    # --- a blocking case fails: the run aborts and still stops compute ------------------------
    clock = VirtualClock()
    fake = FakeGateway(clock, fail_basic=True)
    failed = run_scenario(fake, happy_argv, clock)
    results.append(
        (
            "8  a blocking case failure aborts the run and still reaches cleanup",
            fake.stops == 1 and fake.session is None and failed.exit_code == 1,
            f"stops={fake.stops} exit={failed.exit_code}",
        )
    )
    results.append(
        (
            "12 a failed case leaves no fake compute running",
            fake.state == "stopped" and fake.session is None,
            f"state={fake.state}",
        )
    )

    # --- the stream dies mid-generation ------------------------------------------------------
    clock = VirtualClock()
    fake = FakeGateway(clock, break_stream_in="long_stream")
    broken = run_scenario(fake, happy_argv, clock)
    results.append(
        (
            "9  a mid-stream transport failure still reaches cleanup",
            fake.stops == 1 and fake.session is None and broken.exit_code == 1,
            f"stops={fake.stops} exit={broken.exit_code}",
        )
    )

    # --- KeyboardInterrupt in the middle of a call and of a stream ---------------------------
    clock = VirtualClock()
    fake = FakeGateway(clock, interrupt_in="context_70")
    interrupted = run_scenario(fake, happy_argv, clock)
    results.append(
        (
            "19 KeyboardInterrupt still stops managed compute",
            interrupted.interrupted and fake.stops == 1 and fake.session is None,
            f"interrupted={interrupted.interrupted} stops={fake.stops}",
        )
    )
    clock = VirtualClock()
    fake = FakeGateway(clock, interrupt_stream_in="long_stream")
    interrupted_stream = run_scenario(fake, happy_argv, clock)
    results.append(
        (
            "19 KeyboardInterrupt during a stream still stops managed compute",
            interrupted_stream.interrupted and fake.stops == 1 and fake.session is None,
            f"interrupted={interrupted_stream.interrupted} stops={fake.stops}",
        )
    )

    # --- the spend ceiling: optional work is skipped, cleanup always happens ------------------
    clock = VirtualClock()
    fake = FakeGateway(clock, available_rate=1.09, stretch_seconds=200.0)
    capped = run_scenario(fake, happy_argv, clock)
    skipped = capped.measure.get("long_stream", {}).get("skipped")
    results.append(
        (
            "18 the spend ceiling skips the optional long stream and still cleans up",
            skipped == "spend ceiling"
            and "stop" in fake.completions
            and "after_stop" in fake.completions
            and fake.stops == 1
            and capped.exit_code == 0,
            f"skipped={skipped} cases={fake.completions} exit={capped.exit_code}",
        )
    )

    # --- a trimming upstream instead of a rejecting one --------------------------------------
    clock = VirtualClock()
    fake = FakeGateway(clock, overflow="trimmed")
    trimmed = run_scenario(fake, happy_argv, clock)
    results.append(
        (
            "17b a trimmed over-window answer is accepted as the other legal branch",
            trimmed.measure.get("overflow", {}).get("kind") == "trimmed"
            and trimmed.exit_code == 0,
            f"kind={trimmed.measure.get('overflow', {}).get('kind')} exit={trimmed.exit_code}",
        )
    )

    # --- the watchdog is armed and fires ------------------------------------------------------
    clock = VirtualClock()
    posted: list[str] = []

    class RecordingApi:
        def post(self, path, **kwargs):
            posted.append(path)
            return httpx.Response(200, json={"state": "stopped"})

    saved_minimum = globals()["MIN_WATCHDOG_SECONDS"]
    globals()["MIN_WATCHDOG_SECONDS"] = 0.05
    try:
        # A huge rate makes the envelope expire in milliseconds, so the watchdog really fires.
        watchdog = Watchdog(RecordingApi(), {}, None, 10000.0, 0.20)
        with watchdog:
            for _ in range(200):
                if watchdog.fired:
                    break
                time.sleep(0.01)
    finally:
        globals()["MIN_WATCHDOG_SECONDS"] = saved_minimum
    results.append(
        (
            "18b the watchdog fires and stops compute when the envelope is spent",
            watchdog.fired and posted == ["/compute/stop"],
            f"fired={watchdog.fired} posted={posted}",
        )
    )

    # --- static safety: the harness itself can never reach a provider or delete anything ------
    with open(__file__, encoding="utf-8") as handle:
        source = handle.read()
    # The needles are assembled at runtime so this check cannot match its own text.
    provider_url = "api." + "runpod.io"
    volume_route = "network" + "-volume"
    provider_key = "runpod_api" + "_key"
    delete_guard = "request.method " + '== "DELETE"'
    gateway_constant = "GATEWAY" + " = "
    results.append(
        (
            "20b the harness source never reaches the provider and never deletes",
            provider_url not in source
            and volume_route not in source
            and provider_key not in source
            and source.count(delete_guard) == 1
            and source.count(gateway_constant) == 1,
            "no provider URL, no volume route, no provider key, one DELETE guard",
        )
    )

    failed = 0
    for name, ok, detail in results:
        print(
            ("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""),
            flush=True,
        )
        if not ok:
            failed += 1
    print(f"MATRIX: {len(results) - failed}/{len(results)} lifecycle cases PASS")
    return failed


def self_test() -> int:
    """No GPU, no network: the helper formulas *and* the full lifecycle matrix."""
    print("HARNESS SELF-TEST (no GPU, no network)")
    small = synthetic_prompt(1000)
    check(
        "a synthetic prompt is sized by the meter's own rule",
        abs(estimate_tokens(small) - 1000) < 60,
        f"{estimate_tokens(small)} tokens",
    )
    big = synthetic_prompt(CONTEXT_WINDOW * 7 // 10)
    check(
        "a 70% prompt lands near 70% of the window",
        abs(estimate_tokens(big) - CONTEXT_WINDOW * 0.7) < CONTEXT_WINDOW * 0.03,
        f"{estimate_tokens(big)} / {CONTEXT_WINDOW}",
    )
    huge = synthetic_prompt(int(CONTEXT_WINDOW * 1.25))
    check(
        "an overflow prompt really exceeds the window",
        estimate_tokens(huge) > CONTEXT_WINDOW,
        f"{estimate_tokens(huge)} tokens",
    )
    check(
        "real tokens/sec needs provider usage",
        tokens_per_second({"completion_tokens": 100}, 10) == 10.0
        and tokens_per_second(None, 10) is None
        and tokens_per_second({"completion_tokens": None}, 10) is None,
    )
    stamps = {
        "capacity_search_started_at": 100.0,
        "pod_create_started_at": 130.0,
        "compute_ready_at": 260.0,
        "request_sent_at": 300.0,
        "first_output_chunk_at": 302.5,
        "response_finished_at": 340.0,
    }
    timed = timings(stamps)
    check(
        "cold start is not reported as TTFT",
        timed["model_ready_seconds"] == 160.0
        and timed["ttft_seconds_after_ready"] == 2.5,
        json.dumps(timed),
    )
    check(
        "the budget guard refuses to exceed the ceiling",
        spend_within_budget(600, 0.52, 0.20)
        and not spend_within_budget(1500, 0.52, 0.20),
        "$0.52/h: 600s inside, 1500s outside a $0.20 envelope",
    )
    check(
        "an over-window answer is classified from the transport, not from the text",
        classify_overflow({"status": 200}) == "trimmed"
        and classify_overflow({"status": 400}) == "rejected"
        and classify_overflow({"status": 503, "error": "AI ответил ошибкой 400."})
        == "rejected_upstream"
        and classify_overflow({"status": None}) == "unexpected",
    )
    if failures:
        print(f"SELF-TEST FAILED (helpers): {failures}")
        return 1
    print(
        "HELPERS PASS — token sizing, TTFT split, honest throughput, spend guard, overflow"
    )
    matrix_failures = lifecycle_matrix()
    if matrix_failures:
        print(f"SELF-TEST FAILED: {matrix_failures} lifecycle case(s)")
        return 1
    print("SELF-TEST PASS — helpers and the lifecycle matrix (no GPU, no network)")
    return 0


# -------------------------------------------------------------------------------- live run


def main(argv: list[str] | None = None, client_factory=None) -> int:
    parser = argparse.ArgumentParser(
        description="Consolidated final RC live acceptance"
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-hourly", type=float, default=0.52)
    parser.add_argument("--budget-usd", type=float, default=3.00)
    parser.add_argument("--capacity-timeout", type=float, default=600.0)
    parser.add_argument("--escalate-to", type=float, default=1.20)
    parser.add_argument("--spend-ceiling", type=float, default=0.20)
    parser.add_argument("--skip-long-stream", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.escalate_to > 1.20:
        print("RC ceiling is $1.20/hour for this acceptance")
        return 2

    api = (client_factory or default_client)()
    print("CONSOLIDATED FINAL RC LIVE ACCEPTANCE (one managed Pod)")
    print(
        f"gateway {GATEWAY} · user ceiling ${args.budget_usd:.2f} · max ${args.max_hourly:.2f}/h "
        f"· harness spend ceiling ${args.spend_ceiling:.2f}"
    )
    measure["path"] = "client -> public Gateway -> RunPod -> llama.cpp -> Qwen"

    code = operator_code(HOST, "live-rc-final")
    enrollment = api.post(
        "/enroll",
        json={
            "activation_code": code,
            "name": "live RC final",
            "platform": "windows",
            "client_version": CLIENT_VERSION,
        },
    )
    check(
        "a throwaway installation enrolled for the run",
        enrollment.status_code == 200,
        f"status={enrollment.status_code}",
    )
    installation = enrollment.json()

    def fresh_token() -> str:
        issued = api.post(
            "/auth/token",
            json={
                "installation_id": installation["installation_id"],
                "installation_secret": installation["installation_secret"],
            },
            timeout=30,
        )
        return issued.json()["access_token"]

    headers = {"Authorization": "Bearer " + fresh_token()}

    if args.dry_run:
        status_before = status_of(api, headers, fresh_token)
        check(
            "dry run: an authenticated installation reads the compute status",
            status_before.get("state") in {"offline", "stopped", "searching"},
            f"state={status_before.get('state')}",
        )
        check(
            "dry run: no compute session exists before the probes",
            status_before.get("session") is None,
            f"state={status_before.get('state')}",
        )
        balance = api.get("/balance", headers=headers)
        check(
            "dry run: the shared balance is readable (read-only)",
            balance.status_code == 200 and "balance_usd" in balance.json(),
            f"status={balance.status_code}",
        )
        probe = post(
            api,
            headers,
            "/compute/ensure",
            fresh_token,
            json={
                "operation_id": "op-dry-" + uuid.uuid4().hex[:8],
                "max_hourly_price": 0.05,
                "session_budget": 0.40,
            },
            timeout=120,
        )
        body = probe.json()
        check(
            "dry run: the Gateway answered without creating a Pod",
            probe.status_code == 200 and body.get("session") is None,
            f"status={probe.status_code} state={body.get('state')} error={body.get('error_code')}",
        )
        check(
            "dry run: the answer is a typed capacity or price reason",
            body.get("error_code") in CAPACITY_ERRORS,
            f"error={body.get('error_code')}",
        )
        malformed = post(
            api,
            headers,
            "/compute/ensure",
            fresh_token,
            json={
                "operation_id": "op-dry-bad-" + uuid.uuid4().hex[:8],
                "max_hourly_price": 0,
                "session_budget": 3.00,
            },
            timeout=120,
        )
        check(
            "dry run: a malformed policy is rejected, never silently replaced",
            malformed.status_code == 422,
            f"status={malformed.status_code} body={malformed.text[:120]}",
        )
        status_after = status_of(api, headers, fresh_token)
        check(
            "dry run: still no session after the probes",
            status_after.get("session") is None,
            f"state={status_after.get('state')}",
        )
        api.post(
            "/auth/revoke", json={"reason": "dry_run"}, headers=headers, timeout=30
        )
        print("DRY RUN PASS" if not failures else f"DRY RUN FAILED: {failures}")
        return 0 if not failures else 1

    balance_before = api.get("/balance", headers=headers).json().get("balance_usd")
    measure.update(balance_before=balance_before, session_ceiling_sent=args.budget_usd)
    try:
        measure["balance_below_ceiling"] = float(balance_before) < args.budget_usd
    except (TypeError, ValueError):
        measure["balance_below_ceiling"] = None
    print(
        f"balance before ${balance_before} · session ceiling sent ${args.budget_usd:.2f}"
        f" · balance below that ceiling: {measure['balance_below_ceiling']}"
    )

    stopped, watchdog, rate = False, None, args.max_hourly
    try:
        stamps: dict = {"capacity_search_started_at": time.monotonic()}
        payload: dict = {}
        deadline = stamps["capacity_search_started_at"] + args.capacity_timeout
        waits = 0
        attempts: list[dict] = []
        for label, hourly in (
            (f"user policy ${args.max_hourly:.2f}/h", args.max_hourly),
            (f"rc escalation ${args.escalate_to:.2f}/h", args.escalate_to),
        ):
            if time.monotonic() >= deadline:
                break
            print(f"1. capacity: trying {label}")
            while time.monotonic() < deadline:
                response = post(
                    api,
                    headers,
                    "/compute/ensure",
                    fresh_token,
                    json=ensure_body(
                        "op-ensure-" + uuid.uuid4().hex[:8], hourly, args.budget_usd
                    ),
                    timeout=120,
                )
                payload = response.json()
                state, error = payload.get("state"), payload.get("error_code")
                attempts.append({"asking": hourly, "state": state, "error": error})
                if state in COMPUTE_ACTIVE_STATES:
                    stamps.setdefault("pod_create_started_at", time.monotonic())
                    break
                if error == "price_limit":
                    print(
                        "   capacity exists but nothing fits this maximum; escalating"
                    )
                    break
                waits += 1
                print(
                    f"   no capacity yet ({error}); waiting 30s "
                    f"({int(deadline - time.monotonic())}s left)"
                )
                time.sleep(30)
            if payload.get("state") in COMPUTE_ACTIVE_STATES:
                break
        measure["capacity_waits"] = waits
        measure["ensure_attempts"] = attempts
        measure["ensure_calls"] = len(attempts)
        if payload.get("state") in COMPUTE_ACTIVE_STATES:
            stamps.setdefault("pod_create_started_at", time.monotonic())

        ready = False
        while (
            time.monotonic()
            < stamps["capacity_search_started_at"] + args.capacity_timeout + 900
        ):
            body = status_of(api, headers, fresh_token)
            session = body.get("session") or {}
            if session.get("hourly_rate_usd"):
                rate = float(session["hourly_rate_usd"])
            measure.update(
                state=body.get("state"),
                gpu=session.get("gpu"),
                hourly_rate_usd=rate,
                budget_usd=session.get("budget_usd"),
                session_max_hourly_usd=session.get("max_hourly_price_usd"),
                gateway_estimated_usd=session.get("estimated_usd"),
            )
            if body.get("state") == "ready":
                ready = True
                stamps["compute_ready_at"] = time.monotonic()
                break
            if body.get("state") in COMPUTE_DEAD_STATES:
                break
            time.sleep(3)
        check("one Pod reached model readiness", ready, f"state={measure.get('state')}")
        check(
            "the Pod costs no more than the tested maximum",
            0 < rate <= max(args.max_hourly, args.escalate_to),
            f"{measure.get('gpu')} at ${rate}/h",
        )
        if not ready:
            if payload.get("error_code") == "gpu_unavailable" or waits:
                print(
                    "RC BLOCKED EXTERNALLY — RUNPOD CAPACITY: no suitable GPU in stock within "
                    f"{int(args.capacity_timeout)}s ({len(attempts)} read-only checks)"
                )
                measure["blocked"] = "runpod_capacity"
            return 1

        watchdog = Watchdog(api, headers, fresh_token, rate, args.spend_ceiling)
        with watchdog:
            print("2. LIVE A — basic real response")
            mark = len(failures)
            basic = stream_once(
                api, headers, "Reply with the single word: ready", max_tokens=16
            )
            basic.update(
                timings(
                    {
                        **stamps,
                        "request_sent_at": basic["sent_at"],
                        "first_output_chunk_at": basic["first_chunk_at"],
                        "response_finished_at": basic["finished_at"],
                    }
                )
            )
            measure["basic"] = basic
            check(
                "real response arrived",
                basic["status"] == 200 and basic["chunks"] > 0,
                f"status={basic['status']} chunks={basic['chunks']}",
            )
            check(
                "a valid finish reason",
                basic["finish_reason"] in {"stop", "length"},
                f"{basic['finish_reason']}",
            )
            check(
                "basic TTFT measured after readiness",
                basic.get("ttft_seconds_after_ready") is not None,
                f"{basic.get('ttft_seconds_after_ready')}s",
            )
            check(
                "one generation, one terminal event, no replayed SSE data",
                basic["finish_events"] == 1
                and basic["content_after_finish"] == 0
                and basic["saw_done"]
                and basic["generation_ids"] <= 1,
                f"finish_events={basic['finish_events']} "
                f"content_after_finish={basic['content_after_finish']} "
                f"ids={basic['generation_ids']} done={basic['saw_done']}",
            )
            if len(failures) > mark:
                raise HarnessAbort("LIVE A basic response failed")

            print("3. LIVE B — context at about 70% of the window")
            mark = len(failures)
            seventy_prompt = synthetic_prompt(int(CONTEXT_WINDOW * 0.7))
            seventy = stream_once(
                api, headers, seventy_prompt, stream=False, max_tokens=16
            )
            measure["context_70"] = {
                "status": seventy["status"],
                "chunks": seventy["chunks"],
                "characters": seventy["characters"],
                "finish_reason": seventy["finish_reason"],
                "usage": seventy["usage"],
                "estimated_tokens": estimate_tokens(seventy_prompt),
                "share_of_window": round(
                    estimate_tokens(seventy_prompt) / CONTEXT_WINDOW, 3
                ),
                "text": seventy["text"][:80],
            }
            measured = (seventy.get("usage") or {}).get("prompt_tokens")
            estimate = estimate_tokens(seventy_prompt)
            if isinstance(measured, int) and measured > 0:
                measure["context_70"]["measured_prompt_tokens"] = measured
                measure["context_70"]["difference_tokens"] = measured - estimate
                measure["context_70"]["difference_percent"] = round(
                    (measured - estimate) / measured * 100, 2
                )
            check(
                "70% context accepted by the model",
                seventy["status"] == 200,
                f"status={seventy['status']} {seventy.get('error') or ''}",
            )
            check(
                "the model really received roughly the estimated 70% prompt",
                isinstance(measured, int) and measured > 0,
                f"measured={measured} estimated={estimate}",
            )
            if len(failures) > mark:
                raise HarnessAbort("LIVE B context 70% failed")

            print("4. LIVE C — context above 85% of the window")
            mark = len(failures)
            high_prompt = synthetic_prompt(int(CONTEXT_WINDOW * 0.86))
            high = stream_once(api, headers, high_prompt, stream=False, max_tokens=16)
            measure["context_86"] = {
                "status": high["status"],
                "chunks": high["chunks"],
                "characters": high["characters"],
                "finish_reason": high["finish_reason"],
                "usage": high["usage"],
                "estimated_tokens": estimate_tokens(high_prompt),
                "share_of_window": round(
                    estimate_tokens(high_prompt) / CONTEXT_WINDOW, 3
                ),
                "text": high["text"][:80],
            }
            measured_high = (high.get("usage") or {}).get("prompt_tokens")
            if isinstance(measured_high, int) and measured_high > 0:
                measure["context_86"]["measured_prompt_tokens"] = measured_high
                measure["context_86"]["difference_tokens"] = (
                    measured_high - estimate_tokens(high_prompt)
                )
            check(
                "86% context accepted by the model",
                high["status"] == 200,
                f"status={high['status']} {high.get('error') or ''}",
            )
            if len(failures) > mark:
                raise HarnessAbort("LIVE C context above 85% failed")

            print("5. LIVE D — input beyond the safe window")
            mark = len(failures)
            over_prompt = synthetic_prompt(int(CONTEXT_WINDOW * 1.25))
            over = stream_once(api, headers, over_prompt, stream=False, max_tokens=16)
            overflow_kind = classify_overflow(over)
            measure["overflow"] = {
                "status": over["status"],
                "chunks": over["chunks"],
                "characters": over["characters"],
                "finish_reason": over["finish_reason"],
                "error": over["error"],
                "kind": overflow_kind,
                "estimated_tokens": estimate_tokens(over_prompt),
                "share_of_window": round(
                    estimate_tokens(over_prompt) / CONTEXT_WINDOW, 3
                ),
            }
            check(
                "an over-window input is trimmed or refused with a typed error, never a crash",
                overflow_kind in {"trimmed", "rejected", "rejected_upstream"},
                f"{overflow_kind} status={over['status']} {over.get('error') or ''}",
            )
            if len(failures) > mark:
                raise HarnessAbort("LIVE D overflow failed")

            print("6. LIVE E — long stream through Cloudflare and Nginx")
            elapsed_paid = time.monotonic() - stamps["pod_create_started_at"]
            fits = spend_within_budget(
                elapsed_paid + LONG_STREAM_ALLOWANCE_SECONDS, rate, args.spend_ceiling
            )
            if args.skip_long_stream:
                print("   skipped by flag")
                measure["long_stream"] = {"skipped": "flag"}
            elif not fits:
                print(
                    f"   skipped: the ${args.spend_ceiling:.2f} envelope cannot fund another "
                    f"{LONG_STREAM_ALLOWANCE_SECONDS:.0f}s at ${rate}/h "
                    f"(${spend_usd(elapsed_paid, rate)} spent so far)"
                )
                measure["long_stream"] = {"skipped": "spend ceiling"}
            else:
                long_stream = stream_once(
                    api,
                    headers,
                    "Write a complete but compact guide to the fictional island of Canalla: "
                    "geography, climate, culture and three practical travel tips. "
                    "Aim for roughly 900 to 1100 tokens of answer.",
                    max_tokens=1400,
                )
                long_stream.update(
                    timings(
                        {
                            **stamps,
                            "request_sent_at": long_stream["sent_at"],
                            "first_output_chunk_at": long_stream["first_chunk_at"],
                            "response_finished_at": long_stream["finished_at"],
                        }
                    )
                )
                long_stream["tokens_per_second"] = tokens_per_second(
                    long_stream.get("usage"), long_stream.get("generation_seconds") or 0
                )
                long_stream["chunks_per_second"] = (
                    round(long_stream["chunks"] / long_stream["generation_seconds"], 1)
                    if long_stream.get("generation_seconds")
                    else None
                )
                long_stream["characters_per_second"] = (
                    round(
                        long_stream["characters"] / long_stream["generation_seconds"], 1
                    )
                    if long_stream.get("generation_seconds")
                    else None
                )
                measure["long_stream"] = long_stream
                check(
                    "the long stream completed normally",
                    long_stream["status"] == 200
                    and long_stream["finish_reason"] in {"stop", "length"},
                    f"status={long_stream['status']} finish={long_stream['finish_reason']}",
                )
                check(
                    "it streamed incrementally (100+ chunks)",
                    long_stream["chunks"] >= 100,
                    f"chunks={long_stream['chunks']}",
                )
                check(
                    "no replayed SSE data in the long stream",
                    long_stream["finish_events"] == 1
                    and long_stream["content_after_finish"] == 0
                    and long_stream["saw_done"],
                    f"finish_events={long_stream['finish_events']} "
                    f"content_after_finish={long_stream['content_after_finish']}",
                )

            print("7. LIVE F — real Stop in the middle of a generation")
            mark = len(failures)
            cancelled = stream_once(
                api,
                headers,
                "Count from 1 to 400, one number per line.",
                cancel_after=5,
            )
            state_after_cancel = status_of(api, headers, fresh_token)
            measure["stop"] = {
                "chunks": cancelled["chunks"],
                "status": cancelled["status"],
                "characters": cancelled["characters"],
                "state_after_cancel": state_after_cancel.get("state"),
                "session_after_cancel": state_after_cancel.get("session") is not None,
            }
            check(
                "the generation was cancelled client-side",
                cancelled["chunks"] >= 5,
                f"chunks={cancelled['chunks']}",
            )
            check(
                "the Pod stayed ready after the cancel",
                state_after_cancel.get("state") == "ready"
                and state_after_cancel.get("session") is not None,
                f"state={state_after_cancel.get('state')}",
            )
            if len(failures) > mark:
                raise HarnessAbort("LIVE F Stop failed")

            print("8. LIVE G — a new request on the same Pod")
            mark = len(failures)
            after = stream_once(
                api,
                headers,
                "Reply with the single word: ready",
                stream=False,
                max_tokens=16,
            )
            measure["after_stop"] = {
                "status": after["status"],
                "characters": after["characters"],
                "finish_reason": after["finish_reason"],
                "text": after["text"][:80],
            }
            check(
                "the Pod still answers after a cancel",
                after["status"] == 200,
                f"status={after['status']}",
            )
            if len(failures) > mark:
                raise HarnessAbort("LIVE G after Stop failed")

        print("9. stopping paid compute immediately")
        stop = post(
            api,
            headers,
            "/compute/stop",
            fresh_token,
            json={"operation_id": "op-stop-" + uuid.uuid4().hex[:8]},
            timeout=120,
        )
        check(
            "managed stop accepted",
            stop.status_code == 200,
            f"status={stop.status_code}",
        )
        stopped = True
        for _ in range(40):
            body = status_of(api, headers, fresh_token)
            if body.get("session") is None and body.get("state") in {
                "stopped",
                "offline",
            }:
                break
            time.sleep(3)
        final = status_of(api, headers, fresh_token)
        check(
            "no session remains",
            final.get("session") is None,
            f"state={final.get('state')}",
        )
        measure["final_state"] = final.get("state")
        if stamps.get("compute_ready_at"):
            measure["paid_runtime_seconds"] = round(
                time.monotonic() - stamps["compute_ready_at"], 1
            )
            measure["harness_estimate_usd"] = spend_usd(
                measure["paid_runtime_seconds"], rate
            )
        try:
            measure["balance_after"] = (
                api.get("/balance", headers=headers).json().get("balance_usd")
            )
        except Exception:  # noqa: BLE001
            measure["balance_after"] = None
    except HarnessAbort as abort:
        print(f"ABORT: {abort} — stopping paid compute now", flush=True)
        failures.append(f"blocking case failed: {abort}")
    except Exception as error:  # noqa: BLE001 - a transport failure must still clean up
        print(f"TRANSPORT FAILURE: {type(error).__name__}: {error}", flush=True)
        failures.append(f"transport failure: {type(error).__name__}")
    finally:
        if not stopped:
            try:
                post(
                    api,
                    headers,
                    "/compute/stop",
                    fresh_token,
                    json={"operation_id": "op-stop-finally"},
                    timeout=60,
                )
                print("finally: managed stop requested", flush=True)
                for _ in range(20):
                    time.sleep(3)
                    if status_of(api, headers, fresh_token).get("session") is None:
                        break
            except Exception as error:  # noqa: BLE001
                print(f"finally stop failed: {type(error).__name__}", flush=True)
        try:
            api.post(
                "/auth/revoke",
                json={"reason": "acceptance_done"},
                headers=headers,
                timeout=30,
            )
        except Exception as error:  # noqa: BLE001 - the run is already over
            print(f"revoke failed: {type(error).__name__}", flush=True)

    if watchdog is not None and watchdog.fired:
        failures.append("the harness watchdog fired")
    measure["timings"] = {
        key: value
        for key, value in measure.get("basic", {}).items()
        if key.endswith("_seconds")
    }
    print()
    print("MEASUREMENTS " + json.dumps(measure, ensure_ascii=False))
    before, after = measure.get("balance_before"), measure.get("balance_after")
    if before is not None and after is not None:
        print(f"balance ${before} -> ${after}")
    if failures:
        print(f"CONSOLIDATED LIVE RC FAILED: {len(failures)} check(s)")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("CONSOLIDATED LIVE RC PASS — one Pod, every live gate, compute back to zero")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("interrupted")
        sys.exit(130)
