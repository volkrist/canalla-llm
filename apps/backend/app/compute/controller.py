import asyncio
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from ..config import Settings
from ..database import SessionLocal
from ..models import Message, User, now
from ..providers import LlamaCppProvider
from .models import (
    ComputeControl,
    ComputeEvent,
    ComputePreference,
    ComputeQuote,
    ComputeSession,
    GenerationUsage,
)
from .runpod_api import ERROR_MESSAGES, RunPodAPI, RunPodError
from .schemas import ComputePreferences, GpuOption, StartRequest, StopRequest


def utc(value: datetime):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def estimate(session: ComputeSession, at: datetime):
    seconds = (
        0
        if not session.started_at
        else max(0, int((utc(session.stopped_at or at) - utc(session.started_at)).total_seconds()))
    )
    return seconds, (Decimal(seconds) / 3600 * session.hourly_rate).quantize(Decimal("0.000001"))


class RunPodController:
    """Deployment-wide durable state machine; SQLite + PostgreSQL portable CAS lease.

    Every side effect has a committed intent before the HTTP call. Ambiguous create is
    reconciled by deterministic pod name and NEVER retried automatically.
    """

    def __init__(self, settings: Settings, api=None, sessions=SessionLocal, clock=now):
        self.settings, self.api, self.sessions, self.clock = (
            settings,
            api or RunPodAPI(settings),
            sessions,
            clock,
        )
        self.local_lock = asyncio.Lock()
        self.llm = (
            LlamaCppProvider(settings, target=self.connection_target)
            if settings.llm_provider == "llamacpp"
            else None
        )

    def connection_target(self):
        if self.settings.llm_connection_mode == "static":
            return self.settings.llm_base_url
        with self.sessions() as db:
            control = db.get(ComputeControl, 1)
            row = (
                db.get(ComputeSession, control.active_session_id)
                if control and control.active_session_id
                else None
            )
            if not row or not row.managed or row.pending_stop or not row.pod_id or row.stopped_at:
                return None
            if not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", row.pod_id):
                return None
            return f"https://{row.pod_id}-{self.settings.runpod_gateway_port}.proxy.runpod.net"

    def initialize(self):
        with self.sessions() as db:
            if not db.get(ComputeControl, 1):
                db.add(ComputeControl(id=1, search_state="offline"))
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()

    @asynccontextmanager
    async def operation(self):
        async with self.local_lock:
            self.initialize()
            owner = str(uuid4())
            with self.sessions() as db:
                result = db.execute(
                    update(ComputeControl)
                    .where(
                        ComputeControl.id == 1,
                        or_(ComputeControl.lease_owner.is_(None), ComputeControl.lease_until < self.clock()),
                    )
                    .values(lease_owner=owner, lease_until=self.clock() + timedelta(seconds=120))
                )
                db.commit()
                if result.rowcount != 1:
                    raise HTTPException(409, "Операция compute уже выполняется. Дождитесь её завершения.")
            try:
                async with asyncio.timeout(90):
                    yield
            except TimeoutError:
                raise RunPodError("runpod_timeout", 504) from None
            finally:
                with self.sessions() as db:
                    db.execute(
                        update(ComputeControl)
                        .where(ComputeControl.id == 1, ComputeControl.lease_owner == owner)
                        .values(lease_owner=None, lease_until=None)
                    )
                    db.commit()

    def preferences(self, user_id):
        with self.sessions() as db:
            row = db.get(ComputePreference, user_id)
            if not row:
                return ComputePreferences.defaults(self.settings)
            values = dict(row.values or {})
            if "gpu_id" not in values:
                values["gpu_id"] = "NVIDIA L40S"
            return ComputePreferences.model_validate(values)

    def save_preferences(self, user_id, preferences):
        preferences.enforce(self.settings)
        with self.sessions() as db:
            row = db.get(ComputePreference, user_id)
            if row:
                row.values = preferences.model_dump(mode="json")
            else:
                db.add(ComputePreference(user_id=user_id, values=preferences.model_dump(mode="json")))
            db.commit()
        return preferences

    async def update_preferences(self, user, preferences):
        async with self.operation():
            preferences.enforce(self.settings)
            with self.sessions() as db:
                control = db.get(ComputeControl, 1)
                row = db.get(ComputeSession, control.active_session_id) if control.active_session_id else None
                if row and row.managed and row.started_by_user_id != user.id and user.role != "admin":
                    raise HTTPException(
                        403, "Настройки активной сессии может менять инициатор или администратор"
                    )
                if row and row.managed:
                    row.session_budget = preferences.session_budget
                    row.max_hourly_price = preferences.max_hourly_price
                    row.auto_stop_minutes = preferences.auto_stop_minutes
                    row.updated_at = self.clock()
                if control.search_user_id == user.id:
                    searching = control.next_search_at is not None
                    control.search_settings = preferences.model_dump(mode="json")
                    control.search_quote_id = None
                    control.next_search_at = (
                        self.clock()
                        if searching and (preferences.auto_search or preferences.auto_connect)
                        else None
                    )
                    if not row:
                        control.search_state = "searching" if control.next_search_at else "offline"
                saved_preferences = db.get(ComputePreference, user.id)
                values = preferences.model_dump(mode="json")
                if saved_preferences:
                    saved_preferences.values = values
                else:
                    db.add(ComputePreference(user_id=user.id, values=values))
                db.commit()
        return preferences

    def event(self, db, kind, session=None, actor=None, code=None):
        db.add(
            ComputeEvent(
                session_id=session.id if session else None,
                actor_id=actor,
                kind=kind,
                code=code,
                created_at=self.clock(),
            )
        )

    def rate_limit(self, user_id, action):
        with self.sessions() as db:
            count = db.scalar(
                select(func.count())
                .select_from(ComputeEvent)
                .where(
                    ComputeEvent.actor_id == user_id,
                    ComputeEvent.kind.in_(["start_requested", "stop_requested"]),
                    ComputeEvent.created_at >= self.clock() - timedelta(minutes=1),
                )
            )
            if count >= 6:
                raise HTTPException(429, "Слишком много операций compute. Повторите через минуту.")
            self.event(db, action + "_requested", actor=user_id)
            db.commit()

    def active_generations(self, db):
        return db.scalar(
            select(func.count()).select_from(GenerationUsage).where(GenerationUsage.completed_at.is_(None))
        )

    def session_out(self, session, admin=False, user=None):
        seconds, cost = estimate(session, self.clock())
        owner = bool(user and getattr(user, "id", None) == session.started_by_user_id)
        admin = bool(admin or (user and getattr(user, "role", None) == "admin"))
        data = {
            "id": session.id,
            "gpu_type": session.gpu_type,
            "gpu_vram_mb": session.gpu_vram_mb,
            "hourly_rate": float(session.hourly_rate),
            "status": session.status,
            "started_at": utc(session.started_at).isoformat() if session.started_at else None,
            "ready_at": utc(session.ready_at).isoformat() if session.ready_at else None,
            "stopped_at": utc(session.stopped_at).isoformat() if session.stopped_at else None,
            "created_at": utc(session.created_at).isoformat(),
            "billable_seconds": seconds,
            "estimated_cost": float(cost),
            "actual_cost": float(session.actual_cost) if session.actual_cost is not None else None,
            "session_budget": float(session.session_budget),
            "max_hourly_price": float(session.max_hourly_price),
            "auto_stop_minutes": session.auto_stop_minutes,
            "pending_stop": session.pending_stop,
            "stop_reason": session.stop_reason,
            "error_code": session.error_code,
            "managed": session.managed,
            "datacenter": session.datacenter,
        }
        if admin or owner:
            data["pod_id"] = session.pod_id
        if admin:
            data.update(
                network_volume_id=session.network_volume_id,
                started_by_user_id=session.started_by_user_id,
            )
        return data

    def get_compute_status(self, user: User):
        self.initialize()
        with self.sessions() as db:
            control = db.get(ComputeControl, 1)
            session = db.get(ComputeSession, control.active_session_id) if control.active_session_id else None
            active = self.active_generations(db)
            state = session.status if session else control.search_state
            if state == "ready" and active:
                state = "generating"
            if not self.api.configured:
                state = "not_configured"
            error_code = session.error_code if session else control.error_code
            return {
                "configured": self.api.configured,
                "state": state,
                "session": self.session_out(session, user=user) if session else None,
                "can_control": user.role == "admin" or self.settings.allow_user_compute_start,
                "active_generations": active,
                "active_users": [
                    {"id": uid, "email": email}
                    for uid, email in db.execute(
                        select(User.id, User.email)
                        .join(GenerationUsage, GenerationUsage.user_id == User.id)
                        .where(GenerationUsage.completed_at.is_(None), GenerationUsage.provider == "llamacpp")
                        .distinct()
                    )
                ]
                if user.role == "admin"
                else [],
                "queued_requests": 0,
                "error_code": error_code,
                "message": ERROR_MESSAGES.get(error_code) if error_code else None,
                "server_now": self.clock().isoformat(),
                "next_search_at": utc(control.next_search_at).isoformat() if control.next_search_at else None,
                "quote_id": control.search_quote_id if control.search_user_id == user.id else None,
                "can_cancel_search": control.search_user_id == user.id or user.role == "admin",
                "preferences": self.preferences(user.id).model_dump(mode="json"),
                "search_preferences": control.search_settings if control.next_search_at else None,
                "limits": {
                    "min_vram_gb": self.settings.runpod_min_vram_gb,
                    "max_hourly_price": 100,
                    "session_budget": 1000,
                },
                "datacenter": self.settings.runpod_datacenter,
                "model": self.settings.llm_model,
                "technical": {
                    "runpod": "configured" if self.api.configured else "not_configured",
                    "provider": self.settings.llm_provider,
                    "storage_monthly_cost": None,
                    "storage_cost_note": "Network Volume оплачивается отдельно; API не предоставляет текущий месячный тариф.",
                },
            }

    get_current_compute = get_compute_status
    get_cost_status = get_compute_status

    async def list_gpu_options(self, user_id):
        prefs = self.preferences(user_id).enforce(self.settings)
        await self.api.volume()
        return await self.api.gpu_options(prefs)

    async def _search(self, user_id, prefs):
        prefs.enforce(self.settings)
        await self.api.volume()
        options = await self.api.gpu_options(prefs)
        available = [gpu for gpu in options if gpu.selectable]
        code = (
            None
            if available
            else "price_limit"
            if any(
                gpu.compatible and gpu.availability != "NONE" and gpu.reason == "price_limit"
                for gpu in options
            )
            else "no_compatible_gpu"
        )
        with self.sessions() as db:
            control = db.get(ComputeControl, 1)
            quote = ComputeQuote(
                user_id=user_id,
                options=[gpu.model_dump(mode="json") for gpu in options],
                preferences=prefs.model_dump(mode="json"),
                expires_at=self.clock() + timedelta(seconds=90),
                created_at=self.clock(),
            )
            db.add(quote)
            db.flush()
            control.search_user_id, control.search_settings, control.search_quote_id = (
                user_id,
                prefs.model_dump(mode="json"),
                quote.id,
            )
            control.search_state = (
                "gpu_found"
                if available
                else "searching"
                if (prefs.auto_search or prefs.auto_connect)
                else "no_gpu"
            )
            control.next_search_at = (
                self.clock() + timedelta(seconds=prefs.search_interval)
                if (prefs.auto_search or prefs.auto_connect) and (not available or prefs.auto_connect)
                else None
            )
            control.error_code = code
            db.commit()
            return {
                "quote_id": quote.id,
                "expires_at": utc(quote.expires_at).isoformat(),
                "options": options,
                "selected_gpu_id": available[0].id if available and prefs.selection == "automatic" else None,
                "preferences": prefs,
                "error_code": code,
            }

    async def search_gpu(self, user, prefs):
        async with self.operation():
            with self.sessions() as db:
                control = db.get(ComputeControl, 1)
                if control.active_session_id:
                    return {"existing": True, "status": self.get_compute_status(user)}
            self.save_preferences(user.id, prefs)
            quote = await self._search(user.id, prefs)
            return await self.auto_connect(user.id, prefs, quote)

    async def auto_connect(self, user_id, prefs, quote):
        if not prefs.auto_connect:
            return quote
        with self.sessions() as db:
            user = db.get(User, user_id)
            if not user or not (user.role == "admin" or self.settings.allow_user_compute_start):
                control = db.get(ComputeControl, 1)
                control.next_search_at = None
                control.search_state = "offline"
                control.search_quote_id = None
                db.commit()
                return quote
        gpu = next((g for g in quote["options"] if g.selectable), None)
        if not gpu:
            return quote
        try:
            result = await self._start_compute_locked(
                user,
                StartRequest(
                    quote_id=quote["quote_id"],
                    gpu_id=gpu.id,
                    idempotency_key="auto-" + quote["quote_id"],
                    confirmed=True,
                ),
            )
        except RunPodError as error:
            if error.code != "price_changed":
                raise
            self.resume_search(user.id, prefs)
            result = self.get_compute_status(user)
        return {"existing": True, "status": result}

    async def cancel_gpu_search(self, user):
        async with self.operation():
            with self.sessions() as db:
                control = db.get(ComputeControl, 1)
                if control.search_user_id not in (None, user.id) and user.role != "admin":
                    raise HTTPException(403, "Поиск может отменить инициатор или администратор")
                control.search_state, control.next_search_at, control.search_quote_id = "offline", None, None
                control.error_code = None
                self.event(db, "search_cancelled", actor=user.id)
                db.commit()
        return self.get_compute_status(user)

    def quote(self, user, quote_id):
        with self.sessions() as db:
            quote = db.get(ComputeQuote, quote_id)
            if not quote or quote.user_id != user.id:
                raise HTTPException(404, "Предложение не найдено")
            return {
                "quote_id": quote.id,
                "expires_at": utc(quote.expires_at).isoformat(),
                "options": quote.options,
                "preferences": quote.preferences,
            }

    def matches_volume(self, pod):
        return any(
            mount.get("volumeId") == self.settings.runpod_network_volume_id
            for mount in pod.mounts.get("network", [])
        )

    def new_session(self, user, request, gpu, prefs, managed=True):
        sid = str(uuid4())
        return ComputeSession(
            id=sid,
            started_by_user_id=user.id,
            idempotency_key=request.idempotency_key,
            pod_name="alex-llm-" + sid,
            network_volume_id=self.settings.runpod_network_volume_id,
            datacenter=self.settings.runpod_datacenter,
            gpu_type=gpu.id,
            gpu_vram_mb=gpu.vram_gb * 1024,
            hourly_rate=gpu.hourly_rate,
            max_hourly_price=prefs.max_hourly_price,
            session_budget=prefs.session_budget,
            auto_stop_minutes=prefs.auto_stop_minutes,
            managed=managed,
            status="creating",
            last_activity_at=self.clock(),
            created_at=self.clock(),
            updated_at=self.clock(),
        )

    def resume_search(self, user_id, prefs):
        prefs = prefs.model_copy(update={"auto_search": True, "search_interval": 30})
        with self.sessions() as db:
            control = db.get(ComputeControl, 1)
            control.search_user_id = user_id
            control.search_settings = prefs.model_dump(mode="json")
            control.search_state = "searching"
            control.search_quote_id = None
            control.error_code = "no_compatible_gpu"
            control.next_search_at = self.clock() + timedelta(seconds=30)
            db.commit()

    async def start_compute(self, user, request: StartRequest):
        async with self.operation():
            return await self._start_compute_locked(user, request)

    async def _start_compute_locked(self, user, request: StartRequest):
        if self.llm and len(self.settings.llm_api_key) < 32:
            raise RunPodError("llm_key_missing", 422)
        with self.sessions() as db:
            control = db.get(ComputeControl, 1)
            existing = db.scalar(
                select(ComputeSession).where(ComputeSession.idempotency_key == request.idempotency_key)
            )
            if existing:
                if existing.started_by_user_id != user.id:
                    raise HTTPException(409, "Этот ключ запроса уже использован")
                return self.get_compute_status(user)
            if control.active_session_id:
                return self.get_compute_status(user)
            quote = db.get(ComputeQuote, request.quote_id)
            if not quote or quote.user_id != user.id:
                raise HTTPException(404, "Предложение не найдено")
            if control.search_quote_id != quote.id:
                raise RunPodError("price_changed", 409)
            if utc(quote.expires_at) <= self.clock():
                raise RunPodError("price_changed", 409)
            prefs = ComputePreferences.model_validate(quote.preferences).enforce(self.settings)
            approved = next(
                (GpuOption.model_validate(g) for g in quote.options if g["id"] == request.gpu_id), None
            )
            if (
                not approved
                or not approved.selectable
                or approved.hourly_rate > prefs.max_hourly_price
                or (prefs.gpu_id and approved.id != prefs.gpu_id)
            ):
                raise RunPodError("price_limit", 409)
            if prefs.selection == "automatic":
                cheapest = min(
                    (GpuOption.model_validate(g) for g in quote.options if g["selectable"]),
                    key=lambda gpu: (gpu.hourly_rate, gpu.id),
                )
                if approved.id != cheapest.id:
                    raise RunPodError("price_changed", 409)
        self.rate_limit(user.id, "start")
        await self.api.volume()
        pods = await self.api.list_pods()
        active_pods = [
            pod for pod in pods if self.matches_volume(pod) and pod.status not in {"EXITED", "TERMINATED"}
        ]
        if active_pods:
            if len(active_pods) > 1:
                raise RunPodError("multiple_compute", 409)
            pod = active_pods[0]
            session = self.new_session(user, request, approved, prefs, managed=False)
            session.pod_id, session.pod_name, session.status = pod.id, pod.name, "external_compute"
            session.gpu_type = pod.gpu.get("id", "Unknown GPU")
            session.hourly_rate = pod.cost
            session.gpu_vram_mb = 0  # Host RAM is not VRAM; do not infer it from pod.gpu.memory.
            if pod.started_at:
                session.started_at = utc(datetime.fromisoformat(pod.started_at.replace("Z", "+00:00")))
            with self.sessions() as db:
                db.add(session)
                db.flush()
                db.get(ComputeControl, 1).active_session_id = session.id
                db.commit()
            return self.get_compute_status(user)
        fresh = await self.api.gpu_options(prefs)
        selected = next((gpu for gpu in fresh if gpu.id == approved.id and gpu.selectable), None)
        if selected and selected.hourly_rate > approved.hourly_rate:
            raise RunPodError("price_changed", 409)
        candidates = [selected] if selected else []
        if not candidates:
            self.resume_search(user.id, prefs)
            return self.get_compute_status(user)
        session = self.new_session(user, request, candidates[0], prefs)
        session.max_hourly_price = min(prefs.max_hourly_price, approved.hourly_rate)
        with self.sessions() as db:
            db.add(session)
            db.flush()
            control = db.get(ComputeControl, 1)
            control.active_session_id = session.id
            control.next_search_at, control.search_quote_id, control.error_code = None, None, None
            self.event(db, "creating", session, user.id)
            db.commit()
        for gpu in candidates[:3]:
            with self.sessions() as db:
                row = db.get(ComputeSession, session.id)
                row.gpu_type, row.gpu_vram_mb, row.hourly_rate = (
                    gpu.id,
                    gpu.vram_gb * 1024,
                    gpu.hourly_rate,
                )
                row.status = "creating"
                db.commit()
            try:
                pod = await self.api.create_pod(session.pod_name, gpu)
            except RunPodError as error:
                if error.code == "placement_rejected" or error.status == 403:
                    continue
                with self.sessions() as db:
                    row = db.get(ComputeSession, session.id)
                    unknown = (
                        error.code in {"runpod_timeout", "runpod_unavailable", "malformed_response"}
                        or error.status >= 500
                    )
                    row.status, row.error_code = (
                        ("create_unknown", "create_unknown") if unknown else ("error", error.code)
                    )
                    if not unknown:
                        row.stopped_at = self.clock()
                        db.get(ComputeControl, 1).active_session_id = None
                        db.get(ComputeControl, 1).error_code = error.code
                        db.get(ComputeControl, 1).search_state = "error"
                    self.event(db, row.status, row, user.id, row.error_code)
                    db.commit()
                return self.get_compute_status(user)
            with self.sessions() as db:
                row = db.get(ComputeSession, session.id)
                row.pod_id, row.status = pod.id, "starting_pod"
                self.record_pod(row, pod)
                self.event(db, "starting_pod", row, user.id)
                db.commit()
            if pod.cost > min(prefs.max_hourly_price, approved.hourly_rate):
                await self._terminate(session.id, "price_violation")
            return self.get_compute_status(user)
        with self.sessions() as db:
            row = db.get(ComputeSession, session.id)
            row.status, row.error_code, row.stopped_at = "error", "placement_rejected", self.clock()
            control = db.get(ComputeControl, 1)
            control.active_session_id, control.search_state, control.error_code = (
                None,
                "no_gpu",
                "no_compatible_gpu",
            )
            db.commit()
        self.resume_search(user.id, prefs)
        return self.get_compute_status(user)

    def record_pod(self, row, pod):
        if not row.started_at and pod.started_at and pod.cost > 0:
            row.started_at = utc(datetime.fromisoformat(pod.started_at.replace("Z", "+00:00")))
            row.hourly_rate = pod.cost
        row.billable_seconds, row.estimated_cost = estimate(row, self.clock())
        row.updated_at = self.clock()

    def finish_stopped(self, db, row, reason):
        row.status, row.stopped_at, row.stop_reason = "stopped", self.clock(), reason
        row.pending_stop = False
        row.billable_seconds, row.estimated_cost = estimate(row, self.clock())
        row.updated_at = self.clock()
        control = db.get(ComputeControl, 1)
        control.active_session_id, control.search_state = None, "stopped"
        control.error_code = row.error_code
        self.event(db, "stopped", row, code=reason)

    async def _terminate(self, session_id, reason):
        with self.sessions() as db:
            row = db.get(ComputeSession, session_id)
            if not row.pod_id:
                raise RunPodError("create_unknown", 409)
            row.pending_stop, row.stop_reason = True, reason
            if self.active_generations(db) and reason not in {"session_budget", "price_violation"}:
                db.commit()
                return
            row.status = "stopping"
            if reason in {"startup_failed", "startup_timeout", "price_violation"}:
                row.error_code = reason
            pod_id = row.pod_id
            db.commit()
        try:
            await self.api.terminate_pod(pod_id)
        except RunPodError as error:
            with self.sessions() as db:
                row = db.get(ComputeSession, session_id)
                row.error_code = error.code
                db.commit()
            return
        with self.sessions() as db:
            self.finish_stopped(db, db.get(ComputeSession, session_id), reason)
            db.commit()

    async def stop_compute(self, user, request: StopRequest):
        async with self.operation():
            self.rate_limit(user.id, "stop")
            with self.sessions() as db:
                control = db.get(ComputeControl, 1)
                row = db.get(ComputeSession, control.active_session_id) if control.active_session_id else None
                if not row:
                    return self.get_compute_status(user)
                if not row.managed and (user.role != "admin" or not request.confirm_external):
                    raise HTTPException(
                        409, "Требуется подтверждение администратора для остановки существующего Pod"
                    )
                if self.active_generations(db) and not request.after_generation:
                    raise HTTPException(409, "AI генерирует ответ. Выберите остановку после ответа.")
                sid = row.id
            await self._terminate(sid, "manual" if row.managed else "manual_confirmed_external")
        return self.get_compute_status(user)

    async def begin_generation(self, user_id, chat_id, provider):
        async with self.operation():
            with self.sessions() as db:
                if db.scalar(
                    select(GenerationUsage.id).where(
                        GenerationUsage.chat_id == chat_id, GenerationUsage.completed_at.is_(None)
                    )
                ):
                    raise HTTPException(409, "В этом диалоге уже идёт генерация")
                control = db.get(ComputeControl, 1)
                row = db.get(ComputeSession, control.active_session_id) if control.active_session_id else None
                if row and (
                    row.pending_stop
                    or row.status == "stopping"
                    or (row.managed and estimate(row, self.clock())[1] >= row.session_budget)
                ):
                    raise HTTPException(409, "Compute останавливается или достигнут бюджет сессии")
                if (
                    provider == "llamacpp"
                    and self.settings.llm_connection_mode == "runpod"
                    and (not row or row.status != "ready")
                ):
                    raise HTTPException(409, "AI ещё не готов. Дождитесь загрузки модели.")
                usage = GenerationUsage(
                    user_id=user_id,
                    chat_id=chat_id,
                    provider=provider,
                    compute_session_id=row.id if row and provider != "mock" else None,
                    created_at=self.clock(),
                )
                db.add(usage)
                db.commit()
                return usage.id

    def finish_generation(self, usage_id, status, message_id=None, tokens=None):
        with self.sessions() as db:
            usage = db.get(GenerationUsage, usage_id)
            if usage:
                usage.status, usage.completed_at, usage.message_id = status, self.clock(), message_id
                for field, value in (tokens or {}).items():
                    if field in {"input_tokens", "output_tokens", "total_tokens"}:
                        setattr(usage, field, value)
            control = db.get(ComputeControl, 1)
            if control and control.active_session_id:
                row = db.get(ComputeSession, control.active_session_id)
                row.last_activity_at = self.clock()
            db.commit()

    async def recover(self):
        # Chat streaming still uses the documented single-worker backend. Compute state itself
        # is protected by the shared CAS lease and survives a process restart.
        async with self.operation():
            with self.sessions() as db:
                db.execute(
                    update(GenerationUsage)
                    .where(GenerationUsage.completed_at.is_(None))
                    .values(status="interrupted", completed_at=self.clock())
                )
                db.execute(update(Message).where(Message.status == "generating").values(status="error"))
                db.commit()
        if self.api.configured:
            await self.tick()

    async def tick(self):
        if not self.api.configured:
            return
        async with self.operation():
            with self.sessions() as db:
                control = db.get(ComputeControl, 1)
                row = db.get(ComputeSession, control.active_session_id) if control.active_session_id else None
                if not row:
                    if (
                        control.next_search_at
                        and utc(control.next_search_at) <= self.clock()
                        and control.search_user_id
                    ):
                        user_id, prefs = (
                            control.search_user_id,
                            ComputePreferences.model_validate(control.search_settings),
                        )
                    else:
                        return
                else:
                    user_id, prefs = None, None
            if not row:
                try:
                    quote = await self._search(user_id, prefs)
                    await self.auto_connect(user_id, prefs, quote)
                except RunPodError as error:
                    with self.sessions() as db:
                        control = db.get(ComputeControl, 1)
                        control.error_code = error.code
                        control.next_search_at = self.clock() + timedelta(seconds=prefs.search_interval)
                        db.commit()
                return
            try:
                if not row.pod_id:
                    pods = await self.api.list_pods()
                    matches = [pod for pod in pods if pod.name == row.pod_name and self.matches_volume(pod)]
                    if len(matches) != 1:
                        with self.sessions() as db:
                            saved = db.get(ComputeSession, row.id)
                            saved.status, saved.error_code = (
                                "create_unknown",
                                "multiple_compute" if len(matches) > 1 else "create_unknown",
                            )
                            db.commit()
                        return
                    pod = matches[0]
                else:
                    pod = await self.api.get_pod(row.pod_id)
            except RunPodError as error:
                with self.sessions() as db:
                    saved = db.get(ComputeSession, row.id)
                    if error.code == "not_found" and row.pod_id:
                        self.finish_stopped(db, saved, saved.stop_reason or "external_stop")
                    else:
                        saved.error_code = error.code
                    db.commit()
                return
            with self.sessions() as db:
                saved = db.get(ComputeSession, row.id)
                saved.pod_id = pod.id
                self.record_pod(saved, pod)
                if pod.status in {"EXITED", "TERMINATED"}:
                    self.finish_stopped(db, saved, saved.stop_reason or "external_stop")
                    db.commit()
                    return
                saved.error_code = None
                active = self.active_generations(db)
                # Reserve one polling interval plus the supplier request timeout for shutdown.
                reserve = saved.hourly_rate * Decimal(str(self.settings.compute_poll_seconds + 15)) / 3600
                budget = saved.managed and saved.estimated_cost + reserve >= saved.session_budget
                idle_expired = (
                    saved.managed
                    and saved.status == "ready"
                    and saved.auto_stop_minutes > 0
                    and not active
                    and (self.clock() - utc(saved.last_activity_at)).total_seconds()
                    >= saved.auto_stop_minutes * 60
                )
                reason = (
                    saved.stop_reason
                    if saved.pending_stop
                    else "session_budget"
                    if budget
                    else "idle_timeout"
                    if idle_expired
                    else None
                )
                if reason:
                    saved.pending_stop, saved.stop_reason = True, reason
                if saved.managed and pod.cost > saved.max_hourly_price:
                    reason = "price_violation"
                if (
                    not reason
                    and saved.managed
                    and (
                        pod.status == "ERROR"
                        or (
                            not saved.ready_at
                            and (self.clock() - utc(saved.created_at)).total_seconds()
                            > self.settings.runpod_startup_timeout
                        )
                    )
                ):
                    reason = "startup_failed" if pod.status == "ERROR" else "startup_timeout"
                if reason:
                    saved.pending_stop, saved.stop_reason = True, reason
                db.commit()
            if reason:
                if not active or reason in {"session_budget", "price_violation"}:
                    await self._terminate(row.id, reason)
                return
            if not row.managed:
                return
            phase = "starting_pod" if pod.status == "PROVISIONING" else "starting_environment"
            if pod.status == "RUNNING":
                phases = await self.api.phases(pod.id)
                if phases:
                    phase = phases[-1]
                elif row.ready_at:
                    phase = "health_unknown"
            if phase == "error":
                await self._terminate(row.id, "startup_failed")
                return
            llm_error = None
            if self.llm and pod.status == "RUNNING":
                observed = await self.llm.status()
                if observed == "ready":
                    phase = "ready"
                elif observed == "loading_model":
                    phase = "loading_model"
                else:
                    phase = "connecting"
                    llm_error = (
                        observed
                        if observed in {"connection_auth_failed", "model_mismatch", "malformed_response"}
                        else "connection_failed"
                    )
            with self.sessions() as db:
                saved = db.get(ComputeSession, row.id)
                saved.error_code = llm_error
                if saved.status != phase:
                    self.event(db, phase, saved)
                saved.status = phase
                if phase == "ready" and not saved.ready_at:
                    saved.ready_at = self.clock()
                    saved.last_activity_at = self.clock()
                db.commit()

    async def refresh_billing(self, user):
        if user.role != "admin":
            raise HTTPException(403, "Требуются права администратора")
        async with self.operation():
            with self.sessions() as db:
                rows = db.scalars(
                    select(ComputeSession)
                    .where(
                        ComputeSession.pod_id.is_not(None),
                        ComputeSession.started_at.is_not(None),
                        ComputeSession.stopped_at.is_not(None),
                        ComputeSession.managed.is_(True),
                    )
                    .order_by(ComputeSession.created_at.desc())
                    .limit(20)
                ).all()
            for row in rows:
                actual = await self.api.actual_cost(row.pod_id, utc(row.started_at), utc(row.stopped_at))
                with self.sessions() as db:
                    saved = db.get(ComputeSession, row.id)
                    saved.actual_cost, saved.actual_cost_at = actual, self.clock()
                    db.commit()
        return {"updated": len(rows)}
