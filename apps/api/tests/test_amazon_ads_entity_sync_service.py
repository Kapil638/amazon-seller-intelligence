from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import SecretStr

from app.amazon.ads_client import EntityParseResult, MockAmazonAdsApiClient
from app.amazon.ads_entity_sync_service import AmazonAdsEntitySyncService
from app.amazon.ads_models import AdsAdGroupResponse, AdsCampaignResponse
from app.amazon.secrets import DevelopmentSecretProvider, build_asi_secret_reference
from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID, Settings
from app.persistence.database import reset_persistence, session_scope
from app.persistence.repositories import (
    AmazonAdsAdGroupRepository,
    AmazonAdsCampaignRepository,
    AmazonAdsConnectionRepository,
    AmazonAdsEntitySyncCheckpointRepository,
    AmazonAdsEntitySyncRunRepository,
    AmazonAdsProfileRepository,
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
    """A minimal stub client for exercising a page whose EntityParseResult
    the ordinary MockAmazonAdsApiClient cannot produce (accepted_items=0
    with total_items>0) — MockAmazonAdsApiClient's `_paged` always hands
    back already-accepted items (see its own docstring), so a genuine
    contract-mismatch page is constructed directly here instead."""

    result: EntityParseResult
    calls: list = field(default_factory=list)

    async def list_campaigns(self, ctx, *, next_token=None, page_size=100):
        self.calls.append("list_campaigns")
        return self.result


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
    service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "succeeded"
    assert outcome.pages_processed == 2
    assert outcome.items_accepted == 2

    with session_scope() as session:
        rows, total = AmazonAdsCampaignRepository(session).list_for_profile(ORG_ID, profile_id, offset=0, limit=10)
        assert total == 2
        assert {r.external_campaign_id for r in rows} == {"c-1", "c-2"}
        checkpoint = AmazonAdsEntitySyncCheckpointRepository(session).get(profile_id, "campaign")
        assert checkpoint is not None
        assert checkpoint.last_successful_sync_at is not None


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
        assert run.reconciliation_stale_count is None
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
    client = _FixedResultClient(result=mismatch_result)
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
async def test_ad_group_sync_resolves_parent_campaign_and_counts_missing_parent(monkeypatch) -> None:
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

    assert outcome.outcome == "succeeded"
    assert outcome.items_accepted == 1
    with session_scope() as session:
        run = AmazonAdsEntitySyncRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.items_missing_parent == 1
        rows, total = AmazonAdsAdGroupRepository(session).list_for_profile(ORG_ID, profile_id, offset=0, limit=10)
        assert total == 1
        assert rows[0].external_ad_group_id == "ag-1"
        assert rows[0].ads_campaign_id == campaign_id


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
async def test_reconciliation_counts_only_rows_untouched_by_this_run_within_the_same_profile(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_entity_sync_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()
    _other_connection_id, other_profile_id, _other_secrets = _connected_profile(region="EU", profile_id="222")
    stale_seen_at = datetime.now(UTC) - timedelta(days=30)
    with session_scope() as session:
        repo = AmazonAdsCampaignRepository(session)
        repo.upsert(ORG_ID, profile_id, {"external_campaign_id": "c-stale", "name": "Stale", "state": "ENABLED"})
        repo.upsert(ORG_ID, other_profile_id, {"external_campaign_id": "c-other-profile", "name": "Other", "state": "ENABLED"})
        import sqlalchemy as sa

        from app.persistence.models import AmazonAdsCampaign

        session.execute(sa.update(AmazonAdsCampaign).values(last_seen_at=stale_seen_at))
        session.flush()

    client = MockAmazonAdsApiClient(campaigns_pages=[([AdsCampaignResponse(campaignId="c-fresh", name="Fresh", state="ENABLED")], None)])
    service = AmazonAdsEntitySyncService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.enqueue_sync_request(organization_id=ORG_ID, ads_profile_id=profile_id, entity_type="campaign")

    outcome = await service.process_one_claimed_run()

    assert outcome.outcome == "succeeded"
    with session_scope() as session:
        run = AmazonAdsEntitySyncRunRepository(session).get_owned(ORG_ID, run_id)
        # Only c-stale (this profile's own untouched row) counts — the
        # other profile's untouched row must never leak into this run's
        # reconciliation count.
        assert run.reconciliation_stale_count == 1


def test_stale_lease_always_terminalizes_never_auto_resumed() -> None:
    _connection_id, profile_id, _secrets = _connected_profile()
    with session_scope() as session:
        repo = AmazonAdsEntitySyncRunRepository(session)
        run = repo.enqueue(ORG_ID, profile_id, entity_type="campaign")
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
    _connection_id, profile_id, _secrets = _connected_profile()
    with session_scope() as session:
        repo = AmazonAdsEntitySyncRunRepository(session)
        first = repo.enqueue(ORG_ID, profile_id, entity_type="campaign")
        second = repo.enqueue(ORG_ID, profile_id, entity_type="campaign")
        assert first.id == second.id
