"""12B.1A.2 — AmazonConnectionRepository. Org-scoped metadata access only."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.amazon.secrets import build_asi_secret_reference
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import Organization
from app.persistence.repositories import AmazonConnectionRepository

FORBIDDEN_SECRET_FIELDS = ("refresh_token", "access_token", "client_secret", "client_id")


def test_create_connection_succeeds() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        repo = AmazonConnectionRepository(session)
        row = repo.create(
            organization_id=org_id,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
        )
        assert row.id is not None
        assert row.organization_id == org_id
        assert row.provider == "SP_API"
        assert row.environment == "SANDBOX"
        assert row.region == "eu"
        assert row.status == "not_connected"
        assert row.token_reference is None
        assert row.selling_partner_id is None


def test_get_connection_by_organization() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        repo = AmazonConnectionRepository(session)
        created = repo.create(
            organization_id=org_id,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
        )
        found = repo.get(org_id, provider="SP_API", environment="SANDBOX")
        assert found is not None
        assert found.id == created.id
        assert repo.get(org_id, provider="SP_API", environment="PRODUCTION") is None


def test_organization_a_cannot_retrieve_organization_b_connection() -> None:
    org_a = current_organization_id()
    org_b = uuid4()
    with session_scope() as session:
        session.add(Organization(id=org_b, name="Other"))
        repo = AmazonConnectionRepository(session)
        row_b = repo.create(
            organization_id=org_b,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
        )
        assert repo.get(org_a, provider="SP_API", environment="SANDBOX") is None
        assert repo.get_by_id(org_a, row_b.id) is None
        assert repo.get(org_b, provider="SP_API", environment="SANDBOX") is not None
        assert repo.get_by_id(org_b, row_b.id) is not None


def test_list_connections_returns_only_current_organization() -> None:
    org_a = current_organization_id()
    org_b = uuid4()
    with session_scope() as session:
        session.add(Organization(id=org_b, name="Other"))
        repo = AmazonConnectionRepository(session)
        sandbox = repo.create(
            organization_id=org_a,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
        )
        production = repo.create(
            organization_id=org_a,
            provider="SP_API",
            environment="PRODUCTION",
            region="eu",
        )
        repo.create(
            organization_id=org_b,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
        )
        listed = repo.list_for_org(org_a)
        assert {row.id for row in listed} == {sandbox.id, production.id}
        assert all(row.organization_id == org_a for row in listed)


def test_update_connection_status() -> None:
    org_id = current_organization_id()
    validated_at = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    with session_scope() as session:
        repo = AmazonConnectionRepository(session)
        created = repo.create(
            organization_id=org_id,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
        )
        updated = repo.update(
            org_id,
            created.id,
            status="error",
            last_error_code="authentication",
            last_error_at=validated_at,
            last_successful_validation_at=validated_at,
        )
        assert updated is not None
        assert updated.status == "error"
        assert updated.last_error_code == "authentication"
        assert updated.last_error_at == validated_at
        assert updated.last_successful_validation_at == validated_at
        missing = repo.update(uuid4(), created.id, status="connected")
        assert missing is None


def test_duplicate_connection_constraint() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        repo = AmazonConnectionRepository(session)
        repo.create(
            organization_id=org_id,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
        )
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            repo = AmazonConnectionRepository(session)
            repo.create(
                organization_id=org_id,
                provider="SP_API",
                environment="SANDBOX",
                region="eu",
            )


def test_repository_does_not_accept_secret_fields() -> None:
    create_params = inspect.signature(AmazonConnectionRepository.create).parameters
    update_params = inspect.signature(AmazonConnectionRepository.update).parameters
    for name in FORBIDDEN_SECRET_FIELDS:
        assert name not in create_params
        assert name not in update_params
    assert "token_reference" not in create_params

    org_id = current_organization_id()
    with session_scope() as session:
        repo = AmazonConnectionRepository(session)
        with pytest.raises(TypeError):
            repo.create(
                organization_id=org_id,
                provider="SP_API",
                environment="SANDBOX",
                region="eu",
                refresh_token="Atzr|must-not-store",  # type: ignore[call-arg]
            )
        row = repo.create(
            organization_id=org_id,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
        )
        assert row.token_reference is None
        with pytest.raises(TypeError, match="cannot store secret fields"):
            repo.update(org_id, row.id, refresh_token="Atzr|must-not-store")
        with pytest.raises(TypeError, match="cannot store secret fields"):
            repo.update(org_id, row.id, access_token="Atza|must-not-store")
        with pytest.raises(TypeError, match="cannot store secret fields"):
            repo.update(org_id, row.id, client_secret="must-not-store")
        with pytest.raises(TypeError, match="cannot store secret fields"):
            repo.update(org_id, row.id, token_reference="asi:dev:should-wait-for-12B.1B")
        stored = repo.get_by_id(org_id, row.id)
        assert stored is not None
        assert stored.token_reference is None
        assert stored.status == "not_connected"


def test_delete_is_organization_scoped() -> None:
    org_a = current_organization_id()
    org_b = uuid4()
    with session_scope() as session:
        session.add(Organization(id=org_b, name="Other"))
        repo = AmazonConnectionRepository(session)
        row_a = repo.create(
            organization_id=org_a,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
        )
        row_b = repo.create(
            organization_id=org_b,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
        )
        assert repo.delete(org_a, row_b.id) is False
        assert repo.get_by_id(org_b, row_b.id) is not None
        assert repo.delete(org_a, row_a.id) is True
        assert repo.get_by_id(org_a, row_a.id) is None
        assert repo.get_by_id(org_b, row_b.id) is not None


def test_bind_token_reference_persists_opaque_pointer_only() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        repo = AmazonConnectionRepository(session)
        row = repo.create(
            organization_id=org_id,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
        )
        reference = build_asi_secret_reference(
            provider="SP_API",
            environment="SANDBOX",
            organization_id=org_id,
            connection_id=row.id,
        )
        bound = repo.bind_token_reference(org_id, row.id, reference)
        assert bound is not None
        assert bound.token_reference == reference
        assert "Atza|" not in bound.token_reference
        assert "Atzr|" not in bound.token_reference
        with pytest.raises(TypeError, match="cannot store secret fields"):
            repo.update(org_id, row.id, token_reference=reference)
        with pytest.raises(TypeError, match="cannot store secret fields"):
            repo.bind_token_reference(org_id, row.id, "Atzr|must-not-store")
        with pytest.raises(TypeError, match="organization does not match"):
            repo.bind_token_reference(
                org_id,
                row.id,
                build_asi_secret_reference(
                    provider="SP_API",
                    environment="SANDBOX",
                    organization_id=uuid4(),
                    connection_id=row.id,
                ),
            )
        with pytest.raises(TypeError, match="connection does not match"):
            repo.bind_token_reference(
                org_id,
                row.id,
                build_asi_secret_reference(
                    provider="SP_API",
                    environment="SANDBOX",
                    organization_id=org_id,
                    connection_id=uuid4(),
                ),
            )
        with pytest.raises(TypeError, match="does not match this connection"):
            repo.bind_token_reference(
                org_id,
                row.id,
                build_asi_secret_reference(
                    provider="SP_API",
                    environment="PRODUCTION",
                    organization_id=org_id,
                    connection_id=row.id,
                ),
            )
        stored = repo.get_by_id(org_id, row.id)
        assert stored is not None
        assert stored.token_reference == reference
        assert stored.status == "not_connected"
        cleared = repo.clear_token_reference(org_id, row.id)
        assert cleared is not None
        assert cleared.token_reference is None
