"""Persist batch import source rows independently of company tasks."""

from alembic import op
import sqlalchemy as sa

revision = "0008_batch_intake"
down_revision = "0007_collection_dispatch"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("onboarding_batches")}
    if "intake_json" not in columns:
        op.add_column("onboarding_batches", sa.Column("intake_json", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("onboarding_batches", "intake_json")
