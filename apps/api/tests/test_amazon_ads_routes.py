from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID, get_settings
from app.persistence.database import reset_persistence, session_scope
from app.persistence.repositories import AmazonAdsConnectionRepository, AmazonAdsProfileRepository

ORG_ID = DEFAULT_DEVELOPMENT_ORGANIZATION_ID
OTHER_ORG_ID = UUID("33333333-3333-4333-8333-333333333333")


@pytest.fixture(autouse=True)
def _reset_db():
    reset_persistence()
    get_settings.cache_clear()
    yield
    reset_persistence()
    get_settings.cache_clear()


def test_login_fails_closed_when_unconfigured(client: TestClient) -> None:
    response = client.get("/api/v1/amazon/ads-connection/login", follow_redirects=False)
    assert response.status_code == 503
    assert response.json()["detail"] == "Amazon Ads is not configured."


@pytest.fixture
def configured_ads(monkeypatch: pytest.MonkeyPatch):
    """Ads env vars unset by default (see conftest.py — only SP-API vars
    are seeded there) — `Settings.ads_lwa_client_id` etc. default to
    None/empty, so `AmazonAdsConnectionService.is_configured()` is False
    for every test in this file unless a test opts in via this fixture."""
    monkeypatch.setenv("ADS_LWA_CLIENT_ID", "amzn1.application-oa2-client.test")
    monkeypatch.setenv("ADS_LWA_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("ADS_OAUTH_REDIRECT_URI", "https://api.ewiseintelligence.com/api/v1/amazon/ads-connection/callback")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_status_never_returns_a_token_or_secret_reference(client: TestClient) -> None:
    response = client.get("/api/v1/amazon/ads-connection/status")
    assert response.status_code == 200
    body = response.json()
    dumped = str(body)
    assert "token_reference" not in dumped
    assert "Atzr" not in dumped
    assert "Atza" not in dumped


def test_select_profile_returns_404_for_unknown_profile(client: TestClient) -> None:
    response = client.post(
        "/api/v1/amazon/ads-connection/profiles/select",
        json={"ads_profile_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 404


def test_select_profile_rejects_a_profile_belonging_to_another_organization(client: TestClient) -> None:
    with session_scope() as session:
        other_connection = AmazonAdsConnectionRepository(session).get_or_create_for_org(OTHER_ORG_ID)
        other_profile = AmazonAdsProfileRepository(session).upsert_many(
            OTHER_ORG_ID,
            other_connection.id,
            [
                {
                    "profile_id": "555",
                    "account_id": "a",
                    "account_type": "seller",
                    "marketplace_country_code": "US",
                    "currency_code": "USD",
                    "timezone": "UTC",
                    "region": "NA",
                    "display_name": "Not This Org",
                }
            ],
        )[0]
        foreign_id = str(other_profile.id)

    response = client.post("/api/v1/amazon/ads-connection/profiles/select", json={"ads_profile_id": foreign_id})
    assert response.status_code == 404


def test_campaigns_endpoint_returns_404_for_unknown_profile(client: TestClient) -> None:
    response = client.get(
        "/api/v1/amazon/ads/campaigns", params={"ads_profile_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert response.status_code == 404


def test_campaigns_endpoint_paginates_with_bounded_page_size(client: TestClient) -> None:
    with session_scope() as session:
        connection = AmazonAdsConnectionRepository(session).get_or_create_for_org(ORG_ID)
        profile = AmazonAdsProfileRepository(session).upsert_many(
            ORG_ID,
            connection.id,
            [
                {
                    "profile_id": "111",
                    "account_id": "a",
                    "account_type": "seller",
                    "marketplace_country_code": "US",
                    "currency_code": "USD",
                    "timezone": "UTC",
                    "region": "NA",
                    "display_name": "AJ Duran",
                }
            ],
        )[0]
        from app.persistence.repositories import AmazonAdsCampaignRepository

        repo = AmazonAdsCampaignRepository(session)
        for i in range(5):
            repo.upsert(
                ORG_ID,
                profile.id,
                {
                    "external_campaign_id": f"c-{i}",
                    "name": f"Campaign {i}",
                    "state": "ENABLED",
                    "targeting_type": None,
                    "daily_budget": None,
                    "currency_code": "USD",
                    "start_date": None,
                    "end_date": None,
                    "portfolio_id": None,
                },
            )
        profile_id = str(profile.id)

    response = client.get("/api/v1/amazon/ads/campaigns", params={"ads_profile_id": profile_id, "limit": 2, "offset": 0})
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["page"] == {"offset": 0, "limit": 2, "total": 5}

    # A caller-requested page size above the hard cap is clamped, never honored verbatim.
    response = client.get(
        "/api/v1/amazon/ads/campaigns", params={"ads_profile_id": profile_id, "limit": 10_000, "offset": 0}
    )
    assert response.json()["page"]["limit"] == 100


def test_sync_status_reports_not_configured_before_validating_the_profile(client: TestClient) -> None:
    """`not_configured` is the highest-priority state (it means the whole
    feature is unset up) — the route answers it without needing a real
    profile id, rather than 404ing on the placeholder one a caller sends
    before any profile could possibly exist yet."""
    response = client.get(
        "/api/v1/amazon/ads/sync-status", params={"ads_profile_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "not_configured"


def test_sync_status_distinguishes_not_connected_from_awaiting_first_sync(
    client: TestClient, configured_ads: None
) -> None:
    with session_scope() as session:
        AmazonAdsConnectionRepository(session).get_or_create_for_org(ORG_ID)
    # Connection exists but is not "connected" yet.
    with session_scope() as session:
        connection = AmazonAdsConnectionRepository(session).get_for_org(ORG_ID)
        profile = AmazonAdsProfileRepository(session).upsert_many(
            ORG_ID,
            connection.id,
            [
                {
                    "profile_id": "222",
                    "account_id": "a",
                    "account_type": "seller",
                    "marketplace_country_code": "US",
                    "currency_code": "USD",
                    "timezone": "UTC",
                    "region": "NA",
                    "display_name": "AJ Duran",
                }
            ],
        )[0]
        profile_id = str(profile.id)

    response = client.get("/api/v1/amazon/ads/sync-status", params={"ads_profile_id": profile_id})
    assert response.status_code == 200
    assert response.json()["status"] == "not_connected"

    with session_scope() as session:
        connection = AmazonAdsConnectionRepository(session).get_for_org(ORG_ID)
        AmazonAdsConnectionRepository(session).mark_connected(
            ORG_ID, connection.id, token_reference="asi/amazon/ADS_API/PRODUCTION/x/y", authorized_at=datetime.now()
        )

    response = client.get("/api/v1/amazon/ads/sync-status", params={"ads_profile_id": profile_id})
    assert response.json()["status"] == "awaiting_first_sync"
