"""12B.1A.3 — AmazonConnectionService overlay. No OAuth, SecretProvider, or API redesign."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.amazon.common import public_model_dump, reject_secret_fields
from app.amazon.connection import AmazonConnectionService
from app.amazon.models import MarketplaceParticipationsSandboxResult, SpApiSandboxProvenance
from app.amazon.sandbox import GET_MARKETPLACE_PARTICIPATIONS
from app.amazon.secrets import DevelopmentSecretProvider, build_asi_secret_reference
from app.amazon.seller_validation import SellerValidationResult
from app.core.config import Settings
from app.core.exceptions import PersistenceError
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonConnection, Organization
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonSellerAccountRepository,
)

SECRET_MARKERS = (
    "Atza|",
    "Atzr|",
    "client_secret",
    "refresh_token",
    "access_token",
    "x-amz-access-token",
)
FORBIDDEN_SECRET_FIELDS = ("refresh_token", "access_token", "client_secret", "client_id", "token_reference")


class _OkChecker:
    async def get_marketplace_participations(self) -> MarketplaceParticipationsSandboxResult:
        return MarketplaceParticipationsSandboxResult(
            payload=[],
            participation_count=0,
            provenance=SpApiSandboxProvenance(
                operation=GET_MARKETPLACE_PARTICIPATIONS,
                region="eu",
                endpoint_host="sandbox.sellingpartnerapi-eu.amazon.com",
                fetched_at=datetime.now(UTC),
                http_status=200,
                api_model_version="sellers-api-model/v1",
            ),
        )


def _configured_settings() -> Settings:
    return Settings(
        sp_api_lwa_client_id=SecretStr("amzn1.application-oa2-client.test"),
        sp_api_lwa_client_secret=SecretStr("test-lwa-client-secret-value"),
        sp_api_sandbox_refresh_token=SecretStr("Atzr|test-sandbox-refresh-token"),
        sp_api_application_name="EWise",
        default_marketplace="amazon.in",
        sp_api_region="eu",
    )


def _count_connections() -> int:
    with session_scope() as session:
        return int(session.scalar(select(func.count()).select_from(AmazonConnection)) or 0)


def _assert_public(payload: object) -> None:
    text = str(payload)
    for marker in SECRET_MARKERS:
        assert marker not in text
    reject_secret_fields(payload)
    if isinstance(payload, dict):
        assert "token_reference" not in payload


def test_persisted_connection_is_returned_when_available() -> None:
    service = AmazonConnectionService()
    created = service.create_connection(
        provider="SP_API",
        environment="PRODUCTION",
        region="na",
        selling_partner_id="A1SELLERID",
    )
    overview = service.overview()
    assert overview.persisted is True
    assert overview.connection_status == "not_connected"
    assert overview.status == "NOT_CONNECTED"
    assert overview.provider == "SP_API"
    assert overview.environment == "PRODUCTION"
    assert overview.region == "na"
    assert overview.selling_partner_id == "A1SELLERID"
    assert overview.organization_id == str(created.organization_id)
    dumped = public_model_dump(overview)
    _assert_public(dumped)


def test_overview_ignores_sandbox_authorization_rows() -> None:
    service = AmazonConnectionService()
    service.create_connection(
        provider="SP_API",
        environment="SANDBOX",
        region="eu",
        status="pending_authorization",
    )
    overview = service.overview()
    assert overview.persisted is False
    assert overview.environment == "PRODUCTION"
    assert overview.connection_status == "not_connected"


def test_no_persisted_connection_falls_back_to_production_environment_view() -> None:
    service = AmazonConnectionService()
    overview = service.overview()
    assert overview.persisted is False
    assert overview.connection_status == "not_connected"
    assert overview.status == "NOT_CONNECTED"
    assert overview.environment == "PRODUCTION"
    assert overview.provider == "SP_API"
    assert overview.last_test_at is None
    assert overview.selling_partner_id is None
    _assert_public(public_model_dump(overview))


def test_fallback_does_not_create_database_records() -> None:
    assert _count_connections() == 0
    AmazonConnectionService().overview()
    assert _count_connections() == 0


@pytest.mark.asyncio
async def test_sandbox_test_success_does_not_mark_connection_connected() -> None:
    service = AmazonConnectionService(
        settings=_configured_settings(),
        sandbox_client_factory=_OkChecker,
    )
    service.create_connection(provider="SP_API", environment="SANDBOX", region="eu")
    result = await service.test_sp_api()
    assert result.status == "CONNECTED"
    overview = service.overview()
    assert overview.persisted is False
    assert overview.environment == "PRODUCTION"
    assert overview.status == "NOT_CONNECTED"
    assert overview.connection_status == "not_connected"
    with session_scope() as session:
        stored = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert stored is not None
        assert stored.status == "not_connected"
        assert stored.token_reference is None
    _assert_public(public_model_dump(result))
    _assert_public(public_model_dump(overview))


def test_organization_isolation_is_preserved() -> None:
    other_org = uuid4()
    service = AmazonConnectionService()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other"))
        AmazonConnectionRepository(session).create(
            organization_id=other_org,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
            selling_partner_id="B2OTHER",
        )
    fallback = service.overview()
    assert fallback.persisted is False
    assert fallback.selling_partner_id is None
    created = service.create_connection(provider="SP_API", environment="PRODUCTION", region="na")
    overview = service.overview()
    assert overview.persisted is True
    assert overview.organization_id == str(current_organization_id())
    assert overview.organization_id != str(other_org)
    assert overview.selling_partner_id is None
    with pytest.raises(PersistenceError, match="was not found"):
        service.update_connection(uuid4(), status="error")
    assert created.organization_id == str(current_organization_id())


def test_secret_fields_cannot_flow_through_service_methods() -> None:
    params = inspect.signature(AmazonConnectionService.create_connection).parameters
    for name in FORBIDDEN_SECRET_FIELDS:
        assert name not in params
    service = AmazonConnectionService()
    with pytest.raises(TypeError):
        service.create_connection(
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
            refresh_token="Atzr|must-not-store",  # type: ignore[call-arg]
        )
    created = service.create_connection(provider="SP_API", environment="SANDBOX", region="eu")
    dumped = public_model_dump(created)
    _assert_public(dumped)
    assert "token_reference" not in dumped
    with session_scope() as session:
        row = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert row is not None
        connection_id = row.id
    with pytest.raises(PersistenceError, match="cannot store secret fields"):
        service.update_connection(connection_id, refresh_token="Atzr|must-not-store")
    with pytest.raises(PersistenceError, match="cannot store secret fields"):
        service.update_connection(connection_id, token_reference="asi:dev:ref")
    overview = service.overview()
    assert overview.connection_status == "not_connected"
    _assert_public(public_model_dump(overview))


def test_repository_errors_are_handled() -> None:
    service = AmazonConnectionService()
    service.create_connection(provider="SP_API", environment="SANDBOX", region="eu")
    with pytest.raises(PersistenceError, match="already exists"):
        service.create_connection(provider="SP_API", environment="SANDBOX", region="eu")
    with pytest.raises(PersistenceError, match="was not found"):
        service.update_connection(uuid4(), status="error")
    with pytest.raises(PersistenceError, match="was not found"):
        service.delete_connection(uuid4())
    with session_scope() as session:
        row = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert row is not None
        connection_id = row.id
    updated = service.update_connection(
        connection_id,
        status="error",
        last_error_code="authentication",
        last_successful_validation_at=datetime(2026, 8, 23, tzinfo=UTC),
    )
    assert updated.connection_status == "error"
    assert updated.last_error_code == "authentication"
    assert updated.status == "NOT_CONNECTED"
    assert service.delete_connection(connection_id) is True
    assert service.overview().persisted is False


class _MismatchedIdentityValidator:
    """Defensive-path stub: returns `valid=True` with a `selling_partner_id`
    that disagrees with the connection's own stored identity. Amazon's real
    getMarketplaceParticipations response cannot produce this (it defines no
    such field at all — see AmazonSellerValidationService.validate), but the
    explicit equality guard in AmazonConnectionService._apply_seller_validation
    must still fail closed if it ever happened, e.g. via a future regression
    that reintroduces an independent identity source."""

    def __init__(self, selling_partner_id: str) -> None:
        self._selling_partner_id = selling_partner_id

    async def validate(self, *, organization_id, connection) -> SellerValidationResult:
        return SellerValidationResult(
            valid=True,
            selling_partner_id=self._selling_partner_id,
            marketplaces=[],
            connection_status="connected",
            reason="validated",
        )


@pytest.mark.asyncio
async def test_identity_mismatch_between_stored_and_result_fails_closed() -> None:
    """The connection's own persisted selling_partner_id is authoritative.
    If a SellerValidationResult ever disagrees with it, reconciliation must
    never run and the connection must fail closed — never appear connected."""
    provider = DevelopmentSecretProvider()
    service = AmazonConnectionService(
        settings=_configured_settings(),
        secret_provider=provider,
        seller_validator=_MismatchedIdentityValidator("DIFFERENT_FROM_STORED"),
    )
    org_id = current_organization_id()
    with session_scope() as session:
        repo = AmazonConnectionRepository(session)
        row = repo.create(
            organization_id=org_id,
            provider="SP_API",
            environment="PRODUCTION",
            region="na",
            status="pending_validation",
            selling_partner_id="STOREDSELLERID",
        )
        reference = build_asi_secret_reference(
            provider="SP_API",
            environment="PRODUCTION",
            organization_id=org_id,
            connection_id=row.id,
        )
        repo.bind_token_reference(org_id, row.id, reference)
        connection_id = row.id
    provider.put_secret(reference, SecretStr("Atzr|test-refresh-token"))

    result = await service.test_sp_api()
    assert result.status == "FAILED"
    assert "DIFFERENT_FROM_STORED" not in str(result)
    assert "STOREDSELLERID" not in str(result)

    with session_scope() as session:
        stored = AmazonConnectionRepository(session).get_by_id(org_id, connection_id)
        assert stored is not None
        assert stored.status == "error"
        assert stored.status != "connected"
        assert stored.last_error_code == "identity_conflict"
        # The pre-existing stored identity is left untouched, not overwritten
        # by the disagreeing result.
        assert stored.selling_partner_id == "STOREDSELLERID"
        # Reconciliation must never have been reached.
        assert AmazonSellerAccountRepository(session).list_for_org(org_id) == []
