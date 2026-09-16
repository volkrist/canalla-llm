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
from .policy import ToolPolicy, preferences
from .security import digest, input_summary, sanitized, validate_url

TERMINAL = {"completed", "stopped", "failed", "denied"}
STATES = {"search": "searching", "fetch": "reading", "agent": "running_agent", "browser": "browser_working"}


def public_run(row):
    return {
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
            "cost_estimate",
            "cost_actual",
            "provider_run_id",
            "error_code",
            "result_metadata",
        )
    }


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
        )
    }


def reconcile_tools():
    # A crashed local task cannot resume a paid run implicitly.
    with SessionLocal() as db:
        db.execute(
            update(ToolRun)
            .where(ToolRun.status.not_in(TERMINAL))
            .values(status="failed", finished_at=now(), error_code="backend_interrupted")
        )
        db.commit()


@dataclass
class ExecutionContext:
    user_id: str
    chat_id: str
    generation_id: str | None
    limits: object
    emit: object
    mode: str = "auto"
    explicit: bool = False
    secrets: tuple = ()
    resolver: object = None
    run_id: str | None = None
    settings: object = None
    source_count: int = 0
    sources: list = field(default_factory=list)
    preview: dict = field(default_factory=dict)
    action_fingerprint: str | None = None

    async def progress(self, **values):
        # Providers pass only documented, allowlisted metadata, never raw responses.
        allowed = {
            k: v
            for k, v in values.items()
            if k
            in {"provider_run_id", "steps", "supplier_state", "supplier_stop_confirmed", "budget_enforcement"}
        }
        with SessionLocal() as db:
            row = db.get(ToolRun, self.run_id)
            if row:
                if allowed.get("provider_run_id"):
                    row.provider_run_id = sanitized(allowed.pop("provider_run_id"), self.secrets, 160)
                row.result_metadata = {**row.result_metadata, **allowed}
                db.commit()
                await self.emit("tool", public_run(row))


class ToolExecutor:
    def __init__(self, registry, policy=None):
        self.registry = registry
        self.policy = policy or ToolPolicy()

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
            decision = self.policy.validate(definition, prefs, mode=context.mode, explicit=context.explicit)
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
                risk_level=definition.risk_level.value,
                input_summary={**input_summary(definition, args, context.secrets), **context.preview},
                input_digest=digest(args.model_dump(mode="json")),
                status="waiting_confirmation" if decision == "confirmation_required" else "planning",
                result_metadata={"reserved_budget": reservation, "budget_enforcement": "local_soft"}
                if reservation
                else {},
            )
            db.add(row)
            db.commit()
            return row.id, prefs

    async def execute(self, name, arguments, context):
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
        context.limits.consume(definition, args)
        context.preview, context.action_fingerprint = {}, None
        if (
            definition.capability == "agent"
            and definition.risk_level.value == "READ_ONLY"
            and not getattr(provider, "read_only_enforced", False)
        ):
            raise ToolError("agent_read_only_boundary_unavailable")
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
                    await self.wait_confirmation(run_id, context)
                # Revalidate permissions and the immutable payload immediately before execution.
                with SessionLocal() as db:
                    row = db.get(ToolRun, run_id)
                    fresh = preferences(db, context.user_id)
                    if row.status == "stopped":
                        raise ToolError("cancelled")
                    if row.input_digest != digest(args.model_dump(mode="json")):
                        raise ToolError("confirmation_mismatch")
                    if (
                        self.policy.validate(
                            definition,
                            fresh,
                            mode=context.mode,
                            explicit=context.explicit,
                            confirmed=row.confirmed_at is not None,
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
                    row.status = STATES.get(definition.capability, "running")
                    db.commit()
                    await context.emit("tool", public_run(row))
                started_provider = True
                result = await provider.execute(args, context)
                result.text = sanitized(
                    result.text, context.secrets, max(0, context.limits.max_chars - context.limits.chars)
                )
                context.limits.chars += len(result.text)
                await self.save_sources(result, context, definition)
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
                    db.commit()
                    await context.emit("tool", public_run(row))
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

    async def save_sources(self, result: ToolResult, context, definition):
        if not context.generation_id:
            result.sources = []
            return
        saved = []
        for source in result.sources[:10]:
            try:
                await validate_url(source.get("url", ""), context.resolver)
                await validate_url(source.get("final_url") or source["url"], context.resolver)
            except ToolError:
                result.errors.append("unsafe_source")
                continue
            available = max(0, context.limits.max_chars - context.limits.chars)
            excerpt = sanitized(source.get("excerpt", ""), context.secrets, min(available, 6000))
            if not excerpt:
                continue
            context.limits.chars += len(excerpt)
            context.source_count += 1
            row = WebSourceSnapshot(
                user_id=context.user_id,
                generation_id=context.generation_id,
                tool_run_id=context.run_id,
                label=f"W{context.source_count}",
                url=sanitized(source["url"], context.secrets, 2048),
                final_url=sanitized(source.get("final_url") or source["url"], context.secrets, 2048),
                title=sanitized(source.get("title", ""), context.secrets, 400),
                excerpt=excerpt,
                publisher=sanitized(source["publisher"], context.secrets, 300)
                if source.get("publisher")
                else None,
                published_at=sanitized(source["published_at"], context.secrets, 80)
                if source.get("published_at")
                else None,
                fetched_at=now() if definition.capability != "search" else None,
                searched_at=now() if definition.capability == "search" else None,
                rank=context.source_count,
                provider=definition.provider,
                etag=sanitized(source["etag"], context.secrets, 300) if source.get("etag") else None,
                last_modified=sanitized(source["last_modified"], context.secrets, 100)
                if source.get("last_modified")
                else None,
            )
            with SessionLocal() as db:
                db.add(row)
                db.commit()
                saved.append(public_source(row))
        result.sources = saved
        context.sources.extend(saved)
