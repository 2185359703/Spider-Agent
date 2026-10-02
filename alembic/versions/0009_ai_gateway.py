"""Add runtime AI gateway selection and execution snapshots."""

from alembic import op
import sqlalchemy as sa

revision = "0009_ai_gateway"
down_revision = "0008_batch_intake"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "runtime_settings" not in tables:
        op.create_table(
            "runtime_settings",
            sa.Column("key", sa.String(80), primary_key=True),
            sa.Column("value_json", sa.JSON(), nullable=False),
            sa.Column("updated_by", sa.String(128), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    columns = {c["name"] for c in inspector.get_columns("agent_executions")}
    for name in ("gateway_profile", "gateway_model", "gateway_base_url", "gateway_api_mode"):
        if name not in columns:
            op.add_column("agent_executions", sa.Column(name, sa.String(1024), nullable=True))


def downgrade():
    op.drop_column("agent_executions", "gateway_api_mode")
    op.drop_column("agent_executions", "gateway_base_url")
    op.drop_column("agent_executions", "gateway_model")
    op.drop_column("agent_executions", "gateway_profile")
    op.drop_table("runtime_settings")
