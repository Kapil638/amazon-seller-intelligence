from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from app.amazon.ads_client import EntityParseResult, MockAmazonAdsApiClient
from app.amazon.ads_entity_sync_service import AmazonAdsEntitySyncService
from app.amazon.ads_models import AdsAdGroupResponse, AdsCampaignResponse, AdsKeywordResponse
from app.amazon.secrets import DevelopmentSecretProvider, build_asi_secret_reference
from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID, Settings
from app.persistence.database import reset_persistence, session_scope
from app.persistence.repositories import (
    AmazonAdsAdGroupRepository,
    AmazonAdsCampaignRepository,
    AmazonAdsConnectionRepository,
    AmazonAdsEntitySyncCheckpointRepository,
    AmazonAdsEntitySyncRunRepository,
    AmazonAdsKeywordRepository,
    AmazonAdsProfileRepository,
    AmazonAdsSyncErrorRepository,
)

ORG_ID = DEFAULT_DEVELOPMENT_ORGANIZATION_ID


@pytest.fixture(autouse=True)
def _reset_db():
    reset_persistence()
    yield
    reset_persistence()


def _settings(**overrides) -> Settings:
    base = dict(
        ads_lwa_client_id=SecretStr("client"),
        ads_lwa_client_secret=SecretStr("secret"),
        ads_entity_sync_max_pages=10,
        ads_entity_sync_max_attempts=3,
        ads_entity_sync_lease_duration_seconds=60,
        ads_entity_sync_ad_groups_enabled=True,
        ads_entity_sync_keywords_enabled=True,
        database_url="sqlite://",
    )
    base.update(overrides)
    return Settings(**base)


def _connected_profile(*, region: str = "NA", profile_id: str = "111") -> tuple:
    with session_scope() as session:
        connection = AmazonAdsConnectionRepository(session).get_or_create_for_org(ORG_ID)
        secrets = DevelopmentSecretProvider(default_organization_id=ORG_ID)
        reference = build_asi_secret_reference(
            provider="ADS_API", environment="PRODUCTION", organization_id=ORG_ID, connection_id=connection.id
        )
        secrets.put_secret(reference, SecretStr("Atzr|fake-refresh"))
        AmazonAdsConnectionRepository(session).mark_connected(
            ORG_ID, connection.id, token_reference=reference, authorized_at=datetime.now()
        )
        profile = AmazonAdsProfileRepository(session).upsert_many(
            ORG_ID,
            connection.id,
            [
                {
                    "profile_id": profile_id,
                    "account_id": "a",
                    "account_type": "seller",
                    "marketplace_country_code": "US",
                    "currency_code": "USD",
                    "timezone": "America/Los_Angeles",
                    "region": region,
                    "display_name": "AJ Duran",
                }
            ],
        )[0]
        return connection.id, profile.id, secrets


async def _fake_refresh(*args, **kwargs):
    from app.amazon.models import LwaTokenResponse

    return LwaTokenResponse(access_token=SecretStr("Atza|access"), token_type="bearer", expires_in=3600)


@dataclass
class _FixedResultClient:
    """A minimal stub client for exercising an `EntityParseResult` the
    ordinary `MockAmazonAdsApiClient` cannot produce (rejection/
    unsupported-state counts alongside real accepted items) — its own
    `_paged` always hands back already-accepted items (see its own
    docstring)."""

    campaigns_result: EntityParseResult | None = None
    calls: list = field(default_factory=list)

    async def list_campaigns(self, ctx, *, next_token=None, page_size=100):
        self.calls.append("list_campaigns")
        return self.campaigns_result


def _seed_campaign(profile_id, *, external_id="c-1", is_active: bool = True):
    with session_scope() as session:
        row = AmazonAdsCampaignRepository(session).upsert(
            ORG_ID, profile_id, {"external_campaign_id": external_id, "name": "Seed", "state": "ENABLED"}
        )
        row_id = row.id
        if not is_active:
            import sqlalchemy as sa

            from app.persistence.models import AmazonAdsCampaign

            session.execute(sa.update(AmazonAdsCampaign).where(AmazonAdsCampaign.id == row_id).values(is_active=False))
        session.flush()
        return row_id


@pytest.mark.asyncio
async def test_multipage_campaign_sync_persists_all_items_and_advances_checkpoint(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(
        campaigns_pages=[
            ([AdsCampaignResponse(campaignId="c-1", name="A", state="ENABLED")], "TOK1"),
            ([AdsCampaignResponse(campaignId="c-2", name="B", state="PAUSED")], None),
        ]
    )
    service = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "succeeded"
    assert outcome.pages_processed == 2
    assert outcome.items_accepted == 2

    with session_scope() as session:
        rows, total = AmazonAdsCampaignRepository(session).list_for_profile(ORG_ID, profile_id, offset=0, limit=10)
        assert total == 2
        assert {r.external_campaign_id for r in rows} == {"c-1", "c-2"}
        assert all(r.is_active for r in rows)
        run = AmazonAdsEntitySyncRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.items_deactivated == 0
        assert run.items_reactivated == 0
        checkpoint = AmazonAdsEntitySyncCheckpointRepository(session).get(profile_id, "campaign")
        assert checkpoint is not None
        assert checkpoint.last_successful_sync_at is not None
        assert checkpoint.ads_connection_id == _connection_id


@pytest.mark.asyncio
async def test_idempotent_replay_does_not_duplicate_rows(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()

    def _client() -> MockAmazonAdsApiClient:
        return MockAmazonAdsApiClient(campaigns_pages=[([AdsCampaignResponse(campaignId="c-1", name="A", state="ENABLED")], None)])

    service = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=_client(), lease_owner="w1")
    service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")
    await service.process_one_claimed_run()

    service2 = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=_client(), lease_owner="w1")
    service2.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")
    outcome2 = await service2.process_one_claimed_run()
    assert outcome2.outcome == "succeeded"

    with session_scope() as session:
        _rows, total = AmazonAdsCampaignRepository(session).list_for_profile(ORG_ID, profile_id, offset=0, limit=10)
        assert total == 1


@pytest.mark.asyncio
async def test_cyclic_pagination_token_fails_without_looping_forever_or_leaking_the_token(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    secret_token = "TOK-CYCLE-abc123"
    client = MockAmazonAdsApiClient(
        campaigns_pages=[
            ([AdsCampaignResponse(campaignId="c-1", name="A", state="ENABLED")], secret_token),
            ([AdsCampaignResponse(campaignId="c-2", name="B", state="ENABLED")], secret_token),
        ]
    )
    service = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "failed"
    with session_scope() as session:
        run = AmazonAdsEntitySyncRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.failure_class == "entity_sync_cyclic_pagination_token"
        assert secret_token not in (run.failure_detail or "")
        rows, total = AmazonAdsCampaignRepository(session).list_for_profile(ORG_ID, profile_id, offset=0, limit=10)
        assert total == 0


@pytest.mark.asyncio
async def test_page_limit_exceeded_fails_visibly_and_persists_nothing(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(
        campaigns_pages=[
            ([AdsCampaignResponse(campaignId="c-1", name="A", state="ENABLED")], "TOK1"),
            ([AdsCampaignResponse(campaignId="c-2", name="B", state="ENABLED")], "TOK2"),
            ([AdsCampaignResponse(campaignId="c-3", name="C", state="ENABLED")], None),
        ]
    )
    service = AmazonAdsEntitySyncService(
        settings=_settings(ads_entity_sync_max_pages=2), secret_provider=secrets, ads_client=client, lease_owner="w1"
    )
    run_id = service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "failed"
    with session_scope() as session:
        run = AmazonAdsEntitySyncRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.failure_class == "entity_sync_page_limit_exceeded"
        assert run.items_deactivated is None
        assert run.items_reactivated is None
        _rows, total = AmazonAdsCampaignRepository(session).list_for_profile(ORG_ID, profile_id, offset=0, limit=10)
        assert total == 0
        checkpoint = AmazonAdsEntitySyncCheckpointRepository(session).get(profile_id, "campaign")
        assert checkpoint is None


@pytest.mark.asyncio
async def test_contract_mismatch_all_items_rejected_fails_without_persisting(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    mismatch_result = EntityParseResult(
        items=[], total_items=3, accepted_items=0, schema_rejected_items=3, unsupported_state_items=0, next_token=None
    )
    client = _FixedResultClient(campaigns_result=mismatch_result)
    service = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "failed"
    with session_scope() as session:
        run = AmazonAdsEntitySyncRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.failure_class == "entity_sync_contract_mismatch"
        _rows, total = AmazonAdsCampaignRepository(session).list_for_profile(ORG_ID, profile_id, offset=0, limit=10)
        assert total == 0


@pytest.mark.asyncio
async def test_ad_group_with_missing_parent_produces_partial_not_succeeded(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    with session_scope() as session:
        campaign = AmazonAdsCampaignRepository(session).upsert(
            ORG_ID, profile_id, {"external_campaign_id": "c-1", "name": "A", "state": "ENABLED"}
        )
        campaign_id = campaign.id

    client = MockAmazonAdsApiClient(
        ad_groups_pages=[
            (
                [
                    AdsAdGroupResponse(adGroupId="ag-1", campaignId="c-1", name="Valid", state="ENABLED"),
                    AdsAdGroupResponse(adGroupId="ag-2", campaignId="c-orphan", name="Orphan", state="ENABLED"),
                ],
                None,
            )
        ]
    )
    service = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="ad_group")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "partial"
    assert outcome.items_accepted == 1
    with session_scope() as session:
        run = AmazonAdsEntitySyncRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "partial"
        assert run.items_missing_parent == 1
        assert run.items_mismatched_parent == 0
        assert run.items_deactivated is None
        assert run.items_reactivated is None
        rows, total = AmazonAdsAdGroupRepository(session).list_for_profile(ORG_ID, profile_id, offset=0, limit=10)
        assert total == 1
        assert rows[0].external_ad_group_id == "ag-1"
        assert rows[0].ads_campaign_id == campaign_id
        checkpoint = AmazonAdsEntitySyncCheckpointRepository(session).get(profile_id, "ad_group")
        assert checkpoint is None  # partial runs never advance a checkpoint


def _seed_campaign_and_ad_group(profile_id, *, campaign_external_id: str, ad_group_external_id: str):
    with session_scope() as session:
        campaign = AmazonAdsCampaignRepository(session).upsert(
            ORG_ID, profile_id, {"external_campaign_id": campaign_external_id, "name": "C", "state": "ENABLED"}
        )
        ad_group = AmazonAdsAdGroupRepository(session).upsert(
            ORG_ID, profile_id, campaign.id,
            {"external_ad_group_id": ad_group_external_id, "name": "AG", "state": "ENABLED"},
        )
        return campaign.id, ad_group.id


@pytest.mark.asyncio
async def test_valid_coherent_campaign_ad_group_pair_persists_a_keyword_cleanly(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    campaign_id, ad_group_id = _seed_campaign_and_ad_group(
        profile_id, campaign_external_id="c-1", ad_group_external_id="ag-1"
    )
    client = MockAmazonAdsApiClient(
        keywords_pages=[
            (
                [AdsKeywordResponse(keywordId="kw-1", adGroupId="ag-1", campaignId="c-1", keywordText="shoes", matchType="BROAD", state="ENABLED")],
                None,
            )
        ]
    )
    service = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="keyword")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "succeeded"
    with session_scope() as session:
        run = AmazonAdsEntitySyncRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "succeeded"
        assert run.items_mismatched_parent == 0
        rows, total = AmazonAdsKeywordRepository(session).list_for_profile(ORG_ID, profile_id, offset=0, limit=10)
        assert total == 1
        assert rows[0].ads_campaign_id == campaign_id
        assert rows[0].ads_ad_group_id == ad_group_id


@pytest.mark.asyncio
async def test_ad_group_belonging_to_a_different_campaign_branch_is_mismatched_not_persisted(monkeypatch) -> None:
    """Blocker 1's exact scenario: campaign and ad group both resolve
    independently (both exist locally), but the ad group does not
    actually belong to the campaign the item's own DTO claims."""
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    _campaign_a_id, _ad_group_a_id = _seed_campaign_and_ad_group(
        profile_id, campaign_external_id="c-a", ad_group_external_id="ag-a"
    )
    # ag-b genuinely belongs to c-b, not c-a — a real, separate hierarchy branch.
    _seed_campaign_and_ad_group(profile_id, campaign_external_id="c-b", ad_group_external_id="ag-b")

    client = MockAmazonAdsApiClient(
        keywords_pages=[
            (
                # This keyword's DTO claims campaignId=c-a but adGroupId=ag-b —
                # both resolve independently, but ag-b belongs to c-b.
                [AdsKeywordResponse(keywordId="kw-1", adGroupId="ag-b", campaignId="c-a", keywordText="shoes", matchType="BROAD", state="ENABLED")],
                None,
            )
        ]
    )
    service = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="keyword")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "partial"
    assert outcome.items_accepted == 0
    with session_scope() as session:
        run = AmazonAdsEntitySyncRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.items_mismatched_parent == 1
        assert run.items_missing_parent == 0
        _rows, total = AmazonAdsKeywordRepository(session).list_for_profile(ORG_ID, profile_id, offset=0, limit=10)
        assert total == 0
        errors = AmazonAdsSyncErrorRepository(session).list_recent(ORG_ID, profile_id)
        assert any(e.error_code == "entity_sync_mismatched_parent" for e in errors)


@pytest.mark.asyncio
async def test_same_external_ids_under_a_different_profile_never_resolve(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    # A campaign/ad-group pair with the SAME external ids exists, but
    # under a completely different profile.
    _other_connection_id, other_profile_id, _other_secrets = _connected_profile(region="EU", profile_id="222")
    _seed_campaign_and_ad_group(other_profile_id, campaign_external_id="c-1", ad_group_external_id="ag-1")

    client = MockAmazonAdsApiClient(
        keywords_pages=[
            (
                [AdsKeywordResponse(keywordId="kw-1", adGroupId="ag-1", campaignId="c-1", keywordText="shoes", matchType="BROAD", state="ENABLED")],
                None,
            )
        ]
    )
    service = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="keyword")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "partial"
    with session_scope() as session:
        run = AmazonAdsEntitySyncRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.items_missing_parent == 1  # not found in THIS profile's scope
        assert run.items_mismatched_parent == 0
        _rows, total = AmazonAdsKeywordRepository(session).list_for_profile(ORG_ID, profile_id, offset=0, limit=10)
        assert total == 0


@pytest.mark.asyncio
async def test_schema_rejected_items_prevent_checkpoint_and_reconciliation(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    mixed = EntityParseResult(
        items=[AdsCampaignResponse(campaignId="c-1", name="A", state="ENABLED")],
        total_items=2, accepted_items=1, schema_rejected_items=1, unsupported_state_items=0, next_token=None,
    )
    client = _FixedResultClient(campaigns_result=mixed)
    service = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "partial"
    with session_scope() as session:
        run = AmazonAdsEntitySyncRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "partial"
        assert run.items_deactivated is None
        assert run.items_reactivated is None
        assert AmazonAdsEntitySyncCheckpointRepository(session).get(profile_id, "campaign") is None
        # The one schema-valid item is still persisted — partial is not all-or-nothing.
        _rows, total = AmazonAdsCampaignRepository(session).list_for_profile(ORG_ID, profile_id, offset=0, limit=10)
        assert total == 1


@pytest.mark.asyncio
async def test_unsupported_state_items_prevent_checkpoint_and_reconciliation(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    mixed = EntityParseResult(
        items=[AdsCampaignResponse(campaignId="c-1", name="A", state="ENABLED")],
        total_items=2, accepted_items=1, schema_rejected_items=0, unsupported_state_items=1, next_token=None,
    )
    client = _FixedResultClient(campaigns_result=mixed)
    service = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "partial"
    with session_scope() as session:
        assert AmazonAdsEntitySyncCheckpointRepository(session).get(profile_id, "campaign") is None


@pytest.mark.asyncio
async def test_disabled_entity_type_gate_makes_zero_http_calls(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(
        ad_groups_pages=[([AdsAdGroupResponse(adGroupId="ag-1", campaignId="c-1", name="X", state="ENABLED")], None)]
    )
    service = AmazonAdsEntitySyncService(
        settings=_settings(ads_entity_sync_ad_groups_enabled=False),
        secret_provider=secrets, ads_client=client, lease_owner="w1",
    )
    run_id = service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="ad_group")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "failed"
    assert client.calls == []
    with session_scope() as session:
        run = AmazonAdsEntitySyncRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.failure_class == "entity_sync_disabled_for_entity_type"


@pytest.mark.asyncio
async def test_lease_loss_before_completion_rolls_back_the_whole_persist_transaction(monkeypatch) -> None:
    """Fenced completion — if mark_succeeded reports this worker no
    longer owns the lease, nothing from this run's persist transaction
    survives, including the upserts staged earlier in the same
    transaction (see `_persist_snapshot`'s own docstring)."""
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    monkeypatch.setattr(AmazonAdsEntitySyncRunRepository, "mark_succeeded", lambda self, *a, **kw: False)
    _connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(campaigns_pages=[([AdsCampaignResponse(campaignId="c-1", name="A", state="ENABLED")], None)])
    service = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "lease_lost"
    with session_scope() as session:
        _rows, total = AmazonAdsCampaignRepository(session).list_for_profile(ORG_ID, profile_id, offset=0, limit=10)
        assert total == 0
        checkpoint = AmazonAdsEntitySyncCheckpointRepository(session).get(profile_id, "campaign")
        assert checkpoint is None


@pytest.mark.asyncio
async def test_empty_clean_snapshot_deactivates_all_existing_active_rows(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    _seed_campaign(profile_id, external_id="c-1")
    _seed_campaign(profile_id, external_id="c-2")

    client = MockAmazonAdsApiClient(campaigns_pages=[([], None)])
    service = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "succeeded"
    with session_scope() as session:
        run = AmazonAdsEntitySyncRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.items_deactivated == 2
        assert run.items_reactivated == 0
        rows, _total = AmazonAdsCampaignRepository(session).list_for_profile(ORG_ID, profile_id, offset=0, limit=10)
        assert all(not r.is_active for r in rows)
        assert AmazonAdsEntitySyncCheckpointRepository(session).get(profile_id, "campaign") is not None


@pytest.mark.asyncio
async def test_reactivation_of_a_previously_inactive_row_on_a_later_clean_snapshot(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    row_id = _seed_campaign(profile_id, external_id="c-1", is_active=False)

    client = MockAmazonAdsApiClient(campaigns_pages=[([AdsCampaignResponse(campaignId="c-1", name="A", state="ENABLED")], None)])
    service = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "succeeded"
    with session_scope() as session:
        run = AmazonAdsEntitySyncRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.items_reactivated == 1
        assert run.items_deactivated == 0
        from app.persistence.models import AmazonAdsCampaign

        row = session.get(AmazonAdsCampaign, row_id)
        assert row.is_active is True
        assert row.last_seen_entity_sync_run_id == run_id


@pytest.mark.asyncio
async def test_deactivation_never_leaks_across_profiles(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    _other_connection_id, other_profile_id, _other_secrets = _connected_profile(region="EU", profile_id="222")
    _seed_campaign(profile_id, external_id="c-mine")
    other_row_id = _seed_campaign(other_profile_id, external_id="c-other")

    client = MockAmazonAdsApiClient(campaigns_pages=[([], None)])
    service = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "succeeded"
    with session_scope() as session:
        from app.persistence.models import AmazonAdsCampaign

        other_row = session.get(AmazonAdsCampaign, other_row_id)
        assert other_row.is_active is True  # untouched — different profile


@pytest.mark.asyncio
async def test_incomplete_run_never_deactivates_anything(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    row_id = _seed_campaign(profile_id, external_id="c-1")

    client = MockAmazonAdsApiClient(
        campaigns_pages=[
            ([AdsCampaignResponse(campaignId="c-2", name="B", state="ENABLED")], "TOK1"),
            ([AdsCampaignResponse(campaignId="c-3", name="C", state="ENABLED")], "TOK2"),
        ]
    )
    service = AmazonAdsEntitySyncService(
        settings=_settings(ads_entity_sync_max_pages=1), secret_provider=secrets, ads_client=client, lease_owner="w1"
    )
    service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "failed"
    with session_scope() as session:
        from app.persistence.models import AmazonAdsCampaign

        row = session.get(AmazonAdsCampaign, row_id)
        assert row.is_active is True  # never touched by a failed/incomplete run


def test_stale_lease_always_terminalizes_never_auto_resumed() -> None:
    connection_id, profile_id, _secrets = _connected_profile()
    with session_scope() as session:
        repo = AmazonAdsEntitySyncRunRepository(session)
        run = repo.enqueue(ORG_ID, connection_id, profile_id, entity_type="campaign")
        claimed = repo.claim_next_sync_run(lease_owner="worker-a", lease_duration_seconds=1, max_global_active=10)
        assert claimed is not None and claimed.id == run.id
        import sqlalchemy as sa

        from app.persistence.models import AmazonAdsEntitySyncRun

        session.execute(
            sa.update(AmazonAdsEntitySyncRun).where(AmazonAdsEntitySyncRun.id == run.id).values(
                lease_expires_at=datetime.now(UTC) - timedelta(seconds=5)
            )
        )
        session.flush()

    with session_scope() as session:
        repo = AmazonAdsEntitySyncRunRepository(session)
        result = repo.claim_next_sync_run(lease_owner="worker-b", lease_duration_seconds=60, max_global_active=10)
        assert result is None
        settled = repo.get_owned(ORG_ID, run.id)
        assert settled.status == "timed_out"
        assert settled.lease_owner is None


def test_enqueue_is_idempotent_for_an_already_active_run() -> None:
    connection_id, profile_id, _secrets = _connected_profile()
    with session_scope() as session:
        repo = AmazonAdsEntitySyncRunRepository(session)
        first = repo.enqueue(ORG_ID, connection_id, profile_id, entity_type="campaign")
        second = repo.enqueue(ORG_ID, connection_id, profile_id, entity_type="campaign")
        assert first.id == second.id


def test_checkpoint_advance_rejects_a_run_that_is_not_cleanly_succeeded() -> None:
    connection_id, profile_id, _secrets = _connected_profile()
    with session_scope() as session:
        run_repo = AmazonAdsEntitySyncRunRepository(session)
        run = run_repo.enqueue(ORG_ID, connection_id, profile_id, entity_type="campaign")
        run_repo.claim_next_sync_run(lease_owner="w1", lease_duration_seconds=60, max_global_active=10)
        # Mark it 'partial', not 'succeeded'.
        assert run_repo.mark_partial(
            run.id, lease_owner="w1", pages_processed=1, items_observed=1, items_accepted=0,
            items_schema_rejected=1, items_unsupported_state=0, items_missing_parent=0, items_mismatched_parent=0,
        )
        checkpoint_repo = AmazonAdsEntitySyncCheckpointRepository(session)
        with pytest.raises(ValueError):
            checkpoint_repo.advance(
                ORG_ID, connection_id, profile_id, entity_type="campaign", synced_at=datetime.now(UTC), run_id=run.id
            )
        assert checkpoint_repo.get(profile_id, "campaign") is None


def test_checkpoint_advance_rejects_a_foreign_profile_scope() -> None:
    connection_id, profile_id, _secrets = _connected_profile()
    _other_connection_id, other_profile_id, _other_secrets = _connected_profile(region="EU", profile_id="222")
    with session_scope() as session:
        run_repo = AmazonAdsEntitySyncRunRepository(session)
        run = run_repo.enqueue(ORG_ID, connection_id, profile_id, entity_type="campaign")
        run_repo.claim_next_sync_run(lease_owner="w1", lease_duration_seconds=60, max_global_active=10)
        assert run_repo.mark_succeeded(
            run.id, lease_owner="w1", pages_processed=1, items_observed=0, items_accepted=0,
            items_schema_rejected=0, items_unsupported_state=0, items_missing_parent=0, items_mismatched_parent=0,
            items_deactivated=0, items_reactivated=0,
        )
        checkpoint_repo = AmazonAdsEntitySyncCheckpointRepository(session)
        with pytest.raises(ValueError):
            # Right run id, but the WRONG profile scope.
            checkpoint_repo.advance(
                ORG_ID, connection_id, other_profile_id,
                entity_type="campaign", synced_at=datetime.now(UTC), run_id=run.id,
            )


@pytest.mark.asyncio
async def test_secret_shaped_exception_never_reaches_failure_detail_or_sync_errors(monkeypatch) -> None:
    secret_marker = "ATZA|LEAKED-SECRET-abc123"

    async def _raise_with_secret_text(*args, **kwargs):
        raise RuntimeError(f"connection failed while sending token {secret_marker}")

    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _raise_with_secret_text)
    _connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient()
    service = AmazonAdsEntitySyncService(
        settings=_settings(ads_entity_sync_max_attempts=1),
        secret_provider=secrets, ads_client=client, lease_owner="w1",
    )
    run_id = service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "failed"
    with session_scope() as session:
        run = AmazonAdsEntitySyncRunRepository(session).get_owned(ORG_ID, run_id)
        assert secret_marker not in (run.failure_detail or "")
        errors = AmazonAdsSyncErrorRepository(session).list_recent(ORG_ID, profile_id)
        assert all(secret_marker not in e.error_message for e in errors)
