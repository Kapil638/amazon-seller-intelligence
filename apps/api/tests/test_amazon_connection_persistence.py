"""12B.1A.1 — Amazon connection metadata table. No API, OAuth, or SecretProvider."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonConnection, Organization

FORBIDDEN_SECRET_COLUMNS = ("refresh_token", "access_token", "client_secret", "client_id")
MIGRATION_0007 = (
    Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0007_amazon_connections.py"
)
OPAQUE_TOKEN_REFERENCE = "asi:dev:11111111-1111-4111-8111-111111111111"


def _connection(
    *,
    organization_id=None,
    provider: str = "SP_API",
    environment: str = "SANDBOX",
    region: str = "eu",
    status: str = "not_connected",
    token_reference: str | None = None,
) -> AmazonConnection:
    return AmazonConnection(
        organization_id=organization_id or current_organization_id(),
        provider=provider,
        environment=environment,
        region=region,
        status=status,
        token_reference=token_reference,
    )


def test_amazon_connections_table_is_registered() -> None:
    table = AmazonConnection.__table__
    assert table.name == "amazon_connections"
    assert "id" in table.c
    assert "organization_id" in table.c
    with session_scope() as session:
        session.add(_connection())
        session.flush()
        stored = session.query(AmazonConnection).one()
        assert stored.provider == "SP_API"
        assert stored.environment == "SANDBOX"
        assert stored.region == "eu"
        assert stored.status == "not_connected"
        assert stored.selling_partner_id is None
        assert stored.token_reference is None
        assert stored.last_successful_sync_at is None


def test_amazon_connection_requires_organization_ownership() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        session.add(_connection(organization_id=org_id))
        session.flush()
        row = session.query(AmazonConnection).one()
        assert row.organization_id == org_id
        assert row.organization is not None
        assert row.organization.id == org_id
        fks = AmazonConnection.__table__.c.organization_id.foreign_keys
        assert len(fks) == 1
        assert next(iter(fks)).ondelete == "RESTRICT"


def test_duplicate_org_provider_environment_is_rejected() -> None:
    with session_scope() as session:
        session.add(_connection())
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(_connection())


def test_different_organizations_can_have_separate_connections() -> None:
    other_org = uuid4()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other"))
        session.add(_connection())
        session.add(_connection(organization_id=other_org))
        session.flush()
        rows = session.query(AmazonConnection).all()
        assert len(rows) == 2
        assert {row.organization_id for row in rows} == {current_organization_id(), other_org}


def test_same_org_can_have_sandbox_and_production_connections() -> None:
    with session_scope() as session:
        session.add(_connection(environment="SANDBOX"))
        session.add(_connection(environment="PRODUCTION"))
        session.flush()
        assert session.query(AmazonConnection).count() == 2


def test_amazon_connections_has_no_secret_columns() -> None:
    columns = set(AmazonConnection.__table__.c.keys())
    for name in FORBIDDEN_SECRET_COLUMNS:
        assert name not in columns
    migration = MIGRATION_0007.read_text(encoding="utf-8")
    for name in FORBIDDEN_SECRET_COLUMNS:
        assert name not in migration


def test_token_reference_is_opaque_placeholder_not_secret_storage() -> None:
    assert "token_reference" in AmazonConnection.__table__.c
    with session_scope() as session:
        session.add(_connection(token_reference=OPAQUE_TOKEN_REFERENCE))
        session.flush()
        stored = session.query(AmazonConnection).one()
        assert stored.token_reference == OPAQUE_TOKEN_REFERENCE
        assert stored.token_reference is not None
        assert "Atza|" not in stored.token_reference
        assert "Atzr|" not in stored.token_reference
        assert "refresh_token" not in stored.token_reference


def test_invalid_status_is_rejected() -> None:
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(_connection(status="is_connected"))


def test_migration_0007_is_additive_after_advertising_models() -> None:
    source = MIGRATION_0007.read_text(encoding="utf-8")
    assert 'revision = "0007_amazon_connections"' in source
    assert 'down_revision = "0006_advertising_models"' in source
    assert "create_table" in source
    assert "op.alter_table" not in source
    assert "drop_table(\"advertising" not in source
    assert "drop_table(\"profit" not in source
    assert "copilot_" not in source
