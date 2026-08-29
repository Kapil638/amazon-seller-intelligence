"""12B.3D — AmazonListingsIngestionService. No live Amazon call: the Listings
client is fully faked via `listings_client_factory` (this service's actual
HTTP behavior is already covered by 12B.3C's own test suite). Uses the
shared, per-test-isolated SQLite database (`conftest.py`'s autouse
`reset_persistence()` fixture), matching `test_amazon_marketplace_
reconciliation.py`'s established pattern.

Credential hermeticity (CI remediation): `AmazonListingsIngestionService.
_client()` calls `oauth_application_credentials(cfg)` *before* the injected
`listings_client_factory` is ever invoked — a fake client factory alone does
not skip that check. Every test therefore builds its `Settings` through
`_test_settings()` below, which supplies obviously-synthetic LWA
credentials and passes `_env_file=None` so this file never reads
`apps/api/.env` or ambient process environment variables for those fields
(explicit constructor kwargs already take precedence over both, but
`_env_file=None` removes the dependency outright rather than merely
out-prioritizing it). This is what makes the suite pass in a genuinely
credential-free environment (e.g. CI) instead of only appearing to pass
because a developer machine's real `.env` happened to supply production
credentials. The real, non-test-injected credential path — a genuinely
empty `Settings` instance, exactly like a clean production/CI environment
with nothing configured — still fails closed with the existing sanitized
`SpApiConfigurationError`: proven directly by
`test_missing_application_credentials_fails_closed_through_the_real_client_path`
below, and already proven at the shared `oauth_application_credentials()`
level by `test_missing_application_credentials_and_redirect_uri` in
`test_amazon_lwa_token_exchange.py` (same function, different caller).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.amazon.listings_client import ListingsPageRequest
from app.amazon.listings_ingestion import AmazonListingsIngestionService
from app.amazon.listings_models import Item, ListingsPage, ListingsPageProvenance
from app.amazon.secrets import InvalidSecretReferenceError
from app.core.config import Settings
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiParseFailedError,
    SpApiRequestFailedError,
)
from app.persistence.database import current_organization_id, session_scope
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
    AmazonSellerListingRepository,
)

MARKETPLACE = "ATVPDKIKX0DER"


# --- fakes -------------------------------------------------------------


class _FakeResolver:
    def __init__(self, token: str = "test-refresh-token", raise_error: Exception | None = None) -> None:
        self._token = token
        self._raise_error = raise_error

    def resolve_refresh_token(self, *, organization_id, connection):
        if self._raise_error is not None:
            raise self._raise_error
        return SecretStr(self._token)


class _FakeListingsClient:
    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.requests: list[ListingsPageRequest] = []

    async def fetch_page(self, request: ListingsPageRequest) -> ListingsPage:
        self.requests.append(request)
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _test_settings(**overrides) -> Settings:
    """Obviously-synthetic LWA application credentials for every test in
    this file. `_env_file=None` plus an explicit value for every field
    `oauth_application_credentials()` inspects means this never reads
    `apps/api/.env` or any ambient `SP_API_*`/`SP_API_PRODUCTION_*`
    process environment variable — construction is fully self-contained,
    regardless of what a given machine (developer laptop or CI runner)
    happens to have configured. Never a real credential."""
    fields = dict(
        sp_api_lwa_client_id=SecretStr("test-sandbox-lwa-client-id-DO-NOT-USE"),
        sp_api_lwa_client_secret=SecretStr("test-sandbox-lwa-client-secret-DO-NOT-USE"),
        sp_api_production_lwa_client_id=SecretStr("test-production-lwa-client-id-DO-NOT-USE"),
        sp_api_production_lwa_client_secret=SecretStr("test-production-lwa-client-secret-DO-NOT-USE"),
    )
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def _service(script: list, **kwargs) -> tuple[AmazonListingsIngestionService, _FakeListingsClient]:
    client = _FakeListingsClient(script)

    def factory(**_kwargs):
        return client

    resolver = kwargs.pop("resolver", None) or _FakeResolver()
    lease_owner_factory = kwargs.pop("lease_owner_factory", None) or (lambda: f"lease-{uuid4().hex[:8]}")
    settings = kwargs.pop("settings", None) or _test_settings()
    service = AmazonListingsIngestionService(
        settings=settings,
        resolver=resolver,
        listings_client_factory=factory,
        lease_owner_factory=lease_owner_factory,
        **kwargs,
    )
    return service, client


def _item(sku: str, **overrides) -> dict:
    payload = {"sku": sku}
    payload.update(overrides)
    return payload


def _summary(**overrides) -> dict:
    base = {
        "marketplaceId": MARKETPLACE,
        "productType": "TOY",
        "status": ["BUYABLE"],
        "createdDate": "2026-01-01T00:00:00Z",
        "lastUpdatedDate": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def _page(items: list[dict], *, number_of_results: int | None = None, next_token: str | None = None) -> ListingsPage:
    parsed_items = [Item.model_validate(i) for i in items]
    return ListingsPage(
        items=parsed_items,
        number_of_results=number_of_results if number_of_results is not None else len(parsed_items),
        next_token=next_token,
        marketplace_id=MARKETPLACE,
        page_token_used=None,
        provenance=ListingsPageProvenance(
            operation="searchListingsItems",
            region="na",
            endpoint_host="sellingpartnerapi-na.amazon.com",
            fetched_at=datetime.now(UTC),
            http_status=200,
            api_model_version="listings-items-api-model/2021-08-01",
            attempt_count=1,
        ),
    )


def _seed_scope(
    *,
    seller_account_status: str = "active",
    participation_active: bool = True,
    with_connection: bool = True,
    selling_partner_id: str = "A1B2C3D4E5F6G7",
) -> dict:
    org_id = current_organization_id()
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).create(
            organization_id=org_id, provider="SP_API", environment="PRODUCTION", region="na"
        )
        connection.token_reference = f"asi-amazon-secret:{uuid4().hex}"
        session.flush()
        seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id=selling_partner_id
        )
        seller_account.status = seller_account_status
        session.flush()
        participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id,
            seller_account_id=seller_account.id,
            marketplace_id=MARKETPLACE,
            region="na",
            connection_id=connection.id if with_connection else None,
        )
        participation.is_active = participation_active
        session.flush()
        return {
            "organization_id": org_id,
            "seller_account_id": seller_account.id,
            "marketplace_participation_id": participation.id,
            "connection_id": connection.id,
        }


def _get_run(organization_id, run_id):
    with session_scope() as session:
        return AmazonIngestionRunRepository(session).get_by_id(organization_id, run_id)


def _get_listing(organization_id, marketplace_participation_id, sku):
    with session_scope() as session:
        return AmazonSellerListingRepository(session).get_by_natural_key(
            organization_id, marketplace_participation_id, sku
        )


# --- ownership / security -----------------------------------------------


@pytest.mark.asyncio
async def test_missing_seller_account_fails_closed() -> None:
    scope = _seed_scope()
    service, client = _service([_page([_item("SKU-1")])])
    outcome = await service.sync(
        organization_id=scope["organization_id"],
        seller_account_id=uuid4(),
        marketplace_participation_id=scope["marketplace_participation_id"],
    )
    assert outcome.succeeded is False
    assert outcome.reason == "scope_not_found"
    assert client.requests == []


@pytest.mark.asyncio
async def test_missing_participation_fails_closed() -> None:
    scope = _seed_scope()
    service, client = _service([_page([_item("SKU-1")])])
    outcome = await service.sync(
        organization_id=scope["organization_id"],
        seller_account_id=scope["seller_account_id"],
        marketplace_participation_id=uuid4(),
    )
    assert outcome.reason == "scope_not_found"
    assert client.requests == []


@pytest.mark.asyncio
async def test_cross_organization_seller_account_fails_identically_to_missing() -> None:
    scope = _seed_scope()
    other_org_outcome_reason = None
    with session_scope() as session:
        from app.persistence.models import Organization

        other_org_id = uuid4()
        session.add(Organization(id=other_org_id, name="Other Org"))
    service, client = _service([_page([_item("SKU-1")])])
    outcome = await service.sync(
        organization_id=other_org_id,
        seller_account_id=scope["seller_account_id"],
        marketplace_participation_id=scope["marketplace_participation_id"],
    )
    assert outcome.reason == "scope_not_found"
    assert client.requests == []


@pytest.mark.asyncio
async def test_participation_belonging_to_another_seller_account_fails_identically() -> None:
    scope = _seed_scope()
    org_id = scope["organization_id"]
    with session_scope() as session:
        other_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id="AOTHERACCOUNT01"
        )
        other_account_id = other_account.id
    service, client = _service([_page([_item("SKU-1")])])
    outcome = await service.sync(
        organization_id=org_id,
        seller_account_id=other_account_id,
        marketplace_participation_id=scope["marketplace_participation_id"],
    )
    assert outcome.reason == "scope_not_found"
    assert client.requests == []


@pytest.mark.asyncio
async def test_missing_canonical_identity_fails_closed() -> None:
    scope = _seed_scope()
    with session_scope() as session:
        from app.persistence.models import AmazonSellerAccount

        row = session.get(AmazonSellerAccount, scope["seller_account_id"])
        row.selling_partner_id = "   "
    service, client = _service([_page([_item("SKU-1")])])
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.reason == "identity_missing"
    assert client.requests == []


@pytest.mark.asyncio
async def test_missing_connection_fails_closed() -> None:
    scope = _seed_scope(with_connection=False)
    service, client = _service([_page([_item("SKU-1")])])
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.reason == "connection_unresolvable"
    assert client.requests == []


@pytest.mark.asyncio
async def test_missing_application_credentials_fails_closed_through_the_real_client_path() -> None:
    """The other boundary this file's credential-hermeticity fix must not
    weaken: with a genuinely credential-empty `Settings` instance — matching
    a clean production/CI environment with nothing configured, not the
    synthetic-but-configured `_test_settings()` every other test in this
    file uses — the service's real credential-resolution path still fails
    closed with the existing sanitized `SpApiConfigurationError`, raised
    from `_client()` before any client is ever constructed. The fake
    resolver/client factory are still injected, so this makes zero live
    network calls either way; `_env_file=None` keeps `empty_settings`
    itself independent of `apps/api/.env` and ambient environment
    variables, so this test's outcome cannot depend on what happens to be
    configured on whatever machine runs it.
    """
    scope = _seed_scope()
    empty_settings = Settings(_env_file=None)
    service, client = _service([_page([_item("SKU-1")])], settings=empty_settings)
    with pytest.raises(SpApiConfigurationError):
        await service.sync(
            **{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")}
        )
    assert client.requests == []


@pytest.mark.asyncio
async def test_unresolvable_secret_fails_closed_and_records_a_failed_run() -> None:
    scope = _seed_scope()
    service, client = _service(
        [_page([_item("SKU-1")])], resolver=_FakeResolver(raise_error=InvalidSecretReferenceError())
    )
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.reason == "secret_unresolvable"
    assert outcome.ingestion_run_id is not None
    assert client.requests == []
    run = _get_run(scope["organization_id"], outcome.ingestion_run_id)
    assert run.status == "failed"
    assert run.failure_class == "secret_unresolvable"


@pytest.mark.asyncio
async def test_inactive_seller_account_fails_closed() -> None:
    scope = _seed_scope(seller_account_status="disconnected")
    service, client = _service([_page([_item("SKU-1")])])
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.reason == "scope_inactive"
    assert client.requests == []


@pytest.mark.asyncio
async def test_inactive_participation_fails_closed() -> None:
    scope = _seed_scope(participation_active=False)
    service, client = _service([_page([_item("SKU-1")])])
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.reason == "scope_inactive"
    assert client.requests == []


@pytest.mark.asyncio
async def test_no_identifier_or_secret_leaks_in_outcome() -> None:
    scope = _seed_scope()
    service, _ = _service([_page([_item("SKU-1")])])
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    dumped = str(outcome)
    assert "A1B2C3D4E5F6G7" not in dumped
    assert MARKETPLACE not in dumped
    assert "test-refresh-token" not in dumped


# --- lease / concurrency (service level) ----------------------------------


@pytest.mark.asyncio
async def test_unexpired_run_blocks_a_second_sync() -> None:
    scope = _seed_scope()
    with session_scope() as session:
        AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na",
            environment="PRODUCTION",
            connection_id=scope["connection_id"],
            lease_owner="already-running-owner",
            lease_duration_seconds=300,
        )
    service, client = _service([_page([_item("SKU-1")])])
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.reason == "already_running"
    assert client.requests == []


# --- pagination ------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_page_success() -> None:
    scope = _seed_scope()
    service, client = _service([_page([_item("SKU-1", summaries=[_summary()])])])
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.succeeded is True
    assert outcome.records_received == 1
    assert outcome.pages_fetched == 1
    assert outcome.pagination_complete is True
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_multiple_page_success() -> None:
    scope = _seed_scope()
    service, client = _service(
        [
            _page([_item("SKU-1", summaries=[_summary()])], number_of_results=2, next_token="TOKEN-2"),
            _page([_item("SKU-2", summaries=[_summary()])], number_of_results=2, next_token=None),
        ]
    )
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.succeeded is True
    assert outcome.pages_fetched == 2
    assert outcome.records_received == 2
    assert client.requests[1].page_token == "TOKEN-2"


@pytest.mark.asyncio
async def test_zero_result_success() -> None:
    scope = _seed_scope()
    service, client = _service([_page([], number_of_results=0)])
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.succeeded is True
    assert outcome.records_received == 0
    assert outcome.pagination_complete is True


@pytest.mark.asyncio
async def test_repeated_cyclic_token_is_rejected() -> None:
    scope = _seed_scope()
    service, client = _service(
        [
            _page([_item("SKU-1", summaries=[_summary()])], number_of_results=2, next_token="TOKEN-A"),
            _page([_item("SKU-2", summaries=[_summary()])], number_of_results=2, next_token="TOKEN-A"),
        ]
    )
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.succeeded is False
    assert outcome.reason == "cyclic_pagination_token"


@pytest.mark.asyncio
async def test_ceiling_exceeded_when_pages_run_out_with_token_remaining() -> None:
    scope = _seed_scope()
    pages = [
        _page([_item(f"SKU-{i}", summaries=[_summary()])], number_of_results=1000, next_token=f"TOKEN-{i}")
        for i in range(3)
    ]
    service, client = _service(pages, max_pages=3)
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.succeeded is False
    assert outcome.reason == "result_ceiling_exceeded"
    assert outcome.pages_fetched == 3


@pytest.mark.asyncio
async def test_reported_total_above_ceiling_fails_immediately_even_if_internally_consistent() -> None:
    scope = _seed_scope()
    service, client = _service(
        [_page([_item("SKU-1", summaries=[_summary()])], number_of_results=5000, next_token=None)]
    )
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.succeeded is False
    assert outcome.reason == "result_ceiling_exceeded"
    assert len(client.requests) == 1  # stopped immediately, did not keep paginating


@pytest.mark.asyncio
async def test_record_count_inconsistency_across_pages_is_rejected() -> None:
    scope = _seed_scope()
    service, client = _service(
        [
            _page([_item("SKU-1", summaries=[_summary()])], number_of_results=2, next_token="TOKEN-2"),
            _page([_item("SKU-2", summaries=[_summary()])], number_of_results=3, next_token=None),
        ]
    )
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.reason == "record_count_inconsistent"


@pytest.mark.asyncio
async def test_final_count_inconsistency_at_natural_completion_is_rejected() -> None:
    scope = _seed_scope()
    service, client = _service(
        [_page([_item("SKU-1", summaries=[_summary()])], number_of_results=2, next_token=None)]
    )
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.reason == "record_count_inconsistent"


@pytest.mark.asyncio
async def test_failure_on_a_later_page_is_recorded_truthfully() -> None:
    scope = _seed_scope()
    service, client = _service(
        [
            _page([_item("SKU-1", summaries=[_summary()])], number_of_results=2, next_token="TOKEN-2"),
            SpApiAuthenticationError("boom"),
        ]
    )
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.succeeded is False
    assert outcome.reason == "authentication_failed"
    assert outcome.pages_fetched == 1
    assert outcome.records_received == 1  # page 1 was received before the failure


@pytest.mark.asyncio
async def test_retry_exhausted_client_error_maps_to_transient_request_failed() -> None:
    scope = _seed_scope()
    service, client = _service([SpApiRequestFailedError("exhausted")])
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.reason == "transient_request_failed"


@pytest.mark.asyncio
async def test_malformed_page_from_client_is_recorded_as_malformed_page() -> None:
    scope = _seed_scope()
    service, client = _service([SpApiParseFailedError("bad json")])
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.reason == "malformed_page"


@pytest.mark.asyncio
async def test_no_page_token_appears_in_logs(caplog) -> None:
    scope = _seed_scope()
    service, client = _service(
        [
            _page([_item("SKU-1", summaries=[_summary()])], number_of_results=2, next_token="SECRET-LOOKING-TOKEN-XYZ"),
            _page([_item("SKU-2", summaries=[_summary()])], number_of_results=2, next_token=None),
        ]
    )
    with caplog.at_level("DEBUG"):
        await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    combined = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRET-LOOKING-TOKEN-XYZ" not in combined


# --- normalization integration --------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_sku_across_pages_rejects_whole_snapshot() -> None:
    scope = _seed_scope()
    service, client = _service(
        [
            _page([_item("SKU-DUP", summaries=[_summary()])], number_of_results=2, next_token="TOKEN-2"),
            _page([_item("SKU-DUP", summaries=[_summary()])], number_of_results=2, next_token=None),
        ]
    )
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.reason == "duplicate_sku"
    assert _get_listing(scope["organization_id"], scope["marketplace_participation_id"], "SKU-DUP") is None


@pytest.mark.asyncio
async def test_ambiguous_marketplace_summary_rejects_whole_snapshot() -> None:
    scope = _seed_scope()
    service, client = _service(
        [_page([_item("SKU-1", summaries=[_summary(), _summary()])])]
    )
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.reason == "ambiguous_marketplace_summary"


@pytest.mark.asyncio
async def test_rich_item_normalization_flows_through_to_reconciled_row() -> None:
    scope = _seed_scope()
    rich_item = _item(
        "SKU-RICH",
        summaries=[_summary(asin="B0RICH", conditionType="new_new", itemName="Rich Widget")],
        offers=[{"marketplaceId": MARKETPLACE, "offerType": "B2C", "price": {"currencyCode": "USD", "amount": "12.34"}}],
        fulfillmentAvailability=[{"fulfillmentChannelCode": "DEFAULT", "quantity": 7}],
        issues=[{"code": "X", "message": "m", "severity": "WARNING", "categories": ["LISTING"]}],
        productTypes=[{"marketplaceId": MARKETPLACE, "productType": "TOY"}],
    )
    service, client = _service([_page([rich_item])])
    outcome = await service.sync(**{k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")})
    assert outcome.succeeded is True

    row = _get_listing(scope["organization_id"], scope["marketplace_participation_id"], "SKU-RICH")
    assert row.asin == "B0RICH"
    assert row.is_buyable is True
    assert str(row.price_amount) == "12.34"
    assert row.price_currency == "USD"
    assert row.fulfillment_availability[0]["fulfillmentChannelCode"] == "DEFAULT"
    assert row.issue_count == 1
    assert row.highest_issue_severity == "WARNING"
    assert row.last_ingestion_run_id == outcome.ingestion_run_id


# --- reconciliation ---------------------------------------------------------


@pytest.mark.asyncio
async def test_first_and_second_snapshot_insert_then_update_preserving_first_seen() -> None:
    scope = _seed_scope()
    kwargs = {k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")}

    service1, _ = _service([_page([_item("SKU-1", summaries=[_summary(itemName="Original")])])])
    await service1.sync(**kwargs)
    first_seen = _get_listing(scope["organization_id"], scope["marketplace_participation_id"], "SKU-1").first_seen_at

    service2, _ = _service([_page([_item("SKU-1", summaries=[_summary(itemName="Updated")])])])
    outcome2 = await service2.sync(**kwargs)

    assert outcome2.succeeded is True
    row = _get_listing(scope["organization_id"], scope["marketplace_participation_id"], "SKU-1")
    assert row.item_name == "Updated"
    assert row.first_seen_at == first_seen


@pytest.mark.asyncio
async def test_missing_listing_deactivated_only_after_authoritative_completion() -> None:
    scope = _seed_scope()
    kwargs = {k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")}

    service1, _ = _service(
        [_page([_item("SKU-KEEP", summaries=[_summary()]), _item("SKU-DROP", summaries=[_summary()])])]
    )
    await service1.sync(**kwargs)

    service2, _ = _service([_page([_item("SKU-KEEP", summaries=[_summary()])])])
    outcome2 = await service2.sync(**kwargs)

    assert outcome2.succeeded is True
    assert _get_listing(scope["organization_id"], scope["marketplace_participation_id"], "SKU-KEEP").is_active is True
    assert _get_listing(scope["organization_id"], scope["marketplace_participation_id"], "SKU-DROP").is_active is False


@pytest.mark.asyncio
async def test_failed_snapshot_performs_no_deactivation() -> None:
    scope = _seed_scope()
    kwargs = {k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")}

    service1, _ = _service([_page([_item("SKU-EXISTING", summaries=[_summary()])])])
    await service1.sync(**kwargs)

    # Second sync fails mid-flight (duplicate SKU) — must not touch the
    # first snapshot's listing at all.
    service2, _ = _service(
        [
            _page([_item("SKU-DUP", summaries=[_summary()])], number_of_results=2, next_token="T2"),
            _page([_item("SKU-DUP", summaries=[_summary()])], number_of_results=2, next_token=None),
        ]
    )
    outcome2 = await service2.sync(**kwargs)
    assert outcome2.succeeded is False

    existing = _get_listing(scope["organization_id"], scope["marketplace_participation_id"], "SKU-EXISTING")
    assert existing.is_active is True  # untouched by the failed second attempt


@pytest.mark.asyncio
async def test_lease_lost_before_final_reconciliation_rolls_back_and_writes_no_listings() -> None:
    """Proves the rollback-and-fail-closed guarantee for the final
    transaction. Simulated by stealing the lease (via a raw completion call
    using a different lease_owner) between claim and reconciliation — this
    exercises the exact same `session_scope()` rollback mechanism any other
    exception during final reconciliation would trigger; the mechanism does
    not distinguish by exception type, so this is genuine structural proof,
    not merely proof of one specific cause."""
    scope = _seed_scope()
    kwargs = {k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")}

    captured_run_id = {}

    class _StealingClient(_FakeListingsClient):
        async def fetch_page(self, request):
            page = await super().fetch_page(request)
            # Steal the lease right after the (only) page is fetched, before
            # the service reaches its final reconciliation transaction.
            with session_scope() as session:
                run = AmazonIngestionRunRepository(session)
                # Find the just-claimed run via list_for_org (only one exists).
                runs = run.list_for_org(scope["organization_id"])
                started = next(r for r in runs if r.status == "started")
                captured_run_id["id"] = started.id
                started.lease_owner = "thief"
            return page

    client = _StealingClient([_page([_item("SKU-1", summaries=[_summary()])])])

    def factory(**_kwargs):
        return client

    service = AmazonListingsIngestionService(
        settings=_test_settings(),
        resolver=_FakeResolver(),
        listings_client_factory=factory,
        lease_owner_factory=lambda: "victim-owner",
    )
    outcome = await service.sync(**kwargs)

    assert outcome.succeeded is False
    assert outcome.reason == "lease_lost"
    assert _get_listing(scope["organization_id"], scope["marketplace_participation_id"], "SKU-1") is None


@pytest.mark.asyncio
async def test_inactive_listing_reactivated_end_to_end() -> None:
    scope = _seed_scope()
    kwargs = {k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")}

    service1, _ = _service([_page([_item("SKU-1", summaries=[_summary()])])])
    await service1.sync(**kwargs)

    service2, _ = _service([_page([])])  # SKU-1 absent -> deactivated
    await service2.sync(**kwargs)
    assert _get_listing(scope["organization_id"], scope["marketplace_participation_id"], "SKU-1").is_active is False

    service3, _ = _service([_page([_item("SKU-1", summaries=[_summary()])])])
    outcome3 = await service3.sync(**kwargs)
    assert outcome3.succeeded is True
    assert _get_listing(scope["organization_id"], scope["marketplace_participation_id"], "SKU-1").is_active is True


@pytest.mark.asyncio
async def test_truthful_run_counters_and_timestamps() -> None:
    scope = _seed_scope()
    kwargs = {k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")}
    service, _ = _service(
        [_page([_item("SKU-1", summaries=[_summary()]), _item("SKU-2", summaries=[_summary()])])]
    )
    outcome = await service.sync(**kwargs)
    run = _get_run(scope["organization_id"], outcome.ingestion_run_id)
    assert run.status == "succeeded"
    assert run.run_type == "listings"
    assert run.records_received == 2
    assert run.records_accepted == 2
    assert run.records_rejected == 0
    assert run.pages_fetched == 1
    assert run.reported_total_results == 2
    assert run.pagination_complete is True
    assert run.started_at is not None
    assert run.completed_at is not None
    assert run.lease_owner is None
    assert run.lease_expires_at is None


@pytest.mark.asyncio
async def test_no_raw_payload_or_unapproved_fields_persisted() -> None:
    scope = _seed_scope()
    kwargs = {k: scope[k] for k in ("organization_id", "seller_account_id", "marketplace_participation_id")}
    service, _ = _service([_page([_item("SKU-1", summaries=[_summary()])])])
    await service.sync(**kwargs)
    row = _get_listing(scope["organization_id"], scope["marketplace_participation_id"], "SKU-1")
    for field_name in ("offers", "fulfillment_availability", "issues", "product_types"):
        for entry in getattr(row, field_name):
            assert "attributes" not in entry
            assert "relationships" not in entry
            assert "procurement" not in entry
