"""Add bounded parallel browser lanes for batch onboarding."""

from alembic import op
import sqlalchemy as sa

revision = "0010_browser_lanes"
down_revision = "0009_ai_gateway"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("onboarding_tasks")}
    if "browser_lane" not in columns:
        op.add_column(
            "onboarding_tasks",
            sa.Column("browser_lane", sa.Integer(), nullable=False, server_default="0"),
        )
        op.create_index(
            "ix_onboarding_tasks_browser_lane", "onboarding_tasks", ["browser_lane"]
        )


def downgrade():
    op.drop_index("ix_onboarding_tasks_browser_lane", table_name="onboarding_tasks")
    op.drop_column("onboarding_tasks", "browser_lane")
