from sqlalchemy import select

from ...models import now
from ..contracts import ToolError
from ..models import LocalTask, ToolRun
from .workspace import looks_like_coding, workspace_from_settings


class LocalTaskController:
    """Task-level loop on top of ToolOrchestrator. Not a second agent stack."""

    def attach(self, context):
        settings = getattr(context, "settings", None)
        workspace = workspace_from_settings(settings)
        context.workspace = workspace
        context.files_changed = getattr(context, "files_changed", 0)
        context.task_commands = getattr(context, "task_commands", [])
        context.coding_task = looks_like_coding(getattr(context, "user_prompt", "") or "")
        return workspace

    def open(self, db, context):
        if getattr(context, "computer_mode", "off") == "off":
            return None
        workspace = getattr(context, "workspace", None) or workspace_from_settings(context.settings)
        row = LocalTask(
            user_id=context.user_id,
            chat_id=context.chat_id,
            generation_id=context.generation_id,
            status="PLANNING",
            workspace=(workspace.root or "")[:500],
            checkpoint={
                "workspace": workspace.root,
                "git_root": workspace.git_root,
                "changed_files": [],
                "commands": [],
                "test_results": [],
            },
        )
        db.add(row)
        db.commit()
        context.task_id = row.id
        return row

    def note_command(self, context, name, digest_value, metadata=None):
        commands = getattr(context, "task_commands", None)
        if commands is None:
            context.task_commands = commands = []
        commands.append({"tool": name, "digest": digest_value})
        files = int((metadata or {}).get("files_changed") or 0)
        context.files_changed = getattr(context, "files_changed", 0) + files
        limits = context.limits
        maximum = getattr(limits, "max_files_changed", 20)
        if maximum and context.files_changed > maximum:
            raise ToolError("task_file_limit")

    def checkpoint(self, db, context, status="EXECUTING"):
        task_id = getattr(context, "task_id", None)
        if not task_id:
            return
        row = db.get(LocalTask, task_id)
        if not row or row.user_id != context.user_id:
            return
        runs = []
        if context.generation_id:
            runs = [
                {
                    "tool": item.tool_name,
                    "status": item.status,
                    "digest": item.input_digest,
                    "path": (item.input_summary or {}).get("target")
                    or (item.input_summary or {}).get("path"),
                    "before": (item.result_metadata or {}).get("before_sha256"),
                    "after": (item.result_metadata or {}).get("after_sha256"),
                }
                for item in db.scalars(
                    select(ToolRun).where(
                        ToolRun.generation_id == context.generation_id, ToolRun.user_id == context.user_id
                    )
                )
            ]
        row.status = status
        row.checkpoint = {
            **(row.checkpoint or {}),
            "changed_files": [item for item in runs if item.get("before") or item.get("after")],
            "commands": list(getattr(context, "task_commands", [])),
            "files_changed": getattr(context, "files_changed", 0),
        }
        db.commit()

    def finish(self, db, context, status="COMPLETED"):
        task_id = getattr(context, "task_id", None)
        if not task_id:
            return
        row = db.get(LocalTask, task_id)
        if not row or row.user_id != context.user_id:
            return
        row.status = status
        row.finished_at = now()
        db.commit()
