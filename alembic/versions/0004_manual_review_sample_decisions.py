"""store per-sample manual review decisions

Revision ID: 0004_manual_review_sample_decisions
Revises: 0003_manual_review_issue_details
Create Date: 2026-09-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_manual_review_sample_decisions"
down_revision = "0003_manual_review_issue_details"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("manual_reviews")}
    if "sample_decisions" not in existing:
        op.add_column(
            "manual_reviews",
            sa.Column("sample_decisions", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("manual_reviews")}
    if "sample_decisions" in existing:
        op.drop_column("manual_reviews", "sample_decisions")
