import asyncio
import json
import logging
from datetime import timedelta, timezone

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, delete, or_, select

from .compute.models import GenerationUsage
from .config import get_settings
from .context_builder import ContextBuilder
from .database import SessionLocal
from .documents.service import mutation_lock
from .models import Chat, Message, MessageContext, now
from .providers import LLMError
from .schemas import MessageOut
from .tools.executor import ExecutionContext
from .tools.policy import ToolLimits, preferences
from .tools.registry import make_orchestrator

logger = logging.getLogger(__name__)


def ordered_messages(db, chat_id):
    return db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at, Message.id)
    ).all()


def after(message):
    return or_(
        Message.created_at > message.created_at,
        and_(Message.created_at == message.created_at, Message.id > message.id),
    )


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(jsonable_encoder(data), ensure_ascii=False)}\n\n"


async def stream_response(
    chat,
    content,
    request,
    user,
    db,
    action="send",
    target_id=None,
    web_mode=None,
    computer_mode=None,
    tor_mode=None,
    resume_task_id=None,
):
    if chat.id in request.app.state.generating:
        raise HTTPException(409, "В этом диалоге уже идёт генерация")
    target = None
    if target_id:
        rows = ordered_messages(db, chat.id)
        target = next((row for row in rows if row.id == target_id), None)
        if target is None:
            raise HTTPException(404, "Сообщение не найдено")
        if action == "resend" and target.role != "user":
            raise HTTPException(422, "Редактировать можно только своё сообщение")
        if action == "regenerate":
            if target.role != "assistant":
                raise HTTPException(422, "Повторная генерация доступна для ответа AI")
            previous = [row for row in rows[: rows.index(target)] if row.role == "user"]
            if not previous:
                raise HTTPException(422, "Перед ответом нет сообщения пользователя")
            target = previous[-1]
    compute = request.app.state.compute
    prefs = preferences(db, user.id)
    effective_web_mode = web_mode or prefs.default_mode
    effective_computer_mode = computer_mode or prefs.computer_mode
    effective_tor_mode = tor_mode or prefs.tor_mode
    usage_id = None
    request.app.state.generating.add(chat.id)
    mutation_lock.acquire()
    try:
        user_message: Message
        if action == "resume":
            from .tools.models import LocalTask

            task = db.get(LocalTask, resume_task_id)
            if not task or task.user_id != user.id or (task.chat_id and task.chat_id != chat.id):
                raise HTTPException(404, "Задача не найдена")
            content = task.original_user_request
            prior = ordered_messages(db, chat.id)
            resumed = next((row for row in reversed(prior) if row.role == "user"), None)
            if resumed is None:
                resumed = Message(chat_id=chat.id, role="user", content=content)
                db.add(resumed)
                db.flush()
            user_message = resumed
        elif action == "send":
            prior = ordered_messages(db, chat.id)
            created = now()
            if prior:
                created = max(
                    created, prior[-1].created_at.replace(tzinfo=timezone.utc) + timedelta(microseconds=1)
                )
            user_message = Message(chat_id=chat.id, role="user", content=content, created_at=created)
            db.add(user_message)
            if chat.title == "New chat":
                chat.title = content[:80]
        else:
            if target is None:
                raise HTTPException(404, "Сообщение не найдено")
            user_message = target
            db.execute(delete(Message).where(Message.chat_id == chat.id, after(target)))
            if action == "resend":
                target.content, target.edited_at = content, now()
        chat.updated_at = now()
        db.flush()
        context = ContextBuilder().build(db, user, chat, user_message, track=True)
        history = context["messages"]
        assistant = Message(
            chat_id=chat.id,
            role="assistant",
            content="",
            status="generating",
            generation_started_at=now(),
            created_at=max(
                now(), user_message.created_at.replace(tzinfo=timezone.utc) + timedelta(microseconds=1)
            ),
        )
        db.add(assistant)
        db.flush()
        db.add(
            MessageContext(
                message_id=assistant.id,
                memory_ids=context["memory_ids"],
                snapshot={k: v for k, v in context.items() if k not in {"messages", "memories"}},
            )
        )
        db.commit()
        assistant_id, chat_id = assistant.id, chat.id
        meta = {
            "user": MessageOut.model_validate(user_message).model_dump(mode="json"),
            "assistant": MessageOut.model_validate(assistant).model_dump(mode="json"),
            "replace_after_id": user_message.id,
        }
    except BaseException:
        db.rollback()
        request.app.state.generating.discard(chat.id)
        if usage_id:
            compute.finish_generation(usage_id, "error")
        raise
    finally:
        mutation_lock.release()

    async def generate():
        parts = []
        status = "stopped"
        # Pre-typed containers: the values are heterogeneous (counts, flags, ISO stamps, None).
        tokens: dict[str, object] = {}
        first_token_at = None
        cancellation: dict[str, object] = {"upstream_cancel_confirmed": None}
        started = assistant.generation_started_at
        iterator = None
        tool_task = None
        planner_usage = {}
        parked = False
        detached = False
        tool_context = None
        usage_id = None
        # Verified results of this turn. A halted answer quotes them, so the user sees what the
        # tools already did instead of a blanket failure.
        facts: dict = {}
        queue = asyncio.Queue(maxsize=64)
        from .tools.local.plan import looks_like_autonomous, looks_like_computer
        from .tools.local.public_text import public_assistant_text
        from .tools.local.workspace import looks_like_coding

        async def emit(event, value):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait((event, jsonable_encoder(value)))

        async def finish_detached(task, context):
            """Write the answer of a turn whose client went away, then release the chat.

            A browser reload, a navigation or a closed window must not destroy a multi-step
            task: the tool phase keeps running, its results are already on disk, and the answer
            is written into the same assistant message. The generation slot stays held until
            then, so a second message in the same chat cannot race the detached turn.
            """
            final_status = "complete"
            try:
                enriched = await task
                from .context_builder import ContextBuilder as _ContextBuilder
                from .tools.local.continue_task import finish_resumed_task
                from .tools.local.task import LocalTaskController as _Controller

                enriched = _ContextBuilder.with_tool_results(
                    enriched,
                    getattr(context, "tool_transcript", None),
                    get_settings().tools_max_chars * 4,
                )
                halt = getattr(context, "task_halt", None)
                if halt and halt != "llm_unavailable":
                    # Only the user can resolve these (confirmation, pause, device). Never
                    # fabricate an answer for a turn that is deliberately waiting.
                    with SessionLocal() as db:
                        row = _Controller()._row(db, context)
                        facts = dict(row.facts or {}) if row else {}
                        message = db.get(Message, assistant_id)
                        if message is not None:
                            message.content = public_assistant_text("", halt, facts)
                            message.status = "complete"
                            message.completed_at = now()
                        db.commit()
                else:
                    text = await finish_resumed_task(
                        context.task_id, request.app.state.provider, enriched, context
                    )
                    if text is not None:
                        with SessionLocal() as db:
                            _Controller().promote_for_answer(db, context)
                            _Controller().conclude(db, context)
                            _Controller().clear_transcript(db, context)
                    else:
                        with SessionLocal() as db:
                            row = _Controller()._row(db, context)
                            facts = dict(row.facts or {}) if row else {}
                            message = db.get(Message, assistant_id)
                            if message is not None:
                                message.content = public_assistant_text("", "llm_unavailable", facts)
                                message.status = "complete"
                                message.completed_at = now()
                            db.commit()
            except Exception:
                final_status = "error"
                logger.warning(
                    "detached_turn_failed task_id=%s",
                    getattr(context, "task_id", None),
                    exc_info=True,
                )
            finally:
                if usage_id:
                    compute.finish_generation(usage_id, final_status, assistant_id, tokens)
                request.app.state.generating.discard(chat_id)
                import anyio as _anyio

                with _anyio.CancelScope(shield=True):
                    await request.app.state.presence.publish()

        try:
            await request.app.state.presence.publish()
            yield sse("meta", meta)
            from .compute.demand import production_llm_required, wait_for_production

            wait_state = "ready"
            if production_llm_required(get_settings()):
                async for event, payload in wait_for_production(
                    request, user, chat, user_message, assistant, content, resume_task_id
                ):
                    if event == "_done":
                        wait_state = payload
                        break
                    yield sse(event, payload)
            if wait_state == "parked":
                parked = True
                yield sse("done", {"parked": True, "message_id": assistant_id})
                return
            usage_id = await compute.begin_generation(user.id, chat.id, request.app.state.provider_name)
            await request.app.state.presence.publish()
            with SessionLocal() as link_db:
                usage = link_db.get(GenerationUsage, usage_id)
                if usage:
                    usage.message_id = assistant_id
                    link_db.commit()
            settings = get_settings()
            prompt = content if action in {"send", "resume"} else user_message.content
            coding = effective_computer_mode != "off"
            autonomous = (
                looks_like_autonomous(prompt) or looks_like_coding(prompt) or looks_like_computer(prompt)
            )
            torish = effective_tor_mode != "off"
            # A turn that exposes tools is a task-scale turn even when the prompt does not match
            # one of the intent regexes. The old narrow classification gave a genuine 8-step
            # computer/web task the 180 s chat budget; once it was spent, the next read_file
            # failed instantly with `timeout` although the tool itself was healthy.
            tool_task = bool(
                autonomous
                or coding
                or torish
                or effective_web_mode != "off"
                or effective_computer_mode != "off"
            )
            max_calls = settings.tools_max_calls
            if tool_task:
                max_calls = max(
                    max_calls,
                    settings.tools_max_coding_calls,
                    settings.tools_task_max_calls,
                )
            if torish:
                max_calls = max(max_calls, settings.tools_max_tor_calls)
            max_calls = min(
                max_calls,
                settings.tools_task_hard_calls if tool_task else settings.tools_hard_max_calls,
            )
            # Separate concerns: this is the whole task ceiling, not the per-call timeout.
            # `ToolDefinition.timeout` still bounds each call; long processes use wait=false and
            # process_status instead of holding one call open for the whole build.
            max_seconds = (
                min(settings.tools_task_max_runtime, settings.tools_task_hard_runtime)
                if tool_task
                else settings.tools_max_seconds
            )
            max_search = settings.tools_task_max_search if tool_task else settings.tools_max_search
            max_fetch = settings.tools_task_max_fetch if tool_task else settings.tools_max_fetch

            tool_context = ExecutionContext(
                user.id,
                chat_id,
                assistant_id,
                ToolLimits(
                    max_calls=max_calls,
                    max_search=max_search,
                    max_fetch=max_fetch,
                    max_pages=settings.tools_max_pages,
                    max_chars=settings.tools_max_chars,
                    max_seconds=max_seconds,
                    max_local_calls=max(settings.tools_max_local_calls, max_calls),
                    max_files_changed=(
                        min(settings.tools_task_max_files, settings.tools_task_hard_files)
                        if tool_task
                        else settings.tools_max_files_changed
                    ),
                    max_file_bytes=settings.tools_max_file_bytes,
                    max_process_seconds=settings.tools_max_process_seconds,
                    hard_max_calls=settings.tools_task_hard_calls
                    if tool_task
                    else settings.tools_hard_max_calls,
                    max_tor_search=settings.tools_max_tor_search,
                    max_tor_fetch=settings.tools_max_tor_fetch,
                    max_tor_pages=settings.tools_max_tor_pages,
                    max_tor_calls=settings.tools_max_tor_calls,
                    max_tor_follow=settings.tools_max_tor_follow,
                    max_tor_depth=settings.tools_max_tor_depth,
                    max_tor_candidates=settings.tools_max_tor_candidates,
                    max_tor_seconds=settings.tools_max_tor_seconds,
                ),
                emit,
                mode=effective_web_mode,
                computer_mode=effective_computer_mode,
                tor_enabled=effective_tor_mode != "off",
                tor_mode=effective_tor_mode,
                settings=prefs,
                secrets=(
                    settings.tinyfish_api_key.get_secret_value(),
                    settings.runpod_api_key.get_secret_value(),
                    settings.jwt_secret,
                    settings.llm_api_key,
                ),
                resolver=getattr(request.app.state, "tool_dns_override", None),
            )
            tool_context.user_prompt = content if action in {"send", "resume"} else user_message.content
            tool_context.autonomous = autonomous
            tool_context.resume_task_id = resume_task_id
            tool_context.resuming = bool(resume_task_id)
            # The chat stream concludes the task after the tool phase (or the detached answer
            # does). The orchestrator must not lock it before the prose exists.
            tool_context.defer_conclusion = True
            tool_task = asyncio.create_task(
                make_orchestrator(request.app.state.tools).prepare(
                    request.app.state.provider,
                    history,
                    context["web_insert_index"],
                    tool_context,
                    planner_usage,
                )
            )
            while not tool_task.done() or not queue.empty():
                try:
                    event, value = await asyncio.wait_for(queue.get(), 0.25)
                    yield sse(event, value)
                except TimeoutError:
                    if await request.is_disconnected():
                        task_id = getattr(tool_context, "task_id", None)
                        if tool_task is not None and not tool_task.done() and task_id:
                            # The client is gone; the work is not. Detach the tool phase and let
                            # the answer be written into the same message when it finishes.
                            logger.info(
                                "turn_detached_client_gone task_id=%s chat_id=%s",
                                task_id,
                                chat_id,
                            )
                            detached = True
                            asyncio.create_task(finish_detached(tool_task, tool_context))
                            tool_task = None
                        raise asyncio.CancelledError()
            enriched_history = await tool_task
            # A tool result has to reach the answer that quotes it. Without this the final stream
            # only sees the conversation, so a completed read_file is invisible to the model and
            # it answers that it cannot access the computer.
            from .context_builder import ContextBuilder

            if not getattr(tool_context, "final_after_halt", False):
                # The tool phase is over and `defer_conclusion` kept the task open. Conclude it
                # now, exactly where the orchestrator used to: parked states refuse on their own.
                from .tools.local.task import LocalTaskController as _ConcludeController

                with SessionLocal() as conclude_db:
                    _ConcludeController().conclude(conclude_db, tool_context)

            enriched_history = ContextBuilder.with_tool_results(
                enriched_history,
                getattr(tool_context, "tool_transcript", None),
                settings.tools_max_chars * 4,
            )
            with SessionLocal() as snapshot_db:
                snapshot = snapshot_db.get(MessageContext, assistant_id)
                if snapshot:
                    snapshot.snapshot = {
                        **(snapshot.snapshot or {}),
                        "web_mode": effective_web_mode,
                        "computer_mode": effective_computer_mode,
                        "tor_mode": effective_tor_mode,
                        "web_source_count": len(tool_context.sources),
                        "web_chars": sum(len(source["excerpt"]) for source in tool_context.sources),
                        "planner_usage": planner_usage,
                        "total_chars": sum(len(m.get("content") or "") for m in enriched_history),
                    }
                    snapshot_db.commit()
            halt = getattr(tool_context, "task_halt", None)
            skip_final = bool(getattr(tool_context, "skip_final_stream", False))
            facts = {}
            with SessionLocal() as snapshot_db:
                from .tools.local.task import LocalTaskController

                row = LocalTaskController()._row(snapshot_db, tool_context)
                if row:
                    facts = dict(row.facts or {})
            final_after_halt = bool(getattr(tool_context, "final_after_halt", False))
            if not final_after_halt and (
                skip_final
                or halt
                in {
                    "task_paused",
                    "waiting_workspace",
                    "waiting_confirmation",
                    "host_offline",
                    "llm_unavailable",
                    "task_stopped",
                }
            ):
                text = public_assistant_text("", halt, facts)
                parts.append(text)
                yield sse("delta", {"content": text})
                status = "complete"
            else:
                from .tools.local.facts import bump_metric, record, store
                from .tools.local.grounding import fallback_answer, issue_for, repair_prompt, simple_factual
                from .tools.tinyfish.classify import looks_like_browser_task

                grounded = dict(facts)
                for source in getattr(tool_context, "sources", None) or []:
                    grounded = record(
                        grounded,
                        "BROWSER_PAGE",
                        {
                            "url": source.get("final_url") or source.get("url"),
                            "title": source.get("title"),
                            "source_id": source.get("label"),
                        },
                        tool="web_browser",
                    )
                    grounded = record(
                        grounded,
                        "RESEARCH_SOURCE",
                        {
                            "label": source.get("label"),
                            "url": source.get("final_url") or source.get("url"),
                            "authority": source.get("authority"),
                        },
                        tool=source.get("provider") or "web_browser",
                    )
                if looks_like_browser_task(prompt):
                    text = fallback_answer(grounded, prompt)
                    verified = bool((text or "").strip())
                else:
                    text, verified = "", False
                if verified:
                    parts.append(text)
                    yield sse("delta", {"content": text})
                    status = "complete"
                elif simple_factual(prompt) and store(grounded):
                    text = fallback_answer(grounded, prompt)
                    parts.append(text)
                    yield sse("delta", {"content": text})
                    status = "complete"
                else:
                    if looks_like_browser_task(prompt) and not store(grounded):
                        # Nothing was retrieved and there is no typed browser error to report.
                        # Do not fabricate a page answer: the interface shows the real failure,
                        # and the model may still explain what it did or did not receive.
                        yield sse("web_status", {"state": "failed", "code": "browser_no_page_result"})
                    iterator = request.app.state.provider.stream_with_usage(enriched_history, tokens)
                    answer_failed = False
                    try:
                        async for token in iterator:
                            if await request.is_disconnected():
                                cancellation.update(
                                    application_cancelled=True,
                                    cancel_requested_at=now().isoformat(),
                                    client_stream_closed_at=now().isoformat(),
                                )
                                break
                            if first_token_at is None:
                                first_token_at = now()
                            parts.append(token)
                            visible = public_assistant_text(token)
                            if visible:
                                yield sse("delta", {"content": visible})
                        else:
                            status = "complete"
                    except LLMError as error:
                        if not final_after_halt:
                            raise
                        # The tools did the work; only the prose request failed. Say what is
                        # true and leave the task parked for the queue monitor.
                        logger.warning(
                            "final_answer_after_halt_failed code=%s task_id=%s",
                            error.code,
                            getattr(tool_context, "task_id", None),
                        )
                        text = public_assistant_text("", "llm_unavailable", facts)
                        parts.append(text)
                        yield sse("delta", {"content": text})
                        yield sse("task_status", {"state": "WAITING_LLM", "code": error.code})
                        status = "complete"
                        answer_failed = True
                    if status == "complete" and getattr(tool_context, "final_after_halt", False):
                        # The answer exists, so the task may conclude now (it was left parked only
                        # because its prose was missing). A failed answer stays parked instead.
                        with SessionLocal() as db:
                            LocalTaskController().promote_for_answer(db, tool_context)
                            LocalTaskController().conclude(db, tool_context)
                            LocalTaskController().clear_transcript(db, tool_context)
                            row = LocalTaskController()._row(db, tool_context)
                            payload = LocalTaskController().public(db, row) if row else None
                        if payload:
                            # The last task event said WAITING_LLM; this one ends the recovery view.
                            yield sse("task", payload)
                    joined = public_assistant_text("".join(parts))
                    issue = issue_for(joined, grounded, prompt)
                    if not answer_failed and (
                        (not joined.strip() and (store(grounded) or looks_like_browser_task(prompt)))
                        or (issue and status == "complete")
                    ):
                        repaired = ""
                        if joined.strip() and issue:
                            try:
                                repaired = public_assistant_text(
                                    await request.app.state.provider.chat(
                                        [
                                            *enriched_history,
                                            {"role": "assistant", "content": joined},
                                            {"role": "user", "content": repair_prompt(grounded, issue)},
                                        ]
                                    )
                                )
                            except Exception:
                                repaired = ""
                        final = (
                            repaired
                            if repaired and not issue_for(repaired, grounded, prompt)
                            else fallback_answer(grounded, prompt)
                        )
                        if looks_like_browser_task(prompt) and not (final or "").strip():
                            # A server-owned typed failure, never a fabricated page result.
                            final = public_assistant_text("", "browser_no_page_result")
                            yield sse("web_status", {"state": "failed", "code": "browser_no_page_result"})
                        parts = [final]
                        if final != joined:
                            yield sse("delta", {"content": "\n" + final})
                        with SessionLocal() as metric_db:
                            row = LocalTaskController()._row(metric_db, tool_context)
                            if row:
                                row.facts = bump_metric(row.facts, "final_answer_repairs")
                                metric_db.commit()
        except asyncio.CancelledError:
            cancellation["cancel_requested_at"] = now().isoformat()
            cancellation["client_stream_closed_at"] = now().isoformat()
            cancellation["application_cancelled"] = True
            raise
        except Exception as error:
            status = "error"
            logger.warning(
                "generation_failed type=%s request_id=%s",
                type(error).__name__,
                usage_id or assistant_id,
                exc_info=not isinstance(error, LLMError),
            )
            yield sse(
                "error",
                {
                    "detail": str(error)
                    if isinstance(error, LLMError)
                    else "Ответ прерван. Нажмите «Повторить».",
                    "code": error.code if isinstance(error, LLMError) else "stream_interrupted",
                    "request_id": usage_id,
                },
            )
        finally:
            # Shield cleanup so cancellation cannot skip provider closure and persistence.
            import anyio

            if detached:
                # The detached turn owns the chat slot, the assistant message and the generation
                # record. This generator must not cancel it or persist a half-turn.
                return
            if parked:
                request.app.state.generating.discard(chat_id)
                return
            with anyio.CancelScope(shield=True):
                if tool_task and not tool_task.done():
                    tool_task.cancel()
                    try:
                        await tool_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    try:
                        from .tools.local.machine import TERMINAL
                        from .tools.local.task import LocalTaskController
                        from .tools.models import LocalTask

                        with SessionLocal() as db:
                            row = db.get(
                                LocalTask, getattr(tool_context, "task_id", None) if tool_context else None
                            )
                            if row and row.status not in TERMINAL:
                                if row.stop_requested:
                                    LocalTaskController().stop(db, tool_context)
                                elif row.status not in {
                                    "PAUSED",
                                    "WAITING_LLM",
                                    "WAITING_DEVICE",
                                    "WAITING_CONFIRMATION",
                                    "WAITING_WORKSPACE",
                                }:
                                    LocalTaskController().checkpoint(db, tool_context, "INTERRUPTED")
                    except Exception:
                        pass
                try:
                    if iterator:
                        await iterator.aclose()
                    cancellation["provider_stream_closed_at"] = now().isoformat()
                    cancellation["provider_stream_closed"] = True
                except Exception:
                    cancellation["provider_stream_closed"] = False
            try:
                with SessionLocal() as save_db:
                    saved = save_db.get(Message, assistant_id)
                    if saved:
                        saved.content, saved.status = (
                            public_assistant_text(
                                "".join(parts),
                                getattr(tool_context, "task_halt", None)
                                if getattr(tool_context, "skip_final_stream", False)
                                else None,
                                facts,
                            ),
                            status,
                        )
                        saved.first_token_at = first_token_at
                        saved.completed_at = now()
                        saved.ttft_ms = (
                            max(0, int((first_token_at - started).total_seconds() * 1000))
                            if first_token_at and started
                            else None
                        )
                        saved.cancellation = cancellation
                        saved_chat = save_db.get(Chat, chat_id)
                        if saved_chat:
                            saved_chat.updated_at = now()
                        save_db.commit()
                if status == "complete" and getattr(tool_context, "promoted_task_id", None):
                    try:
                        from .tools.local.continue_task import continue_pending
                        from .tools.local.grounding import fallback_answer
                        from .tools.models import LocalTask as QueuedTask

                        results = await continue_pending(
                            make_orchestrator(request.app.state.tools),
                            request.app.state.provider,
                            user_id=user.id,
                            host_online=True,
                            emit=emit,
                        )
                        for result in results:
                            with SessionLocal() as extra_db:
                                queued = extra_db.get(QueuedTask, getattr(result.context, "task_id", None))
                                if not queued:
                                    continue
                                follow = Message(
                                    chat_id=queued.chat_id
                                    or getattr(result.context, "chat_id", None)
                                    or chat_id,
                                    role="assistant",
                                    content=fallback_answer(queued.facts, queued.original_user_request or ""),
                                    status="complete",
                                    created_at=now(),
                                )
                                extra_db.add(follow)
                                extra_db.commit()
                    except Exception:
                        logger.warning("queue_auto_continue_failed", exc_info=True)
                # Only aggregate counts when every planner response reported its usage.
                if planner_usage.get("_planner_calls") and not planner_usage.get("_planner_usage_incomplete"):
                    for upstream, column in (
                        ("prompt_tokens", "input_tokens"),
                        ("completion_tokens", "output_tokens"),
                        ("total_tokens", "total_tokens"),
                    ):
                        if column in tokens and upstream in planner_usage:
                            tokens[column] += planner_usage[upstream]
                if usage_id:
                    compute.finish_generation(usage_id, status, assistant_id, tokens)
            finally:
                request.app.state.generating.discard(chat_id)
                with anyio.CancelScope(shield=True):
                    await request.app.state.presence.publish()
        if status == "complete":
            yield sse("done", {"message_id": assistant_id})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
