"""Persist graph checkpoints, execution leases and Agent conversations."""

from alembic import op
import sqlalchemy as sa

revision = "0006_durable_execution"
down_revision = "0005_workflow_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 historically uses Base.metadata.create_all, so fresh databases may
    # already contain current tables/columns. Existing databases still migrate.
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("workflow_runs")}
    def create_table(name, *args):
        if name not in tables:
            op.create_table(name, *args)
    def add_column(table, column):
        if column.name not in columns:
            op.add_column(table, column)
    _upgrade(create_table, add_column)


def _upgrade(create_table, add_column) -> None:
    create_table(
        "workflow_dispatches",
        sa.Column("dispatch_id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(32), sa.ForeignKey("onboarding_tasks.task_id"), nullable=False, index=True),
        sa.Column("bundle_id", sa.String(32), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    add_column("workflow_runs", sa.Column("execution_key", sa.String(128), nullable=True))
    op.create_index("uk_run_execution_key", "workflow_runs", ["execution_key"], unique=True)
    add_column("workflow_runs", sa.Column("control", sa.String(20), nullable=True))
    add_column("workflow_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    add_column("workflow_runs", sa.Column("repair_attempt", sa.Integer(), nullable=False, server_default="0"))
    add_column("workflow_runs", sa.Column("context_json", sa.JSON(), nullable=True))
    create_table(
        "graph_checkpoints",
        sa.Column("checkpoint_key", sa.String(64), primary_key=True),
        sa.Column("thread_id", sa.String(32), nullable=False, index=True),
        sa.Column("namespace", sa.String(255), nullable=False),
        sa.Column("checkpoint_id", sa.String(64), nullable=False, index=True),
        sa.Column("parent_id", sa.String(64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
    )
    create_table(
        "graph_writes",
        sa.Column("write_key", sa.String(64), primary_key=True),
        sa.Column("checkpoint_key", sa.String(64), nullable=False, index=True),
        sa.Column("thread_id", sa.String(32), nullable=False, index=True),
        sa.Column("graph_task_id", sa.String(64), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    create_table(
        "workflow_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("workflow_runs.run_id"), nullable=False, index=True),
        sa.Column("step_key", sa.String(128), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "step_key", name="uk_run_step"),
    )
    create_table(
        "execution_leases",
        sa.Column("resource_key", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(32), nullable=False, index=True),
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_table(
        "agent_executions",
        sa.Column("execution_id", sa.String(32), primary_key=True),
        sa.Column("task_id", sa.String(32), sa.ForeignKey("onboarding_tasks.task_id"), nullable=False, index=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("workflow_runs.run_id"), nullable=False, index=True),
        sa.Column("step_key", sa.String(128), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False, unique=True),
        sa.Column("workspace_path", sa.String(1024), nullable=False),
        sa.Column("server_url", sa.String(1024), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "step_key", name="uk_agent_run_step"),
    )


def downgrade() -> None:
    for table in ("workflow_dispatches", "agent_executions", "execution_leases", "workflow_steps", "graph_writes", "graph_checkpoints"):
        op.drop_table(table)
    op.drop_index("uk_run_execution_key", table_name="workflow_runs")
    for column in ("context_json", "repair_attempt", "heartbeat_at", "control", "execution_key"):
        op.drop_column("workflow_runs", column)
