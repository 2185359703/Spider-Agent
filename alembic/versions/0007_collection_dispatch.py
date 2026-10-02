"""Persist human collection delivery and execution ownership."""

from alembic import op
import sqlalchemy as sa

revision = "0007_collection_dispatch"
down_revision = "0006_durable_execution"
branch_labels = None
depends_on = None


def upgrade():
    if "collection_dispatches" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "collection_dispatches",
        sa.Column("manual_run_id", sa.String(32), sa.ForeignKey("manual_runs.manual_run_id"), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("owner", sa.String(32), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_table("collection_dispatches")
