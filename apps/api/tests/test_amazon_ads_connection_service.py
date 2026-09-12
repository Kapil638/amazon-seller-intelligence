from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from app.amazon.ads_client import AdsRequestContext, MockAmazonAdsApiClient
from app.amazon.ads_connection import AmazonAdsConnectionService
from app.amazon.ads_lwa_token import AdsLwaTokenService
from app.amazon.ads_models import AdsAccountInfo, AdsProfileResponse
from app.amazon.models import LwaAuthorizationGrant
from app.amazon.secrets import (
    DevelopmentSecretProvider,
    build_asi_secret_reference,
    parse_asi_amazon_secret_reference,
)
from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID, Settings
from app.core.exceptions import AdsConfigurationError, AdsProfileNotFoundError
from app.persistence.database import reset_persistence, session_scope
from app.persistence.repositories import (
    AmazonAdsConnectionRepository,
    AmazonAdsOAuthStateRepository,
    AmazonAdsProfileRepository,
)

ORG_ID = DEFAULT_DEVELOPMENT_ORGANIZATION_ID
OTHER_ORG_ID = UUID("22222222-2222-4222-8222-222222222222")


class _FakeLwaTokenService:
    def __init__(self, grant: LwaAuthorizationGrant | None = None, *, raises: Exception | None = None) -> None:
        self.grant = grant
        self.raises = raises
        self.calls: list[str] = []

    def exchange_authorization_code(self, authorization_code: SecretStr) -> LwaAuthorizationGrant:
        self.calls.append(authorization_code.get_secret_value())
        if self.raises:
            raise self.raises
        assert self.grant is not None
        return self.grant


def _configured_settings(**overrides) -> Settings:
    base = dict(
        ads_lwa_client_id=SecretStr("amzn1.application-oa2-client.test"),
        ads_lwa_client_secret=SecretStr("test-secret"),
        ads_oauth_redirect_uri="https://api.ewiseintelligence.com/api/v1/amazon/ads-connection/callback",
        database_url="sqlite://",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _reset_db():
    reset_persistence()
    yield
    reset_persistence()


def _grant(refresh_value: str = "Atzr|fake-ads-refresh-token") -> LwaAuthorizationGrant:
    return LwaAuthorizationGrant(
        access_token=SecretStr("Atza|fake-ads-access-token"),
        refresh_token=SecretStr(refresh_value),
        token_type="bearer",
        expires_in=3600,
    )


def _sample_profile(profile_id: int = 111) -> AdsProfileResponse:
    return AdsProfileResponse(
        profileId=profile_id,
        countryCode="US",
        currencyCode="USD",
        timezone="America/Los_Angeles",
        accountInfo=AdsAccountInfo(marketplaceStringId="ATVPDKIKX0DER", id="entity1", type="seller", name="AJ Duran"),
    )


def test_start_authorization_fails_closed_without_configuration() -> None:
    service = AmazonAdsConnectionService(settings=Settings(database_url="sqlite://"))
    with pytest.raises(AdsConfigurationError):
        service.start_authorization()


def test_start_authorization_binds_state_to_org_connection_and_return_path() -> None:
    settings = _configured_settings()
    service = AmazonAdsConnectionService(settings=settings)
    result = service.start_authorization(
        return_path="/seller/advertising", initiating_user_identity="operator@ewisepartners.com"
    )
    assert "https://www.amazon.com/ap/oa" in result.authorization_url
    with session_scope() as session:
        connection = AmazonAdsConnectionRepository(session).get_for_org(ORG_ID)
        assert connection is not None
        assert connection.status == "pending_authorization"
        states = AmazonAdsOAuthStateRepository(session).session.execute(
            __import__("sqlalchemy").select(__import__("app.persistence.models", fromlist=["AmazonAdsOAuthState"]).AmazonAdsOAuthState)
        ).scalars().all()
        assert len(states) == 1
        state = states[0]
        assert state.organization_id == ORG_ID
        assert state.connection_id == connection.id
        assert state.return_path == "/seller/advertising"
        assert state.initiating_user_identity == "operator@ewisepartners.com"
        assert state.consumed_at is None


def test_start_authorization_rejects_disallowed_return_path() -> None:
    settings = _configured_settings()
    service = AmazonAdsConnectionService(settings=settings)
    service.start_authorization(return_path="https://evil.example.com/steal")
    with session_scope() as session:
        from app.persistence.models import AmazonAdsOAuthState
        import sqlalchemy as sa

        state = session.execute(sa.select(AmazonAdsOAuthState)).scalars().first()
        assert state.return_path == "/seller/advertising"


async def test_callback_completes_and_stores_refresh_token_via_secret_provider() -> None:
    settings = _configured_settings()
    secrets = DevelopmentSecretProvider(default_organization_id=ORG_ID)
    client = MockAmazonAdsApiClient(profiles=[_sample_profile()])
    grant = _grant("Atzr|super-secret-ads-refresh")
    lwa = _FakeLwaTokenService(grant=grant)
    service = AmazonAdsConnectionService(settings=settings, secret_provider=secrets, ads_client=client, lwa_token_service=lwa)

    start = service.start_authorization()
    raw_state = start.authorization_url.split("state=")[1].split("&")[0]
    import urllib.parse

    raw_state = urllib.parse.unquote(raw_state)

    result = await service.complete_authorization_callback(state=raw_state, code="auth-code-123", error=None)
    assert result.notice == "success"
    assert lwa.calls == ["auth-code-123"]

    with session_scope() as session:
        connection = AmazonAdsConnectionRepository(session).get_for_org(ORG_ID)
        assert connection.status == "connected"
        assert connection.token_reference is not None
        parsed = parse_asi_amazon_secret_reference(connection.token_reference)
        assert parsed.provider == "ADS_API"
        stored = secrets.get_secret(connection.token_reference)
        assert stored.get_secret_value() == "Atzr|super-secret-ads-refresh"

        profiles = AmazonAdsProfileRepository(session).list_for_connection(ORG_ID, connection.id)
        assert len(profiles) == 1
        assert profiles[0].profile_id == "111"
        assert profiles[0].region == "NA"


async def test_callback_rejects_replayed_state() -> None:
    settings = _configured_settings()
    client = MockAmazonAdsApiClient(profiles=[_sample_profile()])
    lwa = _FakeLwaTokenService(grant=_grant())
    service = AmazonAdsConnectionService(
        settings=settings, secret_provider=DevelopmentSecretProvider(default_organization_id=ORG_ID),
        ads_client=client, lwa_token_service=lwa,
    )
    start = service.start_authorization()
    import urllib.parse

    raw_state = urllib.parse.unquote(start.authorization_url.split("state=")[1].split("&")[0])

    first = await service.complete_authorization_callback(state=raw_state, code="code-a", error=None)
    assert first.notice == "success"
    assert lwa.calls == ["code-a"]

    second = await service.complete_authorization_callback(state=raw_state, code="code-b", error=None)
    assert second.notice == "error"
    # The second, replayed callback must never reach the token exchanger.
    assert lwa.calls == ["code-a"]


async def test_callback_rejects_expired_state() -> None:
    settings = _configured_settings(ads_oauth_state_ttl_seconds=1)
    lwa = _FakeLwaTokenService(grant=_grant())
    service = AmazonAdsConnectionService(
        settings=settings, secret_provider=DevelopmentSecretProvider(default_organization_id=ORG_ID),
        ads_client=MockAmazonAdsApiClient(), lwa_token_service=lwa,
    )
    start = service.start_authorization()
    import urllib.parse

    raw_state = urllib.parse.unquote(start.authorization_url.split("state=")[1].split("&")[0])

    with session_scope() as session:
        from app.amazon.ads_oauth import hash_ads_oauth_state
        from app.persistence.repositories import AmazonAdsOAuthStateRepository as Repo

        state_hash = hash_ads_oauth_state(raw_state)
        row = Repo(session).get_by_hash(state_hash)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=5)
        session.flush()

    result = await service.complete_authorization_callback(state=raw_state, code="late-code", error=None)
    assert result.notice == "expired"
    assert lwa.calls == []


async def test_callback_denied_never_exchanges_a_code() -> None:
    lwa = _FakeLwaTokenService(grant=_grant())
    service = AmazonAdsConnectionService(
        settings=_configured_settings(), secret_provider=DevelopmentSecretProvider(default_organization_id=ORG_ID),
        ads_client=MockAmazonAdsApiClient(), lwa_token_service=lwa,
    )
    result = await service.complete_authorization_callback(state=None, code=None, error="access_denied")
    assert result.notice == "denied"
    assert lwa.calls == []


async def test_callback_with_unknown_state_is_rejected_generically() -> None:
    service = AmazonAdsConnectionService(
        settings=_configured_settings(), secret_provider=DevelopmentSecretProvider(default_organization_id=ORG_ID),
        ads_client=MockAmazonAdsApiClient(), lwa_token_service=_FakeLwaTokenService(grant=_grant()),
    )
    result = await service.complete_authorization_callback(state="forged-state-value", code="x", error=None)
    assert result.notice == "error"


def test_select_profile_rejects_cross_connection_profile() -> None:
    """A profile id from one organization's connection can never be
    selected via another organization's service call — proves the
    ownership check in `select_profile`, not just `get_owned`'s filter."""
    settings = _configured_settings()
    with session_scope() as session:
        conn_a = AmazonAdsConnectionRepository(session).get_or_create_for_org(ORG_ID)
        conn_b = AmazonAdsConnectionRepository(session).get_or_create_for_org(OTHER_ORG_ID)
        profile_b = AmazonAdsProfileRepository(session).upsert_many(
            OTHER_ORG_ID,
            conn_b.id,
            [
                {
                    "profile_id": "999",
                    "account_id": "acct",
                    "account_type": "seller",
                    "marketplace_country_code": "US",
                    "currency_code": "USD",
                    "timezone": "UTC",
                    "region": "NA",
                    "display_name": "Someone Else",
                }
            ],
        )[0]
        profile_b_id = str(profile_b.id)

    service = AmazonAdsConnectionService(settings=settings)
    with pytest.raises(AdsProfileNotFoundError):
        service.select_profile(profile_b_id)


def test_select_profile_deselects_other_profiles_for_same_connection() -> None:
    settings = _configured_settings()
    with session_scope() as session:
        connection = AmazonAdsConnectionRepository(session).get_or_create_for_org(ORG_ID)
        rows = AmazonAdsProfileRepository(session).upsert_many(
            ORG_ID,
            connection.id,
            [
                {
                    "profile_id": "1",
                    "account_id": "a",
                    "account_type": "seller",
                    "marketplace_country_code": "US",
                    "currency_code": "USD",
                    "timezone": "UTC",
                    "region": "NA",
                    "display_name": "Profile One",
                },
                {
                    "profile_id": "2",
                    "account_id": "b",
                    "account_type": "seller",
                    "marketplace_country_code": "US",
                    "currency_code": "USD",
                    "timezone": "UTC",
                    "region": "NA",
                    "display_name": "Profile Two",
                },
            ],
        )
        first_id, second_id = str(rows[0].id), str(rows[1].id)

    service = AmazonAdsConnectionService(settings=settings)
    service.select_profile(first_id)
    selected = service.select_profile(second_id)
    assert selected.is_selected is True

    with session_scope() as session:
        profiles = AmazonAdsProfileRepository(session).list_for_connection(ORG_ID, connection.id)
        selected_ids = [str(p.id) for p in profiles if p.is_selected]
        assert selected_ids == [second_id]
