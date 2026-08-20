"""Milestone 10 persistence schema.

Revision ID: 0001_m10_persistence
Revises:
Create Date: 2026-08-20
"""

from alembic import op
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.persistence.models import Base, Organization

revision = "0001_m10_persistence"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    settings = get_settings()
    session = Session(bind)
    try:
        if session.get(Organization, settings.default_organization_id) is None:
            session.add(
                Organization(id=settings.default_organization_id, name=settings.default_organization_name)
            )
            session.commit()
    finally:
        session.close()


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind)
