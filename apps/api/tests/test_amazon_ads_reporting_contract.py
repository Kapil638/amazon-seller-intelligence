"""Contract tests for Amazon Ads Reporting v3 report creation — added
after a real production `POST /reporting/reports` call was rejected
with `{"code":"400","detail":"Required fields are invalid or missing:
configuration"}` (2026-09-13). The previous flat request body is now a
real, guarded-against regression, not just an unconfirmed assumption.

`MockAmazonAdsApiClient.create_report` (used by most of
`test_amazon_ads_report_service.py`) never serializes its `configuration`
argument at all, so it could never have caught this bug — every test
here instead drives the real `HttpAmazonAdsApiClient` against a fake
`httpx` transport so the actual wire format is what's under test.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.amazon.ads_client import AdsRequestContext, HttpAmazonAdsApiClient
from app.amazon.ads_models import (
    AdsReportConfigurationBody,
    AdsReportRequestConfiguration,
    AdsReportStatusResponse,
)
from app.amazon.ads_report_service import AmazonAdsReportService, report_name_for_run
from app.amazon.secrets import DevelopmentSecretProvider, build_asi_secret_reference
from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID, Settings
from app.core.exceptions import (
    AdsApiDuplicateReportError,
    AdsApiInvalidRequestError,
    AdsApiParseFailedError,
    AdsApiRequestFailedError,
)
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


def _ctx() -> AdsRequestContext:
    return AdsRequestContext(
        access_token=SecretStr("Atza|super-secret-access-token"),
        client_id="amzn1.application-oa2-client.super-secret-id",
        region="NA",
        profile_id="111",
        correlation_id="corr-1",
    )


def _configuration() -> AdsReportRequestConfiguration:
    return AdsReportRequestConfiguration(
        name="asi-sp-campaigns-test-2026-09-06-2026-09-12",
        startDate=date(2026, 9, 6),
        endDate=date(2026, 9, 12),
        configuration=AdsReportConfigurationBody(
            adProduct="SPONSORED_PRODUCTS",
            reportTypeId="spCampaigns",
            timeUnit="DAILY",
            groupBy=["campaign"],
            columns=["date", "campaignId", "impressions", "clicks", "cost", "sales14d", "purchases14d"],
        ),
    )


# --- Serialization shape ---------------------------------------------


def test_configuration_fields_are_nested_under_a_configuration_key() -> None:
    dumped = _configuration().model_dump(by_alias=True, mode="json")
    assert dumped["configuration"] == {
        "adProduct": "SPONSORED_PRODUCTS",
        "reportTypeId": "spCampaigns",
        "timeUnit": "DAILY",
        "format": "GZIP_JSON",
        "groupBy": ["campaign"],
        "columns": ["date", "campaignId", "impressions", "clicks", "cost", "sales14d", "purchases14d"],
    }


def test_the_old_flat_structure_can_no_longer_be_emitted() -> None:
    """Regression guard for the exact bug found live: adProduct/
    reportTypeId/etc must never appear at the top level again."""
    dumped = _configuration().model_dump(by_alias=True, mode="json")
    for flat_key in ("adProduct", "reportTypeId", "timeUnit", "format", "groupBy", "columns"):
        assert flat_key not in dumped, f"{flat_key} leaked to the top level — the old flat bug has regressed"


def test_name_start_date_end_date_remain_top_level() -> None:
    dumped = _configuration().model_dump(by_alias=True, mode="json")
    assert dumped["name"] == "asi-sp-campaigns-test-2026-09-06-2026-09-12"
    assert dumped["startDate"] == "2026-09-06"
    assert dumped["endDate"] == "2026-09-12"
    assert "configuration" not in (dumped["name"], dumped["startDate"], dumped["endDate"])


def test_time_unit_rejects_a_value_outside_the_documented_enum() -> None:
    with pytest.raises(ValidationError):
        AdsReportConfigurationBody(
            adProduct="SPONSORED_PRODUCTS",
            reportTypeId="spCampaigns",
            timeUnit="WEEKLY",  # not DAILY or SUMMARY
            groupBy=["campaign"],
            columns=["date"],
        )


def test_report_name_is_deterministic_per_run_and_contains_no_seller_identity() -> None:
    run_id = UUID("11111111-1111-4111-8111-111111111111")
    name = report_name_for_run(run_id, date(2026, 9, 6), date(2026, 9, 12))
    assert name == f"asi-sp-campaigns-{run_id}-2026-09-06-2026-09-12"
    # No seller/advertiser identity, no secrets, no Amazon profile id.
    for forbidden in ("Nonin", "AJ Duran", "A2Z90VYF98ELKX", "Atza|", "Atzr|"):
        assert forbidden not in name
    assert len(name) < 128  # comfortably under any documented Amazon name length limit


# --- Real HTTP wire format --------------------------------------------


def test_http_client_sends_the_exact_nested_body_on_the_wire() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["path"] = request.url.path
        captured["method"] = request.method
        return httpx.Response(200, json={"reportId": "r-live-1", "status": "PENDING"})

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    asyncio.run(client.create_report(_ctx(), _configuration()))

    assert captured["method"] == "POST"
    assert captured["path"] == "/reporting/reports"
    assert set(captured["body"].keys()) == {"name", "startDate", "endDate", "configuration"}
    assert captured["body"]["configuration"]["adProduct"] == "SPONSORED_PRODUCTS"


def test_create_report_sends_the_documented_media_type() -> None:
    """Per docs/AI_HANDOVER/23_..._BLUEPRINT.md §9 (Reporting v3
    get-started guide, officially verified): Content-Type must be
    application/vnd.createasyncreportrequest.v3+json."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type")
        captured["accept"] = request.headers.get("accept")
        return httpx.Response(200, json={"reportId": "r-live-1", "status": "PENDING"})

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    asyncio.run(client.create_report(_ctx(), _configuration()))

    assert captured["content_type"] == "application/vnd.createasyncreportrequest.v3+json"
    assert captured["accept"] == "application/vnd.createasyncreportrequest.v3+json"


def test_create_report_never_logs_secrets(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"reportId": "r-1", "status": "PENDING"})

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.INFO):
        asyncio.run(client.create_report(_ctx(), _configuration()))

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "super-secret-access-token" not in log_text
    assert "super-secret-id" not in log_text
    assert "[redacted]" in log_text


def test_create_report_reproduces_the_real_400_when_configuration_is_missing() -> None:
    """Fixture built from the real sanitized production error captured
    during diagnosis, so this exact regression is pinned even without a
    live call."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": "400", "detail": "Required fields are invalid or missing: configuration"})

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AdsApiInvalidRequestError):
        asyncio.run(client.create_report(_ctx(), _configuration()))


# --- HTTP 425 duplicate/in-flight report (docs/AI_HANDOVER/23_...
# _BLUEPRINT.md §9/§13.9: "wait and poll the in-flight identical
# report; do not treat as malformed body") ------------------------------


def test_create_report_425_with_a_report_id_is_never_invalid_request() -> None:
    """The single field name Amazon's own documented 200 response uses
    (reportId) is checked defensively; when present, it must be surfaced
    on the exception, never dropped or treated as a generic 4xx."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(425, json={"reportId": "r-existing-1", "message": "duplicate request"})

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AdsApiDuplicateReportError) as excinfo:
        asyncio.run(client.create_report(_ctx(), _configuration()))
    assert excinfo.value.existing_report_id == "r-existing-1"


def test_create_report_425_without_a_report_id_leaves_it_none_not_fabricated() -> None:
    """No official schema is documented for the 425 body. When no
    reportId field is present, existing_report_id must be None — never
    guessed from something else in the body."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(425, json={"message": "duplicate request", "details": "try again later"})

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AdsApiDuplicateReportError) as excinfo:
        asyncio.run(client.create_report(_ctx(), _configuration()))
    assert excinfo.value.existing_report_id is None


def test_create_report_425_with_a_non_json_body_still_raises_cleanly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(425, content=b"not json")

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AdsApiDuplicateReportError) as excinfo:
        asyncio.run(client.create_report(_ctx(), _configuration()))
    assert excinfo.value.existing_report_id is None


def test_create_report_425_is_a_distinct_type_from_invalid_request() -> None:
    """Regression guard for the exact bug this PR fixes: a 425 must
    never be catchable by an `except AdsApiInvalidRequestError` clause —
    that exception's own contract is "never retried"."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(425, json={"reportId": "r-x"})

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    try:
        asyncio.run(client.create_report(_ctx(), _configuration()))
        raised = None
    except Exception as exc:  # noqa: BLE001 - inspecting the exact type deliberately
        raised = exc
    assert raised is not None
    assert not isinstance(raised, AdsApiInvalidRequestError)
    assert isinstance(raised, AdsApiDuplicateReportError)


# --- Report status parsing strictness ----------------------------------


def test_report_status_rejects_an_unrecognized_terminal_state() -> None:
    with pytest.raises(ValidationError):
        AdsReportStatusResponse.model_validate({"reportId": "r-1", "status": "SOMETHING_NEW"})


def test_get_report_status_surfaces_an_unrecognized_status_as_a_typed_parse_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"reportId": "r-1", "status": "SOMETHING_NEW"})

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AdsApiParseFailedError):
        asyncio.run(client.get_report_status(_ctx(), "r-1"))


def test_get_report_status_rejects_a_response_missing_the_required_report_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "PENDING"})

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AdsApiParseFailedError):
        asyncio.run(client.get_report_status(_ctx(), "r-1"))


# --- Download safety --------------------------------------------------


def test_download_report_refuses_a_non_https_url() -> None:
    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(AdsApiInvalidRequestError):
        asyncio.run(client.download_report(_ctx(), "http://example.com/report.gz", max_bytes=1_000_000))


def test_download_report_never_follows_a_redirect() -> None:
    """If a redirect were ever followed, the 'evil' URL below would be
    fetched and return valid gzip content, making the call *succeed* —
    the precise failure mode this guards against. With
    `follow_redirects=False`, the 302 itself must surface as an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/report.gz":
            return httpx.Response(302, headers={"location": "https://evil.example.com/steal"})
        if str(request.url) == "https://evil.example.com/steal":
            return httpx.Response(200, content=gzip.compress(b"[]"))
        raise AssertionError(f"unexpected request: {request.url}")

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AdsApiRequestFailedError):
        asyncio.run(client.download_report(_ctx(), "https://example.com/report.gz", max_bytes=1_000_000))


# --- Full fixture-based state machine, driven through the REAL client --


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
                    "display_name": "AJ Duran",
                }
            ],
        )[0]
        return connection.id, profile.id, secrets


async def _fake_refresh(*args, **kwargs):
    from app.amazon.models import LwaTokenResponse

    return LwaTokenResponse(access_token=SecretStr("Atza|access"), token_type="bearer", expires_in=3600)


@pytest.mark.asyncio
async def test_fixture_driven_create_poll_download_ingest_through_the_real_http_client(monkeypatch) -> None:
    """End-to-end proof that the corrected nested body drives the real
    state machine correctly: accepted (PENDING) -> poll (PROCESSING then
    COMPLETED) -> download (gzip) -> parse -> persist -> checkpoint."""
    monkeypatch.setattr("app.amazon.ads_report_service.refresh_ads_access_token", _fake_refresh)
    _connection_id, profile_id, secrets = _connected_profile()

    report_row = [
        {
            "date": "2026-09-06",
            "campaignId": "c-live-1",
            "impressions": 500,
            "clicks": 20,
            "cost": "5.55",
            "sales14d": "44.44",
            "purchases14d": 2,
            "currency": "USD",
        }
    ]
    gz_body = gzip.compress(json.dumps(report_row).encode("utf-8"))

    poll_calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/reporting/reports":
            body = json.loads(request.content)
            assert "configuration" in body, "the fix must produce a nested body, or this whole flow is invalid"
            return httpx.Response(200, json={"reportId": "r-e2e-1", "status": "PENDING"})
        if request.method == "GET" and request.url.path == "/reporting/reports/r-e2e-1":
            poll_calls["count"] += 1
            if poll_calls["count"] == 1:
                return httpx.Response(200, json={"reportId": "r-e2e-1", "status": "PROCESSING"})
            return httpx.Response(
                200, json={"reportId": "r-e2e-1", "status": "COMPLETED", "url": "https://example.com/report.gz"}
            )
        if request.method == "GET" and str(request.url) == "https://example.com/report.gz":
            return httpx.Response(200, content=gz_body, headers={"content-type": "application/gzip"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    service = AmazonAdsReportService(
        settings=Settings(
            ads_lwa_client_id=SecretStr("client"),
            ads_lwa_client_secret=SecretStr("secret"),
            ads_report_poll_max_attempts=5,
            ads_report_poll_interval_seconds=0.001,
            database_url="sqlite://",
        ),
        secret_provider=secrets,
        ads_client=client,
        lease_owner="contract-test",
    )
    run_id = service.create_report_request(
        organization_id=ORG_ID, ads_profile_id=profile_id, start_date=date(2026, 9, 6), end_date=date(2026, 9, 6)
    )
    outcome = await service.process_one_claimed_job()

    assert outcome.outcome == "succeeded"
    assert outcome.records_ingested == 1
    assert poll_calls["count"] == 2  # PROCESSING once, then COMPLETED

    with session_scope() as session:
        run = AmazonAdsReportRunRepository(session).get_owned(ORG_ID, run_id)
        assert run.status == "succeeded"
        assert run.amazon_report_id == "r-e2e-1"
        facts = AmazonAdsDailyPerformanceFactRepository(session).series_for_profile(
            ORG_ID, profile_id, start=date(2026, 9, 6), end=date(2026, 9, 6)
        )
        assert len(facts) == 1
        assert facts[0].cost == Decimal("5.55")


@pytest.mark.asyncio
async def test_rerunning_the_same_parsed_fact_payload_produces_no_duplicates(monkeypatch) -> None:
    """Prove the persistence step itself (not just a second report run)
    is idempotent: re-upserting the exact same row twice in the same
    transaction shape yields one row, not two."""
    _connection_id, profile_id, _secrets = _connected_profile()
    data = {
        "entity_type": "campaign",
        "entity_external_id": "c-dup-1",
        "fact_date": date(2026, 9, 6),
        "attribution_window": "14d",
        "currency_code": "USD",
        "impressions": 10,
        "clicks": 2,
        "cost": Decimal("1.11"),
        "attributed_sales": Decimal("2.22"),
        "attributed_conversions": 1,
    }
    with session_scope() as session:
        repo = AmazonAdsDailyPerformanceFactRepository(session)
        repo.upsert(ORG_ID, profile_id, data, report_run_id=None)
        repo.upsert(ORG_ID, profile_id, data, report_run_id=None)

    with session_scope() as session:
        facts = AmazonAdsDailyPerformanceFactRepository(session).series_for_profile(
            ORG_ID, profile_id, start=date(2026, 9, 6), end=date(2026, 9, 6)
        )
        assert len(facts) == 1
