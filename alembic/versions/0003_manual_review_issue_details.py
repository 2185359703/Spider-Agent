"""store structured field-level manual review issues

Revision ID: 0003_manual_review_issue_details
Revises: 0002_platform_spec_v1
Create Date: 2026-09-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_manual_review_issue_details"
down_revision = "0002_platform_spec_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("manual_reviews")}
    if "issue_details" not in existing:
        op.add_column(
            "manual_reviews",
            sa.Column("issue_details", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("manual_reviews")}
    if "issue_details" in existing:
        op.drop_column("manual_reviews", "issue_details")
