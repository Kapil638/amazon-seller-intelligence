from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import SecretStr

from app.amazon.ads_client import MockAmazonAdsApiClient
from app.amazon.ads_models import AdsReportStatusResponse
from app.amazon.ads_report_service import AmazonAdsReportService, next_sync_window
from app.amazon.secrets import DevelopmentSecretProvider, build_asi_secret_reference
from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID, Settings
from app.core.exceptions import AdsApiDuplicateReportError, AdsApiInvalidRequestError, AdsApiRateLimitedError
from app.persistence.database import reset_persistence, session_scope
from app.persistence.repositories import (
    AmazonAdsConnectionRepository,
    AmazonAdsDailyPerformanceFactRepository,
    AmazonAdsProfileRepository,
    AmazonAdsReportRunRepository,
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
        ads_report_poll_max_attempts=3,
        ads_report_poll_interval_seconds=0.001,
        database_url="sqlite://",
    )
    base.update(overrides)
    return Settings(**base)


def _connected_profile(*, region: str = "NA") -> tuple:
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
                    "profile_id": "111",
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


def _report_body(rows: list[dict]) -> bytes:
    """`AmazonAdsApiClient.download_report`'s documented contract is
    "return decompressed report bytes" — `MockAmazonAdsApiClient.
    download_report` is a dumb passthrough of `report_bodies`, so tests
    supply plain JSON here, not gzip-compressed bytes (gzip decoding is
    exercised separately in `test_amazon_ads_client.py` against the real
    `decompress_gzip_json` helper)."""
    return json.dumps(rows).encode("utf-8")


async def _fake_refresh(*args, **kwargs):
    from app.amazon.models import LwaTokenResponse

    return LwaTokenResponse(access_token=SecretStr("Atza|access"), token_type="bearer", expires_in=3600)


def test_next_sync_window_never_synced_starts_at_lookback() -> None:
    today = date(2026, 9, 12)
    start, end = next_sync_window(synced_through_date=None, today=today, lookback_days=3)
    assert start == date(2026, 9, 9)
    assert end == today


def test_next_sync_window_reapplies_rolling_lookback_even_when_synced() -> None:
    today = date(2026, 9, 12)
    start, end = next_sync_window(synced_through_date=date(2026, 9, 10), today=today, lookback_days=3)
    # Re-requests from before the last checkpoint, not just the new day —
    # late attribution adjustments on already-synced dates are refreshed.
    assert start == date(2026, 9, 7)
    assert end == today


def test_next_sync_window_never_produces_a_start_after_end() -> None:
    today = date(2026, 9, 12)
    start, end = next_sync_window(synced_through_date=date(2026, 9, 12), today=today, lookback_days=0)
    assert start <= end


@pytest.mark.asyncio
async def test_report_job_succeeds_and_ingests_decimal_safe_facts(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    body = _report_body(
        [
            {
                "date": "2026-09-01",
                "campaignId": "c-1",
                "impressions": 1000,
                "clicks": 42,
                "cost": "12.34",
                "sales14d": "99.99",
                "purchases14d": 3,
                "currency": "USD",
            }
        ]
    )
    client = MockAmazonAdsApiClient(
        report_status_sequence=[
            AdsReportStatusResponse(reportId="r-1", status="COMPLETED", url="https://x/report.gz")
        ],
        report_bodies={"https://x/report.gz": body},
    )
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="test-1")
    run_id = service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2026, 9, 1), end_date=date(2026, 9, 1)
    )
    outcome = await service.process_one_claimed_job()
    assert outcome.outcome == "succeeded"
    assert outcome.records_ingested == 1

    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "succeeded"
        facts = AmazonAdsDailyPerformanceFactRepository(session).series_for_profile(
            ORG_ID, profile_id, start=date(2026, 9, 1), end=date(2026, 9, 1)
        )
        assert len(facts) == 1
        fact = facts[0]
        assert fact.cost == Decimal("12.34")
        assert fact.attributed_sales == Decimal("99.99")
        assert fact.attributed_conversions == 3
        assert fact.currency_code == "USD"


@pytest.mark.asyncio
async def test_reingesting_the_same_report_does_not_duplicate_facts(monkeypatch) -> None:
    """Idempotent retry: processing the same date/campaign row twice
    upserts the same row rather than creating a second one."""
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    body = _report_body(
        [{"date": "2026-09-01", "campaignId": "c-1", "impressions": 5, "clicks": 1, "cost": "1.00", "sales14d": "2.00", "purchases14d": 1}]
    )
    client = MockAmazonAdsApiClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-1", status="COMPLETED", url="https://x/r.gz")],
        report_bodies={"https://x/r.gz": body},
    )
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")

    run_id_1 = service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2026, 9, 1), end_date=date(2026, 9, 1)
    )
    await service.process_one_claimed_job()
    run_id_2 = service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2026, 9, 1), end_date=date(2026, 9, 1)
    )
    await service.process_one_claimed_job()

    with session_scope() as session:
        facts = AmazonAdsDailyPerformanceFactRepository(session).series_for_profile(
            ORG_ID, profile_id, start=date(2026, 9, 1), end=date(2026, 9, 1)
        )
        assert len(facts) == 1  # not 2
        assert run_id_1 != run_id_2


@pytest.mark.asyncio
async def test_no_job_returns_no_job_outcome_without_touching_secrets() -> None:
    _connected_profile()
    service = AmazonAdsReportService(
        settings=_settings(),
        secret_provider=DevelopmentSecretProvider(default_organization_id=ORG_ID),
        ads_client=MockAmazonAdsApiClient(),
        lease_owner="idle",
    )
    outcome = await service.process_one_claimed_job()
    assert outcome.outcome == "no_job"


@pytest.mark.asyncio
async def test_terminal_amazon_failure_status_schedules_retry_then_eventually_fails(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-1", status="FAILURE", failureReason="boom")]
    )
    service = AmazonAdsReportService(
        settings=_settings(ads_report_poll_max_attempts=1), secret_provider=secrets, ads_client=client, lease_owner="w1"
    )
    run_id = service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2026, 9, 1), end_date=date(2026, 9, 1)
    )
    outcome = await service.process_one_claimed_job()
    assert outcome.outcome in ("retrying", "failed")

    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status in ("waiting_to_retry", "failed")
        assert run.failure_class in ("report_failed",)


@pytest.mark.asyncio
async def test_rate_limited_status_check_waits_and_retries_without_failing_the_job(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()

    class _RateLimitedThenDoneClient(MockAmazonAdsApiClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._first = True

        async def get_report_status(self, ctx, report_id):
            self.calls.append("get_report_status")
            if self._first:
                self._first = False
                raise AdsApiRateLimitedError("slow down", retry_after_seconds=0.001)
            return AdsReportStatusResponse(reportId=report_id, status="COMPLETED", url="https://x/r.gz")

    client = _RateLimitedThenDoneClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-1", status="PENDING")],
        report_bodies={
            "https://x/r.gz": _report_body(
                [{"date": "2026-09-01", "campaignId": "c-1", "impressions": 1, "clicks": 1, "cost": "0.10", "sales14d": "0.20", "purchases14d": 0}]
            )
        },
    )
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2026, 9, 1), end_date=date(2026, 9, 1)
    )
    outcome = await service.process_one_claimed_job()
    assert outcome.outcome == "succeeded"
    assert client.calls.count("get_report_status") == 2


# --- HTTP 425 duplicate/in-flight report (a real production run hit a
# transient network failure mid-poll after Amazon had already accepted
# the report; this is the related "Amazon itself says a duplicate is
# already in flight" case — see AdsApiDuplicateReportError's docstring
# and docs/AI_HANDOVER/23_..._BLUEPRINT.md §9/§13.9) -----------------


@pytest.mark.asyncio
async def test_425_with_an_existing_report_id_resumes_polling_without_a_second_create(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    body = _report_body(
        [{"date": "2020-01-01", "campaignId": 1, "impressions": 1, "clicks": 0, "cost": 1.0, "sales14d": 0, "purchases14d": 0}]
    )

    class _DuplicateThenPollClient(MockAmazonAdsApiClient):
        async def create_report(self, ctx, configuration):
            self.calls.append("create_report")
            raise AdsApiDuplicateReportError("duplicate", existing_report_id="r-425-1")

    client = _DuplicateThenPollClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-425-1", status="COMPLETED", url="https://x/r.gz")],
        report_bodies={"https://x/r.gz": body},
    )
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1)
    )
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "succeeded"
    assert outcome.records_ingested == 1
    assert client.calls.count("create_report") == 1  # exactly the one call that got the 425 — never retried as a create
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "succeeded"
        assert run.amazon_report_id == "r-425-1"


@pytest.mark.asyncio
async def test_425_without_a_report_id_retries_conservatively_not_permanently(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(raise_on={"create_report": AdsApiDuplicateReportError("duplicate, no id")})
    service = AmazonAdsReportService(
        settings=_settings(ads_report_duplicate_create_max_attempts=5),
        secret_provider=secrets, ads_client=client, lease_owner="w1",
    )
    run_id = service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1)
    )
    before = datetime.now(UTC)
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "retrying"  # not "failed" — this is not treated as a permanent contract mismatch
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "waiting_to_retry"
        assert run.failure_class == "report_create_duplicate_unresolved"
        assert run.amazon_report_id is None  # nothing fabricated
        # Uses the dedicated duplicate-create delay setting, not the
        # general poll interval — proven by an explicit, distinct value.
        delta = (run.next_retry_at.replace(tzinfo=UTC) - before).total_seconds()
        assert 0 < delta <= _settings(ads_report_duplicate_create_max_attempts=5).ads_report_duplicate_create_retry_seconds + 1


@pytest.mark.asyncio
async def test_425_without_a_report_id_honors_retry_after_over_the_dedicated_default(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(
        raise_on={"create_report": AdsApiDuplicateReportError("duplicate, no id", retry_after_seconds=77.0)}
    )
    service = AmazonAdsReportService(
        settings=_settings(ads_report_duplicate_create_retry_seconds=5.0),
        secret_provider=secrets, ads_client=client, lease_owner="w1",
    )
    run_id = service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1)
    )
    before = datetime.now(UTC)
    await service.process_one_claimed_job()

    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        # 77s is far outside the configured 5s default — only possible
        # if Retry-After was honored directly, not the dedicated default.
        delta = (run.next_retry_at.replace(tzinfo=UTC) - before).total_seconds()
        assert 60 < delta < 95


@pytest.mark.asyncio
async def test_425_without_a_report_id_uses_its_own_small_budget_not_the_poll_budget(monkeypatch) -> None:
    """Proves the dedicated ads_report_duplicate_create_max_attempts
    budget is what's enforced, NOT ads_report_poll_max_attempts:
    deliberately sets the poll budget large (40) and the duplicate-
    create budget small (2), pre-seeds the row's attempt_count to
    simulate it already being on its final allowed duplicate-create
    attempt, and confirms it fails on exactly this attempt rather than
    being allowed anywhere near 40."""
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(raise_on={"create_report": AdsApiDuplicateReportError("duplicate, no id")})
    service = AmazonAdsReportService(
        settings=_settings(ads_report_poll_max_attempts=40, ads_report_duplicate_create_max_attempts=2),
        secret_provider=secrets, ads_client=client, lease_owner="w1",
    )
    run_id = service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1)
    )
    from app.persistence.models import AmazonAdsReportRun
    import sqlalchemy as sa

    # Pre-seed attempt_count=1 so THIS claim becomes the 2nd (final
    # allowed) duplicate-create attempt under the small budget.
    with session_scope() as session:
        session.execute(sa.update(AmazonAdsReportRun).where(AmazonAdsReportRun.id == run_id).values(attempt_count=1))

    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "failed"
    assert client.calls.count("create_report") == 1  # exactly one create call this invocation
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.attempt_count == 2  # claim incremented 1 -> 2; 2 >= max(2) -> failed, proving the exact boundary
        assert run.status == "failed"
        assert run.failure_class == "report_create_duplicate_unresolved"


@pytest.mark.asyncio
async def test_425_without_a_report_id_below_its_budget_still_retries(monkeypatch) -> None:
    """Complements the exhaustion test above: one attempt below the
    small dedicated budget must still retry, not fail early."""
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(raise_on={"create_report": AdsApiDuplicateReportError("duplicate, no id")})
    service = AmazonAdsReportService(
        settings=_settings(ads_report_duplicate_create_max_attempts=3),
        secret_provider=secrets, ads_client=client, lease_owner="w1",
    )
    run_id = service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1)
    )
    outcome = await service.process_one_claimed_job()  # attempt_count becomes 1, 1 < 3

    assert outcome.outcome == "retrying"
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.attempt_count == 1
        assert run.status == "waiting_to_retry"


@pytest.mark.asyncio
async def test_ordinary_invalid_request_fails_immediately_not_left_started(monkeypatch) -> None:
    """Fixes the exact bug flagged in PR #35 review: a non-425
    AdsApiInvalidRequestError from create() must be caught, mark the
    ledger row failed immediately with a distinct failure_class, record
    a sanitized sync error, never schedule a retry, and never leave the
    job `started` waiting for lease recovery."""
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(raise_on={"create_report": AdsApiInvalidRequestError("bad request shape")})
    service = AmazonAdsReportService(
        settings=_settings(ads_report_poll_max_attempts=40), secret_provider=secrets, ads_client=client, lease_owner="w1"
    )
    run_id = service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1)
    )
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "failed"
    assert client.calls.count("create_report") == 1
    assert client.calls.count("get_report_status") == 0  # never reached polling
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "failed"  # never left `started`
        assert run.failure_class == "report_create_invalid_request"
        assert run.next_retry_at is None  # no retry was ever scheduled
        assert run.lease_owner is None


@pytest.mark.asyncio
async def test_425_resumption_does_not_leak_secrets_or_log_the_report_body(monkeypatch, caplog) -> None:
    import logging

    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    body = _report_body(
        [{"date": "2020-01-01", "campaignId": 1, "impressions": 1, "clicks": 0, "cost": 1.0, "sales14d": 0, "purchases14d": 0}]
    )

    class _DuplicateThenPollClient(MockAmazonAdsApiClient):
        async def create_report(self, ctx, configuration):
            self.calls.append("create_report")
            raise AdsApiDuplicateReportError("duplicate", existing_report_id="r-425-secret-test")

    client = _DuplicateThenPollClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-425-secret-test", status="COMPLETED", url="https://x/r.gz")],
        report_bodies={"https://x/r.gz": body},
    )
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1)
    )
    with caplog.at_level(logging.INFO):
        await service.process_one_claimed_job()

    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Atza|" not in log_text
    assert "Atzr|" not in log_text
    assert "campaignId" not in log_text  # no raw row data logged


def test_stale_lease_terminalizes_instead_of_hanging_or_double_processing() -> None:
    """Mirrors `claim_next_sales_traffic_job`'s own proven design: a
    lease that expired while a worker held `started` is NOT silently
    re-queued for another worker to pick up (that would risk two workers
    believing they both own the same job). It terminalizes to
    `timed_out` — recovery means "never hangs forever as `started`," not
    "automatically retries" — matching `AmazonIngestionRun`'s own
    documented guarantee exactly. A separate, real retry (`waiting_to_
    retry`, via `mark_retry`) is a distinct, explicit transition (see
    `test_terminal_amazon_failure_status_schedules_retry_then_eventually_
    fails`), never an accident of stale-lease cleanup."""
    _connection_id, profile_id, _secrets = _connected_profile()
    with session_scope() as session:
        repo = AmazonAdsReportRunRepository(session)
        run = repo.create(ORG_ID, profile_id, report_type="sponsored_products_daily", start_date=date(2026, 9, 1), end_date=date(2026, 9, 1))
        claimed = repo.claim_next_report_job(
            lease_owner="worker-a", lease_duration_seconds=1, max_global_active=10, max_active_per_profile=10
        )
        assert claimed is not None and claimed.id == run.id
        # Force the lease into the past, simulating a worker that died
        # mid-job without ever marking the run terminal.
        from app.persistence.models import AmazonAdsReportRun
        import sqlalchemy as sa

        session.execute(
            sa.update(AmazonAdsReportRun).where(AmazonAdsReportRun.id == run.id).values(
                lease_expires_at=datetime.now(UTC) - timedelta(seconds=5)
            )
        )
        session.flush()

    with session_scope() as session:
        repo = AmazonAdsReportRunRepository(session)
        # No other queued/waiting_to_retry job exists, so this returns
        # None — but the stale row itself must no longer be claimable or
        # stuck "started" forever.
        result = repo.claim_next_report_job(
            lease_owner="worker-b", lease_duration_seconds=60, max_global_active=10, max_active_per_profile=10
        )
        assert result is None
        settled = repo.get_owned(ORG_ID, run.id)
        assert settled.status == "timed_out"
        assert settled.lease_owner is None


def test_per_profile_concurrency_cap_prevents_starving_other_advertisers() -> None:
    _connection_id, profile_id, _secrets = _connected_profile()
    with session_scope() as session:
        repo = AmazonAdsReportRunRepository(session)
        repo.create(ORG_ID, profile_id, report_type="sponsored_products_daily", start_date=date(2026, 9, 1), end_date=date(2026, 9, 1))
        repo.create(ORG_ID, profile_id, report_type="sponsored_products_daily", start_date=date(2026, 9, 2), end_date=date(2026, 9, 2))
        first = repo.claim_next_report_job(
            lease_owner="w1", lease_duration_seconds=300, max_global_active=10, max_active_per_profile=1
        )
        assert first is not None
        second = repo.claim_next_report_job(
            lease_owner="w2", lease_duration_seconds=300, max_global_active=10, max_active_per_profile=1
        )
        # The profile already has one active job — the cap blocks a second
        # concurrent claim for the SAME profile, even though a job exists.
        assert second is None
