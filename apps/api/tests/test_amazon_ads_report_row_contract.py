"""Tests for the Ads Reporting v3 row-parsing contract and the report
lifecycle's success/failure semantics — added after a real completed
`spCampaigns` report (2026-09-13) was found to return `campaignId` as a
JSON integer, not the string `AdsReportRow` assumed, causing all 35 real
rows to fail validation while the report run was still marked
`succeeded`.

Every value in this file is invented — no real campaign id, name,
date, or performance metric from the diagnosis appears here. The shape
(field names, JSON types) mirrors what was actually observed live.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import SecretStr, ValidationError

from app.amazon.ads_client import MockAmazonAdsApiClient
from app.amazon.ads_models import AdsReportRow, AdsReportStatusResponse
from app.amazon.ads_report_service import AmazonAdsReportService
from app.amazon.secrets import DevelopmentSecretProvider, build_asi_secret_reference
from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID, Settings
from app.persistence.database import reset_persistence, session_scope
from app.persistence.repositories import (
    AmazonAdsConnectionRepository,
    AmazonAdsDailyPerformanceFactRepository,
    AmazonAdsProfileRepository,
    AmazonAdsReportRunRepository,
    AmazonAdsSyncCheckpointRepository,
)

ORG_ID = DEFAULT_DEVELOPMENT_ORGANIZATION_ID


@pytest.fixture(autouse=True)
def _reset_db():
    reset_persistence()
    yield
    reset_persistence()


# --- Row-contract: campaignId (and sibling id fields) as a JSON int ----


def test_the_real_observed_shape_parses_campaign_id_as_int() -> None:
    """Synthetic fixture preserving the real shape observed live: a bare
    JSON integer campaignId, float cost/sales14d. Invented numbers."""
    row = AdsReportRow.model_validate(
        {
            "date": "2020-01-15",
            "campaignId": 123456789012,
            "impressions": 10,
            "clicks": 2,
            "cost": 3.21,
            "sales14d": 6.42,
            "purchases14d": 1,
        }
    )
    assert row.campaign_id == "123456789012"
    assert isinstance(row.campaign_id, str)


def test_the_previously_assumed_string_shape_still_parses() -> None:
    """Backward compatible: the v3 entity-list endpoints return ids as
    strings (confirmed separately) — that shape must keep working too."""
    row = AdsReportRow.model_validate({"date": "2020-01-15", "campaignId": "123456789012"})
    assert row.campaign_id == "123456789012"


@pytest.mark.parametrize("field,alias", [
    ("campaign_id", "campaignId"),
    ("ad_group_id", "adGroupId"),
    ("keyword_id", "keywordId"),
    ("target_id", "targetId"),
    ("ad_id", "adId"),
])
def test_every_sibling_id_field_normalizes_an_int_safely(field: str, alias: str) -> None:
    row = AdsReportRow.model_validate({alias: 42})
    assert getattr(row, field) == "42"


def test_a_bool_id_is_not_silently_coerced() -> None:
    """`bool` is an `int` subclass in Python — True/False must never be
    accepted as a plausible-looking entity id string."""
    with pytest.raises(ValidationError):
        AdsReportRow.model_validate({"campaignId": True})


def test_a_float_id_is_not_silently_coerced() -> None:
    """Only the confirmed-live int shape is normalized; a float id would
    be a different, unexpected shape and must fail visibly, not be
    silently turned into e.g. '42.0'."""
    with pytest.raises(ValidationError):
        AdsReportRow.model_validate({"campaignId": 42.5})


def test_decimal_currency_values_remain_exact_from_float_input() -> None:
    """Guards against binary-float imprecision leaking into money
    fields — pydantic's Decimal coercion goes through the value's
    string representation, not the raw float bit pattern."""
    row = AdsReportRow.model_validate({"cost": 3.21, "sales14d": 6.42})
    assert row.cost == Decimal("3.21")
    assert row.attributed_sales_14d == Decimal("6.42")


def test_missing_and_explicit_null_fields_both_default_safely() -> None:
    row_missing = AdsReportRow.model_validate({})
    row_null = AdsReportRow.model_validate({"campaignId": None, "date": None})
    assert row_missing.campaign_id is None
    assert row_null.campaign_id is None
    assert row_missing.impressions == 0
    assert row_missing.cost == Decimal("0")


# --- Lifecycle: nonempty report, zero accepted rows, must not succeed --


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


def _connected_profile() -> tuple:
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
                    "region": "NA",
                    "display_name": "Test Advertiser",
                }
            ],
        )[0]
        return connection.id, profile.id, secrets


async def _fake_refresh(*args, **kwargs):
    from app.amazon.models import LwaTokenResponse

    return LwaTokenResponse(access_token=SecretStr("Atza|access"), token_type="bearer", expires_in=3600)


@pytest.mark.asyncio
async def test_a_nonempty_report_where_every_row_is_rejected_fails_the_run(monkeypatch, caplog) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()

    # Every one of these rows fails validation for the SAME reason
    # (an unparseable "cost") — this is not a partial/mixed batch.
    import json as _json

    body = _json.dumps(
        [
            {"date": "2020-01-01", "campaignId": 1, "cost": "not-a-number"},
            {"date": "2020-01-02", "campaignId": 2, "cost": "also-not-a-number"},
        ]
    ).encode("utf-8")

    client = MockAmazonAdsApiClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-bad", status="COMPLETED", url="https://x/r.gz")],
        report_bodies={"https://x/r.gz": body},
    )
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 2)
    )

    with caplog.at_level(logging.WARNING):
        outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "failed"
    assert outcome.records_ingested == 0

    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "failed"
        assert run.failure_class == "report_row_contract_mismatch"
        assert run.records_ingested == 0
        facts = AmazonAdsDailyPerformanceFactRepository(session).series_for_profile(
            ORG_ID, profile_id, start=date(2020, 1, 1), end=date(2020, 1, 2)
        )
        assert facts == []
        checkpoint = AmazonAdsSyncCheckpointRepository(session).get(profile_id)
        assert checkpoint is None  # never advanced

    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "not-a-number" not in log_text
    assert "campaignId" not in log_text  # no raw field values, only counts


@pytest.mark.asyncio
async def test_a_partially_rejected_report_still_succeeds_with_only_accepted_rows(monkeypatch) -> None:
    """Contrast case: SOME rows invalid, but not all — this must remain
    a success (existing behavior, unchanged), ingesting only the valid
    rows. Distinguishes "a few bad rows" from "systemic contract
    mismatch"."""
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    import json as _json

    body = _json.dumps(
        [
            {"date": "2020-01-01", "campaignId": 1, "impressions": 1, "clicks": 0, "cost": 1.0, "sales14d": 0, "purchases14d": 0},
            {"date": "2020-01-02", "campaignId": 2, "cost": "not-a-number"},
        ]
    ).encode("utf-8")

    client = MockAmazonAdsApiClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-mixed", status="COMPLETED", url="https://x/r.gz")],
        report_bodies={"https://x/r.gz": body},
    )
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")
    run_id = service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 2)
    )
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "succeeded"
    assert outcome.records_ingested == 1

    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "succeeded"
        checkpoint = AmazonAdsSyncCheckpointRepository(session).get(profile_id)
        assert checkpoint is not None
        assert checkpoint.synced_through_date == date(2020, 1, 2)


@pytest.mark.asyncio
async def test_checkpoint_advances_only_after_a_successful_ingest_not_before(monkeypatch) -> None:
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    connection_id, profile_id, secrets = _connected_profile()
    import json as _json

    body = _json.dumps(
        [{"date": "2020-01-01", "campaignId": 1, "impressions": 1, "clicks": 0, "cost": 1.0, "sales14d": 0, "purchases14d": 0}]
    ).encode("utf-8")
    client = MockAmazonAdsApiClient(
        report_status_sequence=[AdsReportStatusResponse(reportId="r-ok", status="COMPLETED", url="https://x/r.gz")],
        report_bodies={"https://x/r.gz": body},
    )
    service = AmazonAdsReportService(settings=_settings(), secret_provider=secrets, ads_client=client, lease_owner="w1")

    with session_scope() as session:
        assert AmazonAdsSyncCheckpointRepository(session).get(profile_id) is None

    service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2020, 1, 1), end_date=date(2020, 1, 1)
    )
    outcome = await service.process_one_claimed_job()
    assert outcome.outcome == "succeeded"

    with session_scope() as session:
        checkpoint = AmazonAdsSyncCheckpointRepository(session).get(profile_id)
        assert checkpoint is not None
        assert checkpoint.synced_through_date == date(2020, 1, 1)
