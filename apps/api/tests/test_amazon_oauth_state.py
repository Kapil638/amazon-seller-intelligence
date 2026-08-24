"""12B.1C.2 — Hashed Amazon OAuth state persistence. No callback or tokens."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.amazon.oauth import hash_oauth_state, new_oauth_state, oauth_state_is_usable
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonOAuthState, Organization
from app.persistence.repositories import AmazonConnectionRepository, AmazonOAuthStateRepository

FORBIDDEN_SECRET_COLUMNS = (
    "refresh_token",
    "access_token",
    "client_secret",
    "client_id",
    "authorization_code",
    "oauth_code",
    "spapi_oauth_code",
    "state",
)
MIGRATION_0008 = (
    Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0008_amazon_oauth_states.py"
)


def test_amazon_oauth_states_table_is_registered() -> None:
    table = AmazonOAuthState.__table__
    assert table.name == "amazon_oauth_states"
    for name in (
        "id",
        "organization_id",
        "provider",
        "environment",
        "connection_id",
        "state_hash",
        "amazon_state",
        "expires_at",
        "consumed_at",
        "created_at",
    ):
        assert name in table.c
    raw, digest = new_oauth_state()
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).create(
            organization_id=current_organization_id(),
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
        )
        session.add(
            AmazonOAuthState(
                organization_id=current_organization_id(),
                provider="SP_API",
                environment="SANDBOX",
                connection_id=connection.id,
                state_hash=digest,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        session.flush()
        stored = session.query(AmazonOAuthState).one()
        assert stored.state_hash == digest
        assert stored.state_hash != raw
        assert stored.consumed_at is None
        assert stored.amazon_state is None
        assert raw not in {stored.state_hash, stored.amazon_state, str(stored.id)}


def test_oauth_state_is_organization_scoped() -> None:
    org_a = current_organization_id()
    org_b = uuid4()
    _, digest = new_oauth_state()
    with session_scope() as session:
        session.add(Organization(id=org_b, name="Other"))
        repo = AmazonConnectionRepository(session)
        connection_a = repo.create(
            organization_id=org_a, provider="SP_API", environment="SANDBOX", region="eu"
        )
        connection_b = repo.create(
            organization_id=org_b, provider="SP_API", environment="SANDBOX", region="eu"
        )
        states = AmazonOAuthStateRepository(session)
        states.create(
            organization_id=org_a,
            provider="SP_API",
            environment="SANDBOX",
            connection_id=connection_a.id,
            state_hash=digest,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        assert states.get_by_hash(org_a, digest) is not None
        assert states.get_by_hash(org_b, digest) is None
        assert states.get_usable_by_hash(org_b, digest) is None
        other_digest = hash_oauth_state("other-org-state-token-value-not-stored")
        states.create(
            organization_id=org_b,
            provider="SP_API",
            environment="SANDBOX",
            connection_id=connection_b.id,
            state_hash=other_digest,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        assert {row.organization_id for row in states.list_for_org(org_a)} == {org_a}


def test_oauth_state_expires_correctly() -> None:
    org_id = current_organization_id()
    _, digest = new_oauth_state()
    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).create(
            organization_id=org_id, provider="SP_API", environment="SANDBOX", region="eu"
        )
        repo = AmazonOAuthStateRepository(session)
        repo.create(
            organization_id=org_id,
            provider="SP_API",
            environment="SANDBOX",
            connection_id=connection.id,
            state_hash=digest,
            expires_at=expired_at,
        )
        stored = repo.get_by_hash(org_id, digest)
        assert stored is not None
        assert oauth_state_is_usable(expires_at=stored.expires_at, consumed_at=stored.consumed_at) is False
        assert repo.get_usable_by_hash(org_id, digest) is None

    _, live_digest = new_oauth_state()
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            org_id, provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        repo = AmazonOAuthStateRepository(session)
        live = repo.create(
            organization_id=org_id,
            provider="SP_API",
            environment="SANDBOX",
            connection_id=connection.id,
            state_hash=live_digest,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        assert oauth_state_is_usable(expires_at=live.expires_at, consumed_at=live.consumed_at) is True
        assert repo.get_usable_by_hash(org_id, live_digest) is not None
        assert repo.get_usable_by_hash(org_id, live_digest, now=datetime.now(UTC) + timedelta(hours=1)) is None


def test_oauth_state_has_no_secret_columns() -> None:
    columns = set(AmazonOAuthState.__table__.c.keys())
    for name in FORBIDDEN_SECRET_COLUMNS:
        assert name not in columns
    assert "token_reference" not in columns
    migration = MIGRATION_0008.read_text(encoding="utf-8")
    for name in (
        "refresh_token",
        "access_token",
        "client_secret",
        "client_id",
        "authorization_code",
        "oauth_code",
        "spapi_oauth_code",
        "token_reference",
    ):
        assert name not in migration
    assert 'sa.Column("state"' not in migration
    assert "state_hash" in migration


def test_oauth_state_repository_rejects_raw_state_and_secrets() -> None:
    create_params = inspect.signature(AmazonOAuthStateRepository.create).parameters
    assert "state" not in create_params
    for name in FORBIDDEN_SECRET_COLUMNS:
        assert name not in create_params
    org_id = current_organization_id()
    _, digest = new_oauth_state()
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).create(
            organization_id=org_id, provider="SP_API", environment="SANDBOX", region="eu"
        )
        repo = AmazonOAuthStateRepository(session)
        with pytest.raises(TypeError):
            repo.create(
                organization_id=org_id,
                provider="SP_API",
                environment="SANDBOX",
                connection_id=connection.id,
                state_hash=digest,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
                state="raw-state-must-not-store",  # type: ignore[call-arg]
            )
        with pytest.raises(TypeError):
            repo.create(
                organization_id=org_id,
                provider="SP_API",
                environment="SANDBOX",
                connection_id=connection.id,
                state_hash=digest,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
                refresh_token="Atzr|must-not-store",  # type: ignore[call-arg]
            )


def test_duplicate_state_hash_is_rejected() -> None:
    org_id = current_organization_id()
    _, digest = new_oauth_state()
    expires = datetime.now(UTC) + timedelta(minutes=10)
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).create(
            organization_id=org_id, provider="SP_API", environment="SANDBOX", region="eu"
        )
        AmazonOAuthStateRepository(session).create(
            organization_id=org_id,
            provider="SP_API",
            environment="SANDBOX",
            connection_id=connection.id,
            state_hash=digest,
            expires_at=expires,
        )
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            connection = AmazonConnectionRepository(session).get(
                org_id, provider="SP_API", environment="SANDBOX"
            )
            assert connection is not None
            AmazonOAuthStateRepository(session).create(
                organization_id=org_id,
                provider="SP_API",
                environment="SANDBOX",
                connection_id=connection.id,
                state_hash=digest,
                expires_at=expires,
            )


def test_wrong_organization_cannot_use_connection_for_oauth_state() -> None:
    org_a = current_organization_id()
    org_b = uuid4()
    _, digest = new_oauth_state()
    with session_scope() as session:
        session.add(Organization(id=org_b, name="Other"))
        repo = AmazonConnectionRepository(session)
        connection_b = repo.create(
            organization_id=org_b, provider="SP_API", environment="SANDBOX", region="eu"
        )
        assert repo.get_by_id(org_a, connection_b.id) is None
        with pytest.raises(TypeError, match="another organization"):
            AmazonOAuthStateRepository(session).create(
                organization_id=org_a,
                provider="SP_API",
                environment="SANDBOX",
                connection_id=connection_b.id,
                state_hash=digest,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )


def test_migration_0008_is_additive_after_amazon_connections() -> None:
    source = MIGRATION_0008.read_text(encoding="utf-8")
    assert 'revision = "0008_amazon_oauth_states"' in source
    assert 'down_revision = "0007_amazon_connections"' in source
    assert "amazon_oauth_states" in source
    assert "op.alter_table" not in source
    assert "token_reference" not in source
    assert "amazon_connections" in source
    assert 'drop_table("amazon_connections")' not in source
    assert "copilot_" not in source
