"""12B.1A.4 — Amazon connection HTTP API. No OAuth, SecretProvider, or frontend."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import func, select

from app.amazon.common import reject_secret_fields
from app.amazon.connection import (
    AmazonConnectionOverview,
    AmazonConnectionService,
    AmazonConnectionTestResult,
    get_amazon_connection_service,
)
from app.amazon.models import MarketplaceParticipationsSandboxResult, SpApiSandboxProvenance
from app.amazon.sandbox import GET_MARKETPLACE_PARTICIPATIONS
from app.core.config import Settings
from app.main import app
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonConnection, Organization
from app.persistence.repositories import AmazonConnectionRepository

CONNECTION_URL = "/api/v1/amazon/connection"
TEST_URL = "/api/v1/amazon/connection/test"
SECRET_MARKERS = (
    "Atza|",
    "Atzr|",
    "client_secret",
    "refresh_token",
    "access_token",
    "x-amz-access-token",
    "token_reference",
    "client_id",
)
FORBIDDEN_SCHEMA_FIELDS = (
    "token_reference",
    "refresh_token",
    "access_token",
    "client_secret",
    "client_id",
)


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
        for name in FORBIDDEN_SCHEMA_FIELDS:
            assert name not in payload


def test_get_overview_returns_persisted_metadata(client) -> None:
    AmazonConnectionService().create_connection(
        provider="SP_API",
        environment="PRODUCTION",
        region="na",
        selling_partner_id="A1SELLERID",
    )
    response = client.get(CONNECTION_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NOT_CONNECTED"
    assert body["connection_status"] == "not_connected"
    assert body["persisted"] is True
    assert body["provider"] == "SP_API"
    assert body["environment"] == "PRODUCTION"
    assert body["region"] == "na"
    assert body["selling_partner_id"] == "A1SELLERID"
    assert body["marketplace"] == "amazon.com"
    assert body["application"] == "EWise"
    assert body["ads_api"]["status"] == "NOT_CONNECTED"
    assert "token_reference" not in body
    _assert_public(body)


def test_get_overview_falls_back_when_no_row(client) -> None:
    response = client.get(CONNECTION_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NOT_CONNECTED"
    assert body["connection_status"] == "not_connected"
    assert body["persisted"] is False
    assert body["provider"] == "SP_API"
    assert body["environment"] == "PRODUCTION"
    assert body["selling_partner_id"] is None
    assert body["last_test_at"] is None
    _assert_public(body)


def test_get_fallback_does_not_create_database_records(client) -> None:
    assert _count_connections() == 0
    response = client.get(CONNECTION_URL)
    assert response.status_code == 200
    assert response.json()["persisted"] is False
    assert _count_connections() == 0


def test_post_connection_test_returns_sandbox_result(client) -> None:
    service = AmazonConnectionService(
        settings=_configured_settings(),
        sandbox_client_factory=_OkChecker,
    )
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.post(TEST_URL, json={})
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CONNECTED"
    assert body["provider"] == "SP_API"
    assert body["environment"] == "SANDBOX"
    assert body["operation"] == GET_MARKETPLACE_PARTICIPATIONS
    assert body["tested_at"]
    _assert_public(body)


def test_post_connection_test_does_not_persist_connected(client) -> None:
    AmazonConnectionService().create_connection(
        provider="SP_API",
        environment="SANDBOX",
        region="eu",
    )
    service = AmazonConnectionService(
        settings=_configured_settings(),
        sandbox_client_factory=_OkChecker,
    )
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        tested = client.post(TEST_URL, json={})
        overview = client.get(CONNECTION_URL)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert tested.status_code == 200
    assert tested.json()["status"] == "CONNECTED"
    body = overview.json()
    assert body["persisted"] is False
    assert body["environment"] == "PRODUCTION"
    assert body["status"] == "NOT_CONNECTED"
    assert body["connection_status"] == "not_connected"
    with session_scope() as session:
        stored = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert stored is not None
        assert stored.status == "not_connected"
        assert stored.token_reference is None
    _assert_public(body)


def test_get_cannot_read_other_organization_connection(client) -> None:
    other_org = uuid4()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other"))
        AmazonConnectionRepository(session).create(
            organization_id=other_org,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
            selling_partner_id="B2OTHER",
        )
    response = client.get(CONNECTION_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["persisted"] is False
    assert body["selling_partner_id"] is None
    assert body["organization_id"] == str(current_organization_id())
    assert body["organization_id"] != str(other_org)
    _assert_public(body)


def test_connection_overview_exposes_canonical_marketplaces_after_test(client) -> None:
    """12B.2B — GET /connection surfaces reconciled seller identity, additively."""
    import json
    from pathlib import Path

    import httpx
    from pydantic import SecretStr

    from app.amazon.secrets import DevelopmentSecretProvider, build_asi_secret_reference
    from app.amazon.seller_validation import AmazonSellerValidationService
    from app.amazon.sellers import MARKETPLACE_PARTICIPATIONS_PATH

    # Amazon's real getMarketplaceParticipations response defines no
    # sellingPartnerId field at all — the connection's own OAuth-captured
    # selling_partner_id (set below) is the only authoritative identity.
    fixtures = Path(__file__).parent / "fixtures" / "sp_api"
    payload = json.loads((fixtures / "get_marketplace_participations.sandbox.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "Atza|x", "token_type": "bearer", "expires_in": 3600})
        assert request.url.path == MARKETPLACE_PARTICIPATIONS_PATH
        return httpx.Response(200, json=payload)

    settings = Settings(
        sp_api_lwa_client_id=SecretStr("amzn1.application-oa2-client.test"),
        sp_api_lwa_client_secret=SecretStr("test-lwa-client-secret-value"),
        sp_api_lwa_token_url="https://api.amazon.com/auth/o2/token",
        sp_api_application_name="EWise",
        default_marketplace="amazon.in",
        sp_api_region="na",
        cors_origins=["http://localhost:3000"],
    )
    provider = DevelopmentSecretProvider()
    service = AmazonConnectionService(
        settings=settings,
        secret_provider=provider,
        seller_validator=AmazonSellerValidationService(
            settings=settings,
            secret_provider=provider,
            transport=httpx.MockTransport(handler),
        ),
    )
    with session_scope() as session:
        row = AmazonConnectionRepository(session).create(
            organization_id=current_organization_id(),
            provider="SP_API",
            environment="PRODUCTION",
            region="na",
            status="pending_validation",
            selling_partner_id="A3FHEXAMPLEYWS",
        )
        reference = build_asi_secret_reference(
            provider="SP_API",
            environment="PRODUCTION",
            organization_id=current_organization_id(),
            connection_id=row.id,
        )
        AmazonConnectionRepository(session).bind_token_reference(current_organization_id(), row.id, reference)
    provider.put_secret(reference, SecretStr("Atzr|test-refresh-token"))

    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        tested = client.post(TEST_URL, json={})
        overview = client.get(CONNECTION_URL)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)

    assert tested.status_code == 200
    assert tested.json()["status"] == "CONNECTED"
    body = overview.json()
    assert body["connection_status"] == "connected"
    assert body["seller_account_id"] is not None
    assert body["seller_account_display_name"] == "BestSellerStore"
    assert body["marketplaces"][0]["marketplace_id"] == "ATVPDKIKX0DER"
    assert body["marketplaces"][0]["is_participating"] is True
    # 12B.3F: the participation's own id, so the frontend can address the
    # 12B.3E Listings Read API's `{marketplace_participation_id}` path.
    assert body["marketplaces"][0]["id"] is not None
    assert body["marketplaces"][0]["id"] != body["seller_account_id"]
    assert body["latest_ingestion"]["status"] == "succeeded"
    _assert_public(body)


def test_connection_overview_ignores_a_listings_run_when_computing_latest_ingestion() -> None:
    """Regression (12B.3G): live-reproduced against a real database on
    2026-08-29 as a 500 (`pydantic_core.ValidationError` on
    `AmazonIngestionStatusRead.started_at`) the moment any *queued*
    Listings run existed for a connection — a queued/waiting_to_retry
    Listings run has a NULL `started_at` (a `marketplace_participations`
    run is always immediately started), which sorts *before* a completed
    marketplace run under real PostgreSQL's default NULLS-FIRST-on-DESC
    ordering (SQLite does the opposite, so that exact manifestation can't
    be reproduced portably here).

    What's tested here instead is the underlying invariant the fix
    actually establishes, in a way that fails on any backend regardless
    of NULL-ordering semantics: a Listings run — even a `started` one
    with a perfectly valid, strictly *later* `started_at` than the
    marketplace run — must never become `latest_ingestion`. That field
    has only ever meant seller-validation ingestion status, never
    Listings synchronization.
    """
    from app.amazon.marketplace_reconciliation import INGESTION_DOMAIN
    from app.persistence.repositories import (
        AmazonIngestionRunRepository,
        AmazonMarketplaceParticipationRepository,
        AmazonSellerAccountRepository,
    )

    org_id = current_organization_id()
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).create(
            organization_id=org_id, provider="SP_API", environment="PRODUCTION", region="na"
        )
        session.flush()

        runs = AmazonIngestionRunRepository(session)
        marketplace_run = runs.start(
            organization_id=org_id,
            domain=INGESTION_DOMAIN,
            region="na",
            environment="PRODUCTION",
            connection_id=connection.id,
        )
        runs.complete(org_id, marketplace_run.id, status="succeeded", records_accepted=6)

        seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id="A1B2C3D4E5F6G7"
        )
        session.flush()
        participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id,
            seller_account_id=seller_account.id,
            marketplace_id="ATVPDKIKX0DER",
            region="na",
            connection_id=connection.id,
        )
        session.flush()
        # Claimed (not merely enqueued) *after* the marketplace run
        # completes, so its `started_at` (set via `func.now()`) is
        # strictly later — this must still lose to the marketplace run
        # under any DB's ordering, because it is the wrong `run_type`
        # entirely, not because of how the two timestamps compare.
        claim = runs.claim_listings_run(
            organization_id=org_id,
            seller_account_id=seller_account.id,
            marketplace_participation_id=participation.id,
            region="na",
            environment="PRODUCTION",
            connection_id=connection.id,
            lease_owner="test-worker",
            lease_duration_seconds=300,
        )
        assert claim.claimed is True
        session.commit()
        connection_id = connection.id

    service = AmazonConnectionService(settings=Settings(_env_file=None))
    with session_scope() as session:
        row = session.get(AmazonConnection, connection_id)
        latest = service._latest_ingestion_read_state(session, row)

    assert latest is not None
    assert latest.status == "succeeded"
    assert latest.started_at is not None


def test_connection_overview_degrades_when_seller_identity_tables_are_missing(client) -> None:
    """12B.2B regression — a pre-0009 database (e.g. the configured Supabase
    instance, which is deliberately kept on 0008) must not crash GET /connection.
    The 12B.2A canonical tables are dropped here to reproduce that exact schema.
    """
    from sqlalchemy import text

    from app.persistence.database import get_engine
    from app.persistence.models import Base

    with session_scope() as session:
        AmazonConnectionRepository(session).create(
            organization_id=current_organization_id(),
            provider="SP_API",
            environment="PRODUCTION",
            region="na",
            status="connected",
            selling_partner_id="A1SELLERID",
        )
    engine = get_engine()
    assert engine is not None
    for table_name in ("amazon_marketplace_participations", "amazon_ingestion_runs", "amazon_seller_accounts"):
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
    try:
        response = client.get(CONNECTION_URL)
        assert response.status_code == 200
        body = response.json()
        assert body["connection_status"] == "connected"
        assert body["selling_partner_id"] == "A1SELLERID"
        assert body["seller_account_id"] is None
        assert body["marketplaces"] == []
        assert body["latest_ingestion"] is None
        _assert_public(body)
    finally:
        Base.metadata.create_all(engine)


def test_secret_fields_cannot_be_returned(client) -> None:
    with session_scope() as session:
        session.add(
            AmazonConnection(
                organization_id=current_organization_id(),
                provider="SP_API",
                environment="PRODUCTION",
                region="na",
                status="not_connected",
                token_reference="asi:dev:must-not-appear",
            )
        )
    response = client.get(CONNECTION_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["persisted"] is True
    assert "token_reference" not in body
    assert "asi:dev:must-not-appear" not in str(body)
    _assert_public(body)

    overview_schema = AmazonConnectionOverview.model_json_schema()
    test_schema = AmazonConnectionTestResult.model_json_schema()
    for name in FORBIDDEN_SCHEMA_FIELDS:
        assert name not in overview_schema.get("properties", {})
        assert name not in test_schema.get("properties", {})

    rejected = client.post(TEST_URL, json={"refresh_token": "x"})
    assert rejected.status_code == 400
