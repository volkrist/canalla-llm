"""The one shared-compute lifecycle: automatic chat demand and the manual prewarm button.

Shared compute is created only by the Gateway and only one Pod may ever exist, so a chat request
that needs the model and the Settings «Запустить AI» button must drive *the same* bounded ensure.
This module owns that attempt:

* **one attempt.** ``ensure`` is single-flight: concurrent chat events, the UI and the manual
  button share one in-flight Gateway call, and an attempt that is already running is reused
  instead of racing it. Nothing here ever simulates a UI click or asks the user to press a button.
* **bounded, 60 s.** A capacity search may run for ``CAPACITY_WINDOW_SECONDS`` at most, with one
  immediate catalogue read plus at most two short retries inside it — the product's live-run
  rule. Never 1200 s, never an unbounded poll. On expiry the caller gets a typed reason
  (``gpu_capacity_unavailable`` and friends), not a spinner.
* **the Gateway stays the authority.** Once a Pod exists, readiness is the Gateway's own state
  and its D-9 startup deadline; this module never creates a second Pod, never probes the model
  endpoint while compute is known not ready, and a failure ends with a typed code.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from ..providers import LLMError
from .state import CloudState, search_active

# The product rule, and the same hard bound the Gateway enforces on its own side.
CAPACITY_WINDOW_SECONDS = 60.0
# One immediate check plus at most two short retries, spread over the window.
MAX_SEARCH_ATTEMPTS = 3
# How often the cached Gateway state is re-read while waiting. The read itself is TTL-bounded
# by ``CloudState``, so polling here can never become a poll of the Gateway.
POLL_SECONDS = 1.5
# Readiness after the capacity step is the Gateway's D-9 startup deadline (300 s by default);
# this margin only covers the client's own polling and the Gateway's final write.
STARTUP_MARGIN_SECONDS = 60.0
HARD_STARTUP_SECONDS = 300.0 + STARTUP_MARGIN_SECONDS

READY_STATES = {"ready", "generating"}
# States in which a Pod is really on its way up. `searching` is deliberately absent: it is only
# a transition while a bounded search operation exists (see ``search_active``).
STARTING_STATES = {"gpu_found", "creating", "starting_pod", "mounting_storage", "loading_model"}
SETTLED_STATES = {"offline", "stopped"}
BLOCKED_STATES = {"create_unknown", "multiple_compute", "external_compute", "not_configured"}

STAGE_TEXT = {
    "searching": "Ищем GPU",
    "gpu_found": "GPU найдена, готовлю запуск",
    "creating": "Создаю compute",
    "starting_pod": "Запускаем Pod",
    "mounting_storage": "Подключаем хранилище",
    "loading_model": "Загружаем модель",
}

# Capacity failures, in the vocabulary the chat already understands. A capacity search that ran
# out of time is not a generic "AI unavailable": it has its own typed reason.
TRANSIENT_CAPACITY = "gpu_unavailable"
CAPACITY_CODES = {TRANSIENT_CAPACITY, "no_compatible_gpu", "price_limit"}
CAPACITY_UNAVAILABLE = "gpu_capacity_unavailable"


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _parse_stamp(value) -> datetime | None:
    """An ISO timestamp from the Gateway, or ``None``. A malformed stamp is never a deadline."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def capacity_code(compute: dict) -> str:
    """The typed reason for a search that found no compute. Never a bare "failed".

    A transient lack of capacity has its own code, because the honest advice is "ask again".
    Every other code (a price ceiling, a missing catalogue entry, an empty account) is passed
    through: it needs a decision, not a retry.
    """
    code = compute.get("error_code")
    if isinstance(code, str) and code and code != TRANSIENT_CAPACITY:
        return code
    return CAPACITY_UNAVAILABLE


def policy_caps(controller, user_id: str) -> dict:
    """The user's own money policy, in the shape the Gateway expects. Never widened here.

    ``allow_community`` carries the user's *own* opt-in for the second cloud tier: the Gateway
    only honours it inside the deployment's own policy, so sending it can never widen what the
    server offers.
    """
    prefs = controller.preferences(user_id) if controller is not None else None
    if prefs is None:
        return {}
    return {
        "max_hourly_price": float(prefs.max_hourly_price),
        "session_budget": float(prefs.session_budget),
        "min_vram_gb": int(prefs.min_vram_gb),
        "selection": prefs.selection,
        "gpu_id": prefs.gpu_id,
        "allow_community": bool(prefs.allow_community),
    }


def outcome_of(compute: dict, *, now: datetime, search_open: bool = False) -> dict:
    """The typed meaning of one Gateway compute payload for a caller that needs the model.

    ``search_open`` is the only client-side judgement: it says whether *this* request's bounded
    search window is still open. It is never used to keep a dead search alive — only to decide
    whether a reported ``searching`` is still this request's own attempt.
    """
    state = compute.get("state")
    code = compute.get("error_code")
    if state in READY_STATES:
        return {"kind": "ready", "code": None, "state": state}
    if state in STARTING_STATES:
        return {"kind": "starting", "code": None, "state": state}
    if state == "searching":
        if search_open:
            return {"kind": "searching", "code": None, "state": state}
        return {"kind": "unavailable", "code": capacity_code(compute), "state": state}
    if state in BLOCKED_STATES:
        return {"kind": "blocked", "code": code or state, "state": state}
    if state == "error":
        return {"kind": "error", "code": code or "runpod_unavailable", "state": state}
    if state in SETTLED_STATES:
        return {
            "kind": "unavailable",
            "code": capacity_code(compute) if code in CAPACITY_CODES else "offline",
            "state": state,
        }
    if state == "stopping":
        return {"kind": "stopped", "code": "offline", "state": state}
    return {"kind": "error", "code": code or "gateway_unavailable", "state": state}


class SharedDemand:
    """One bounded ensure attempt, shared by the chat path and the manual prewarm."""

    def __init__(self, cloud: CloudState, *, sleep=asyncio.sleep, clock=None):
        self.cloud = cloud
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Task | None = None
        self._origin: str = "chat"
        self._first: asyncio.Future | None = None

    # ----------------------------------------------------------------------------- readouts

    def compute(self) -> dict:
        return self.cloud.compute or {}

    def state(self) -> str | None:
        return self.compute().get("state")

    def ready(self) -> bool:
        return self.state() in READY_STATES

    def reusable(self) -> bool:
        """Is there already a compute this request may share instead of starting a search?"""
        compute = self.compute()
        state = compute.get("state")
        if state in READY_STATES or state in STARTING_STATES:
            return True
        # A live bounded search belongs to this request's attempt family too: a second search
        # would be a second set of catalogue reads, never a second Pod.
        return state == "searching" and search_active(compute, at=self._clock())

    # ------------------------------------------------------------------------------- ensure

    async def ensure(
        self,
        *,
        task_id: str | None = None,
        caps: dict | None = None,
        origin: str = "chat",
    ) -> dict:
        """One bounded ensure attempt: the outcome the caller must act on, or fail with."""
        task = await self.start(task_id=task_id, caps=caps, origin=origin)
        return await task

    async def start(
        self,
        *,
        task_id: str | None = None,
        caps: dict | None = None,
        origin: str = "chat",
    ) -> asyncio.Task:
        """Attach to the one in-flight attempt, or start it. Never a second attempt.

        The origin of the *attempt* is the origin of whoever started it. A background retry that
        attaches to a chat's attempt does not relabel it: the audit must keep saying `chat`, or the
        question "did the chat start compute?" becomes unanswerable again.
        """
        async with self._lock:
            if self._inflight is not None and not self._inflight.done():
                return self._inflight
            if self.reusable():
                # Already on its way (or ready): report what the Gateway actually says, never
                # a promise. ``search_open`` only carries this caller's own live window.
                return self._resolved(outcome_of(self.compute(), now=self._clock(), search_open=True))
            self._first = asyncio.get_running_loop().create_future()
            self._origin = origin
            self._inflight = asyncio.ensure_future(self._attempt(task_id, caps))
            return self._inflight

    async def prewarm(
        self,
        *,
        task_id: str | None = None,
        caps: dict | None = None,
        origin: str = "manual_prewarm",
    ) -> dict:
        """Manual «Запустить AI»: start (or reuse) the same attempt and answer straight away.

        The attempt keeps running in the background, so the button and a chat request can never
        run two searches. The answer is the Gateway's *first* answer — never the end of the
        whole bounded window, because the desktop aborts a request after 15 seconds.
        """
        task = await self.start(task_id=task_id, caps=caps, origin=origin)
        first = self._first
        if not task.done() and first is not None:
            await asyncio.wait({task, first}, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            error = task.exception()
            if error is not None:
                raise error
            return self.compute()
        return first.result() if first is not None and first.done() else self.compute()

    def cancel_pending(self) -> None:
        """«Остановить AI» is authoritative: a pending attempt must not resurrect compute."""
        task, self._inflight = self._inflight, None
        self._first = None
        if task is not None and not task.done():
            task.cancel()

    def _resolved(self, outcome: dict) -> asyncio.Task:
        async def answer() -> dict:
            return outcome

        return asyncio.ensure_future(answer())

    # -------------------------------------------------------------------------- the attempt

    async def _attempt(self, task_id: str | None, caps: dict | None) -> dict:
        # Every retry inside one attempt keeps the attempt's own origin: these are the bounded
        # retries of a single ensure, not a background loop, and the audit must show the event
        # that actually caused the work.
        deadline = self._clock() + timedelta(seconds=CAPACITY_WINDOW_SECONDS)
        attempts = 0
        while True:
            attempts += 1
            compute = await self._gateway_ensure(task_id, caps)
            open_window = self._clock() < deadline
            outcome = outcome_of(compute, now=self._clock(), search_open=open_window)
            if outcome["kind"] != "searching":
                return outcome
            left = MAX_SEARCH_ATTEMPTS - attempts
            if left <= 0:
                # The window is spent and nothing is bookable: a typed external blocker, not a
                # spinner. A later, new request may start one new bounded attempt.
                return {"kind": "unavailable", "code": capacity_code(compute), "state": "searching"}
            # Spread the remaining attempts across what is left of the window, so the attempt
            # ends with the window instead of leaving amber behind it.
            wait = max(0.0, ((deadline - self._clock()).total_seconds()) / left)
            await self._sleep(wait)

    async def _gateway_ensure(self, task_id: str | None, caps: dict | None) -> dict:
        from uuid import uuid4

        payload = await self.cloud.client.ensure_compute(
            operation_id="local-" + str(uuid4()),
            task_id=task_id,
            caps=caps or {},
            origin=self._origin,
        )
        self.cloud.compute = payload
        self.cloud.fetched_at = self.cloud.clock()
        if self._first is not None and not self._first.done():
            self._first.set_result(payload)
        return payload

    # --------------------------------------------------------------------------- readiness

    async def wait_until_ready(
        self,
        *,
        task_id: str | None = None,
        caps: dict | None = None,
        origin: str = "chat",
    ):
        """Yield the chat's progress events until the model is ready, or raise a typed LLMError.

        The order the product requires: need the model → ensure compute → wait for the
        *Gateway's* readiness → let the caller use the model. The model endpoint is never
        polled here, so a not-ready model cannot turn into a hot loop of 409s.
        """
        yield ("progress", {"state": "starting_ai", "text": "Проверяю AI…"})
        outcome = await self.ensure(task_id=task_id, caps=caps, origin=origin)
        self._fail_if_terminal(outcome)
        if outcome["kind"] == "ready":
            yield ("_done", "ready")
            return
        announced = None
        hard = self._clock() + timedelta(seconds=HARD_STARTUP_SECONDS)
        while True:
            compute = self.compute()
            state = compute.get("state")
            if state != announced:
                announced = state
                text = STAGE_TEXT.get(state or "")
                if text:
                    yield ("progress", {"state": "starting_ai", "text": text})
            # A live bounded search (this request's attempt, or one already running) is still a
            # transition: it has an identity and a deadline, and the deadline ends the wait.
            live_search = search_active(compute, at=self._clock())
            outcome = outcome_of(compute, now=self._clock(), search_open=live_search)
            if outcome["kind"] == "ready":
                yield ("_done", "ready")
                return
            if outcome["kind"] not in {"starting", "searching"}:
                # Terminal for this request: the caller shows the reason it actually got.
                raise LLMError(outcome.get("code") or CAPACITY_UNAVAILABLE)
            if self._clock() >= self._horizon(compute, hard):
                # The Gateway's own deadline (D-9 startup, or the capacity window) closed without
                # readiness: the Pod is not left billing and the user gets the typed reason.
                if state == "searching":
                    raise LLMError(capacity_code(compute))
                raise LLMError(compute.get("error_code") or "startup_timeout")
            await self._sleep(POLL_SECONDS)
            await self.cloud.refresh()

    def _horizon(self, compute: dict, hard: datetime) -> datetime:
        """The earliest deadline the Gateway published, plus the margin; never past the hard bound.

        Ready-or-fail is the Gateway's decision (its D-9 startup deadline, or the 60-second
        capacity window). The client only waits for the deadline it was told about.
        """
        horizon = hard
        search = compute.get("search") if isinstance(compute.get("search"), dict) else {}
        for value in (compute.get("startup_deadline"), search.get("deadline")):
            parsed = _parse_stamp(value)
            if parsed is not None:
                horizon = min(horizon, parsed + timedelta(seconds=STARTUP_MARGIN_SECONDS))
        return horizon

    @staticmethod
    def _fail_if_terminal(outcome: dict) -> None:
        """A typed failure, never a spinner: the caller shows the reason it actually got."""
        if outcome["kind"] in {"ready", "starting", "searching"}:
            return
        raise LLMError(outcome.get("code") or CAPACITY_UNAVAILABLE)
