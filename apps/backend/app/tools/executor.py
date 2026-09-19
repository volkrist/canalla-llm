import asyncio
import json
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import select, update

from ..database import SessionLocal
from ..models import Chat, Message, User, now
from .contracts import ToolError, ToolResult
from .models import ToolRun, WebSourceSnapshot
from .policy import ToolPolicy, effective_risk, network_channel, preferences
from .security import digest, input_summary, sanitized, validate_url
from .web_router import canonical_url

TERMINAL = {"completed", "stopped", "failed", "denied"}
STATES = {
    "search": "searching",
    "fetch": "reading",
    "tor_search": "searching",
    "tor_fetch": "reading",
    "tor_browser": "browser_working",
    "agent": "running_agent",
    "browser": "browser_working",
    "local_fs": "running",
    "local_process": "running",
    "local_info": "running",
    "local_registry": "running",
    "local_service": "running",
    "local_install": "running",
    "local_system": "running",
    "local_git": "running",
}


def public_run(row):
    metadata = dict(getattr(row, "result_metadata", None) or {})
    metadata.pop("host_args", None)
    payload = {
        key: getattr(row, key)
        for key in (
            "id",
            "chat_id",
            "generation_id",
            "tool_name",
            "provider",
            "risk_level",
            "status",
            "started_at",
            "finished_at",
            "cancelled_at",
            "input_summary",
            "input_digest",
            "cost_estimate",
            "cost_actual",
            "provider_run_id",
            "error_code",
            "origin",
            "assigned_device_id",
        )
        if hasattr(row, key)
    }
    payload["result_metadata"] = metadata
    return payload


def public_job(row):
    payload = public_run(row)
    payload["host_args"] = (row.result_metadata or {}).get("host_args") or {}
    return payload


def public_source(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "generation_id",
            "tool_run_id",
            "label",
            "url",
            "final_url",
            "title",
            "excerpt",
            "publisher",
            "published_at",
            "fetched_at",
            "searched_at",
            "rank",
            "provider",
            "etag",
            "last_modified",
            "channel",
            "authority",
            "canonical_url",
            "kind",
            "details",
        )
        if hasattr(row, key)
    }


def reconcile_tools():
    from .local.task import recover_interrupted

    with SessionLocal() as db:
        db.execute(
            update(ToolRun)
            .where(ToolRun.status.not_in(TERMINAL))
            .values(status="failed", finished_at=now(), error_code="backend_interrupted")
        )
        db.commit()
    recover_interrupted()


@dataclass
class ExecutionContext:
    user_id: str
    chat_id: str
    generation_id: str | None
    limits: object
    emit: object
    mode: str = "auto"
    computer_mode: str = "off"
    tor_enabled: bool = False
    tor_mode: str = "off"
    explicit: bool = False
    secrets: tuple = ()
    resolver: object = None
    run_id: str | None = None
    settings: object = None
    source_count: int = 0
    web_source_count: int = 0
    tor_source_count: int = 0
    sources: list = field(default_factory=list)
    preview: dict = field(default_factory=dict)
    action_fingerprint: str | None = None
    origin: str = "model"
    assigned_device_id: str | None = None
    host_online: bool = False
    web_search_done: bool = False
    web_fetch_done: bool = False
    tor_search_done: bool = False
    tor_fetch_done: bool = False
    tor_browser_done: bool = False
    tor_browser_navigated: bool = False
    seen_canonical: set = field(default_factory=set)
    tor_visited: set = field(default_factory=set)
    tor_visited_hosts: set = field(default_factory=set)
    tor_candidates: list = field(default_factory=list)
    tor_queries: list = field(default_factory=list)
    user_prompt: str = ""
    workspace: object = None
    task_id: str | None = None
    files_changed: int = 0
    task_commands: list = field(default_factory=list)
    coding_task: bool = False
    autonomous: bool = False
    resume_task_id: str | None = None
    task_title: str = ""
    completed_digests: list = field(default_factory=list)
    resuming: bool = False
    skip_final_stream: bool = False
    task_halt: str | None = None

    async def progress(self, **values):
        # Providers pass only documented, allowlisted metadata, never raw responses.
        allowed = {
            k: v
            for k, v in values.items()
            if k
            in {
                "provider_run_id",
                "steps",
                "supplier_state",
                "supplier_stop_confirmed",
                "budget_enforcement",
                "event_kind",
                "session_id",
                "estimated_provider_cost",
            }
        }
        with SessionLocal() as db:
            row = db.get(ToolRun, self.run_id)
            if row:
                run_id_value = allowed.get("provider_run_id")
                if run_id_value:
                    row.provider_run_id = sanitized(str(run_id_value), self.secrets, 160)
                    allowed = {k: v for k, v in allowed.items() if k != "provider_run_id"}
                row.result_metadata = {**row.result_metadata, **allowed}
                db.commit()
                if self.task_id and allowed.get("event_kind"):
                    from .local.journal import append_event
                    from .models import LocalTask
                    from .tinyfish.budget import load_budget, store_budget

                    mapped = {
                        "STARTED": "TINYFISH_AGENT_STARTED",
                        "PROGRESS": "TINYFISH_AGENT_STEP",
                        "COMPLETE": "TINYFISH_AGENT_COMPLETED",
                        "CANCELLED": "TINYFISH_AGENT_CANCELLED",
                        "BROWSER_STARTED": "TINYFISH_BROWSER_STARTED",
                        "BROWSER_CONNECTED": "TINYFISH_BROWSER_CONNECTED",
                        "BROWSER_CLOSED": "TINYFISH_BROWSER_CLOSED",
                    }.get(str(allowed.get("event_kind")))
                    if mapped:
                        stored_run = row.provider_run_id or run_id_value
                        append_event(
                            db,
                            self.task_id,
                            mapped,
                            {
                                "steps": allowed.get("steps"),
                                "run": stored_run,
                                "session": allowed.get("session_id"),
                            },
                            self.secrets,
                        )
                        task = db.get(LocalTask, self.task_id)
                        if task:
                            budget = load_budget(task.checkpoint)
                            if mapped == "TINYFISH_AGENT_STARTED" and stored_run:
                                budget.active_agent_run_id = str(stored_run)[:160]
                            if mapped in {"TINYFISH_AGENT_COMPLETED", "TINYFISH_AGENT_CANCELLED"}:
                                budget.active_agent_run_id = ""
                            if mapped == "TINYFISH_BROWSER_STARTED":
                                budget.active_browser_session_id = str(
                                    allowed.get("session_id") or stored_run or ""
                                )[:160]
                            if mapped == "TINYFISH_BROWSER_CLOSED":
                                budget.active_browser_session_id = ""
                            task.checkpoint = store_budget(task.checkpoint, budget)
                        db.commit()
                await self.emit("tool", public_run(row))


class ToolExecutor:
    def __init__(self, registry, policy=None):
        self.registry = registry
        self.policy = policy or ToolPolicy()

    def _run_metadata(self, definition, args, context, reservation):
        metadata = {"origin": getattr(context, "origin", "model") or "model"}
        channel = network_channel(definition)
        if channel:
            metadata["network"] = channel
        if reservation:
            metadata.update(reserved_budget=reservation, budget_enforcement="local_soft")
        if definition.provider == "local_device":
            metadata["host_args"] = args.model_dump(mode="json")
        return metadata

    def _preflight_tinyfish(self, definition, args, context):
        if not getattr(context, "task_id", None):
            return
        from ..config import get_settings
        from .local.journal import append_event
        from .local.task import LocalTaskController
        from .tinyfish.budget import load_budget, preflight_agent, preflight_browser

        settings = get_settings()
        prefs = getattr(context, "settings", None)
        with SessionLocal() as db:
            row = LocalTaskController()._row(db, context)
            if not row:
                return
            budget = load_budget(row.checkpoint)
            try:
                if definition.capability == "agent":
                    preflight_agent(budget, settings, prefs, requested_steps=1)
                elif definition.name == "browser_start" or (
                    definition.name == "web_browser" and getattr(args, "operation", "open") == "open"
                ):
                    if not budget.active_browser_session_id:
                        preflight_browser(budget, settings, prefs, requested_minutes=1)
            except ToolError as error:
                if error.code == "run_budget":
                    append_event(
                        db,
                        row.id,
                        "BUDGET_EXHAUSTED",
                        {"tool": definition.name},
                        getattr(context, "secrets", ()),
                    )
                    db.commit()
                raise

    def create_run(self, definition, args, context):
        with SessionLocal() as db:
            # Lock the user's budget across backend processes. SQLite obtains its writer lock
            # before reading the reservation ledger; PostgreSQL uses the user row lock.
            if db.bind.dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            user = db.scalar(select(User).where(User.id == context.user_id).with_for_update())
            chat = db.get(Chat, context.chat_id)
            message = db.get(Message, context.generation_id) if context.generation_id else None
            if (
                not user
                or not chat
                or chat.user_id != user.id
                or (context.generation_id and (not message or message.chat_id != chat.id))
            ):
                raise ToolError("not_found")
            prefs = preferences(db, user.id)
            decision = self.policy.validate(
                definition,
                prefs,
                mode=context.mode,
                computer_mode=context.computer_mode,
                tor_enabled=context.tor_enabled,
                tor_mode=getattr(context, "tor_mode", "off"),
                explicit=context.explicit,
                args=args,
            )
            reservation = 0
            if definition.cost_class == "paid":
                reservation = prefs.agent_run_budget
                midnight = now().replace(hour=0, minute=0, second=0, microsecond=0)
                runs = db.scalars(
                    select(ToolRun).where(ToolRun.user_id == user.id, ToolRun.started_at >= midnight)
                )
                spent = sum(
                    float(
                        r.cost_actual
                        if r.cost_actual is not None
                        else r.cost_estimate
                        if r.cost_estimate is not None
                        else r.result_metadata.get("reserved_budget", 0)
                    )
                    for r in runs
                )
                if spent + reservation > prefs.agent_daily_budget + 1e-9:
                    raise ToolError("daily_budget")
            row = ToolRun(
                user_id=user.id,
                chat_id=chat.id,
                generation_id=context.generation_id,
                tool_name=definition.name,
                provider=definition.provider,
                risk_level=effective_risk(definition, args).value,
                origin=getattr(context, "origin", "model") or "model",
                assigned_device_id=context.assigned_device_id,
                input_summary={
                    **input_summary(
                        definition,
                        args,
                        context.secrets,
                        follow=definition.capability == "tor_fetch" and bool(context.tor_fetch_done),
                    ),
                    **context.preview,
                    **(
                        {
                            key: value
                            for key, value in {
                                "task": getattr(context, "task_title", "") or "",
                                "step": getattr(context, "current_step", "") or "",
                            }.items()
                            if value
                        }
                        if getattr(context, "task_id", None)
                        else {}
                    ),
                },
                input_digest=digest(args.model_dump(mode="json"), definition.name),
                status="waiting_confirmation" if decision == "confirmation_required" else "planning",
                result_metadata=self._run_metadata(definition, args, context, reservation),
                task_id=getattr(context, "task_id", None),
            )
            db.add(row)
            db.commit()
            return row.id, prefs

    async def execute(self, name, arguments, context, origin="model"):
        context.origin = origin
        definition, provider = self.registry.get(name)
        try:
            if isinstance(arguments, str):
                if len(arguments) > 16000:
                    raise ValueError()
                arguments = json.loads(arguments)
            args = definition.input_model.model_validate(arguments)
        except (ValueError, TypeError, ValidationError):
            raise ToolError("invalid_arguments") from None
        import re

        payload = json.dumps(args.model_dump(mode="json"), ensure_ascii=False)
        if any(secret and secret in payload for secret in context.secrets) or re.search(
            r"(?i)bearer\s+|(?:password|passwd|api[_-]?key|access_token|secret)\s*[:=]", payload
        ):
            raise ToolError("sensitive_arguments")
        digest_value = digest(args.model_dump(mode="json"), definition.name)
        if definition.name in {"git_commit", "git_push"}:
            from .local.plan import looks_like_commit_request, looks_like_push_request

            prompt = getattr(context, "user_prompt", "") or ""
            settings = getattr(context, "settings", None)
            if definition.name == "git_commit" and not (
                looks_like_commit_request(prompt) or getattr(settings, "auto_commit", False)
            ):
                raise ToolError("git_commit_not_requested")
            if definition.name == "git_push" and not (
                looks_like_push_request(prompt) or getattr(settings, "allow_push", False)
            ):
                raise ToolError("git_push_not_requested")
        if getattr(context, "task_id", None) and definition.provider == "local_device":
            from .local.task import LocalTaskController

            with SessionLocal() as db:
                LocalTaskController().preflight(db, context, definition.name, digest_value)
                replay = db.scalar(
                    select(ToolRun).where(
                        ToolRun.task_id == context.task_id,
                        ToolRun.input_digest == digest_value,
                        ToolRun.status == "completed",
                    )
                )
                if replay:
                    hosted = (replay.result_metadata or {}).get("host_result") or {}
                    meta = replay.result_metadata or {}
                    return ToolResult(
                        text=str(hosted.get("text") or meta.get("text") or "already completed"),
                        metadata={
                            key: hosted.get(key, meta.get(key))
                            for key in (
                                "before_sha256",
                                "after_sha256",
                                "exit_code",
                                "files_changed",
                                "conflict",
                                "cwd",
                                "pid",
                                "digest",
                                "sha256",
                                "path",
                                "tool_run_id",
                                "verified_dead",
                            )
                            if hosted.get(key, meta.get(key)) is not None
                        },
                    )
        if definition.provider == "local_device":
            from .local.devices import active_device
            from .local.provider import _guard_paths

            with SessionLocal() as db:
                prefs = preferences(db, context.user_id)
                device = active_device(db, context.user_id)
            if not device:
                if getattr(context, "task_id", None):
                    from .local.task import LocalTaskController

                    with SessionLocal() as db:
                        LocalTaskController().waiting_device(db, context)
                raise ToolError("host_offline")
            bound = getattr(context, "assigned_device_id", None)
            if getattr(context, "task_id", None):
                from .models import LocalTask

                with SessionLocal() as db:
                    task = db.get(LocalTask, context.task_id)
                    bound = (task.device_id if task else None) or bound
            if bound and device.id != bound:
                if getattr(context, "task_id", None):
                    from .local.task import LocalTaskController

                    with SessionLocal() as db:
                        LocalTaskController().waiting_device(db, context)
                raise ToolError("host_offline")
            context.assigned_device_id = device.id
            _guard_paths(args, prefs.workspace_roots)
            context.settings = prefs
        context.limits.consume(definition, args)
        context.preview, context.action_fingerprint = {}, None
        if definition.capability == "agent":
            from .tinyfish.classify import classify_agent_goal

            goal = getattr(args, "goal", "") or getattr(args, "query", "")
            classified = classify_agent_goal(goal, getattr(context, "user_prompt", "") or goal)
            if classified.kind != "READ_ONLY":
                raise ToolError("agent_side_effect_not_supported")
        if hasattr(provider, "preview"):
            context.preview = await provider.preview(args, context)
        run_id, prefs = self.create_run(definition, args, context)
        context.run_id, context.settings = run_id, prefs
        started_provider = False
        try:
            async with asyncio.timeout(min(definition.timeout, context.limits.remaining)):
                with SessionLocal() as db:
                    row = db.get(ToolRun, run_id)
                    await context.emit("tool", public_run(row))
                    waiting = row.status == "waiting_confirmation"
                if waiting:
                    from .local.task import LocalTaskController

                    with SessionLocal() as db:
                        LocalTaskController().checkpoint(db, context, "WAITING_CONFIRMATION")
                    await self.wait_confirmation(run_id, context)
                # Revalidate permissions and the immutable payload immediately before execution.
                with SessionLocal() as db:
                    row = db.get(ToolRun, run_id)
                    fresh = preferences(db, context.user_id)
                    if row.status == "stopped":
                        raise ToolError("cancelled")
                    if row.input_digest != digest(args.model_dump(mode="json"), definition.name):
                        raise ToolError("confirmation_mismatch")
                    if (
                        self.policy.validate(
                            definition,
                            fresh,
                            mode=context.mode,
                            computer_mode=context.computer_mode,
                            tor_enabled=context.tor_enabled,
                            tor_mode=getattr(context, "tor_mode", "off"),
                            explicit=context.explicit,
                            confirmed=row.confirmed_at is not None,
                            args=args,
                        )
                        != "allowed"
                    ):
                        raise ToolError("confirmation_required")
                    if definition.cost_class == "paid" and fresh.agent_run_budget < prefs.agent_run_budget:
                        raise ToolError("budget_changed")
                    if definition.cost_class == "paid":
                        midnight = now().replace(hour=0, minute=0, second=0, microsecond=0)
                        ledger = db.scalars(
                            select(ToolRun).where(
                                ToolRun.user_id == context.user_id, ToolRun.started_at >= midnight
                            )
                        ).all()
                        reserved = sum(
                            float(
                                r.cost_actual
                                if r.cost_actual is not None
                                else r.cost_estimate
                                if r.cost_estimate is not None
                                else r.result_metadata.get("reserved_budget", 0)
                            )
                            for r in ledger
                        )
                        if reserved > fresh.agent_daily_budget + 1e-9:
                            raise ToolError("daily_budget")
                    context.settings = fresh
                    row.status = (
                        "waiting_host"
                        if definition.provider == "local_device"
                        else STATES.get(definition.capability, "running")
                    )
                    db.commit()
                    await context.emit("tool", public_run(row))
                if definition.provider == "tinyfish" and definition.cost_class == "paid":
                    self._preflight_tinyfish(definition, args, context)
                if definition.provider == "local_device":
                    await self.wait_host(run_id, context)
                    with SessionLocal() as db:
                        hosted = db.get(ToolRun, run_id)
                        context.preview = {
                            **context.preview,
                            "host_result": (hosted.result_metadata or {}).get("host_result", {}),
                        }
                started_provider = True
                if definition.capability == "tor_fetch":
                    self._guard_tor_fetch(args, context)
                result = await provider.execute(args, context)
                result.text = sanitized(
                    result.text, context.secrets, max(0, context.limits.max_chars - context.limits.chars)
                )
                context.limits.chars += len(result.text)
                self._remember_tor(result, context, definition, args)
                await self.save_sources(result, context, definition)
                if definition.capability == "search":
                    context.web_search_done = True
                if definition.capability == "fetch":
                    context.web_fetch_done = True
                if definition.capability == "tor_search":
                    context.tor_search_done = True
                    query = getattr(args, "query", "")
                    if query:
                        context.tor_queries.append(query)
                if definition.capability == "tor_fetch":
                    context.tor_fetch_done = True
                    if context.limits.tor_fetches > 1:
                        context.limits.tor_follows += 1
                if definition.capability == "tor_browser":
                    context.tor_fetch_done = True
                    context.tor_browser_done = True
                    if getattr(args, "operation", "open") in {"click", "navigate"}:
                        context.tor_browser_navigated = True
                with SessionLocal() as db:
                    row = db.get(ToolRun, run_id)
                    row.status, row.finished_at = "completed", now()
                    row.cost_actual = (
                        Decimal(str(result.cost_actual)) if result.cost_actual is not None else None
                    )
                    row.cost_estimate = (
                        Decimal(str(result.cost_estimate)) if result.cost_estimate is not None else None
                    )
                    if result.provider_run_id:
                        row.provider_run_id = sanitized(result.provider_run_id, context.secrets, 160)
                    row.result_metadata = {
                        **row.result_metadata,
                        **result.metadata,
                        "partial_errors": result.errors[:10],
                    }
                    digest_value = row.input_digest
                    db.commit()
                    await context.emit("tool", public_run(row))
                from .local.task import LocalTaskController
                from .policy import LOCAL_CAPABILITIES

                if definition.capability in LOCAL_CAPABILITIES:
                    LocalTaskController().note_command(
                        context, definition.name, digest_value, result.metadata
                    )
                    status = (
                        "VERIFYING"
                        if definition.name in {"run_python", "run_process", "run_powershell"}
                        else "EXECUTING"
                    )
                    with SessionLocal() as db:
                        LocalTaskController().checkpoint(db, context, status)
                if definition.provider == "tinyfish" and definition.cost_class == "paid":
                    with SessionLocal() as db:
                        LocalTaskController().consume_tinyfish(db, context, definition, result)
                return result
        except BaseException as error:
            cancelled = isinstance(error, asyncio.CancelledError) or (
                isinstance(error, ToolError) and error.code in {"cancelled", "confirmation_denied"}
            )
            code = (
                error.code
                if isinstance(error, ToolError)
                else "timeout"
                if isinstance(error, TimeoutError)
                else "provider_unavailable"
            )
            with SessionLocal() as db:
                row = db.get(ToolRun, run_id)
                if row:
                    row.status = "stopped" if cancelled else "failed"
                    row.finished_at, row.error_code = now(), code
                    if cancelled:
                        row.cancelled_at = now()
                    if not started_provider:
                        row.result_metadata = {**row.result_metadata, "reserved_budget": 0}
                    db.commit()
                    if not isinstance(error, asyncio.CancelledError):
                        await context.emit("tool", public_run(row))
            if isinstance(error, asyncio.CancelledError):
                raise
            raise ToolError(code) from None

    async def wait_confirmation(self, run_id, context):
        while True:
            with SessionLocal() as db:
                row = db.get(ToolRun, run_id)
                if not row or row.user_id != context.user_id or row.status in TERMINAL:
                    raise ToolError("confirmation_denied")
                if row.started_at.replace(tzinfo=timezone.utc) < now() - timedelta(minutes=5):
                    raise ToolError("confirmation_expired")
                if row.status == "approved":
                    # The conditional transition makes approval consumable exactly once.
                    changed = db.execute(
                        update(ToolRun)
                        .where(ToolRun.id == run_id, ToolRun.status == "approved")
                        .values(status="authorized")
                    )
                    db.commit()
                    if changed.rowcount == 1:
                        return
            await asyncio.sleep(0.25)

    async def wait_host(self, run_id, context):
        while True:
            with SessionLocal() as db:
                row = db.get(ToolRun, run_id)
                if not row or row.user_id != context.user_id:
                    raise ToolError("host_offline")
                if row.status == "stopped":
                    raise ToolError("cancelled")
                if row.status in TERMINAL:
                    raise ToolError("host_offline")
                from .local.devices import public_device
                from .models import PairedDevice

                device = db.get(PairedDevice, row.assigned_device_id) if row.assigned_device_id else None
                online = bool(device and public_device(device)["online"])
                if not online:
                    from .local.task import LocalTaskController

                    LocalTaskController().waiting_device(db, context)
                    await context.emit(
                        "task_status",
                        {"state": "WAITING_DEVICE", "code": "host_offline", "task_id": context.task_id},
                    )
                    raise ToolError("host_offline")
                elif getattr(context, "task_id", None) and row.status == "waiting_host":
                    from .local.machine import WAITING_DEVICE
                    from .local.task import LocalTaskController
                    from .models import LocalTask as TaskRow

                    task = db.get(TaskRow, context.task_id)
                    if task and task.status == WAITING_DEVICE:
                        LocalTaskController().checkpoint(db, context, "EXECUTING")
                timeout_at = now() - timedelta(minutes=5)
                if getattr(context, "autonomous", False):
                    if context.limits.remaining <= 0:
                        raise ToolError("timeout")
                elif row.started_at.replace(tzinfo=timezone.utc) < timeout_at:
                    raise ToolError("timeout")
                if row.status == "host_ready":
                    return
            await asyncio.sleep(0.25)

    async def save_sources(self, result: ToolResult, context, definition):
        if not context.generation_id:
            result.sources = []
            return
        saved, returned = [], []
        channel = "tor" if definition.capability.startswith("tor") else "web"
        kind = "search" if definition.capability in {"search", "tor_search"} else "fetch"
        budget = 3 if kind == "search" else 10
        for source in result.sources[:budget]:
            url = source.get("url", "")
            final_url = source.get("final_url") or url
            if channel == "tor":
                from .tor.urls import validate_tor_url

                try:
                    await validate_tor_url(url)
                    await validate_tor_url(final_url)
                except ToolError:
                    result.errors.append("unsafe_source")
                    continue
            else:
                try:
                    await validate_url(url, context.resolver)
                    await validate_url(final_url, context.resolver)
                except ToolError:
                    result.errors.append("unsafe_source")
                    continue
            key = canonical_url(final_url)
            if not key:
                continue
            available = max(0, context.limits.max_chars - context.limits.chars)
            excerpt = sanitized(source.get("excerpt", ""), context.secrets, min(available, 6000))
            if not excerpt:
                continue
            if key in context.seen_canonical:
                if kind == "fetch":
                    upgraded = self._upgrade_search_source(context, key, excerpt, source, definition)
                    if upgraded:
                        returned.append(upgraded)
                continue
            context.seen_canonical.add(key)
            context.limits.chars += len(excerpt)
            if channel == "tor":
                context.tor_source_count += 1
                label = f"T{context.tor_source_count}"
            else:
                context.web_source_count += 1
                label = f"W{context.web_source_count}"
            context.source_count += 1
            row = WebSourceSnapshot(
                user_id=context.user_id,
                generation_id=context.generation_id,
                tool_run_id=context.run_id,
                label=label,
                url=sanitized(url, context.secrets, 2048),
                final_url=sanitized(final_url, context.secrets, 2048),
                title=sanitized(source.get("title", ""), context.secrets, 400),
                excerpt=excerpt,
                publisher=sanitized(source["publisher"], context.secrets, 300)
                if source.get("publisher")
                else None,
                published_at=sanitized(source["published_at"], context.secrets, 80)
                if source.get("published_at")
                else None,
                fetched_at=now() if kind != "search" else None,
                searched_at=now() if kind == "search" else None,
                rank=context.source_count,
                provider=definition.provider,
                etag=sanitized(source["etag"], context.secrets, 300) if source.get("etag") else None,
                last_modified=sanitized(source["last_modified"], context.secrets, 100)
                if source.get("last_modified")
                else None,
                channel=channel,
                authority=source.get("authority"),
                canonical_url=key[:2048],
                kind=kind,
                details=self._source_details(source, channel, kind),
            )
            with SessionLocal() as db:
                db.add(row)
                db.commit()
                public = public_source(row)
                saved.append(public)
                returned.append(public)
        result.sources = returned
        context.sources.extend(saved)

    def _upgrade_search_source(self, context, key, excerpt, source, definition):
        with SessionLocal() as db:
            row = db.scalar(
                select(WebSourceSnapshot).where(
                    WebSourceSnapshot.generation_id == context.generation_id,
                    WebSourceSnapshot.canonical_url == key,
                    WebSourceSnapshot.user_id == context.user_id,
                )
            )
            if not row:
                return None
            context.limits.chars += max(0, len(excerpt) - len(row.excerpt or ""))
            row.excerpt = excerpt
            row.kind = "fetch"
            row.fetched_at = now()
            row.tool_run_id = context.run_id
            row.provider = definition.provider
            if source.get("title"):
                row.title = sanitized(source.get("title", ""), context.secrets, 400)
            channel = "tor" if definition.capability.startswith("tor") else "web"
            row.details = {**(row.details or {}), **self._source_details(source, channel, "fetch")}
            db.commit()
            public = public_source(row)
        for item in context.sources:
            if item.get("canonical_url") == key or item.get("id") == public["id"]:
                item.update(public)
                break
        return public

    def _source_details(self, source, channel, kind):
        links = source.get("links") or []
        return {
            "transport": "tor" if channel == "tor" else "direct",
            "reachable": bool(source.get("reachable", True)),
            "parent_source": source.get("parent_source"),
            "depth": int(source.get("depth") or (0 if kind == "search" else 1)),
            "search_query": source.get("search_query"),
            "links": links[:20],
            "needs_browser": bool(source.get("needs_browser")),
            "retrieval": source.get("retrieval") or ("http" if channel == "tor" else "direct"),
            "rendered": bool(source.get("rendered")),
            "browser_session_id": source.get("browser_session_id"),
        }

    def _guard_tor_fetch(self, args, context):
        from .tor.router import blocked_link, normalize_http_url

        kept = []
        for url in list(args.urls):
            key = normalize_http_url(url)
            if blocked_link(url) or not key:
                continue
            if key in context.tor_visited:
                continue
            kept.append(url)
        if not kept:
            raise ToolError("tool_limit")
        args.urls = kept[:3]

    def _remember_tor(self, result, context, definition, args):
        if not definition.capability.startswith("tor"):
            return
        from urllib.parse import urlsplit

        from .tor.router import normalize_http_url
        from .tor.urls import is_onion

        query = getattr(args, "query", None)
        parent_depth = 0
        for url in getattr(args, "urls", []) or []:
            key = normalize_http_url(url)
            for item in context.tor_candidates:
                if normalize_http_url(item.get("url") or "") == key:
                    parent_depth = max(parent_depth, int(item.get("depth") or 0))
                    break
        for source in result.sources or []:
            url = source.get("final_url") or source.get("url") or ""
            key = normalize_http_url(url)
            host = (urlsplit(url).hostname or "").rstrip(".").lower()
            if key and definition.capability in {"tor_fetch", "tor_browser"}:
                context.tor_visited.add(key)
            if host:
                context.tor_visited_hosts.add(host)
            depth = 0 if definition.capability == "tor_search" else max(parent_depth, 1)
            source["depth"] = source.get("depth", depth)
            source["search_query"] = source.get("search_query") or query
            source["reachable"] = "not reachable" not in (source.get("excerpt") or "").lower()
            if definition.capability in {"tor_fetch", "tor_browser"}:
                for item in context.tor_candidates:
                    if normalize_http_url(item.get("url") or "") == key:
                        source["parent_source"] = item.get("parent_source") or source.get("parent_source")
                        break
            for link in source.get("links") or []:
                if len(context.tor_candidates) >= context.limits.max_tor_candidates:
                    break
                link_key = normalize_http_url(link.get("url") or "")
                if not link_key or link_key in context.tor_visited:
                    continue
                context.tor_candidates.append(
                    {
                        **link,
                        "depth": depth + 1,
                        "parent_source": source.get("title") or url,
                        "authority": source.get("authority"),
                    }
                )
            if definition.capability == "tor_search" and is_onion(host):
                context.tor_candidates.append(
                    {
                        "url": key or url,
                        "text": source.get("title") or "",
                        "source_page": url,
                        "is_onion": True,
                        "is_clearnet": False,
                        "same_host": True,
                        "depth": 1,
                        "parent_source": source.get("title") or url,
                        "authority": source.get("authority"),
                    }
                )
