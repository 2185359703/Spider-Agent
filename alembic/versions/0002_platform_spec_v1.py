"""add PlatformSpec v1 metadata

Revision ID: 0002_platform_spec_v1
Revises: 0001_initial
Create Date: 2026-09-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_platform_spec_v1"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {column["name"] for column in inspector.get_columns("platform_specs")}
    if "schema_version" not in existing:
        op.add_column(
            "platform_specs",
            sa.Column(
                "schema_version",
                sa.String(length=20),
                nullable=False,
                server_default="1.0",
            ),
        )
    if "spec_hash" not in existing:
        op.add_column(
            "platform_specs",
            sa.Column("spec_hash", sa.String(length=64), nullable=True),
        )
        op.execute("UPDATE platform_specs SET spec_hash = '' WHERE spec_hash IS NULL")
        # SQLite does not implement ALTER COLUMN. The application always writes
        # a hash, so keeping the column nullable on SQLite preserves the same
        # runtime contract while allowing local development migrations to run.
        if bind.dialect.name != "sqlite":
            op.alter_column("platform_specs", "spec_hash", nullable=False)
    if "status" not in existing:
        op.add_column(
            "platform_specs",
            sa.Column("status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        )
    if "confidence_summary" not in existing:
        op.add_column(
            "platform_specs",
            sa.Column("confidence_summary", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("platform_specs", "confidence_summary")
    op.drop_column("platform_specs", "status")
    op.drop_column("platform_specs", "spec_hash")
    op.drop_column("platform_specs", "schema_version")
