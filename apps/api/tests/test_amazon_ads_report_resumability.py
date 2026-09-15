"""PR A2 — Reporting v3 resumability and failure-classification tests.

Covers what PR #35 (HTTP 425 handling) deliberately left for this PR:
- Stale-lease recovery that distinguishes "nothing to resume" (never
  reached Amazon) from "Amazon already accepted this report" (resume
  polling the same amazon_report_id, never re-create).
- Checkpoint monotonicity (never regresses synced_through_date).
- Bounded exponential backoff with full jitter, honoring Amazon's own
  Retry-After over computed backoff.
- Truthful, terminal-vs-retryable classification at every lifecycle
  stage (create/poll/download), including the specific gap flagged in
  PR #35's second review: an invalid report-status request (including
  a defensively-adopted 425 report id that the status endpoint does
  not recognize) must terminalize cleanly, never leave the run
  `started`, and never trigger a second create.

No live Amazon call anywhere in this file — every client is
`MockAmazonAdsApiClient` or a small subclass of it.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import UTC, date, datetime, timedelta

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from app.amazon.ads_client import MockAmazonAdsApiClient
from app.amazon.ads_models import AdsReportStatusResponse
from app.amazon.ads_report_service import AmazonAdsReportService, compute_backoff_delay
from app.amazon.secrets import DevelopmentSecretProvider, build_asi_secret_reference
from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID, Settings
from app.core.exceptions import (
    AdsApiAuthenticationError,
    AdsApiInvalidRequestError,
    AdsApiParseFailedError,
    AdsApiRateLimitedError,
    AdsApiRequestFailedError,
    AdsReportFailedError,
    AdsReportOversizedError,
)
from app.persistence.database import reset_persistence, session_scope
from app.persistence.models import AmazonAdsReportRun
from app.persistence.repositories import (
    AmazonAdsConnectionRepository,
    AmazonAdsProfileRepository,
    AmazonAdsReportRunRepository,
    AmazonAdsSyncCheckpointRepository,
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
        ads_report_poll_max_attempts=3,
        ads_report_poll_interval_seconds=0.001,
        ads_report_retry_base_seconds=0.001,
        ads_report_retry_max_seconds=1.0,
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
    return json.dumps(rows).encode("utf-8")


async def _fake_refresh(*args, **kwargs):
    from app.amazon.models import LwaTokenResponse

    return LwaTokenResponse(access_token=SecretStr("Atza|access"), token_type="bearer", expires_in=3600)


def _set_attempt_count(run_id, value: int) -> None:
    with session_scope() as session:
        session.execute(sa.update(AmazonAdsReportRun).where(AmazonAdsReportRun.id == run_id).values(attempt_count=value))


# --------------------------------------------------------------------
# compute_backoff_delay
# --------------------------------------------------------------------


def test_backoff_delay_grows_with_attempt_count_but_is_capped() -> None:
    rng = random.Random(1)
    small = compute_backoff_delay(0, base_seconds=1.0, max_seconds=1000.0, rng=rng)
    large = compute_backoff_delay(10, base_seconds=1.0, max_seconds=1000.0, rng=rng)
    assert 0 <= small <= 1.0
    assert 0 <= large <= 1000.0


def test_backoff_delay_never_exceeds_max_seconds_regardless_of_attempts() -> None:
    rng = random.Random(2)
    for attempt in (0, 5, 20, 100):
        delay = compute_backoff_delay(attempt, base_seconds=5.0, max_seconds=30.0, rng=rng)
        assert 0 <= delay <= 30.0


def test_backoff_delay_is_deterministic_with_an_injected_rng() -> None:
    a = compute_backoff_delay(3, base_seconds=2.0, max_seconds=100.0, rng=random.Random(42))
    b = compute_backoff_delay(3, base_seconds=2.0, max_seconds=100.0, rng=random.Random(42))
    assert a == b


# --------------------------------------------------------------------
# Checkpoint monotonicity
# --------------------------------------------------------------------


def test_checkpoint_advance_never_regresses_synced_through_date() -> None:
    _connection_id, profile_id, _secrets = _connected_profile()
    with session_scope() as session:
        repo = AmazonAdsSyncCheckpointRepository(session)
        run_repo = AmazonAdsReportRunRepository(session)
        later_run = run_repo.create(
            ORG_ID, profile_id, report_type="sponsored_products_daily", start_date=date(2026, 9, 1), end_date=date(2026, 9, 10)
        )
        earlier_run = run_repo.create(
            ORG_ID, profile_id, report_type="sponsored_products_daily", start_date=date(2026, 8, 1), end_date=date(2026, 8, 5)
        )
        repo.advance(ORG_ID, profile_id, synced_through_date=date(2026, 9, 10), report_run_id=later_run.id)
        # A historical re-pull (older window) must not move the frontier backward.
        repo.advance(ORG_ID, profile_id, synced_through_date=date(2026, 8, 5), report_run_id=earlier_run.id)
        checkpoint = repo.get(profile_id)
        assert checkpoint.synced_through_date == date(2026, 9, 10)
        assert checkpoint.last_successful_report_run_id == later_run.id


def test_checkpoint_advance_still_moves_forward_normally() -> None:
    _connection_id, profile_id, _secrets = _connected_profile()
    with session_scope() as session:
        repo = AmazonAdsSyncCheckpointRepository(session)
        run_repo = AmazonAdsReportRunRepository(session)
        first = run_repo.create(
            ORG_ID, profile_id, report_type="sponsored_products_daily", start_date=date(2026, 9, 1), end_date=date(2026, 9, 1)
        )
        second = run_repo.create(
            ORG_ID, profile_id, report_type="sponsored_products_daily", start_date=date(2026, 9, 2), end_date=date(2026, 9, 2)
        )
        repo.advance(ORG_ID, profile_id, synced_through_date=date(2026, 9, 1), report_run_id=first.id)
        repo.advance(ORG_ID, profile_id, synced_through_date=date(2026, 9, 2), report_run_id=second.id)
        checkpoint = repo.get(profile_id)
        assert checkpoint.synced_through_date == date(2026, 9, 2)
        assert checkpoint.last_successful_report_run_id == second.id


# --------------------------------------------------------------------
# Stale-lease recovery: with vs. without amazon_report_id
# --------------------------------------------------------------------


def test_stale_lease_without_amazon_report_id_terminalizes_not_resumes() -> None:
    """Nothing to resume — Amazon never accepted a report for this run.
    Matches AmazonIngestionRun's own 'never auto-retries' guarantee."""
    _connection_id, profile_id, _secrets = _connected_profile()
    with session_scope() as session:
        repo = AmazonAdsReportRunRepository(session)
        run = repo.create(ORG_ID, profile_id, report_type="sponsored_products_daily", start_date=date(2026, 9, 1), end_date=date(2026, 9, 1))
        claimed = repo.claim_next_report_job(lease_owner="worker-a", lease_duration_seconds=1, max_global_active=10, max_active_per_profile=10)
        assert claimed is not None and claimed.amazon_report_id is None
        session.execute(
            sa.update(AmazonAdsReportRun).where(AmazonAdsReportRun.id == run.id).values(
                lease_expires_at=datetime.now(UTC) - timedelta(seconds=5)
            )
        )
        session.flush()

    with session_scope() as session:
        repo = AmazonAdsReportRunRepository(session)
        result = repo.claim_next_report_job(lease_owner="worker-b", lease_duration_seconds=60, max_global_active=10, max_active_per_profile=10)
        assert result is None  # not immediately reclaimable
        settled = repo.get_owned(ORG_ID, run.id)
        assert settled.status == "timed_out"
        assert settled.failure_class == "lease_expired"
        assert settled.lease_owner is None


def test_stale_lease_with_an_amazon_report_id_becomes_immediately_reclaimable() -> None:
    """A crash AFTER Amazon accepted the report leaves real in-flight
    work on Amazon's side — this must resume polling the SAME
    amazon_report_id, not terminalize as if nothing happened."""
    _connection_id, profile_id, _secrets = _connected_profile()
    with session_scope() as session:
        repo = AmazonAdsReportRunRepository(session)
        run = repo.create(ORG_ID, profile_id, report_type="sponsored_products_daily", start_date=date(2026, 9, 1), end_date=date(2026, 9, 1))
        repo.claim_next_report_job(lease_owner="worker-a", lease_duration_seconds=1, max_global_active=10, max_active_per_profile=10)
        repo.set_amazon_report(run.id, amazon_report_id="r-crash-1", amazon_report_status="PENDING")
        session.execute(
            sa.update(AmazonAdsReportRun).where(AmazonAdsReportRun.id == run.id).values(
                lease_expires_at=datetime.now(UTC) - timedelta(seconds=5)
            )
        )
        session.flush()

    with session_scope() as session:
        repo = AmazonAdsReportRunRepository(session)
        reclaimed = repo.claim_next_report_job(lease_owner="worker-b", lease_duration_seconds=60, max_global_active=10, max_active_per_profile=10)
        assert reclaimed is not None
        assert reclaimed.id == run.id
        assert reclaimed.amazon_report_id == "r-crash-1"  # preserved, never cleared
        assert reclaimed.status == "started"
        assert reclaimed.lease_owner == "worker-b"


@pytest.mark.asyncio
async def test_crash_after_create_then_resume_never_issues_a_second_create(monkeypatch) -> None:
    """End-to-end: simulate a worker dying right after Amazon accepted
    the report (amazon_report_id persisted) but before polling ever
    ran. A second worker resumes and must poll the existing report,
    never call create_report again."""
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    body = _report_body(
        [{"date": "2026-09-01", "campaignId": 1, "impressions": 1, "clicks": 0, "cost": 1.0, "sales14d": 0, "purchases14d": 0}]
    )
    client = MockAmazonAdsApiClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-crash-2", status="COMPLETED", url="https://x/r.gz")],
        report_bodies={"https://x/r.gz": body},
    )
    with session_scope() as session:
        run_repo = AmazonAdsReportRunRepository(session)
        run = run_repo.create(ORG_ID, profile_id, report_type="sponsored_products_daily", start_date=date(2026, 9, 1), end_date=date(2026, 9, 1))
        run_repo.claim_next_report_job(lease_owner="dead-worker", lease_duration_seconds=1, max_global_active=10, max_active_per_profile=10)
        run_repo.set_amazon_report(run.id, amazon_report_id="r-crash-2", amazon_report_status="PENDING")
        session.execute(
            sa.update(AmazonAdsReportRun).where(AmazonAdsReportRun.id == run.id).values(
                lease_expires_at=datetime.now(UTC) - timedelta(seconds=5)
            )
        )
        session.flush()
        run_id = run.id

    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="live-worker")
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "succeeded"
    assert client.calls.count("create_report") == 0  # never re-created
    assert client.calls.count("get_report_status") == 1
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "succeeded"
        assert run.amazon_report_id == "r-crash-2"


# --------------------------------------------------------------------
# CREATE stage: authentication / contract-mismatch classification
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_authentication_failure_is_permanent_and_visible(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(raise_on={"create_report": AdsApiAuthenticationError("bad token")})
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.create_report_request(organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1))
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "failed"
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "failed"
        assert run.failure_class == "report_create_authentication_failed"
        assert run.next_retry_at is None
        assert run.lease_owner is None
        errors = AmazonAdsSyncErrorRepository(session).list_recent(ORG_ID, profile_id)
        assert any(e.error_code == "report_create_authentication_failed" for e in errors)


@pytest.mark.asyncio
async def test_create_contract_mismatch_is_permanent(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(raise_on={"create_report": AdsApiParseFailedError("unparseable create response")})
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.create_report_request(organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1))
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "failed"
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "failed"
        assert run.failure_class == "report_create_contract_mismatch"
        assert run.lease_owner is None


@pytest.mark.asyncio
async def test_create_rate_limit_honors_retry_after_over_computed_backoff(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(raise_on={"create_report": AdsApiRateLimitedError("slow down", retry_after_seconds=42.0)})
    service = AmazonAdsReportService(
        settings=_settings(ads_report_retry_base_seconds=0.001, ads_report_retry_max_seconds=1.0),
        secret_provider=secrets, ads_client=client, lease_owner="w1",
    )
    run_id = service.create_report_request(organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1))
    before = datetime.now(UTC)
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "retrying"
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.failure_class == "report_create_rate_limited"
        delta = (run.next_retry_at.replace(tzinfo=UTC) - before).total_seconds()
        # 42s is far outside the tiny configured backoff bounds — only
        # possible if Retry-After was honored directly.
        assert 30 < delta < 50


# --------------------------------------------------------------------
# POLL stage: the gap flagged in PR #35's second review
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_invalid_request_is_permanent_never_leaves_started(monkeypatch) -> None:
    """The exact gap flagged in review: AdsApiInvalidRequestError from
    get_report_status() must be caught, terminalize the run, clear the
    lease, and never trigger another create."""
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()

    class _BadStatusClient(MockAmazonAdsApiClient):
        async def get_report_status(self, ctx, report_id):
            self.calls.append("get_report_status")
            raise AdsApiInvalidRequestError("unrecognized reportId")

    client = _BadStatusClient(report_status_sequence=[AdsReportStatusResponse(reportId="r-1", status="PENDING")])
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.create_report_request(organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1))
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "failed"
    assert client.calls.count("create_report") == 1  # exactly the original create — never a second one
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "failed"  # never left `started`
        assert run.failure_class == "report_poll_invalid_request"
        assert run.next_retry_at is None
        assert run.lease_owner is None


@pytest.mark.asyncio
async def test_425_adopted_report_id_later_rejected_by_poll_terminalizes_cleanly(monkeypatch) -> None:
    """A defensively-adopted 425 reportId (see AdsApiDuplicateReportError's
    docstring) that the status endpoint does not actually recognize
    must terminalize cleanly, never remain `started`, and never issue
    another create request."""
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()

    from app.core.exceptions import AdsApiDuplicateReportError

    class _DuplicateThenBadStatusClient(MockAmazonAdsApiClient):
        async def create_report(self, ctx, configuration):
            self.calls.append("create_report")
            raise AdsApiDuplicateReportError("duplicate", existing_report_id="r-adopted-bad")

        async def get_report_status(self, ctx, report_id):
            self.calls.append("get_report_status")
            assert report_id == "r-adopted-bad"
            raise AdsApiInvalidRequestError("Amazon does not recognize this reportId")

    client = _DuplicateThenBadStatusClient()
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.create_report_request(organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1))
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "failed"
    assert client.calls.count("create_report") == 1  # never re-created after the adopted id was rejected
    assert client.calls.count("get_report_status") == 1
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "failed"
        assert run.amazon_report_id == "r-adopted-bad"  # preserved for audit, never cleared or replaced
        assert run.failure_class == "report_poll_invalid_request"
        assert run.lease_owner is None


@pytest.mark.asyncio
async def test_poll_authentication_failure_is_permanent(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()

    class _AuthFailClient(MockAmazonAdsApiClient):
        async def get_report_status(self, ctx, report_id):
            self.calls.append("get_report_status")
            raise AdsApiAuthenticationError("token revoked")

    client = _AuthFailClient(report_status_sequence=[AdsReportStatusResponse(reportId="r-1", status="PENDING")])
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.create_report_request(organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1))
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "failed"
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.failure_class == "report_poll_authentication_failed"
        assert run.lease_owner is None


@pytest.mark.asyncio
async def test_poll_contract_mismatch_is_permanent(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()

    class _BadContractClient(MockAmazonAdsApiClient):
        async def get_report_status(self, ctx, report_id):
            self.calls.append("get_report_status")
            raise AdsApiParseFailedError("status response malformed")

    client = _BadContractClient(report_status_sequence=[AdsReportStatusResponse(reportId="r-1", status="PENDING")])
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.create_report_request(organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1))
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "failed"
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.failure_class == "report_poll_contract_mismatch"
        assert run.lease_owner is None


@pytest.mark.asyncio
async def test_poll_transport_failure_retries_with_backoff_then_exhausts(monkeypatch) -> None:
    """Retry-budget exhaustion: a persistent transport failure at the
    poll stage must retry (bounded) and eventually fail — never loop
    forever."""
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-1", status="PENDING")],
        raise_on={"get_report_status": AdsApiRequestFailedError("connection reset")},
    )
    service = AmazonAdsReportService(
        settings=_settings(ads_report_poll_max_attempts=2, ads_report_retry_base_seconds=0.001, ads_report_retry_max_seconds=0.01),
        secret_provider=secrets, ads_client=client, lease_owner="w1",
    )
    run_id = service.create_report_request(organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1))

    _set_attempt_count(run_id, 2)  # simulate this claim being the final allowed attempt
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "failed"
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "failed"
        assert run.failure_class == "report_poll_failed"
        assert run.lease_owner is None


# --------------------------------------------------------------------
# DOWNLOAD stage
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_authentication_failure_is_permanent(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-1", status="COMPLETED", url="https://x/r.gz")],
        raise_on={"download_report": AdsApiAuthenticationError("token revoked mid-download")},
    )
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.create_report_request(organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1))
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "failed"
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.failure_class == "report_download_authentication_failed"
        assert run.lease_owner is None
        assert run.records_ingested == 0
        checkpoint = AmazonAdsSyncCheckpointRepository(session).get(profile_id)
        assert checkpoint is None  # never advanced on failure


@pytest.mark.asyncio
async def test_download_invalid_request_is_permanent(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-1", status="COMPLETED", url="https://x/r.gz")],
        raise_on={"download_report": AdsApiInvalidRequestError("presigned URL rejected")},
    )
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.create_report_request(organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1))
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "failed"
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.failure_class == "report_download_invalid_request"
        assert run.lease_owner is None


@pytest.mark.asyncio
async def test_download_rate_limit_honors_retry_after(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-1", status="COMPLETED", url="https://x/r.gz")],
        raise_on={"download_report": AdsApiRateLimitedError("slow down", retry_after_seconds=33.0)},
    )
    service = AmazonAdsReportService(
        settings=_settings(ads_report_retry_base_seconds=0.001, ads_report_retry_max_seconds=1.0),
        secret_provider=secrets, ads_client=client, lease_owner="w1",
    )
    run_id = service.create_report_request(organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1))
    before = datetime.now(UTC)
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "retrying"
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        delta = (run.next_retry_at.replace(tzinfo=UTC) - before).total_seconds()
        assert 20 < delta < 45


@pytest.mark.asyncio
async def test_oversized_download_is_permanent_not_retried(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-1", status="COMPLETED", url="https://x/r.gz")],
        raise_on={"download_report": AdsReportOversizedError("too big")},
    )
    service = AmazonAdsReportService(
        settings=_settings(ads_report_poll_max_attempts=40), secret_provider=secrets, ads_client=client, lease_owner="w1"
    )
    run_id = service.create_report_request(organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1))
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "failed"  # immediate, despite a large poll_max_attempts budget
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.failure_class == "report_oversized"
        assert run.next_retry_at is None
        assert run.lease_owner is None


@pytest.mark.asyncio
async def test_malformed_report_body_is_permanent_not_retried(monkeypatch) -> None:
    """Decompression/JSON/shape failure — deterministic given the exact
    bytes already downloaded, so it must not consume the shared retry
    budget waiting for an identical re-download to fail identically."""
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    client = MockAmazonAdsApiClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-1", status="COMPLETED", url="https://x/r.gz")],
        report_bodies={"https://x/r.gz": b"not json at all {["},
    )
    service = AmazonAdsReportService(
        settings=_settings(ads_report_poll_max_attempts=40), secret_provider=secrets, ads_client=client, lease_owner="w1"
    )
    run_id = service.create_report_request(organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1))
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "failed"
    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.failure_class == "report_malformed"
        assert run.next_retry_at is None
        assert run.lease_owner is None


@pytest.mark.asyncio
async def test_partial_row_rejection_still_succeeds_but_exposes_counts(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    body = _report_body(
        [
            {"date": "2020-01-01", "campaignId": 1, "impressions": 1, "clicks": 0, "cost": 1.0, "sales14d": 0, "purchases14d": 0},
            {"date": "2020-01-02", "campaignId": 2, "cost": "not-a-number"},
        ]
    )
    client = MockAmazonAdsApiClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-1", status="COMPLETED", url="https://x/r.gz")],
        report_bodies={"https://x/r.gz": body},
    )
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.create_report_request(organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 2))
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "succeeded"
    assert outcome.records_ingested == 1
    with session_scope() as session:
        errors = AmazonAdsSyncErrorRepository(session).list_recent(ORG_ID, profile_id)
        partial = [e for e in errors if e.error_code == "report_partial_row_rejection"]
        assert len(partial) == 1
        assert "total=2" in partial[0].error_message
        assert "accepted=1" in partial[0].error_message
        assert "rejected=1" in partial[0].error_message


# --------------------------------------------------------------------
# Logging hygiene for the new permanent-failure paths
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_invalid_request_failure_does_not_log_secrets_or_report_id_payload(monkeypatch, caplog) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()

    class _BadStatusClient(MockAmazonAdsApiClient):
        async def get_report_status(self, ctx, report_id):
            self.calls.append("get_report_status")
            raise AdsApiInvalidRequestError("unrecognized reportId")

    client = _BadStatusClient(report_status_sequence=[AdsReportStatusResponse(reportId="r-1", status="PENDING")])
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    service.create_report_request(organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1))
    with caplog.at_level(logging.INFO):
        await service.process_one_claimed_job()

    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Atza|" not in log_text
    assert "Atzr|" not in log_text
