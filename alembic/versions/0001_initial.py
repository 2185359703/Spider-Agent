"""create onboarding platform tables

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-29
"""

from auto_spider.db import models  # noqa: F401
from auto_spider.db.base import Base
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
