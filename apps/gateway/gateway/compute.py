"""Global compute authority.

One RunPod account is shared by every installation, so the authority for "is there compute, who
owns it, what may it cost and when does it stop" lives here, server-side, in the database — not
in a client process and not in an asyncio lock.

Rules this module must never break:

* **one Pod.** A database lease plus a committed create intent means simultaneous
  ``ensure`` calls from different installations produce exactly one provider create.
* **never pinned to one slot.** A model-required request walks an ordered *set* of compatible
  placements (see the shared candidate policy ``app.compute.candidates``): one GPU id, one cloud
  tier and one datacenter are not an allocation plan. A candidate that answers "no capacity",
  "placement rejected" or "unavailable host" is left behind immediately, and the next compatible
  candidate is tried at once.
* **one total budget.** ``COMPUTE_SEARCH_TIMEOUT_SECONDS`` (60 s, server-side, hard-bounded) is
  the whole user-facing allocation budget, never a per-candidate one: a walk re-reads the
  remaining budget before every attempt and each provider call is capped by what is left.
* **no blind retry.** An ambiguous create becomes ``create_unknown`` and is resolved by
  reconciling with the provider, never by firing another create. Retries are bounded by a
  server-side attempt budget, so a create that actually succeeded while answering with a
  timeout can never be followed by a second Pod.
* **no destructive guessing.** Several Pods on the Volume, or a Pod Alex did not create,
  are reported (``multiple_compute`` / ``external_compute``) and left untouched. No code path
  deletes the Network Volume.
* **the user owns the money policy.** ``$0.52/h`` and ``$3`` per session are the *defaults* a new
  user starts with; the caller's own values are honoured inside the technical bounds ($100/h,
  $1000/session) and automatic mode always books the cheapest compatible GPU inside them.
* **server startup deadline.** A Pod that never becomes ready is stopped by the Gateway, not
  by a client that may already be gone: see ``startup_expired`` and the startup watchdog.
* **read-only status.** Nothing in ``status()`` starts, resumes or stops compute, and it never
  performs a provider round trip.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from uuid import uuid4

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .balance import SharedBalance
from .config import (
    ABSOLUTE_MAX_HOURLY_PRICE,
    ABSOLUTE_MAX_SESSION_BUDGET,
    GatewaySettings,
)
from .errors import GatewayError
from .models import AuditEvent, GatewayCompute, GatewayOperation, GatewaySession, now
from .provider import (
    allocation_policy,
    compact_ai,
    compute_preferences,
    gpu_option,
    provider_api,
    provider_error_messages,
)

logger = logging.getLogger(__name__)

# The existing product state vocabulary. Shared mode must not invent a second one.
COMPUTE_STATES = (
    "offline",
    "searching",
    "gpu_found",
    "creating",
    "starting_pod",
    "loading_model",
    "ready",
    "generating",
    "stopping",
    "stopped",
    "create_unknown",
    "multiple_compute",
    "external_compute",
    "error",
    "not_configured",
)

ACTIVE_STATES = {
    "searching",
    "gpu_found",
    "creating",
    "starting_pod",
    "loading_model",
    "ready",
    "generating",
    "stopping",
}

POD_STATES = {"PROVISIONING": "starting_pod", "STARTING": "starting_pod", "RUNNING": "loading_model"}

# D-9: the startup phase is the window between the create intent and the proof of readiness.
# A managed Pod still inside it when the server-side deadline closes is stopped by the Gateway.
# This is deliberately separate from the three other timers, each with its own semantics:
# capacity (no Pod yet), startup (Pod exists, model not ready), idle (ready, no traffic) and
# the session budget (money).
STARTUP_STATES = {"creating", "starting_pod", "loading_model"}

# The reason a startup stop carries, front to back: the client already knows this code and
# reports it as a recoverable model-startup failure.
STARTUP_TIMEOUT = "startup_timeout"

# An unresolved create is confirmed by provider evidence, then retried at most once per
# attempt budget. The budget lives on the singleton control row, not on a session row, so
# a resolved-and-retried create cannot reset it.
MAX_CREATE_ATTEMPTS = 2
CREATE_RESOLVE_SECONDS = 45
LEASE_SECONDS = 120
OPERATION_TTL_SECONDS = 24 * 3600
ALLOWED_AUTO_STOP = (0, 5, 10, 15, 30)

# The provider's own usability vocabulary lives in the shared candidate policy
# (``app.compute.candidates.USABLE_STOCK``); the Gateway must not keep a second copy of it.

# A user's session budget is a ceiling, not a prepaid requirement: a session may start as
# long as the account can fund a *meaningful* positive amount of compute. Below one cent
# there is nothing to buy, so the request is refused honestly.
MIN_START_BUDGET = Decimal("0.01")

# Money values are quantized down to whole 1/100ths of a cent, the precision the shared
# money schema accepts; rounding down never widens a spend bound.
MONEY_QUANTUM = Decimal("0.0001")

# Conditions that describe the provider catalogue rather than a broken compute.
SEARCH_ERRORS = {"no_compatible_gpu", "price_limit", "gpu_unavailable"}

# The state that says "a capacity search is in flight". It is legitimate only with an identity
# and an unexpired deadline (see ``search_active``); without them it is a leftover and collapses
# to the honest failure below, never to an indefinite «Connecting».
SEARCH_STATE = "searching"
SEARCH_COLLAPSED_STATE = "offline"
CAPACITY_UNAVAILABLE = "gpu_unavailable"

MESSAGES = {
    "offline": "AI не запущен. GPU запускается только по запросу.",
    "searching": "Ищу подходящую GPU…",
    "gpu_found": "GPU найдена, готовлю запуск…",
    "creating": "Создаю compute…",
    "starting_pod": "Запускаю Pod…",
    "loading_model": "Загружаю модель…",
    "ready": "AI готов.",
    "generating": "AI отвечает.",
    "stopping": "Останавливаю compute…",
    "stopped": "Compute остановлен.",
    "create_unknown": "Результат запуска пока неизвестен. Выполняется сверка с провайдером.",
    "multiple_compute": "Обнаружено несколько compute для аккаунта. Требуется проверка оператора.",
    "external_compute": "Обнаружен compute, созданный не Alex Cloud.",
    "error": "Ошибка compute.",
    "not_configured": "Alex Cloud не настроен на сервере: нет доступа к RunPod.",
}


def utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    value = utc(value)
    return None if value is None else value.isoformat()


def utc_present(value: datetime) -> datetime:
    """`utc()` for a timestamp the caller knows is present; `utc()` returns None only for None."""
    return utc(value) or value


def decimal_or_none(value) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        return None
    return result if result.is_finite() else None


def estimate(session: GatewaySession, at: datetime) -> tuple[int, Decimal]:
    """Same billing estimate the direct-mode controller uses."""
    start = utc(session.started_at)
    if start is None:
        return 0, Decimal("0")
    end = utc(session.stopped_at) or utc_present(at)
    seconds = max(0, int((end - start).total_seconds()))
    return seconds, (Decimal(seconds) / Decimal(3600) * session.hourly_rate).quantize(Decimal("0.000001"))


class ComputeAuthority:
    def __init__(
        self,
        settings: GatewaySettings,
        *,
        api=None,
        balance: SharedBalance | None = None,
        sessions=None,
        clock=now,
        probe=None,
        queue=None,
        transport=None,
    ):
        self.settings = settings
        if sessions is None:
            from .database import SessionLocal

            sessions = SessionLocal
        self.sessions = sessions
        self.api = api if api is not None else provider_api(settings)
        self.balance = balance or SharedBalance(
            self.api,
            active=self.gpu_active,
            active_interval=settings.balance_active_seconds,
            idle_interval=settings.balance_idle_seconds,
            messages=provider_error_messages(),
        )
        self.clock = clock
        self.probe = probe or self._http_probe
        self.transport = transport
        self.queue = queue
        self.lock = asyncio.Lock()
        self._last_activity_write: datetime | None = None
        # The non-secret allocation record of the ensure call this process just served. The
        # durable copy lives in the audit trail; this only saves the caller a second read.
        self._last_allocation: dict | None = None

    # ------------------------------------------------------------------ infrastructure

    def initialize(self) -> None:
        with self.sessions() as db:
            if not db.get(GatewayCompute, 1):
                db.add(GatewayCompute(id=1, state="offline", updated_at=self.clock()))
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()

    def control(self, db) -> GatewayCompute:
        row = db.get(GatewayCompute, 1)
        if row is None:
            db.add(GatewayCompute(id=1, state="offline", updated_at=self.clock()))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
            row = db.get(GatewayCompute, 1)
        return row

    @asynccontextmanager
    async def lease(self, owner: str):
        """Database CAS lease. A busy lease means another operation is authoritative."""
        async with self.lock:
            self.initialize()
            token = f"{owner}:{uuid4()}"
            with self.sessions() as db:
                self.control(db)
                result = db.execute(
                    update(GatewayCompute)
                    .where(
                        GatewayCompute.id == 1,
                        (GatewayCompute.lease_owner.is_(None))
                        | (GatewayCompute.lease_until.is_(None))
                        | (GatewayCompute.lease_until < self.clock()),
                    )
                    .values(lease_owner=token, lease_until=self.clock() + timedelta(seconds=LEASE_SECONDS))
                )
                db.commit()
                if result.rowcount != 1:
                    raise GatewayError("gateway_busy")
            try:
                yield
            finally:
                with self.sessions() as db:
                    db.execute(
                        update(GatewayCompute)
                        .where(GatewayCompute.id == 1, GatewayCompute.lease_owner == token)
                        .values(lease_owner=None, lease_until=None)
                    )
                    db.commit()

    def session_row(self, db) -> GatewaySession | None:
        control = self.control(db)
        if not control.active_session_id:
            return None
        return db.get(GatewaySession, control.active_session_id)

    def gpu_active(self) -> bool:
        try:
            with self.sessions() as db:
                row = self.session_row(db)
                return bool(row and row.state in ACTIVE_STATES)
        except Exception:
            return False

    def mark_activity(self) -> None:
        """Publish generation activity so idle never stops compute another client uses."""
        stamp = self.clock()
        if self._last_activity_write and (stamp - self._last_activity_write).total_seconds() < 2:
            return
        self._last_activity_write = stamp
        try:
            with self.sessions() as db:
                control = self.control(db)
                control.last_activity_at = stamp
                row = self.session_row(db)
                if row and row.state in {"ready", "generating"}:
                    row.last_activity_at = stamp
                    row.state = "generating"
                db.commit()
        except Exception:
            logger.warning("activity_mark_failed")

    def active_inferences(self) -> int:
        return self.queue.active() if self.queue is not None else 0

    # ------------------------------------------------------------------------ helpers

    def _set_state(self, db, state: str, *, error_code=None, touch=False):
        control = self.control(db)
        if state != control.state:
            control.revision += 1
        control.state = state
        control.error_code = error_code
        control.updated_at = self.clock()
        if touch:
            control.last_activity_at = control.last_activity_at or self.clock()
        db.commit()

    # ---------------------------------------------------------------- bounded capacity search

    def search_operation(self, control: GatewayCompute) -> str | None:
        """The operation that owns the current search, or ``None``.

        A `searching` state without an operation id is a leftover: it can never be a promise
        that something is happening, so the status surface reports it as the failure it is.
        """
        value = getattr(control, "last_operation_id", None)
        return value if isinstance(value, str) and value else None

    def search_started(self, control: GatewayCompute) -> datetime | None:
        """When the search window opened. Persisted (``updated_at``), never an in-process timer.

        ``_set_state`` stamps it when the state becomes ``searching``; nothing inside a live
        window is allowed to refresh it, so a search cannot renew its own deadline and become
        an unbounded «searching» (the defect this module now refuses to reproduce).
        """
        if control.state != SEARCH_STATE:
            return None
        return utc(control.updated_at)

    def search_deadline(self, control: GatewayCompute) -> str | None:
        started = self.search_started(control)
        if started is None:
            return None
        return (started + timedelta(seconds=self.settings.compute_search_timeout_seconds)).isoformat()

    def search_active(self, control: GatewayCompute) -> bool:
        """Is a real, bounded search in flight: an identity *and* an unexpired deadline?"""
        if self.search_operation(control) is None:
            return False
        started = self.search_started(control)
        if started is None:
            return False
        elapsed = (utc_present(self.clock()) - started).total_seconds()
        return 0 <= elapsed < self.settings.compute_search_timeout_seconds

    def search_payload(self, control: GatewayCompute) -> dict | None:
        if control.state != SEARCH_STATE:
            return None
        return {
            "operation_id": self.search_operation(control),
            "started_at": iso(self.search_started(control)),
            "deadline": self.search_deadline(control),
            "timeout_seconds": self.settings.compute_search_timeout_seconds,
            "active": self.search_active(control),
            "reason": control.error_code,
        }

    def effective_state(self, control: GatewayCompute) -> str:
        """What the state really is. A search that ran out of identity or deadline is over."""
        if control.state == SEARCH_STATE and not self.search_active(control):
            return SEARCH_COLLAPSED_STATE
        return control.state

    def _note_search(self, db, *, reason: str, operation_id: str | None, installation_id: str) -> None:
        """Record one bounded capacity search, or reuse the live window without extending it."""
        control = self.control(db)
        if control.state == SEARCH_STATE and self.search_active(control):
            # An attempt inside a live window keeps the existing identity and deadline: a search
            # must never renew itself into an unbounded «searching».
            if control.error_code != reason:
                control.error_code = reason
                db.commit()
            return
        if operation_id:
            # Without an operation id the state would carry no identity, which is exactly the
            # leftover `search_active` refuses to call a transition — so it is never claimed.
            control.last_operation_id = operation_id
        self._set_state(db, SEARCH_STATE, error_code=reason)
        self.event(
            db,
            "search",
            "error",
            installation_id=installation_id,
            error_code=reason,
            detail=f"{self.settings.compute_search_timeout_seconds}s",
        )
        db.commit()

    def collect_expired_search(self, *, force: bool = False) -> bool:
        """Collapse a search that outlived its deadline. Returns whether one was collapsed.

        Server-side and independent of any client: the deadline is read from persisted
        timestamps, so a vanished caller cannot leave `searching` on the control row.

        ``force`` closes a window whose allocation operation has *concluded* (every compatible
        candidate was refused, or the budget ran out) before its deadline: `searching` may only
        ever mean that a real allocation operation is active, so a finished one is closed here
        instead of being left as amber until the deadline.
        """
        with self.sessions() as db:
            control = self.control(db)
            if control.state != SEARCH_STATE or (not force and self.search_active(control)):
                return False
            code = control.error_code or CAPACITY_UNAVAILABLE
            control.state = SEARCH_COLLAPSED_STATE
            control.error_code = code
            control.revision += 1
            control.updated_at = self.clock()
            self.event(
                db,
                "search_closed" if force else "search_expired",
                "error",
                error_code=code,
                detail=CAPACITY_UNAVAILABLE,
            )
            db.commit()
            return True

    def _caps(self, caps: dict | None) -> dict:
        """Honour the caller's own policy; enforce only technical validity.

        A user owns their money policy: an authenticated installation that asks for a higher
        maximum is not clamped to a product ceiling any more. Malformed or out-of-range values
        are rejected explicitly instead of being silently replaced (that silent replacement is
        exactly what used to hide a user's own setting).
        """
        caps = caps or {}
        hourly = decimal_or_none(caps.get("max_hourly_price"))
        if hourly is None:
            hourly = self.settings.max_hourly_price
        if not hourly.is_finite() or hourly <= 0 or hourly > ABSOLUTE_MAX_HOURLY_PRICE:
            raise GatewayError(
                "compute_policy_invalid",
                detail=f"Максимум $/час должен быть больше 0 и не больше {ABSOLUTE_MAX_HOURLY_PRICE}.",
            )
        budget = decimal_or_none(caps.get("session_budget"))
        if budget is None:
            budget = self.settings.max_session_budget
        if not budget.is_finite() or budget <= 0 or budget > ABSOLUTE_MAX_SESSION_BUDGET:
            raise GatewayError(
                "compute_policy_invalid",
                detail=f"Бюджет сессии должен быть больше 0 и не больше {ABSOLUTE_MAX_SESSION_BUDGET}.",
            )
        idle = caps.get("auto_stop_minutes")
        idle = idle if idle in ALLOWED_AUTO_STOP else self.settings.compute_idle_minutes
        vram = caps.get("min_vram_gb")
        vram = int(vram) if isinstance(vram, int) and 1 <= vram <= 1024 else self.settings.runpod_min_vram_gb
        selection = caps.get("selection")
        selection = selection if selection in {"automatic", "manual"} else "automatic"
        selected = caps.get("gpu_id")
        selected = selected if isinstance(selected, str) and 1 <= len(selected) <= 160 else None
        community = caps.get("allow_community")
        community = bool(community) if isinstance(community, bool) else False
        return {
            "max_hourly_price": hourly,
            "session_budget": budget,
            "auto_stop_minutes": idle,
            "min_vram_gb": max(vram, self.settings.runpod_min_vram_gb),
            "selection": selection,
            "gpu_id": selected if selection == "manual" else None,
            # The user's own opt-in for the second cloud tier, *and* the deployment's policy: a
            # preference can never switch on a tier the server does not offer (no hidden
            # behaviour change — see docs/compute-preferences.md §11).
            "allow_community": bool(community and self.settings.runpod_allow_community_cloud),
        }

    def _operation(self, db, operation_id: str, installation_id: str, kind: str) -> dict | None:
        row = db.get(GatewayOperation, operation_id)
        if row is None:
            return None
        if (row.installation_id and row.installation_id != installation_id) or row.kind != kind:
            raise GatewayError("gateway_invalid_request", detail="Идентификатор операции уже использован.")
        return dict(row.result or {})

    def _record_operation(
        self, operation_id: str, installation_id: str, kind: str, result: dict, digest: str
    ):
        with self.sessions() as db:
            if db.get(GatewayOperation, operation_id) is not None:
                return
            db.add(
                GatewayOperation(
                    id=operation_id,
                    installation_id=installation_id,
                    kind=kind,
                    request_digest=digest,
                    result_state=result.get("state", ""),
                    result=result,
                    created_at=self.clock(),
                    updated_at=self.clock(),
                )
            )
            try:
                db.commit()
            except IntegrityError:
                db.rollback()

    def event(
        self,
        db,
        operation: str,
        result: str,
        *,
        installation_id=None,
        error_code=None,
        detail="",
        record: dict | None = None,
        origin: str | None = None,
    ):
        """Append one audit row.

        ``record`` carries the structured, non-secret diagnostic payload (the ``cost`` JSON
        column). It is what makes "why did Canalla fail to find a GPU?" answerable without
        guessing: candidate, cloud tier, datacenter, attempt result, elapsed time, failure code.
        Credentials, Pod keys and provider URLs never enter it.

        ``origin`` names the caller that caused this ensure — the same structured payload, because
        "which caller started this" is the other half of that question.
        """
        payload = dict(record) if record else {}
        if origin is not None:
            payload["origin"] = origin
        db.add(
            AuditEvent(
                installation_id=installation_id,
                operation=operation,
                result=result,
                error_code=error_code,
                detail=str(detail)[:200],
                cost=payload,
                created_at=self.clock(),
            )
        )

    def prune_operations(self) -> None:
        cutoff = self.clock() - timedelta(seconds=OPERATION_TTL_SECONDS)
        with self.sessions() as db:
            for row in db.scalars(select(GatewayOperation).where(GatewayOperation.created_at < cutoff)).all():
                db.delete(row)
            db.commit()

    # ------------------------------------------------------- allocation candidates (walking)

    def candidate_limit(self) -> int:
        return int(allocation_policy().MAX_CANDIDATE_ATTEMPTS)

    def placements(self, volume=None) -> tuple:
        """The placements a Pod may be created in, the Volume's own datacenter first.

        The primary datacenter comes from the Network Volume the provider reports (the model
        lives there), never from a constant, so scheduling is not restricted to one datacenter by
        the code. Extra placements are the operator's own (``RUNPOD_DATACENTERS``), each with the
        Volume it mounts there: adding a datacenter that cannot mount the model's Volume would
        only produce Pods that cannot serve the configured model.
        """
        policy = allocation_policy()
        datacenter = str(getattr(volume, "dataCenter", "") or self.settings.runpod_datacenter)
        primary = policy.Placement(datacenter=datacenter, volume_id=self.settings.runpod_network_volume_id)
        return policy.parse_placements(self.settings.runpod_datacenters, primary=primary)

    def policy_payload(self) -> dict:
        """What the allocator may use, in the product's own words. Read-only, never secret."""
        policy = allocation_policy()
        placements = self.placements()
        allow = bool(self.settings.runpod_allow_community_cloud)
        blocked = policy.community_blocked_by(allow_community=allow, placements=placements)
        return {
            "cloud_tiers": list(policy.cloud_tiers(allow_community=allow)),
            "community_allowed": blocked is None,
            "community_blocked_by": blocked,
            "datacenters": [placement.audit() for placement in placements],
            "candidate_limit": self.candidate_limit(),
            "candidate_call_timeout_seconds": int(policy.CANDIDATE_CALL_TIMEOUT_SECONDS),
            "allocation_timeout_seconds": int(self.settings.compute_search_timeout_seconds),
        }

    async def allocation_plan(self, caps: dict, *, volume=None):
        """The ordered, bounded candidate set for this request, from the provider catalogue.

        Derived from the product's own configuration: the model's CUDA floor (the catalogue
        query), the VRAM floor, the user's own maximum $/hour and selection, the cloud-tier
        policy and the placements that can mount the model's Network Volume. Never from guesses.
        """
        policy = allocation_policy()
        placements = self.placements(volume)
        offers = await self.api.gpu_offers()
        return policy.build_plan(
            offers,
            min_vram_gb=int(caps["min_vram_gb"]),
            max_hourly_price=caps["max_hourly_price"],
            placements=placements,
            tiers=policy.cloud_tiers(allow_community=bool(caps.get("allow_community"))),
            selection=str(caps.get("selection") or "automatic"),
            gpu_id=caps.get("gpu_id"),
        )

    def allocation_window(self, control: GatewayCompute | None = None) -> tuple[datetime, datetime]:
        """The open bounded window: ``(started, deadline)``. Reused, never extended.

        An existing live search window owns the remaining budget, so a second attempt inside it
        cannot get another 60 s: that is what makes the timeout a *total* budget rather than one
        budget per candidate.
        """
        if control is None:
            with self.sessions() as db:
                control = self.control(db)
        started = self.search_started(control) or utc_present(self.clock())
        return started, started + timedelta(seconds=self.settings.compute_search_timeout_seconds)

    def candidate_call_timeout(self, deadline: datetime) -> float:
        """How long one candidate's provider call may take: never more than what is left."""
        remaining = (deadline - utc_present(self.clock())).total_seconds()
        cap = float(allocation_policy().CANDIDATE_CALL_TIMEOUT_SECONDS)
        return min(cap, max(1.0, remaining))

    def allocation_record(
        self,
        *,
        started: datetime,
        deadline: datetime,
        outcome: str,
        reason: str | None,
        attempts: list[dict],
        selected: dict | None = None,
        plan=None,
    ) -> dict:
        """The non-secret record of one allocation walk: the answer to "why no GPU?".

        Deliberately carries no request metadata (no operation id, no task id) and nothing
        secret: it describes *placements* — candidate GPU, cloud tier, datacenter, attempt
        result, elapsed time, failure code — and the client-facing copy stays free of request
        identifiers. The durable audit copy adds the operation id.
        """
        finished = utc_present(self.clock())
        record = {
            "outcome": outcome,
            "reason": reason,
            "started_at": utc_present(started).isoformat(),
            "finished_at": finished.isoformat(),
            "elapsed_seconds": round((finished - utc_present(started)).total_seconds(), 3),
            "budget_seconds": int(self.settings.compute_search_timeout_seconds),
            "deadline": deadline.isoformat(),
            "candidate_limit": self.candidate_limit(),
            "attempts": attempts,
            "selected": selected,
            "policy": self.policy_payload(),
        }
        if plan is not None:
            record["plan"] = plan.audit()
        return record

    def record_allocation(
        self,
        installation_id,
        record: dict,
        *,
        operation_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Persist one allocation walk (one audit row, structured and non-secret)."""
        summary = "; ".join(
            f"{item.get('cloud')}/{item.get('datacenter')}/{item.get('gpu')} "
            f"{item.get('result')}" + (f"={item.get('error_code')}" if item.get("error_code") else "")
            for item in record.get("attempts") or []
        )
        self._last_allocation = dict(record)
        durable = dict(record, operation_id=operation_id)
        with self.sessions() as db:
            self.event(
                db,
                "allocation",
                "ok" if record.get("outcome") == "selected" else "error",
                installation_id=installation_id,
                error_code=reason or record.get("reason"),
                detail=summary or (record.get("outcome") or ""),
                record=durable,
            )
            db.commit()

    def allocation_for_operation(self, operation_id: str | None) -> dict | None:
        """The allocation record of one operation id. The PK makes it unambiguous."""
        if not operation_id:
            return None
        with self.sessions() as db:
            row = db.get(GatewayOperation, operation_id)
            payload = dict(row.result or {}) if row is not None else {}
        record = payload.get("allocation")
        return record if isinstance(record, dict) else None

    @staticmethod
    def create_verdict(error) -> str:
        """What a failed create means for the candidate walk (shared with direct mode)."""
        return allocation_policy().create_verdict(error)

    @staticmethod
    def refusal_reason(error) -> str:
        """The product code a walked-past refusal ends with (shared with direct mode)."""
        return allocation_policy().refusal_reason(error)

    # ------------------------------------------------------------------------- status

    def status_payload(
        self, control: GatewayCompute | None = None, session: GatewaySession | None = None
    ) -> dict:
        last_session = None
        if control is None or session is None:
            with self.sessions() as db:
                control = control or self.control(db)
                if session is None:
                    session = self.session_row(db)
                    if session is None:
                        # The last finished run stays visible so a user can see what the
                        # previous compute cost and why it ended.
                        last_session = db.scalar(
                            select(GatewaySession).order_by(GatewaySession.created_at.desc()).limit(1)
                        )
        state = self.effective_state(control)
        error_code = control.error_code
        search_active = state == SEARCH_STATE and self.search_active(control)
        configured = bool(getattr(self.api, "configured", False))
        ai, label = compact_ai(
            provider=self.settings.llm_provider,
            app_env=self.settings.app_env,
            configured=configured,
            compute_state=state,
            error_code=error_code,
            search_active=search_active,
        )
        return {
            "state": state,
            "ai": ai,
            "ai_label": label,
            "revision": control.revision,
            "error_code": error_code,
            "detail": provider_error_messages().get(error_code) if error_code else None,
            "message": MESSAGES.get(state),
            "configured": configured,
            "global": True,
            "managed": bool(session.managed) if session else False,
            "adopted": bool(session.adopted) if session else False,
            "create_attempts": int(control.create_attempts or 0),
            "queue": {"depth": self.queue_depth(), "active": self.queue_active()},
            "session": self.session_payload(session),
            "last_session": self.session_payload(last_session, final=True),
            "idle_deadline": self.idle_deadline(session),
            "startup_deadline": self.startup_deadline(session),
            # A search is only reported as one while it has an identity and a live deadline.
            "search": self.search_payload(control),
            # What the allocator may use, and what the last operation actually tried. Both are
            # read-only, non-secret, and answer "why did Canalla fail to find a GPU?".
            "policy": self.policy_payload(),
            "allocation": self.allocation_for_operation(control.last_operation_id),
            "updated_at": iso(control.updated_at),
        }

    def session_payload(self, session: GatewaySession | None, *, final: bool = False) -> dict | None:
        if session is None:
            return None
        seconds, cost = estimate(session, self.clock())
        return {
            "id": session.id,
            "state": session.state,
            # The Pod id, the provider proxy URL and the Pod bearer key are never exposed:
            # clients must not be able to reach llama.cpp around the Gateway.
            "gpu": session.gpu_type,
            "hourly_rate_usd": str(session.hourly_rate),
            "max_hourly_price_usd": str(session.max_hourly_price),
            "budget_usd": str(session.session_budget),
            "billable_seconds": seconds,
            "estimated_usd": str(cost),
            "auto_stop_minutes": session.auto_stop_minutes,
            "managed": bool(session.managed),
            "adopted": bool(session.adopted),
            "started_at": iso(session.started_at),
            "ready_at": iso(session.ready_at),
            "stopped_at": iso(session.stopped_at) if final else None,
            "stop_reason": session.stop_reason if final else None,
            "error_code": session.error_code if final else None,
            "started_by_installation_id": session.created_by_installation_id,
        }

    def idle_deadline(self, session: GatewaySession | None) -> str | None:
        if session is None or not session.auto_stop_minutes or session.state not in {"ready", "generating"}:
            return None
        last = utc(session.last_activity_at) or utc_present(self.clock())
        return (last + timedelta(minutes=session.auto_stop_minutes)).isoformat()

    def startup_reference(self, session: GatewaySession | None) -> datetime | None:
        """Persisted baseline of the startup deadline: never an in-process timer.

        Deriving it from the create intent is what makes the watchdog survive a Gateway
        restart, a client that vanished, or a process that was killed mid-create.
        """
        if session is None:
            return None
        return utc(session.intent_at) or utc(session.created_at) or utc(session.started_at)

    def startup_started(self, session: GatewaySession | None) -> bool:
        """Is this managed session inside the startup phase the watchdog guards?"""
        if session is None or not session.managed:
            return False
        if session.ready_at is not None:
            # Readiness was proven, so the startup phase is over for good: from here the idle
            # policy owns the Pod and the deadline must never fire again.
            return False
        if session.state in STARTUP_STATES:
            return True
        return self.startup_stop_pending(session)

    def startup_stop_pending(self, session: GatewaySession | None) -> bool:
        """A startup stop the provider refused is retried, never abandoned."""
        return bool(
            session is not None and session.state == "stopping" and session.stop_reason == STARTUP_TIMEOUT
        )

    def startup_expired(self, session: GatewaySession | None) -> bool:
        """Has this session outlived the server-side startup deadline?"""
        if not self.startup_started(session):
            return False
        reference = self.startup_reference(session)
        if reference is None:
            return False
        elapsed = (utc_present(self.clock()) - reference).total_seconds()
        return elapsed >= self.settings.compute_startup_timeout_seconds

    def startup_deadline(self, session: GatewaySession | None) -> str | None:
        if not self.startup_started(session):
            return None
        reference = self.startup_reference(session)
        if reference is None:
            return None
        return (reference + timedelta(seconds=self.settings.compute_startup_timeout_seconds)).isoformat()

    async def status(self) -> dict:
        payload = self.status_payload()
        payload["balance"] = await self.balance.snapshot()
        return payload

    async def pod_target(self) -> tuple[str, str] | None:
        """Gateway-only upstream for production inference. Never serialized to a client."""
        with self.sessions() as db:
            session = self.session_row(db)
        if not session or not session.pod_id or session.state not in ACTIVE_STATES:
            return None
        key = self.settings.llm_api_key
        if len(key) < 32:
            return None
        port = self.settings.runpod_gateway_port
        return f"https://{session.pod_id}-{port}.proxy.runpod.net", key

    async def upstream_target(self) -> tuple[str, str] | None:
        with self.sessions() as db:
            session = self.session_row(db)
        if not session or session.state not in {"ready", "generating"}:
            return None
        return await self.pod_target()

    # ------------------------------------------------------------------------ provider

    def matches_volume(self, pod) -> bool:
        return any(
            mount.get("volumeId") == self.settings.runpod_network_volume_id
            for mount in pod.mounts.get("network", [])
        )

    def active_pods(self, pods) -> list:
        return [
            pod for pod in pods if self.matches_volume(pod) and pod.status not in {"EXITED", "TERMINATED"}
        ]

    async def _http_probe(self, base_url: str, key: str) -> bool:
        """Readiness is the model gateway answering with the configured alias."""
        try:
            async with httpx.AsyncClient(
                timeout=8, follow_redirects=False, transport=self.transport
            ) as client:
                response = await client.get(
                    base_url + "/v1/models", headers={"Authorization": "Bearer " + key}
                )
                if response.status_code != 200:
                    return False
                data = response.json()
        except Exception:
            return False
        if not isinstance(data, dict):
            return False
        ids = [str(item.get("id", "")) for item in data.get("data", []) if isinstance(item, dict)]
        return self.settings.llm_model in ids

    async def probe_ready(self) -> bool:
        target = await self.pod_target()
        if target is None:
            return False
        return bool(await self.probe(*target))

    # --------------------------------------------------------------------- reconcile

    async def reconcile(self) -> dict:
        """Refresh the authoritative view of provider compute. Never mutates the provider."""
        async with self.lease("reconcile"):
            return await self._reconcile_locked()

    async def _reconcile_locked(self) -> dict:
        if not getattr(self.api, "configured", False):
            with self.sessions() as db:
                self._set_state(db, "not_configured", error_code="not_configured")
            return self.status_payload()
        try:
            pods = await self.api.list_pods()
        except Exception as error:
            code = getattr(error, "code", "runpod_unavailable")
            with self.sessions() as db:
                control = self.control(db)
                session = self.session_row(db)
                if session is not None and session.state in ACTIVE_STATES:
                    control.error_code = code
                    control.updated_at = self.clock()
                    db.commit()
                elif session is None:
                    self._set_state(db, "offline", error_code=code)
                else:
                    control.error_code = code
                    control.updated_at = self.clock()
                    db.commit()
            return self.status_payload()

        with self.sessions() as db:
            active = self.active_pods(pods)
            control = self.control(db)
            session = self.session_row(db)
            if len(active) > 1:
                self._set_state(db, "multiple_compute", error_code="multiple_compute")
                self.event(db, "reconcile", "error", error_code="multiple_compute")
                db.commit()
                return self.status_payload()
            if len(active) == 1:
                pod = active[0]
                session = self._adopt_pod(db, pod, session)
                control = self.control(db)
                control.active_session_id = session.id
                state = POD_STATES.get(pod.status, session.state)
                if session.state == "ready":
                    state = "ready"
                elif not session.managed:
                    # A Pod Alex Cloud did not create is reported as external until the
                    # model gateway demonstrably answers with this deployment's alias.
                    state = "external_compute"
                if session.pending_stop:
                    state = "stopping"
                self._set_state(db, state, error_code=None, touch=True)
                return self.status_payload()
            # No active Pod for this Volume.
            if session is not None and (session.state in ACTIVE_STATES or session.state == "create_unknown"):
                if session.pod_id:
                    self._finalize(db, session, "provider_missing", error_code="not_found")
                elif session.state in {"creating", "create_unknown"}:
                    intent = utc(session.intent_at) or utc(session.created_at) or utc_present(self.clock())
                    age = (utc_present(self.clock()) - intent).total_seconds()
                    attempts = int(control.create_attempts or 0)
                    if age >= CREATE_RESOLVE_SECONDS and attempts < MAX_CREATE_ATTEMPTS:
                        # Evidence-based, bounded resolution: a successful provider read
                        # confirmed no Pod exists for this Volume, so the create with an
                        # ambiguous answer did not take effect. The attempt is charged.
                        self._finalize(db, session, "create_resolved", error_code="create_unknown")
                        self._set_state(db, "offline", error_code="create_unknown")
                        self.event(db, "reconcile", "ok", detail="create_resolved")
                    else:
                        self._keep_intent(db, session, "create_unknown")
                else:
                    self._set_state(db, "offline", error_code=None)
                return self.status_payload()
            control = self.control(db)
            search_error = control.error_code if control.error_code in SEARCH_ERRORS else None
            if control.state == SEARCH_STATE and self.search_active(control):
                # A live bounded search is kept as it is — and deliberately *not* restamped:
                # refreshing the timestamp here is what used to renew the window on every
                # reconciliation and make «searching» immortal.
                return self.status_payload()
            # A failed GPU search stays visible (with its typed reason) until it is resolved or
            # retried — as a failure with no search in flight, never as a transition to green.
            self._set_state(db, SEARCH_COLLAPSED_STATE, error_code=search_error)
        return self.status_payload()

    def _adopt_pod(self, db, pod, session: GatewaySession | None) -> GatewaySession:
        now_value = self.clock()
        if session is None:
            session = GatewaySession(
                pod_id=pod.id,
                pod_name=pod.name,
                state=POD_STATES.get(pod.status, "starting_pod"),
                managed=False,
                adopted=True,
                auto_stop_minutes=self.settings.compute_idle_minutes,
                hourly_rate=pod.cost,
                max_hourly_price=self.settings.max_hourly_price,
                session_budget=self.settings.max_session_budget,
                created_at=now_value,
                last_activity_at=now_value,
            )
            db.add(session)
            self.event(db, "adopt", "ok", detail=pod.name)
        else:
            session.pod_id = pod.id
            if session.pod_name != pod.name:
                # Never claim ownership that cannot be proved by name.
                session.managed = False
                session.adopted = True
        if pod.started_at and pod.cost > 0:
            if session.started_at is None:
                session.started_at = utc(datetime.fromisoformat(pod.started_at.replace("Z", "+00:00")))
            session.hourly_rate = pod.cost
        if pod.status == "RUNNING" and session.state not in {"ready", "generating"} and session.managed:
            session.state = POD_STATES.get(pod.status, session.state)
        seconds, cost = estimate(session, now_value)
        session.billable_seconds, session.estimated_cost = seconds, cost
        session.last_activity_at = session.last_activity_at or now_value
        db.commit()
        return session

    def _finalize(self, db, session: GatewaySession, reason: str, *, error_code=None, keep_intent=False):
        if keep_intent:
            session.state = "create_unknown"
            session.error_code = "create_unknown"
            session.pending_stop = False
        else:
            session.state = "stopped"
            session.stopped_at = self.clock()
            session.stop_reason = reason
            session.pending_stop = False
            if error_code and not session.error_code:
                session.error_code = error_code
            session.billable_seconds, session.estimated_cost = estimate(session, self.clock())
        control = self.control(db)
        control.state = "create_unknown" if keep_intent else "stopped"
        if not keep_intent:
            control.active_session_id = None
        # The reason survives on the control row so the five-chip layer can explain it.
        control.error_code = error_code or session.error_code
        control.revision += 1
        control.updated_at = self.clock()
        if error_code == "create_unknown":
            # The attempt budget is charged and kept, so a resolved retry cannot loop.
            control.create_attempts = int(control.create_attempts or 0) + 1
        self.event(db, "stopped", "ok", error_code=error_code, detail=reason)
        db.commit()

    def _keep_intent(self, db, session: GatewaySession, state: str):
        """An unresolved create keeps its intent: no new Pod may be created meanwhile."""
        self._finalize(db, session, "create_unknown", error_code="create_unknown", keep_intent=True)

    def reset_budget(self) -> None:
        """Operator path after a manual review of an unresolved create."""
        with self.sessions() as db:
            control = self.control(db)
            control.create_attempts = 0
            control.error_code = None
            db.commit()

    # -------------------------------------------------------------------------- ensure

    async def ensure(
        self,
        installation_id: str,
        *,
        operation_id: str,
        task_id: str | None = None,
        caps: dict | None = None,
        origin: str | None = None,
    ) -> dict:
        caps = self._caps(caps)
        digest = repr(sorted(caps.items()))
        with self.sessions() as db:
            cached = self._operation(db, operation_id, installation_id, "ensure")
        if cached is not None:
            return self._live_payload(cached)
        async with self.lease(installation_id):
            with self.sessions() as db:
                cached = self._operation(db, operation_id, installation_id, "ensure")
            if cached is not None:
                return self._live_payload(cached)
            self._last_allocation = None
            payload = await self._reconcile_locked()
            state = payload["state"]
            with self.sessions() as db:
                tracked = self.control(db).active_session_id
            if (
                state not in {"multiple_compute", "create_unknown", "not_configured", "external_compute"}
                and not tracked
            ):
                # A create is allowed only when reconciliation proved that no Pod for this
                # Volume is tracked or exists: an existing Pod is never duplicated.
                payload = await self._create_locked(installation_id, caps, task_id, operation_id=operation_id)
                state = payload["state"]
            with self.sessions() as db:
                control = self.control(db)
                control.last_operation_id = operation_id
                if state in ACTIVE_STATES:
                    control.last_activity_at = control.last_activity_at or self.clock()
                self.event(
                    db,
                    "ensure",
                    "error" if state in {"error", "create_unknown"} else "ok",
                    installation_id=installation_id,
                    error_code=control.error_code,
                    detail=state,
                    origin=origin,
                )
                db.commit()
            payload = self.status_payload()
            payload["balance"] = await self.balance.snapshot()
            payload["task_id"] = task_id
            if origin is not None:
                # Echoed back so the client can show, and an acceptance can prove, which event
                # started this attempt.
                payload["origin"] = origin
            if self._last_allocation is not None:
                payload["allocation"] = self._last_allocation
            # Ambiguous states are deliberately not cached: the client retries the same
            # operation id to learn the reconciled answer, and never to trigger a create.
            if state not in {"create_unknown", "multiple_compute", "error", "not_configured"}:
                self._record_operation(operation_id, installation_id, "ensure", payload, digest)
            return payload

    def _live_payload(self, cached: dict) -> dict:
        """An idempotent answer, re-derived only when it would claim a dead transition.

        A recorded ``searching`` is a promise about a bounded window. Replaying it after that
        window closed would be the very defect this fixes, so the live state is re-read and
        overlaid. Every other recorded answer is returned exactly as it was recorded, which is
        what operation idempotency means.
        """
        if cached.get("state") != SEARCH_STATE:
            return cached
        with self.sessions() as db:
            control = self.control(db)
            if control.state == SEARCH_STATE and self.search_active(control):
                return cached
        return {**cached, **self.status_payload(), "task_id": cached.get("task_id")}

    async def _create_locked(
        self, installation_id: str, caps: dict, task_id: str | None, *, operation_id: str | None = None
    ) -> dict:
        key = self.settings.llm_api_key
        if self.settings.llm_provider == "llamacpp" and len(key) < 32:
            with self.sessions() as db:
                self._set_state(db, "error", error_code="llm_key_missing")
            return self.status_payload()
        with self.sessions() as db:
            control = self.control(db)
            if int(control.create_attempts or 0) >= MAX_CREATE_ATTEMPTS:
                # Hard server-side guard: the attempt budget is exhausted, so nothing is
                # created until an operator reviews the provider and clears the budget.
                self._set_state(db, "create_unknown", error_code="create_unknown")
                self.event(db, "create_blocked", "error", error_code="create_unknown")
                db.commit()
                return self.status_payload()

        balance = await self.balance.snapshot()
        available = decimal_or_none(balance.get("balance_usd"))
        if balance.get("available") and available is not None:
            # The user's own ceiling and the money actually on the account both bound this
            # session: min(budget, balance). A $3 budget next to a $0.81 balance starts fine —
            # the session simply cannot spend more than $0.81. The saved preference is never
            # rewritten; only this session's ceiling is, quantized down to whole 1/100ths of a
            # cent so the value always satisfies the money schema.
            if available < MIN_START_BUDGET:
                with self.sessions() as db:
                    self._set_state(db, "offline", error_code="runpod_balance")
                return self.status_payload()
            effective = min(caps["session_budget"], available).quantize(MONEY_QUANTUM, rounding=ROUND_DOWN)
            if effective < MIN_START_BUDGET:
                with self.sessions() as db:
                    self._set_state(db, "offline", error_code="runpod_balance")
                return self.status_payload()
            caps = {**caps, "session_budget": effective}

        # The shared schema still validates exactly like direct mode (one policy vocabulary).
        compute_preferences(
            selection=caps["selection"],
            min_vram_gb=caps["min_vram_gb"],
            max_hourly_price=caps["max_hourly_price"],
            session_budget=caps["session_budget"],
            auto_stop_minutes=caps["auto_stop_minutes"],
            gpu_id=caps["gpu_id"],
            allow_community=caps["allow_community"],
        )
        started, deadline = self.allocation_window()
        try:
            volume = await self.api.volume()
            plan = await self.allocation_plan(caps, volume=volume)
        except Exception as error:
            code = getattr(error, "code", "runpod_unavailable")
            with self.sessions() as db:
                self._set_state(db, "error", error_code=code)
            return self.status_payload()
        if not plan:
            # Say what is actually missing: capacity, price, or a GPU that fits the model.
            with self.sessions() as db:
                self._note_search(
                    db, reason=plan.reason, operation_id=operation_id, installation_id=installation_id
                )
            self.record_allocation(
                installation_id,
                self.allocation_record(
                    started=started,
                    deadline=deadline,
                    outcome="no_candidates",
                    reason=plan.reason,
                    attempts=[],
                    plan=plan,
                ),
                operation_id=operation_id,
                reason=plan.reason,
            )
            return self.status_payload()
        return await self._walk_candidates(
            installation_id, caps, task_id, plan, deadline=deadline, operation_id=operation_id
        )

    async def _walk_candidates(
        self,
        installation_id: str,
        caps: dict,
        task_id: str | None,
        plan,
        *,
        deadline: datetime,
        operation_id: str | None,
    ) -> dict:
        """Try the ordered candidates until one is created, the budget is spent or the answer is
        ambiguous. A refused candidate costs nothing and never waits: the next compatible one is
        tried immediately, so the allocation can never be pinned to one machine.

        Exactly one Pod can come out of this: an ambiguous create ends the walk (existing
        ``create_unknown`` rule) and a successful one returns immediately.
        """
        started = utc_present(self.clock())
        session_id = str(uuid4())
        opened = False
        attempts: list[dict] = []
        for index, candidate in enumerate(plan.candidates):
            remaining = (deadline - utc_present(self.clock())).total_seconds()
            if remaining <= 0:
                attempts.append(
                    {
                        "index": index,
                        "gpu": candidate.gpu_id,
                        "cloud": candidate.cloud,
                        "datacenter": candidate.datacenter,
                        "result": "skipped",
                        "error_code": "allocation_deadline",
                        "reason": CAPACITY_UNAVAILABLE,
                        "elapsed_seconds": 0.0,
                    }
                )
                break
            if not opened:
                self._open_candidate(session_id, installation_id, candidate, caps, started, task_id=task_id)
                opened = True
            else:
                self._repoint_candidate(session_id, candidate)
            attempt_started = utc_present(self.clock())
            gpu = gpu_option(
                id=candidate.gpu_id,
                name=candidate.gpu_name,
                vram_gb=candidate.vram_gb,
                hourly_rate=candidate.hourly_rate,
                availability=candidate.availability,
                compatible=True,
                selectable=True,
            )
            try:
                pod = await self.api.create_pod(
                    f"alex-gw-{session_id}",
                    gpu,
                    cloud=candidate.cloud,
                    datacenter=candidate.datacenter,
                    volume_id=candidate.volume_id,
                    timeout=self.candidate_call_timeout(deadline),
                )
            except Exception as error:
                code = getattr(error, "code", "runpod_unavailable")
                verdict = self.create_verdict(error)
                attempts.append(
                    {
                        "index": index,
                        "gpu": candidate.gpu_id,
                        "cloud": candidate.cloud,
                        "datacenter": candidate.datacenter,
                        "result": "refused" if verdict == "walk" else verdict,
                        "error_code": code,
                        # The product conclusion this refusal would end with, so a walked-past
                        # `not_found` is not reported as "no capacity" later on.
                        "reason": self.refusal_reason(error),
                        "elapsed_seconds": round(
                            (utc_present(self.clock()) - attempt_started).total_seconds(), 3
                        ),
                    }
                )
                if verdict == "walk":
                    # Nothing was created, so the next compatible candidate is tried at once.
                    continue
                with self.sessions() as db:
                    row = db.get(GatewaySession, session_id)
                    if verdict == "ambiguous":
                        row.state = "create_unknown"
                        row.error_code = "create_unknown"
                        self._set_state(db, "create_unknown", error_code="create_unknown")
                        self.event(
                            db, "create_unknown", "error", installation_id=installation_id, error_code=code
                        )
                        outcome = "create_unknown"
                    else:
                        self._finalize(db, row, "create_failed", error_code=code)
                        outcome = "failed"
                self.record_allocation(
                    installation_id,
                    self.allocation_record(
                        started=started,
                        deadline=deadline,
                        outcome=outcome,
                        reason=code,
                        attempts=attempts,
                        plan=plan,
                    ),
                    operation_id=operation_id,
                    reason=code,
                )
                return self.status_payload()
            attempts.append(
                {
                    "index": index,
                    "gpu": candidate.gpu_id,
                    "cloud": candidate.cloud,
                    "datacenter": candidate.datacenter,
                    "result": "selected",
                    "error_code": None,
                    "elapsed_seconds": round(
                        (utc_present(self.clock()) - attempt_started).total_seconds(), 3
                    ),
                }
            )
            with self.sessions() as db:
                row = db.get(GatewaySession, session_id)
                row.pod_id = pod.id
                row.state = "starting_pod"
                if pod.started_at and pod.cost > 0:
                    row.started_at = utc(datetime.fromisoformat(pod.started_at.replace("Z", "+00:00")))
                    row.hourly_rate = pod.cost
                control = self.control(db)
                control.create_attempts = 0
                self._set_state(db, "starting_pod", error_code=None)
                self.event(db, "starting_pod", "ok", installation_id=installation_id)
            self.record_allocation(
                installation_id,
                self.allocation_record(
                    started=started,
                    deadline=deadline,
                    outcome="selected",
                    reason=None,
                    attempts=attempts,
                    selected=candidate.audit(),
                    plan=plan,
                ),
                operation_id=operation_id,
            )
            if pod.cost > caps["max_hourly_price"]:
                await self._terminate(session_id, "price_violation")
            return self.status_payload()
        # Every compatible candidate was refused (or the window closed before one could be tried).
        # The allocation operation is over, so it is closed here: `searching` may only mean that a
        # real operation is active, and the user gets the typed capacity conclusion, not amber.
        reason = CAPACITY_UNAVAILABLE
        if attempts:
            reason = attempts[-1].get("reason") or CAPACITY_UNAVAILABLE
        if reason not in SEARCH_ERRORS:
            reason = CAPACITY_UNAVAILABLE
        with self.sessions() as db:
            row = db.get(GatewaySession, session_id) if opened else None
            if row is not None:
                self._finalize(db, row, "create_failed", error_code=reason)
            self._note_search(db, reason=reason, operation_id=operation_id, installation_id=installation_id)
        self.collect_expired_search(force=True)
        self.record_allocation(
            installation_id,
            self.allocation_record(
                started=started,
                deadline=deadline,
                outcome="capacity_unavailable",
                reason=reason,
                attempts=attempts,
                plan=plan,
            ),
            operation_id=operation_id,
            reason=reason,
        )
        return self.status_payload()

    def _open_candidate(
        self, session_id: str, installation_id: str, candidate, caps: dict, started, *, task_id=None
    ):
        """Commit the create intent for the first candidate before any provider call."""
        with self.sessions() as db:
            session = GatewaySession(
                id=session_id,
                pod_name="alex-gw-" + session_id,
                state="creating",
                gpu_type=candidate.gpu_id,
                gpu_vram_mb=candidate.vram_gb * 1024,
                hourly_rate=candidate.hourly_rate,
                max_hourly_price=min(caps["max_hourly_price"], candidate.hourly_rate),
                session_budget=caps["session_budget"],
                auto_stop_minutes=caps["auto_stop_minutes"],
                created_by_installation_id=installation_id,
                managed=True,
                adopted=False,
                create_attempts=int(self.control(db).create_attempts or 0),
                intent_at=started,
                created_at=started,
                last_activity_at=started,
            )
            db.add(session)
            control = self.control(db)
            control.active_session_id = session.id
            self.event(db, "creating", "ok", installation_id=installation_id, detail=task_id or "")
            self._set_state(db, "creating", error_code=None, touch=True)

    def _repoint_candidate(self, session_id: str, candidate) -> None:
        """The refused candidate is replaced by the next one on the same attempt record."""
        with self.sessions() as db:
            row = db.get(GatewaySession, session_id)
            row.gpu_type = candidate.gpu_id
            row.gpu_vram_mb = candidate.vram_gb * 1024
            row.hourly_rate = candidate.hourly_rate
            row.max_hourly_price = min(row.max_hourly_price, candidate.hourly_rate)
            row.intent_at = self.clock()
            db.commit()

    # ---------------------------------------------------------------------------- stop

    async def stop(self, installation_id: str, *, operation_id: str, reason: str = "manual") -> dict:
        with self.sessions() as db:
            cached = self._operation(db, operation_id, installation_id, "stop")
        if cached is not None:
            return cached
        async with self.lease(installation_id):
            with self.sessions() as db:
                cached = self._operation(db, operation_id, installation_id, "stop")
                if cached is not None:
                    return cached
                session = self.session_row(db)
            if session is not None:
                if not session.managed:
                    raise GatewayError("external_compute")
                if self.active_inferences() and reason == "manual":
                    with self.sessions() as db:
                        row = self.session_row(db)
                        # The same row was read a few lines above under this lease.
                        if row is None:  # pragma: no cover
                            raise GatewayError("not_found")
                        row.pending_stop = True
                        self._set_state(db, "stopping", error_code=None)
                else:
                    await self._terminate(session.id, reason)
            with self.sessions() as db:
                self.event(db, "stop", "ok", installation_id=installation_id, detail=reason)
                db.commit()
            payload = self.status_payload()
            payload["balance"] = await self.balance.snapshot()
            self._record_operation(operation_id, installation_id, "stop", payload, reason)
            return payload

    async def _terminate(self, session_id: str, reason: str) -> None:
        with self.sessions() as db:
            row = db.get(GatewaySession, session_id)
            if row is None:
                return
            if not row.pod_id:
                # A create intent without a provider object is never terminated blindly.
                self._keep_intent(db, row, "create_unknown")
                return
            row.state = "stopping"
            row.pending_stop = False
            # The *request* reason is written before the provider call, so a refused termination
            # can be retried from persisted state instead of being forgotten (D-9).
            row.stop_reason = reason
            if reason == "session_budget":
                row.error_code = "COMPUTE_BUDGET_REACHED"
            elif reason in {"price_violation", "startup_failed", "startup_timeout"}:
                row.error_code = reason
            pod_id = row.pod_id
            control = self.control(db)
            control.state = "stopping"
            control.revision += 1
            control.updated_at = self.clock()
            db.commit()
        try:
            await self.api.terminate_pod(pod_id)
        except Exception as error:
            code = getattr(error, "code", "runpod_unavailable")
            with self.sessions() as db:
                row = db.get(GatewaySession, session_id)
                control = self.control(db)
                # A refused termination is never reported as a completed stop: the transport
                # failure lands on the control row (what is wrong right now) while the session
                # row keeps the request reason so the retry can find it again.
                row.error_code = code
                control.error_code = code
                control.updated_at = self.clock()
                db.commit()
            logger.warning("compute_terminate_failed session=%s code=%s", session_id, code)
            return
        with self.sessions() as db:
            self._finalize(db, db.get(GatewaySession, session_id), reason)

    async def startup_watchdog(self, session_id: str) -> None:
        """D-9: stop a managed startup that outlived its deadline, and prove it, not assume it.

        The deadline is re-read from persisted timestamps, so a Gateway restart, a vanished
        client or a killed process cannot reset it. A Pod that exists is terminated through the
        normal managed path; a create intent that never produced a Pod is decided by provider
        evidence, never by a blind terminate and never by clearing the intent. Nothing here
        creates compute, and a termination the provider refuses stays visible and is retried on
        the next tick.
        """
        session = self.session_by_id(session_id)
        if session is None or not self.startup_expired(session):
            return
        if session.pod_id is None:
            # No provider object yet: ask the provider whether one exists before deciding.
            await self._reconcile_locked()
            session = self.session_by_id(session_id)
            if session is None or not self.startup_expired(session):
                return
        if not self.startup_stop_pending(session):
            with self.sessions() as db:
                self.event(
                    db,
                    STARTUP_TIMEOUT,
                    "error",
                    error_code=STARTUP_TIMEOUT,
                    detail=f"{session.state}:{session.pod_id or 'no_pod'}",
                )
                db.commit()
        if not session.pod_id:
            # Reconciliation could not turn the intent into a provider object (blind provider, or
            # no Pod for this Volume). There is nothing to terminate, and clearing the intent
            # would strand a Pod this read could not see: the existing create_unknown flow keeps
            # it instead (no second Pod, no blind delete) and an operator's reconciliation owns
            # the resolution. The deadline itself stays in the audit trail above.
            with self.sessions() as db:
                row = db.get(GatewaySession, session_id)
                if row is None:  # pragma: no cover - the row was read under the same lease
                    return
                self._keep_intent(db, row, "create_unknown")
            return
        await self._terminate(session_id, STARTUP_TIMEOUT)

    def session_by_id(self, session_id: str) -> GatewaySession | None:
        with self.sessions() as db:
            return db.get(GatewaySession, session_id)

    # ---------------------------------------------------------------------- background

    async def tick(self) -> None:
        """Global lifecycle: readiness, budgets, price guard and shared idle stop."""
        async with self.lease("tick"):
            with self.sessions() as db:
                session = self.session_row(db)
            if session is None or session.state not in ACTIVE_STATES:
                # No Pod to look after. The one thing left to own is a capacity search that
                # outlived its deadline: it is collapsed here, server-side, so no client (and
                # no vanished harness) can leave the control row answering `searching` forever.
                self.collect_expired_search()
                return
            try:
                pod = await self.api.get_pod(session.pod_id) if session.pod_id else None
            except Exception as error:
                code = getattr(error, "code", "runpod_unavailable")
                with self.sessions() as db:
                    control = self.control(db)
                    control.error_code = code
                    control.updated_at = self.clock()
                    db.commit()
                return
            if pod is None:
                # A monitored Pod that disappeared is the reconciler's business. A create intent
                # that never produced a provider object is still guarded: D-9 gives it the same
                # deadline, resolved by provider evidence rather than by guessing.
                if session.pod_id is None and self.startup_expired(session):
                    await self.startup_watchdog(session.id)
                return
            with self.sessions() as db:
                row = db.get(GatewaySession, session.id)
                if pod.started_at and pod.cost > 0:
                    row.hourly_rate = pod.cost
                    if row.started_at is None:
                        row.started_at = utc(datetime.fromisoformat(pod.started_at.replace("Z", "+00:00")))
                seconds, cost = estimate(row, self.clock())
                row.billable_seconds, row.estimated_cost = seconds, cost
                state = POD_STATES.get(pod.status)
                if state is None:
                    state = row.state
                if pod.status == "RUNNING" and row.ready_at is not None:
                    # Readiness, once proven by the model gateway, is not downgraded by a
                    # later pod-status read.
                    state = row.state
                if not row.managed and row.state != "ready":
                    state = "external_compute"
                # D-9: a startup stop the provider refused is still in flight. It owns the state
                # until a retry lands, so the Pod is never re-reported as a healthy startup.
                startup_stop_pending = self.startup_stop_pending(row)
                if state == "loading_model" and not startup_stop_pending:
                    row.state = "loading_model"
                control = self.control(db)
                if startup_stop_pending:
                    state = "stopping"
                if state != control.state:
                    control.revision += 1
                control.state = state
                if not startup_stop_pending:
                    control.error_code = None
                control.updated_at = self.clock()
                db.commit()

            with self.sessions() as db:
                row = db.get(GatewaySession, session.id)
                managed = bool(row.managed)
                pending = bool(row.pending_stop)
                over_budget = row.estimated_cost > 0 and row.estimated_cost >= row.session_budget
                over_price = row.hourly_rate > row.max_hourly_price
                auto_stop = int(row.auto_stop_minutes or 0)
                stale = (
                    None
                    if not auto_stop
                    else (
                        utc_present(self.clock()) - (utc(row.last_activity_at) or utc_present(self.clock()))
                    ).total_seconds()
                )
                control = self.control(db)
                global_last = (
                    utc(control.last_activity_at)
                    or utc(row.last_activity_at)
                    or utc_present(control.updated_at)
                )
                global_idle = (utc_present(self.clock()) - global_last).total_seconds()
                ready_state = row.state in {"ready", "generating"}
                startup_expired = self.startup_expired(row)
            busy = bool(self.active_inferences())
            if not managed and not pending:
                # Never terminate provider resources Alex Cloud does not own: an adopted or
                # external Pod is reported and left running for the operator.
                if pod.status == "RUNNING" and await self.probe_ready():
                    with self.sessions() as db:
                        row = db.get(GatewaySession, session.id)
                        row.state = "ready"
                        row.ready_at = row.ready_at or self.clock()
                        self._set_state(db, "ready", error_code=None, touch=True)
                return
            if pending and not busy:
                await self._terminate(session.id, "manual")
                return
            if over_budget:
                await self._terminate(session.id, "session_budget")
                return
            if over_price:
                await self._terminate(session.id, "price_violation")
                return
            if startup_expired and not busy:
                # D-9: the Pod never proved readiness inside the server-side deadline, so it is
                # stopped here — server-side, independently of any client. The reason stays
                # recoverable: the user sees startup_timeout and may start again deliberately.
                await self.startup_watchdog(session.id)
                return
            if (
                stale is not None
                and stale >= auto_stop * 60
                and global_idle >= auto_stop * 60
                and not busy
                and ready_state
            ):
                # Idle is global: installation A finishing never stops compute that B uses.
                await self._terminate(session.id, "idle")
                return
            if row.state == "loading_model" and pod.status == "RUNNING" and await self.probe_ready():
                with self.sessions() as db:
                    row = db.get(GatewaySession, session.id)
                    row.state = "ready"
                    row.ready_at = row.ready_at or self.clock()
                    row.error_code = None
                    self._set_state(db, "ready", error_code=None, touch=True)

    async def serve(self) -> None:
        """Background loop. Never starts compute on its own: trust is unchanged by time."""
        while True:
            try:
                await self.tick()
            except Exception:
                logger.warning("compute_tick_failed")
            await asyncio.sleep(self.settings.compute_poll_seconds)

    def queue_depth(self) -> int:
        return int(self.queue.depth()) if self.queue is not None else 0

    def queue_active(self) -> int:
        return int(self.queue.active()) if self.queue is not None else 0
