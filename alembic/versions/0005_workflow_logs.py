"""add durable workflow console logs

Revision ID: 0005_workflow_logs
Revises: 0004_sample_review_decisions
Create Date: 2026-09-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_workflow_logs"
down_revision = "0004_sample_review_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "workflow_logs" not in inspector.get_table_names():
        op.create_table(
            "workflow_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("log_id", sa.String(length=32), nullable=False),
            sa.Column("task_id", sa.String(length=32), nullable=False),
            sa.Column("run_id", sa.String(length=32), nullable=True),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("stage", sa.String(length=80), nullable=False),
            sa.Column("level", sa.String(length=20), nullable=False, server_default="INFO"),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("detail_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["task_id"], ["onboarding_tasks.task_id"]),
            sa.UniqueConstraint("log_id", name="uq_workflow_logs_log_id"),
            sa.UniqueConstraint("task_id", "sequence", name="uk_task_log_sequence"),
        )
        op.create_index("ix_workflow_logs_task_id", "workflow_logs", ["task_id"])
        op.create_index("ix_workflow_logs_run_id", "workflow_logs", ["run_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "workflow_logs" in inspector.get_table_names():
        op.drop_index("ix_workflow_logs_run_id", table_name="workflow_logs")
        op.drop_index("ix_workflow_logs_task_id", table_name="workflow_logs")
        op.drop_table("workflow_logs")
