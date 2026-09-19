"""Persistent autonomous tasks, plans, journals, checkpoints and workspace locks."""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("local_tasks") as batch:
        batch.alter_column("chat_id", existing_type=sa.String(length=36), nullable=True)
        batch.add_column(sa.Column("project_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("device_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("title", sa.String(length=200), nullable=False, server_default=""))
        batch.add_column(sa.Column("original_user_request", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("plan_revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("current_step", sa.String(length=80), nullable=False, server_default=""))
        batch.add_column(
            sa.Column("current_phase", sa.String(length=40), nullable=False, server_default="CREATED")
        )
        batch.add_column(
            sa.Column("pause_requested", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )
        batch.add_column(
            sa.Column("stop_requested", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )
        batch.add_column(sa.Column("tool_calls_used", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("tool_budget", sa.Integer(), nullable=False, server_default="40"))
        batch.add_column(sa.Column("elapsed_runtime", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("runtime_budget", sa.Integer(), nullable=False, server_default="1800"))
        batch.add_column(sa.Column("files_changed", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("file_change_budget", sa.Integer(), nullable=False, server_default="20"))
        batch.add_column(sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("retry_budget", sa.Integer(), nullable=False, server_default="3"))
        batch.add_column(sa.Column("last_error", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("completion_summary", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("success_criteria", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch.add_column(sa.Column("facts", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("verification", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            )
        )
    with op.batch_alter_table("tool_runs") as batch:
        batch.add_column(sa.Column("task_id", sa.String(length=36), nullable=True))
        batch.create_index("ix_tool_runs_task_id", ["task_id"], unique=False)
    op.create_table(
        "task_steps",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tool_category", sa.String(length=40), nullable=False),
        sa.Column("depends_on", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("verification_required", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("key", sa.String(length=40), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["task_id"], ["local_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("task_steps") as batch:
        batch.create_index("ix_task_steps_task_id", ["task_id"], unique=False)
        batch.create_index("ix_task_steps_task_order", ["task_id", "position"], unique=False)
    op.create_table(
        "task_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("tool_run_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["local_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("task_events") as batch:
        batch.create_index("ix_task_events_task_id", ["task_id"], unique=False)
        batch.create_index("ix_task_events_task_created", ["task_id", "created_at"], unique=False)
    op.create_table(
        "task_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("plan_revision", sa.Integer(), nullable=False),
        sa.Column("current_step", sa.String(length=80), nullable=False),
        sa.Column("completed_steps", sa.JSON(), nullable=False),
        sa.Column("workspace_state", sa.JSON(), nullable=False),
        sa.Column("verification", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["local_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("task_checkpoints") as batch:
        batch.create_index("ix_task_checkpoints_task_id", ["task_id"], unique=False)
    op.create_table(
        "workspace_locks",
        sa.Column("workspace", sa.String(length=500), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["local_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace"),
    )


def downgrade():
    op.drop_table("workspace_locks")
    op.drop_table("task_checkpoints")
    op.drop_table("task_events")
    op.drop_table("task_steps")
    with op.batch_alter_table("tool_runs") as batch:
        batch.drop_index("ix_tool_runs_task_id")
        batch.drop_column("task_id")
    with op.batch_alter_table("local_tasks") as batch:
        batch.drop_column("updated_at")
        batch.drop_column("verification")
        batch.drop_column("facts")
        batch.drop_column("success_criteria")
        batch.drop_column("completion_summary")
        batch.drop_column("last_error")
        batch.drop_column("retry_budget")
        batch.drop_column("retry_count")
        batch.drop_column("file_change_budget")
        batch.drop_column("files_changed")
        batch.drop_column("runtime_budget")
        batch.drop_column("elapsed_runtime")
        batch.drop_column("tool_budget")
        batch.drop_column("tool_calls_used")
        batch.drop_column("stop_requested")
        batch.drop_column("pause_requested")
        batch.drop_column("current_phase")
        batch.drop_column("current_step")
        batch.drop_column("plan_revision")
        batch.drop_column("original_user_request")
        batch.drop_column("title")
        batch.drop_column("device_id")
        batch.drop_column("project_id")
        batch.alter_column("chat_id", existing_type=sa.String(length=36), nullable=False)
