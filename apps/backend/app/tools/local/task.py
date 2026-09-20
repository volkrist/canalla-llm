import re
from datetime import timezone

from sqlalchemy import select

from ...config import get_settings
from ...models import Chat, now
from ..contracts import ToolError
from ..models import LocalTask, TaskCheckpoint, TaskStep, ToolRun
from . import machine
from .journal import append_event
from .locks import acquire_write, enqueue_write, owns_write, reconcile_locks, release, waiter_position
from .plan import (
    default_plan,
    looks_like_autonomous,
    looks_like_commit_request,
    looks_like_computer,
    looks_like_push_request,
    looks_like_write,
    needs_research,
    revision_steps,
)
from .workspace import looks_like_coding, workspace_from_settings

STEP_DONE = {"COMPLETED", "SKIPPED", "CANCELLED"}
WRITE_TOOLS = {"write_file", "patch_file", "create_directory", "copy_file", "move_file", "delete_file"}
VERIFY_TOOLS = {"run_python", "run_process", "run_powershell", "git_status", "git_diff"}
RETRYABLE = {
    "timeout",
    "provider_unavailable",
    "provider_timeout",
    "rate_limited",
    "llm_timeout",
    "llm_unavailable",
}


class LocalTaskController:
    """Task-level loop on top of ToolOrchestrator. Not a second agent stack."""

    def attach(self, context):
        settings = getattr(context, "settings", None)
        workspace = workspace_from_settings(settings)
        context.workspace = workspace
        context.files_changed = getattr(context, "files_changed", 0)
        context.task_commands = getattr(context, "task_commands", [])
        prompt = getattr(context, "user_prompt", "") or ""
        context.coding_task = looks_like_coding(prompt)
        git_task = looks_like_commit_request(prompt) or looks_like_push_request(prompt)
        context.autonomous = bool(
            getattr(context, "autonomous", False)
            or looks_like_autonomous(prompt)
            or looks_like_computer(prompt)
            or context.coding_task
            or git_task
        )
        return workspace

    def open(self, db, context):
        prompt = getattr(context, "user_prompt", "") or ""
        git_task = looks_like_commit_request(prompt) or looks_like_push_request(prompt)
        autonomous = bool(
            getattr(context, "autonomous", False)
            or looks_like_autonomous(prompt)
            or looks_like_computer(prompt)
            or getattr(context, "coding_task", False)
            or git_task
        )
        computer = getattr(context, "computer_mode", "off") != "off"
        if not autonomous:
            return None
        workspace = getattr(context, "workspace", None) or workspace_from_settings(context.settings)
        resume_id = getattr(context, "resume_task_id", None)
        if resume_id:
            row = db.get(LocalTask, resume_id)
            if not row or row.user_id != context.user_id:
                raise ToolError("not_found")
            if row.status == machine.PAUSED and not getattr(context, "resuming", False):
                context.task_id = row.id
                context.autonomous = True
                context.task_title = row.title
                return row
            return self._resume_row(db, context, row)
        if getattr(context, "task_id", None):
            existing = db.get(LocalTask, context.task_id)
            if existing and existing.user_id == context.user_id:
                context.task_title = existing.title
                existing.device_id = getattr(context, "assigned_device_id", None) or existing.device_id
                self._bind_runtime(context, existing)
                return existing
        secrets = getattr(context, "secrets", ())
        cfg = get_settings()
        chat = db.get(Chat, context.chat_id) if context.chat_id else None
        write = computer and looks_like_write(prompt)
        computer_only = looks_like_computer(prompt) and not bool(context.coding_task) and not git_task
        from .scope import build_scope

        roots = list(getattr(getattr(context, "settings", None), "workspace_roots", None) or [])
        preview_scope = build_scope(prompt, roots, "pending")
        if git_task and workspace.git_root:
            lock_key = workspace.git_root
        elif computer_only and preview_scope.primary_root:
            lock_key = preview_scope.primary_root
        elif computer_only:
            lock_key = f"user:{context.user_id}:computer"
        else:
            lock_key = workspace.root or f"user:{context.user_id}:computer"
        queued = False
        if write:
            from .locks import busy_writer

            if busy_writer(db, lock_key):
                queued = True
        row = LocalTask(
            user_id=context.user_id,
            chat_id=context.chat_id,
            generation_id=context.generation_id,
            project_id=getattr(chat, "project_id", None),
            device_id=getattr(context, "assigned_device_id", None),
            status=machine.CREATED,
            workspace=lock_key[:500],
            title=(prompt[:80] or "Task"),
            original_user_request=prompt[:16000],
            current_phase=machine.PLANNING,
            tool_budget=min(
                cfg.tools_task_max_calls,
                cfg.tools_task_hard_calls,
                int(
                    getattr(context.limits, "max_calls", cfg.tools_task_max_calls) or cfg.tools_task_max_calls
                ),
                int(
                    getattr(context.limits, "hard_max_calls", cfg.tools_task_hard_calls)
                    or cfg.tools_task_hard_calls
                ),
            ),
            runtime_budget=min(
                cfg.tools_task_max_runtime,
                cfg.tools_task_hard_runtime,
                int(
                    getattr(context.limits, "max_seconds", cfg.tools_task_max_runtime)
                    or cfg.tools_task_max_runtime
                ),
            ),
            file_change_budget=min(
                cfg.tools_task_max_files,
                cfg.tools_task_hard_files,
                int(
                    getattr(context.limits, "max_files_changed", cfg.tools_task_max_files)
                    or cfg.tools_task_max_files
                ),
            ),
            retry_budget=cfg.tools_task_max_retries,
            success_criteria=self._criteria(prompt, context.coding_task, needs_research(prompt)),
            facts={},
            verification={},
            checkpoint={
                "workspace": workspace.root,
                "git_root": workspace.git_root,
                "changed_files": [],
                "commands": [],
                "test_results": [],
                "digests_completed": [],
                "owned_processes": [],
                "owned_browser": False,
                "test_command": list(workspace.test_command),
                "test_via": workspace.test_via,
            },
        )
        db.add(row)
        db.flush()
        if write and not queued:
            try:
                acquire_write(db, context.user_id, lock_key, row.id)
            except ToolError:
                queued = True
        if write and queued:
            enqueue_write(db, context.user_id, lock_key, row.id)
            facts = dict(row.facts or {})
            facts["waiting_workspace"] = True
            row.facts = facts
        extra = _git_snapshot(workspace.git_root)
        if extra:
            row.checkpoint = {**(row.checkpoint or {}), **extra}
        machine.transition(row, machine.PLANNING)
        lowered = prompt.lower()
        steps = default_plan(
            prompt,
            coding=bool(context.coding_task),
            research=needs_research(prompt),
            tor=getattr(context, "tor_mode", "off") != "off" and ("tor" in lowered or ".onion" in lowered),
        )
        self._store_plan(db, row, steps)
        if queued:
            machine.transition(row, machine.WAITING_WORKSPACE)
        else:
            machine.transition(row, machine.READY)
        row.updated_at = now()
        append_event(db, row.id, "TASK_CREATED", {"title": row.title}, secrets)
        append_event(
            db, row.id, "PLAN_CREATED", {"revision": row.plan_revision, "steps": len(steps)}, secrets
        )
        if write and not queued:
            append_event(db, row.id, "WORKSPACE_LOCK_ACQUIRED", {"workspace": lock_key}, secrets)
        if queued:
            append_event(db, row.id, "WAITING_WORKSPACE", {"workspace": lock_key}, secrets)
        db.commit()
        context.task_id = row.id
        context.autonomous = autonomous
        context.task_title = row.title
        from .scope import build_scope

        scope = build_scope(prompt, roots, row.id)
        context.task_scope = scope
        facts = dict(row.facts or {})
        facts["scope"] = {"primary_root": scope.primary_root, "scratch": scope.scratch}
        facts["autonomy"] = "HIGH"
        facts["research_depth"] = "DEEP"
        row.facts = facts
        db.commit()
        self._bind_runtime(context, row)
        if queued:
            context.skip_final_stream = True
            context.task_halt = "waiting_workspace"
        return row

    def _resume_row(self, db, context, row):
        secrets = getattr(context, "secrets", ())
        row.pause_requested = False
        row.stop_requested = False
        row.generation_id = context.generation_id
        row.device_id = getattr(context, "assigned_device_id", None) or row.device_id
        facts = dict(row.facts or {})
        facts.pop("promoted_from_queue", None)
        if row.status == machine.WAITING_WORKSPACE:
            if owns_write(db, row.workspace, row.id):
                machine.transition(row, machine.RECOVERING)
                machine.transition(row, machine.READY)
            else:
                context.skip_final_stream = True
                context.task_halt = "waiting_workspace"
                row.facts = facts
                db.commit()
                context.task_id = row.id
                context.autonomous = True
                context.task_title = row.title
                return row
        elif row.status in {
            machine.INTERRUPTED,
            machine.PAUSED,
            machine.WAITING_LLM,
            machine.WAITING_DEVICE,
            machine.STOPPED,
            machine.FAILED,
        }:
            facts["task_continuation"] = True
            if row.status != machine.PAUSED:
                machine.transition(row, machine.RECOVERING)
            machine.transition(row, machine.READY)
        row.facts = facts
        row.updated_at = now()
        append_event(db, row.id, "RESUMED", {"from": row.current_phase}, secrets)
        db.commit()
        context.task_id = row.id
        context.autonomous = True
        context.task_title = row.title
        context.files_changed = row.files_changed
        context.task_commands = list((row.checkpoint or {}).get("commands") or [])
        context.completed_digests = list((row.checkpoint or {}).get("digests_completed") or [])
        self._bind_runtime(context, row)
        return row

    def _bind_runtime(self, context, row):
        from .scope import build_scope

        facts = row.facts or {}
        roots = list(getattr(getattr(context, "settings", None), "workspace_roots", None) or [])
        scope = build_scope(
            row.original_user_request or getattr(context, "user_prompt", "") or "", roots, row.id
        )
        stored = facts.get("scope") or {}
        if stored.get("primary_root"):
            scope.primary_root = stored["primary_root"]
        if stored.get("scratch"):
            scope.scratch = stored["scratch"]
        context.task_scope = scope
        owned = list((row.checkpoint or {}).get("owned_processes") or [])
        if owned:
            context.owned_process = owned[-1]
        else:
            context.owned_process = self.last_owned_process(row.user_id)

    def last_owned_process(self, user_id: str):
        from ...database import SessionLocal

        with SessionLocal() as db:
            rows = db.scalars(
                select(LocalTask)
                .where(LocalTask.user_id == user_id)
                .order_by(LocalTask.updated_at.desc())
                .limit(8)
            ).all()
            for row in rows:
                owned = list((row.checkpoint or {}).get("owned_processes") or [])
                if owned:
                    return owned[-1]
        return None

    def _store_plan(self, db, row, steps, revision=1):
        row.plan_revision = revision
        for index, item in enumerate(steps):
            db.add(
                TaskStep(
                    task_id=row.id,
                    position=index,
                    title=item["title"][:200],
                    description=item.get("description") or "",
                    status=item.get("status") or "PENDING",
                    tool_category=item.get("tool_category") or "",
                    depends_on=item.get("depends_on") or [],
                    max_attempts=int(item.get("max_attempts") or 3),
                    verification_required=bool(item.get("verification_required")),
                    key=item.get("key") or "",
                )
            )

    def _criteria(self, prompt, coding, research):
        items = ["Original request is addressed"]
        if coding:
            items.extend(
                [
                    "All affected tests pass",
                    "No uncommitted unintended changes",
                    "Final git status and git diff reviewed",
                ]
            )
        if research:
            items.append("Claims are backed by collected sources")
        return {"all": items, "prompt": prompt[:500]}

    def note_command(self, context, name, digest_value, metadata=None):
        commands = getattr(context, "task_commands", None)
        if commands is None:
            context.task_commands = commands = []
        commands.append({"tool": name, "digest": digest_value})
        files = int((metadata or {}).get("files_changed") or 0)
        if name in WRITE_TOOLS and not files:
            files = 1 if (metadata or {}).get("after_sha256") else 0
        context.files_changed = getattr(context, "files_changed", 0) + files
        limits = context.limits
        maximum = getattr(limits, "max_files_changed", 20)
        if maximum and context.files_changed > maximum:
            raise ToolError("task_file_limit")
        task_id = getattr(context, "task_id", None)
        if task_id:
            completed = getattr(context, "completed_digests", None)
            if completed is None:
                context.completed_digests = completed = []
            if digest_value and digest_value not in completed:
                completed.append(digest_value)

    def checkpoint(self, db, context, status="EXECUTING"):
        task_id = getattr(context, "task_id", None)
        if not task_id:
            return
        row = db.get(LocalTask, task_id)
        if not row or row.user_id != context.user_id:
            return
        if row.status not in machine.TERMINAL:
            machine.transition(row, status)
        self._refresh_checkpoint(db, context, row)
        row.updated_at = now()
        db.commit()

    def _refresh_checkpoint(self, db, context, row):
        runs = []
        if context.generation_id or row.id:
            query = select(ToolRun).where(ToolRun.user_id == context.user_id)
            if row.id:
                query = query.where(
                    (ToolRun.task_id == row.id) | (ToolRun.generation_id == context.generation_id)
                )
            runs = [
                {
                    "tool": item.tool_name,
                    "status": item.status,
                    "digest": item.input_digest,
                    "path": (item.input_summary or {}).get("target")
                    or (item.input_summary or {}).get("path"),
                    "before": (item.result_metadata or {}).get("before_sha256"),
                    "after": (item.result_metadata or {}).get("after_sha256"),
                    "exit_code": (item.result_metadata or {}).get("exit_code"),
                }
                for item in db.scalars(query)
            ]
        elapsed = 0
        if row.started_at:
            started = row.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed = int(max(0, (now() - started).total_seconds()))
        row.elapsed_runtime = elapsed
        row.tool_calls_used = max(row.tool_calls_used, int(getattr(context.limits, "calls", 0)))
        row.files_changed = getattr(context, "files_changed", row.files_changed)
        row.generation_id = context.generation_id or row.generation_id
        completed = [
            item["digest"] for item in runs if item.get("status") == "completed" and item.get("digest")
        ]
        row.checkpoint = {
            **(row.checkpoint or {}),
            "changed_files": [item for item in runs if item.get("before") or item.get("after")],
            "commands": list(getattr(context, "task_commands", [])),
            "files_changed": row.files_changed,
            "digests_completed": completed,
            "git_root": getattr(getattr(context, "workspace", None), "git_root", None),
        }
        snapshot = TaskCheckpoint(
            task_id=row.id,
            plan_revision=row.plan_revision,
            current_step=row.current_step or "",
            completed_steps=[step.key for step in self.steps(db, row.id) if step.status == "COMPLETED"],
            workspace_state={
                "workspace": row.workspace,
                "git_head": (row.checkpoint or {}).get("git_head"),
                "changed_files": row.checkpoint.get("changed_files") if row.checkpoint else [],
            },
            verification=row.verification or {},
        )
        db.add(snapshot)
        append_event(
            db, row.id, "CHECKPOINT_CREATED", {"status": row.status}, getattr(context, "secrets", ())
        )

    def finish(self, db, context, status="COMPLETED"):
        task_id = getattr(context, "task_id", None)
        if not task_id:
            return
        row = db.get(LocalTask, task_id)
        if not row or row.user_id != context.user_id:
            return
        if row.status not in machine.TERMINAL:
            try:
                machine.transition(row, status)
            except ToolError:
                row.status = status
                row.current_phase = status
                row.finished_at = now()
        row.updated_at = now()
        kind = "COMPLETED" if status == "COMPLETED" else "FAILED" if status == "FAILED" else "STOPPED"
        append_event(db, row.id, kind, {"status": status}, getattr(context, "secrets", ()))
        if status in machine.TERMINAL:
            promoted = release(db, row.id)
            if promoted:
                from .continue_task import mark_auto_continue

                mark_auto_continue(promoted)
                if context is not None:
                    context.promoted_task_id = promoted.id
        from .scope import cleanup_scratch

        cleanup_scratch(row.id)
        db.commit()

    def steps(self, db, task_id):
        return db.scalars(
            select(TaskStep).where(TaskStep.task_id == task_id).order_by(TaskStep.position, TaskStep.id)
        ).all()

    def events(self, db, task_id, limit=80):
        from ..models import TaskEvent

        return db.scalars(
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.created_at.desc(), TaskEvent.id.desc())
            .limit(limit)
        ).all()

    def blocked(self, context) -> str | None:
        task_id = getattr(context, "task_id", None)
        if not task_id:
            return None
        from ...database import SessionLocal

        with SessionLocal() as db:
            row = db.get(LocalTask, task_id)
            if not row:
                return None
            if row.stop_requested or row.status in {machine.STOPPING, machine.STOPPED}:
                return "task_stopped"
            if row.pause_requested or row.status == machine.PAUSED:
                return "task_paused"
            if row.status == machine.WAITING_WORKSPACE:
                return "waiting_workspace"
            if row.status == machine.WAITING_LLM:
                return "llm_unavailable"
            if row.status == machine.WAITING_DEVICE:
                return "host_offline"
            cfg = get_settings()
            call_ceiling = min(
                row.tool_budget,
                cfg.tools_task_hard_calls,
                int(getattr(context.limits, "max_calls", row.tool_budget) or row.tool_budget),
                int(
                    getattr(context.limits, "hard_max_calls", cfg.tools_task_hard_calls)
                    or cfg.tools_task_hard_calls
                ),
            )
            if row.tool_calls_used >= call_ceiling:
                return "task_budget"
            elapsed = 0
            if row.started_at:
                started = row.started_at
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                elapsed = int(max(0, (now() - started).total_seconds()))
            if elapsed >= min(row.runtime_budget, cfg.tools_task_hard_runtime):
                return "task_runtime_limit"
            if row.files_changed >= min(row.file_change_budget, cfg.tools_task_hard_files):
                return "task_file_limit"
            from ..policy import WebSettings
            from ..tinyfish.budget import exhausted

            prefs = getattr(context, "settings", None) or WebSettings()
            if exhausted(row.checkpoint, cfg, prefs):
                return "task_budget"
        return None

    def preflight(self, db, context, name, digest_value):
        row = self._row(db, context)
        if not row:
            return
        code = self.blocked(context)
        if code:
            if code == "task_paused":
                self.pause(db, context)
            elif code == "task_stopped":
                self.stop(db, context)
            elif code in {"task_budget", "task_runtime_limit", "task_file_limit"}:
                row.last_error = code
                machine.transition(row, machine.FAILED)
                append_event(db, row.id, "BUDGET_EXHAUSTED", {"code": code}, getattr(context, "secrets", ()))
                release(db, row.id)
                db.commit()
            raise ToolError(code)
        same = [
            item
            for item in (row.checkpoint or {}).get("commands") or []
            if item.get("digest") == digest_value and item.get("tool") == name
        ]
        if len(same) >= get_settings().tools_task_max_same_payload + 1:
            raise ToolError("task_budget")
        prompt = row.original_user_request or getattr(context, "user_prompt", "") or ""
        settings = getattr(context, "settings", None)
        if name == "git_commit":
            if not (looks_like_commit_request(prompt) or getattr(settings, "auto_commit", False)):
                raise ToolError("git_commit_not_requested")
            if getattr(context, "coding_task", False):
                verification = row.verification or {}
                if verification.get("tests") != "passed" or not verification.get("git_reviewed"):
                    raise ToolError("git_commit_unverified")
        if name == "git_push":
            if not (looks_like_push_request(prompt) or getattr(settings, "allow_push", False)):
                raise ToolError("git_push_not_requested")
            if getattr(context, "coding_task", False):
                verification = row.verification or {}
                if verification.get("tests") != "passed" or not verification.get("git_reviewed"):
                    raise ToolError("git_push_unverified")

    def observe_tool(self, db, context, name, output, error=None, arguments=None):
        row = self._row(db, context)
        if not row:
            return
        secrets = getattr(context, "secrets", ())
        row.tool_calls_used = max(row.tool_calls_used, int(getattr(context.limits, "calls", 0)))
        meta = (output or {}).get("metadata") or {}
        if name in WRITE_TOOLS and (meta.get("after_sha256") or meta.get("files_changed")):
            append_event(
                db, row.id, "FILE_CHANGED", {"tool": name, "path": str(meta.get("path") or "")[:200]}, secrets
            )
        if error == "conflict":
            facts = dict(row.facts or {})
            retries = int(facts.get("conflict_retries") or 0) + 1
            facts["conflict_retries"] = retries
            facts["conflict_path"] = str((arguments or {}).get("path") or meta.get("path") or "")
            row.facts = facts
            row.last_error = "conflict"
            append_event(db, row.id, "CONFLICT", {"tool": name, "retries": retries}, secrets)
            self._advance_step(db, row, name, failed=True, summary="conflict: re-read before patching")
            if retries <= 3 and facts.get("conflict_path"):
                machine.transition(row, machine.INSPECTING)
            else:
                machine.transition(row, machine.CONFLICT)
        elif error:
            if name == "web_browser":
                from .facts import from_tool as facts_from_tool

                row.facts = facts_from_tool(
                    row.facts or {},
                    name,
                    {"error": error, "metadata": meta, "text": ""},
                    arguments or {},
                    getattr(context, "run_id", None),
                )
            append_event(db, row.id, "TOOL_COMPLETED", {"tool": name, "error": error}, secrets)
            if error in RETRYABLE:
                row.retry_count += 1
                append_event(db, row.id, "RETRY", {"tool": name, "error": error}, secrets)
                if row.retry_count >= row.retry_budget:
                    row.last_error = error
                    machine.transition(row, machine.FAILED)
                    release(db, row.id)
                else:
                    machine.transition(row, machine.RETRYING)
            elif error == "confirmation_required":
                machine.transition(row, machine.WAITING_CONFIRMATION)
                append_event(db, row.id, "CONFIRMATION_REQUESTED", {"tool": name}, secrets)
            elif error == "host_offline":
                self.waiting_device(db, context)
            elif error == "workspace_scope":
                from .facts import bump_metric

                row.facts = bump_metric(row.facts, "workspace_scope_violations_blocked")
                self._advance_step(db, row, name, failed=True, summary=error)
            else:
                self._advance_step(db, row, name, failed=True, summary=error)
        else:
            append_event(db, row.id, "TOOL_COMPLETED", {"tool": name}, secrets)
            self._capture_facts(row, name, output, arguments=arguments, context=context)
            self._advance_step(
                db, row, name, failed=False, summary=str((output or {}).get("text") or "")[:200]
            )
            phase = machine.VERIFYING if name in VERIFY_TOOLS else machine.EXECUTING
            if name in {"web_search", "web_fetch", "tor_search", "tor_fetch", "tor_browser"}:
                phase = machine.RESEARCHING
            elif name in {"list_directory", "read_file", "search_code"}:
                phase = machine.INSPECTING
            if row.status not in machine.TERMINAL | {
                machine.PAUSED,
                machine.WAITING_CONFIRMATION,
                machine.WAITING_DEVICE,
                machine.WAITING_LLM,
                machine.WAITING_WORKSPACE,
            }:
                machine.transition(row, phase)
        row.updated_at = now()
        db.commit()

    def _capture_facts(self, row, name, output, arguments=None, context=None):
        from .facts import bump_metric, from_tool

        facts = from_tool(
            row.facts or {}, name, output or {}, arguments or {}, getattr(context, "run_id", None)
        )
        meta = (output or {}).get("metadata") or {}
        text = str((output or {}).get("text") or "")
        args = arguments if isinstance(arguments, dict) else {}
        purpose = str(args.get("purpose") or "")
        argv = " ".join(str(item) for item in (args.get("argv") or []))
        testish = bool(
            re.search(
                r"(?i)(pytest|unittest|cargo test|npm test|go test|verify project tests)",
                f"{purpose} {argv}",
            )
            or "-m pytest" in argv
        )
        if (
            meta.get("exit_code") is not None
            and name in {"run_python", "run_process", "run_powershell"}
            and testish
        ):
            facts["last_exit_code"] = meta.get("exit_code")
            facts["tests_passed"] = meta.get("exit_code") == 0
            if facts.get("baseline_tests") is None:
                facts["baseline_tests"] = facts["tests_passed"]
            facts["verification_stale"] = False
        failures = (output or {}).get("failures") or []
        if failures:
            facts["last_failures"] = failures[:8]
        if meta.get("after_sha256"):
            facts.setdefault("file_hashes", {})
            path = str(meta.get("path") or "")
            if path:
                facts["file_hashes"][path] = meta.get("after_sha256")
        if name == "git_status":
            facts["git_status"] = text[:400]
        if name == "git_diff":
            facts["git_diff_seen"] = True
        if (output or {}).get("sources"):
            facts["source_count"] = facts.get("source_count", 0) + len(output["sources"])
        facts = bump_metric(facts, "tool_calls_total")
        facts = bump_metric(
            facts, "tool_calls_success" if not (output or {}).get("error") else "tool_calls_failed"
        )
        facts = bump_metric(facts, "verified_facts", 0)
        facts["verified_facts"] = len(facts.get("verified") or [])
        row.facts = facts
        pid = meta.get("pid")
        if not pid:
            match = re.search(r"pid=(\d+)", text)
            pid = int(match.group(1)) if match else None
        run_id = meta.get("tool_run_id") or getattr(context, "run_id", None)
        if pid or (run_id and name in {"run_python", "run_process", "run_powershell"}):
            checkpoint = dict(row.checkpoint or {})
            owned = list(checkpoint.get("owned_processes") or [])
            owned.append({"pid": pid, "tool_run_id": run_id, "started_by_alex": True})
            checkpoint["owned_processes"] = owned[-8:]
            row.checkpoint = checkpoint
            if context is not None:
                context.owned_process = owned[-1]
        if name in WRITE_TOOLS:
            facts["tests_passed"] = False
            facts["verification_stale"] = True
            facts.pop("conflict_path", None)
        row.facts = facts
        verification = dict(row.verification or {})
        if facts.get("verification_stale"):
            verification["tests"] = "stale"
        elif facts.get("tests_passed"):
            verification["tests"] = "passed"
        elif facts.get("last_exit_code") not in (None, 0) and testish:
            verification["tests"] = "failed"
        if facts.get("git_diff_seen"):
            verification["git_reviewed"] = True
        row.verification = verification

    def _advance_step(self, db, row, name, failed, summary):
        steps = self.steps(db, row.id)
        current = next((step for step in steps if step.status == "RUNNING"), None)
        if current is None:
            current = next((step for step in steps if step.status in {"PENDING", "WAITING"}), None)
            if current:
                current.status = "RUNNING"
                current.started_at = now()
                current.attempts += 1
                row.current_step = current.key or current.title
                append_event(db, row.id, "STEP_STARTED", {"step": current.title})
        if current is None:
            return
        current.result_summary = (summary or "")[:500]
        category = current.tool_category
        mapped = {
            "local_fs": name in WRITE_TOOLS | {"list_directory", "read_file", "search_code", "search_files"},
            "local_git": name.startswith("git_"),
            "local_process": name in {"run_python", "run_process", "run_powershell"},
            "search": name in {"web_search", "tor_search"},
            "fetch": name in {"web_fetch", "tor_fetch", "tor_browser"},
            "tor_search": name.startswith("tor_"),
            "verify": name in VERIFY_TOOLS,
            "plan": True,
            "execute": True,
        }
        if failed:
            if current.attempts >= current.max_attempts:
                current.status = "FAILED"
                current.finished_at = now()
                append_event(db, row.id, "STEP_FAILED", {"step": current.title})
            else:
                current.status = "WAITING"
            return
        if mapped.get(category) or category in {"plan", "execute", "verify"}:
            if (
                name in WRITE_TOOLS
                and category == "local_fs"
                and current.key in {"inspect", "diagnose", "discover"}
            ):
                return
            if name in {"list_directory", "read_file"} and current.key == "fix":
                return
            current.status = "COMPLETED"
            current.finished_at = now()
            append_event(db, row.id, "STEP_COMPLETED", {"step": current.title})

    def next_forced_action(self, context):
        task_id = getattr(context, "task_id", None)
        if not task_id or not getattr(context, "autonomous", False):
            return None
        workspace = getattr(context, "workspace", None)
        if not workspace or not workspace.root:
            return None
        from app.database import SessionLocal

        with SessionLocal() as db:
            row = db.get(LocalTask, task_id)
            if not row or row.status in machine.TERMINAL | {
                machine.PAUSED,
                machine.STOPPED,
                machine.WAITING_DEVICE,
                machine.WAITING_LLM,
                machine.WAITING_CONFIRMATION,
                machine.WAITING_WORKSPACE,
                machine.CONFLICT,
            }:
                return None
            facts = row.facts or {}
            command = list((row.checkpoint or {}).get("test_command") or workspace.test_command)
            via = (row.checkpoint or {}).get("test_via") or workspace.test_via
            if getattr(context, "computer_mode", "off") == "off":
                return None
            commands = (row.checkpoint or {}).get("commands") or []
            last_tool = commands[-1]["tool"] if commands else ""
            write_tools = {"write_file", "patch_file", "create_directory"}
            conflict_path = (facts or {}).get("conflict_path")
            if conflict_path and int(facts.get("conflict_retries") or 0) <= 3 and last_tool != "read_file":
                return (
                    "read_file",
                    {"path": conflict_path, "purpose": "reread current file after stale patch"},
                )
            tests_ok = facts.get("tests_passed") is True and (row.verification or {}).get("tests") == "passed"
            if context.coding_task and not tests_ok:
                if last_tool in {"run_python", "run_process"}:
                    return None
                tested = any(item.get("tool") in {"run_python", "run_process"} for item in commands)
                if tested and last_tool not in write_tools:
                    return None
                if via == "run_python":
                    return (
                        "run_python",
                        {"argv": command, "cwd": workspace.root, "purpose": "Verify project tests"},
                    )
                return (
                    "run_process",
                    {
                        "executable": command[0],
                        "argv": command[1:],
                        "cwd": workspace.root,
                        "purpose": "Verify project tests",
                    },
                )
            if context.coding_task and tests_ok and not facts.get("git_diff_seen"):
                if last_tool != "git_status":
                    return ("git_status", {"cwd": workspace.root, "purpose": "Final git review"})
                return ("git_diff", {"cwd": workspace.root, "purpose": "Final git review"})
        return None

    def maybe_revise_plan(self, db, context):
        row = self._row(db, context)
        if not row:
            return
        facts = row.facts or {}
        if (
            facts.get("last_progress_error") == "no_progress"
            and (row.checkpoint or {}).get("revised_for") != "no_progress"
        ):
            from .facts import bump_metric

            row.facts = bump_metric(facts, "replans")
            row.plan_revision += 1
            row.checkpoint = {**(row.checkpoint or {}), "revised_for": "no_progress"}
            append_event(
                db,
                row.id,
                "PLAN_UPDATED",
                {"revision": row.plan_revision, "reason": "no_progress"},
                getattr(context, "secrets", ()),
            )
            db.commit()
            facts = row.facts or {}
        if not getattr(context, "coding_task", False):
            return
        last = ((row.checkpoint or {}).get("commands") or [])[-1:]
        if not last or last[0].get("tool") not in {"run_python", "run_process"}:
            return
        if facts.get("tests_passed") is False and facts.get("last_failures"):
            signature = str(facts["last_failures"][:1])
            if (row.checkpoint or {}).get("revised_for") == signature:
                return
            cfg = get_settings()
            if row.plan_revision >= cfg.tools_task_max_plan_revisions:
                return
            extra = revision_steps(str(facts["last_failures"][:1]))
            start = len(self.steps(db, row.id))
            row.plan_revision += 1
            row.checkpoint = {**(row.checkpoint or {}), "revised_for": signature}
            for index, item in enumerate(extra):
                db.add(
                    TaskStep(
                        task_id=row.id,
                        position=start + index,
                        title=item["title"],
                        description=item["description"],
                        status="PENDING",
                        tool_category=item["tool_category"],
                        depends_on=[],
                        verification_required=item.get("verification_required") or False,
                        key=item["key"],
                    )
                )
            append_event(
                db, row.id, "PLAN_UPDATED", {"revision": row.plan_revision}, getattr(context, "secrets", ())
            )
            db.commit()

    def pause(self, db, context=None, task=None, user_id=None):
        row = task or self._row(db, context)
        if not row:
            return None
        if user_id and row.user_id != user_id:
            raise ToolError("not_found")
        row.pause_requested = True
        if row.status == machine.WAITING_WORKSPACE:
            append_event(db, row.id, "PAUSED", {"queued": True})
            row.updated_at = now()
            db.commit()
            return row
        if row.status not in machine.TERMINAL:
            machine.transition(row, machine.PAUSED)
        append_event(db, row.id, "PAUSED", {})
        row.updated_at = now()
        db.commit()
        return row

    def resume(self, db, task, user_id, context=None):
        if task.user_id != user_id:
            raise ToolError("not_found")
        task.pause_requested = False
        task.stop_requested = False
        if task.status == machine.WAITING_WORKSPACE:
            if owns_write(db, task.workspace, task.id):
                machine.transition(task, machine.RECOVERING)
                machine.transition(task, machine.READY)
        elif task.status in {
            machine.PAUSED,
            machine.INTERRUPTED,
            machine.STOPPED,
            machine.WAITING_LLM,
            machine.WAITING_DEVICE,
        }:
            if task.status in {
                machine.STOPPED,
                machine.INTERRUPTED,
                machine.WAITING_LLM,
                machine.WAITING_DEVICE,
            }:
                machine.transition(task, machine.RECOVERING)
            machine.transition(task, machine.READY)
        append_event(db, task.id, "RESUMED", {})
        task.updated_at = now()
        db.commit()
        return task

    def stop(self, db, context=None, task=None, user_id=None):
        row = task or self._row(db, context)
        if not row:
            return None
        if user_id and row.user_id != user_id:
            raise ToolError("not_found")
        row.stop_requested = True
        if row.status not in machine.TERMINAL:
            if machine.can_transition(row.status, machine.STOPPING):
                machine.transition(row, machine.STOPPING)
            machine.transition(row, machine.STOPPED)
        for step in self.steps(db, row.id):
            if step.status in {"PENDING", "RUNNING", "WAITING"}:
                step.status = "CANCELLED"
        append_event(db, row.id, "STOPPED", {})
        release(db, row.id)
        row.updated_at = now()
        db.commit()
        return row

    def consume_tinyfish(self, db, context, definition, result):
        row = self._row(db, context)
        if not row:
            return
        from ..tinyfish.budget import load_budget, store_budget

        budget = load_budget(row.checkpoint)
        secrets = getattr(context, "secrets", ())
        cost = float(result.cost_estimate or 0)
        if definition.capability == "agent":
            steps = int((result.metadata or {}).get("steps") or 0)
            budget.agent_runs += 1
            budget.agent_steps += steps
            budget.paid_spent += cost
            budget.active_agent_run_id = ""
            append_event(
                db,
                row.id,
                "TINYFISH_AGENT_COMPLETED",
                {"steps": steps, "estimated_provider_cost": cost},
                secrets,
            )
        elif definition.name in {"web_browser", "browser_start"}:
            session_id = (result.metadata or {}).get("session_id") or ""
            if definition.name == "browser_start" or (result.metadata or {}).get("started_by_alex"):
                if not budget.active_browser_session_id:
                    budget.browser_sessions += 1
                budget.active_browser_session_id = session_id
                append_event(db, row.id, "TINYFISH_BROWSER_STARTED", {"session": session_id[:12]}, secrets)
                append_event(db, row.id, "TINYFISH_BROWSER_CONNECTED", {"session": session_id[:12]}, secrets)
            if (result.metadata or {}).get("local_controller_stopped") or (result.metadata or {}).get(
                "supplier_stop_confirmed"
            ):
                seconds = float((result.metadata or {}).get("duration_seconds") or 0)
                budget.browser_seconds += seconds
                budget.paid_spent += cost
                budget.active_browser_session_id = ""
                append_event(
                    db,
                    row.id,
                    "TINYFISH_BROWSER_CLOSED",
                    {"estimated_provider_cost": cost},
                    secrets,
                )
        row.checkpoint = store_budget(row.checkpoint, budget)
        append_event(
            db,
            row.id,
            "BUDGET_UPDATED",
            {"paid_spent": round(budget.paid_spent, 4), "agent_steps": budget.agent_steps},
            secrets,
        )
        db.commit()

    def waiting_device(self, db, context):
        row = self._row(db, context)
        if not row or row.status in machine.TERMINAL:
            return
        machine.transition(row, machine.WAITING_DEVICE)
        row.last_error = "host_offline"
        append_event(db, row.id, "WAITING_DEVICE", {})
        row.updated_at = now()
        db.commit()

    def waiting_llm(self, db, context, code="llm_unavailable"):
        row = self._row(db, context)
        if not row or row.status in machine.TERMINAL:
            return
        machine.transition(row, machine.WAITING_LLM)
        row.last_error = code
        append_event(db, row.id, "WAITING_LLM", {"code": code})
        row.updated_at = now()
        db.commit()

    def conclude(self, db, context):
        row = self._row(db, context)
        if not row:
            return
        if row.stop_requested:
            self.stop(db, context)
            return
        if row.pause_requested:
            self.pause(db, context)
            return
        if row.status in {
            machine.WAITING_CONFIRMATION,
            machine.WAITING_DEVICE,
            machine.WAITING_LLM,
            machine.WAITING_WORKSPACE,
            machine.PAUSED,
            machine.INTERRUPTED,
            machine.CONFLICT,
        }:
            return
        if row.status in machine.TERMINAL:
            return
        coding = looks_like_coding(row.original_user_request) or bool(getattr(context, "coding_task", False))
        if not getattr(context, "autonomous", False) and not coding:
            machine.transition(row, machine.COMPLETED)
            append_event(db, row.id, "COMPLETED", {})
            release(db, row.id)
            row.updated_at = now()
            db.commit()
            return
        review = self.final_review(row)
        row.completion_summary = review["summary"]
        if not review["ok"]:
            exhausted = self.blocked(context) in {"task_budget", "task_runtime_limit", "task_file_limit"}
            row.last_error = "task_budget" if exhausted else review["reason"]
            if exhausted:
                append_event(db, row.id, "BUDGET_EXHAUSTED", {"code": row.last_error})
            self._finish_open_steps(db, row, success=False)
            machine.transition(row, machine.FAILED)
            append_event(db, row.id, "FAILED", {"reason": row.last_error})
            promoted = release(db, row.id)
        else:
            self._finish_open_steps(db, row, success=True)
            machine.transition(row, machine.COMPLETED)
            append_event(db, row.id, "COMPLETED", {})
            promoted = release(db, row.id)
        if promoted:
            from .continue_task import mark_auto_continue

            mark_auto_continue(promoted)
            if context is not None:
                context.promoted_task_id = promoted.id
        from .scope import cleanup_scratch

        cleanup_scratch(row.id)
        row.updated_at = now()
        db.commit()

    def _finish_open_steps(self, db, row, *, success: bool):
        for step in self.steps(db, row.id):
            if step.status in STEP_DONE | {"FAILED"}:
                continue
            if success:
                if step.status == "RUNNING":
                    step.status = "COMPLETED"
                    append_event(db, row.id, "STEP_COMPLETED", {"step": step.title})
                else:
                    step.status = "SKIPPED"
            else:
                step.status = "CANCELLED"
            if step.finished_at is None:
                step.finished_at = now()

    def final_review(self, row) -> dict:
        facts = row.facts or {}
        verification = row.verification or {}
        missing = []
        if row.original_user_request and not (row.tool_calls_used or facts):
            missing.append("no_actions")
        if looks_like_coding(row.original_user_request) and verification.get("tests") != "passed":
            missing.append(
                "verification_required"
                if (facts.get("verification_stale") or verification.get("tests") == "stale")
                else "tests_not_verified"
            )
        if looks_like_coding(row.original_user_request) and facts.get("verification_stale"):
            if "verification_required" not in missing:
                missing.append("verification_required")
        if looks_like_coding(row.original_user_request):
            commands = (row.checkpoint or {}).get("commands") or []
            changed = any(item.get("tool") in WRITE_TOOLS for item in commands) or bool(
                facts.get("file_hashes")
            )
            if facts.get("baseline_tests") is False and not changed:
                missing.append("no_effective_change")
        if looks_like_coding(row.original_user_request) and not verification.get("git_reviewed"):
            missing.append("git_not_reviewed")
        if needs_research(row.original_user_request) and not (
            facts.get("source_count")
            or any(item.get("kind") == "RESEARCH_SOURCE" for item in facts.get("verified") or [])
        ):
            missing.append("sources_missing")
        report = {
            "files": [
                item
                for item in facts.get("verified") or []
                if str(item.get("kind") or "").startswith("FILE_")
            ],
            "system": [item for item in facts.get("verified") or [] if item.get("kind") == "SYSTEM_INFO"],
            "hash": [item for item in facts.get("verified") or [] if item.get("kind") == "HASH_RESULT"],
            "process": [
                item
                for item in facts.get("verified") or []
                if item.get("kind") in {"PROCESS_STARTED", "PROCESS_STOPPED"}
            ],
            "sources": [
                item
                for item in facts.get("verified") or []
                if item.get("kind") in {"BROWSER_PAGE", "RESEARCH_SOURCE"}
            ],
        }
        row.verification = {**(row.verification or {}), "report": report}
        ok = not missing
        summary = (
            "Completed: " + "; ".join((row.success_criteria or {}).get("all") or ["request addressed"])
            if ok
            else "Incomplete: " + ", ".join(missing)
        )
        return {"ok": ok, "reason": missing[0] if missing else "", "summary": summary[:1500]}

    def _verified(self, row):
        return self.final_review(row)["ok"]

    def _row(self, db, context):
        task_id = getattr(context, "task_id", None) if context is not None else None
        if not task_id:
            return None
        row = db.get(LocalTask, task_id)
        if not row or (context and row.user_id != context.user_id):
            return None
        return row

    def public(self, db, row, include_events=False):
        steps = self.steps(db, row.id)
        payload = {
            "id": row.id,
            "title": row.title,
            "status": row.status,
            "chat_id": row.chat_id,
            "project_id": row.project_id,
            "workspace": row.workspace,
            "current_step": row.current_step,
            "current_phase": row.current_phase,
            "plan_revision": row.plan_revision,
            "tool_calls_used": row.tool_calls_used,
            "tool_budget": row.tool_budget,
            "files_changed": row.files_changed,
            "file_change_budget": row.file_change_budget,
            "elapsed_runtime": row.elapsed_runtime,
            "runtime_budget": row.runtime_budget,
            "retry_count": row.retry_count,
            "last_error": row.last_error,
            "completion_summary": row.completion_summary,
            "pause_requested": row.pause_requested,
            "stop_requested": row.stop_requested,
            "created_at": row.started_at,
            "updated_at": row.updated_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "success_criteria": row.success_criteria,
            "verification": row.verification,
            "message": status_message(row),
            "queue_position": waiter_position(db, row.id),
            "promoted_from_queue": bool((row.facts or {}).get("promoted_from_queue")),
            "metrics": (row.facts or {}).get("metrics") or {},
            "tinyfish": (row.checkpoint or {}).get("tinyfish") or {},
            "steps": [
                {
                    "id": step.id,
                    "title": step.title,
                    "description": step.description,
                    "status": step.status,
                    "tool_category": step.tool_category,
                    "verification_required": step.verification_required,
                    "attempts": step.attempts,
                    "result_summary": step.result_summary,
                    "key": step.key,
                }
                for step in steps
            ],
        }
        if include_events:
            payload["events"] = [
                {"id": item.id, "kind": item.kind, "payload": item.payload, "created_at": item.created_at}
                for item in reversed(list(self.events(db, row.id)))
            ]
        return payload

    def context_prompt(self, db, context) -> str:
        from .facts import public_block

        row = self._row(db, context)
        if not row:
            return ""
        steps = self.steps(db, row.id)
        plan = "; ".join(f"{step.status}:{step.title}" for step in steps[:12])
        facts = row.facts or {}
        scope = facts.get("scope") or {}
        metrics = facts.get("metrics") or {}
        checkpoint = row.checkpoint or {}
        current = next((step.title for step in steps if step.status == "RUNNING"), row.current_step or "none")
        criteria = "; ".join((row.success_criteria or {}).get("all") or [])
        verified = public_block(facts)
        continuation = (
            " This is TASK CONTINUATION after restart or pause, not token-stream continuation. "
            "Do not repeat completed tool digests or rewrite already changed files. "
            if facts.get("task_continuation") or getattr(context, "resuming", False)
            else ""
        )
        return (
            f" TASK GOAL: {(row.original_user_request or '')[:500]}. "
            f"CURRENT STEP: {current}. "
            f"SUCCESS CRITERIA: {criteria or 'Original request is addressed'}. "
            f"WORKSPACE SCOPE: primary={scope.get('primary_root') or row.workspace} "
            f"scratch={scope.get('scratch') or ''} write_outside=false. "
            f"{verified + ' ' if verified else ''}"
            f"LAST ERROR: {row.last_error or 'none'}. "
            f"REMAINING BUDGET: tool_calls={row.tool_calls_used}/{row.tool_budget} "
            f"files={row.files_changed}/{row.file_change_budget} "
            f"runtime_s={row.elapsed_runtime}/{row.runtime_budget}. "
            f"Autonomy=HIGH research_depth=DEEP. Propose one next action only. "
            f"Plan: {plan}. Changed files: {str(checkpoint.get('changed_files') or [])[:300]}. "
            f"Metrics={metrics}. TinyFish cost is enforced by the server. "
            "Never claim lack of access when VERIFIED_RESULTS list a success. "
            "Never disable SENSITIVE/CRITICAL confirmations. "
            "Do not write helper files on the Desktop." + continuation
        )


def status_message(row) -> str:
    mapping = {
        machine.WAITING_DEVICE: "Device offline",
        machine.WAITING_CONFIRMATION: "Waiting for confirmation",
        machine.WAITING_LLM: "LLM unavailable",
        machine.WAITING_WORKSPACE: "Workspace занят. Задача в очереди.",
        machine.PAUSED: "Paused",
        machine.STOPPED: "Stopped",
        machine.INTERRUPTED: "Interrupted",
        machine.CONFLICT: "Conflict: file changed externally",
        machine.FAILED: {
            "task_budget": "Budget exhausted",
            "task_runtime_limit": "Budget exhausted",
            "task_file_limit": "Budget exhausted",
            "host_offline": "Device offline",
            "timeout": "Tool timed out",
            "tests_not_verified": "Tests still failing",
            "conflict": "Conflict: file changed externally",
            "llm_unavailable": "LLM unavailable",
            "tor_unavailable": "Tor unavailable",
            "web_disabled": "Web unavailable",
        }.get(row.last_error or "", "Task failed"),
        machine.COMPLETED: "Completed",
        machine.EXECUTING: "Working",
        machine.VERIFYING: "Working",
        machine.PLANNING: "Working",
        machine.READY: "Working",
        machine.INSPECTING: "Working",
        machine.RESEARCHING: "Working",
        machine.RETRYING: "Working",
        machine.RECOVERING: "Interrupted",
    }
    return mapping.get(row.status, row.status)


def _git_snapshot(root: str | None) -> dict:
    if not root:
        return {}
    import subprocess

    def run(args):
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    status = run(["status", "--porcelain"])
    return {
        "git_head": run(["rev-parse", "HEAD"]),
        "git_branch": run(["rev-parse", "--abbrev-ref", "HEAD"]),
        "git_dirty": bool(status),
    }


def recover_interrupted():
    from sqlalchemy import update

    from ...database import SessionLocal
    from ..models import LocalTask

    with SessionLocal() as db:
        db.execute(
            update(LocalTask)
            .where(
                LocalTask.finished_at.is_(None),
                LocalTask.status.in_(
                    [
                        item
                        for item in machine.ACTIVE
                        if item
                        not in {
                            machine.PAUSED,
                            machine.WAITING_CONFIRMATION,
                            machine.WAITING_DEVICE,
                            machine.WAITING_LLM,
                            machine.WAITING_WORKSPACE,
                            machine.CONFLICT,
                        }
                    ]
                ),
            )
            .values(status=machine.INTERRUPTED, current_phase=machine.INTERRUPTED)
        )
        db.commit()
        reconcile_locks(db)
        leftovers = []
        leftover_agents = []
        from sqlalchemy import select

        for row in db.scalars(select(LocalTask).where(LocalTask.finished_at.is_(None))).all():
            raw = ((row.checkpoint or {}).get("tinyfish") or {}) if isinstance(row.checkpoint, dict) else {}
            session_id = str(raw.get("active_browser_session_id") or "")
            run_id = str(raw.get("active_agent_run_id") or "")
            if session_id:
                leftovers.append(session_id)
            if run_id:
                leftover_agents.append(run_id)
            if session_id or run_id:
                updated = dict(row.checkpoint or {})
                tiny = dict(raw)
                tiny["active_browser_session_id"] = ""
                tiny["active_agent_run_id"] = ""
                updated["tinyfish"] = tiny
                row.checkpoint = updated
        if leftovers or leftover_agents:
            db.commit()
    _terminate_tinyfish_leftovers(leftovers, leftover_agents)


def _terminate_tinyfish_leftovers(sessions, agents):
    import re
    from contextlib import suppress

    import httpx

    from ...config import get_settings
    from ..tinyfish.client import AGENT, BROWSER

    key = get_settings().tinyfish_api_key.get_secret_value()
    if not key:
        return
    with httpx.Client(headers={"X-API-Key": key}, timeout=10, follow_redirects=False) as http:
        for session_id in sessions:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", session_id):
                continue
            with suppress(Exception):
                http.delete(BROWSER + "/" + session_id)
        for run_id in agents:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", run_id):
                continue
            with suppress(Exception):
                http.post(AGENT + "/runs/" + run_id + "/cancel")
