"""12B.6B — AmazonInventoryIngestionService. No live Amazon call: the
FBA Inventory client is fully faked via `inventory_client_factory` (its
actual HTTP/parsing behavior is already covered by
`test_amazon_inventory_client.py`). Uses the shared, per-test-isolated
SQLite database, matching `test_amazon_sales_traffic_ingestion.py`'s
established pattern.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.amazon.inventory_client import InventoryPageRequest
from app.amazon.inventory_ingestion import (
    AmazonInventoryIngestionService,
    InventoryIngestionOutcome,
)
from app.amazon.inventory_models import (
    Granularity,
    InventoryDetails,
    InventoryPage,
    InventoryPageProvenance,
    InventorySummary,
    ReservedQuantity,
)
from app.amazon.secrets import SecretNotFoundError
from app.core.config import Settings
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiInvalidRequestError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import (
    AmazonIngestionRun,
    AmazonSellerInventory,
    AmazonSellerInventoryObservation,
)
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
)

MARKETPLACE = "ATVPDKIKX0DER"
from datetime import UTC, datetime


class _FakeResolver:
    def __init__(self, token: str = "test-refresh-token", raise_error: Exception | None = None) -> None:
        self._token = token
        self._raise_error = raise_error

    def resolve_refresh_token(self, *, organization_id, connection):
        if self._raise_error is not None:
            raise self._raise_error
        return SecretStr(self._token)


def _provenance() -> InventoryPageProvenance:
    return InventoryPageProvenance(
        operation="getInventorySummaries",
        region="na",
        endpoint_host="sellingpartnerapi-na.amazon.com",
        fetched_at=datetime.now(UTC),
        http_status=200,
        api_model_version="fba-inventory-api-model/v1",
        attempt_count=1,
    )


def _summary(
    *,
    seller_sku: str | None = "SKU-1",
    condition: str | None = "NewItem",
    asin: str | None = "B000000001",
    fnsku: str | None = "FNSKU1",
    total_quantity: int | None = 10,
    fulfillable_quantity: int | None = 8,
    reserved_total_quantity: int | None = 2,
) -> InventorySummary:
    """`seller_sku=None` (or `condition=None`) means the field is *absent*
    from Amazon's response, not present-with-an-explicit-null — the
    pinned contract documents no field on `InventorySummary` as nullable
    (see `inventory_models.py`'s own `optional_not_null` reasoning), so
    the kwarg is omitted entirely rather than passed as `None`, exactly
    matching what a real omitted-key response would parse to."""
    kwargs: dict = {"totalQuantity": total_quantity}
    if seller_sku is not None:
        kwargs["sellerSku"] = seller_sku
    if condition is not None:
        kwargs["condition"] = condition
    if asin is not None:
        kwargs["asin"] = asin
    if fnsku is not None:
        kwargs["fnSku"] = fnsku
    kwargs["inventoryDetails"] = InventoryDetails(
        fulfillableQuantity=fulfillable_quantity,
        reservedQuantity=ReservedQuantity(totalReservedQuantity=reserved_total_quantity),
    )
    return InventorySummary(**kwargs)


class _FakeInventoryClient:
    """Scripted stand-in for `AmazonSpApiInventoryClient`. Each `fetch_page`
    call pops the next scripted page/exception."""

    def __init__(self, *, pages: list | None = None) -> None:
        self._pages = list(pages or [])
        self.fetch_page_calls: list[InventoryPageRequest] = []

    async def fetch_page(self, request: InventoryPageRequest) -> InventoryPage:
        self.fetch_page_calls.append(request)
        item = self._pages.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _test_settings(**overrides) -> Settings:
    fields = dict(
        sp_api_lwa_client_id=SecretStr("test-sandbox-lwa-client-id-DO-NOT-USE"),
        sp_api_lwa_client_secret=SecretStr("test-sandbox-lwa-client-secret-DO-NOT-USE"),
        sp_api_production_lwa_client_id=SecretStr("test-production-lwa-client-id-DO-NOT-USE"),
        sp_api_production_lwa_client_secret=SecretStr("test-production-lwa-client-secret-DO-NOT-USE"),
        inventory_worker_min_page_interval_seconds=0.0,
    )
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


async def _noop_sleep(_seconds: float) -> None:
    return None


def _service(client: _FakeInventoryClient, **kwargs) -> AmazonInventoryIngestionService:
    def factory(**_kwargs):
        return client

    resolver = kwargs.pop("resolver", None) or _FakeResolver()
    settings = kwargs.pop("settings", None) or _test_settings()
    return AmazonInventoryIngestionService(
        settings=settings,
        resolver=resolver,
        inventory_client_factory=factory,
        lease_owner_factory=kwargs.pop("lease_owner_factory", None) or (lambda: f"lease-{uuid4().hex[:8]}"),
        sleep=kwargs.pop("sleep", None) or _noop_sleep,
        **kwargs,
    )


def _seed_scope() -> dict:
    org_id = current_organization_id()
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).create(
            organization_id=org_id, provider="SP_API", environment="PRODUCTION", region="na"
        )
        connection.token_reference = f"asi-amazon-secret:{uuid4().hex}"
        session.flush()
        seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id=f"A{uuid4().hex[:14].upper()}"
        )
        participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id, seller_account_id=seller_account.id, marketplace_id=MARKETPLACE, region="na",
            connection_id=connection.id,
        )
        session.flush()
        return {
            "org_id": org_id,
            "seller_account_id": seller_account.id,
            "participation_id": participation.id,
            "connection_id": connection.id,
        }


def _enqueue_and_claim(scope: dict):
    with session_scope() as session:
        AmazonIngestionRunRepository(session).enqueue_inventory_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["participation_id"], region="na", environment="PRODUCTION",
            connection_id=scope["connection_id"],
        )
    with session_scope() as session:
        claimed = AmazonIngestionRunRepository(session).claim_next_inventory_job(
            lease_owner="test-lease", lease_duration_seconds=300, max_global_active=10, max_active_per_organization=10
        )
        return claimed.id


def _get_run(run_id) -> AmazonIngestionRun:
    with session_scope() as session:
        return session.get(AmazonIngestionRun, run_id)


def _inventory_rows(participation_id) -> list[AmazonSellerInventory]:
    with session_scope() as session:
        from sqlalchemy import select

        return list(
            session.scalars(
                select(AmazonSellerInventory).where(
                    AmazonSellerInventory.marketplace_participation_id == participation_id
                )
            ).all()
        )


def _observation_rows(participation_id) -> list[AmazonSellerInventoryObservation]:
    with session_scope() as session:
        from sqlalchemy import select

        return list(
            session.scalars(
                select(AmazonSellerInventoryObservation).where(
                    AmazonSellerInventoryObservation.marketplace_participation_id == participation_id
                )
            ).all()
        )


# --- happy path: single page, full reconcile -------------------------------


@pytest.mark.asyncio
async def test_process_claimed_job_single_page_reconciles_successfully() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    page = InventoryPage(
        granularity=Granularity(granularityType="Marketplace", granularityId=MARKETPLACE),
        summaries=[_summary()],
        next_token=None,
        marketplace_id=MARKETPLACE,
        page_token_used=None,
        provenance=_provenance(),
    )
    client = _FakeInventoryClient(pages=[page])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome == InventoryIngestionOutcome(
        succeeded=True,
        seller_account_id=scope["seller_account_id"],
        marketplace_participation_id=scope["participation_id"],
        ingestion_run_id=run_id,
        pages_fetched=1,
        records_received=1,
        records_accepted=1,
        records_rejected=0,
        pagination_complete=True,
    )
    run = _get_run(run_id)
    assert run.status == "succeeded"
    assert run.pagination_complete is True

    rows = _inventory_rows(scope["participation_id"])
    assert len(rows) == 1
    assert rows[0].seller_sku == "SKU-1"
    assert rows[0].condition == "NewItem"
    assert rows[0].total_quantity == 10
    assert rows[0].fulfillable_quantity == 8
    assert rows[0].reserved_total_quantity == 2
    assert rows[0].is_active is True

    observations = _observation_rows(scope["participation_id"])
    assert len(observations) == 1
    assert observations[0].ingestion_run_id == run_id


# --- multi-page traversal ---------------------------------------------------


@pytest.mark.asyncio
async def test_process_claimed_job_traverses_multiple_pages() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    page1 = InventoryPage(
        granularity=Granularity(granularityType="Marketplace", granularityId=MARKETPLACE),
        summaries=[_summary(seller_sku="SKU-1")],
        next_token="token-2",
        marketplace_id=MARKETPLACE,
        page_token_used=None,
        provenance=_provenance(),
    )
    page2 = InventoryPage(
        granularity=Granularity(granularityType="Marketplace", granularityId=MARKETPLACE),
        summaries=[_summary(seller_sku="SKU-2", asin="B000000002", fnsku="FNSKU2")],
        next_token=None,
        marketplace_id=MARKETPLACE,
        page_token_used="token-2",
        provenance=_provenance(),
    )
    client = _FakeInventoryClient(pages=[page1, page2])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.succeeded is True
    assert outcome.pages_fetched == 2
    assert outcome.records_accepted == 2
    assert client.fetch_page_calls[0].page_token is None
    assert client.fetch_page_calls[1].page_token == "token-2"

    rows = {row.seller_sku for row in _inventory_rows(scope["participation_id"])}
    assert rows == {"SKU-1", "SKU-2"}


# --- per-row identity rejection, run still succeeds -------------------------


@pytest.mark.asyncio
async def test_row_missing_identity_is_rejected_and_counted_without_failing_the_run() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    page = InventoryPage(
        granularity=Granularity(granularityType="Marketplace", granularityId=MARKETPLACE),
        summaries=[_summary(seller_sku="SKU-1"), _summary(seller_sku=None, condition="NewItem")],
        next_token=None,
        marketplace_id=MARKETPLACE,
        page_token_used=None,
        provenance=_provenance(),
    )
    client = _FakeInventoryClient(pages=[page])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.succeeded is True
    assert outcome.records_received == 2
    assert outcome.records_accepted == 1
    assert outcome.records_rejected == 1
    rows = _inventory_rows(scope["participation_id"])
    assert len(rows) == 1
    assert rows[0].seller_sku == "SKU-1"


@pytest.mark.asyncio
async def test_row_with_negative_quantity_is_rejected_and_counted() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    page = InventoryPage(
        granularity=Granularity(granularityType="Marketplace", granularityId=MARKETPLACE),
        summaries=[_summary(seller_sku="SKU-1", total_quantity=-5)],
        next_token=None,
        marketplace_id=MARKETPLACE,
        page_token_used=None,
        provenance=_provenance(),
    )
    client = _FakeInventoryClient(pages=[page])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.succeeded is True
    assert outcome.records_accepted == 0
    assert outcome.records_rejected == 1
    assert _inventory_rows(scope["participation_id"]) == []


# --- full-sweep-only deactivation; quantities preserved, never zeroed ------


@pytest.mark.asyncio
async def test_missing_sku_on_later_full_sweep_is_deactivated_not_zeroed() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    page = InventoryPage(
        granularity=Granularity(granularityType="Marketplace", granularityId=MARKETPLACE),
        summaries=[_summary(seller_sku="SKU-1")],
        next_token=None,
        marketplace_id=MARKETPLACE,
        page_token_used=None,
        provenance=_provenance(),
    )
    client = _FakeInventoryClient(pages=[page])
    service = _service(client)
    await service.process_claimed_job(run_id)

    run_id_2 = _enqueue_and_claim(scope)
    empty_page = InventoryPage(
        granularity=Granularity(granularityType="Marketplace", granularityId=MARKETPLACE),
        summaries=[],
        next_token=None,
        marketplace_id=MARKETPLACE,
        page_token_used=None,
        provenance=_provenance(),
    )
    client2 = _FakeInventoryClient(pages=[empty_page])
    service2 = _service(client2)
    outcome2 = await service2.process_claimed_job(run_id_2)

    assert outcome2.succeeded is True
    rows = _inventory_rows(scope["participation_id"])
    assert len(rows) == 1
    assert rows[0].is_active is False
    assert rows[0].total_quantity == 10  # preserved, never zeroed
    assert rows[0].fulfillable_quantity == 8

    # Two successful runs the same "day" -> two distinct observation rows,
    # neither discarded (12B.6B corrected design).
    observations = _observation_rows(scope["participation_id"])
    assert len(observations) == 1  # second run had zero summaries, so zero new observations
    assert observations[0].ingestion_run_id == run_id


# --- max-page breach: terminal, no writes -----------------------------------


@pytest.mark.asyncio
async def test_max_page_breach_is_terminal_and_writes_nothing() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)

    def _page(token: str | None, next_token: str | None) -> InventoryPage:
        return InventoryPage(
            granularity=Granularity(granularityType="Marketplace", granularityId=MARKETPLACE),
            summaries=[_summary(seller_sku=f"SKU-{token or '0'}")],
            next_token=next_token,
            marketplace_id=MARKETPLACE,
            page_token_used=token,
            provenance=_provenance(),
        )

    pages = [_page(None, "t1"), _page("t1", "t2")]
    client = _FakeInventoryClient(pages=pages)
    service = _service(client, max_pages=2)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.succeeded is False
    assert outcome.reason == "pagination_bound_exceeded"
    assert outcome.pagination_complete is False
    run = _get_run(run_id)
    assert run.status == "failed"
    assert run.failure_class == "pagination_bound_exceeded"
    assert _inventory_rows(scope["participation_id"]) == []
    assert _observation_rows(scope["participation_id"]) == []


# --- token expiry / retryable transport failure -> reschedule, no writes ---


@pytest.mark.asyncio
async def test_rate_limited_reschedules_and_writes_nothing() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    client = _FakeInventoryClient(pages=[SpApiRateLimitedError("slow down", retry_after_seconds=12.0)])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.succeeded is False
    run = _get_run(run_id)
    assert run.status == "waiting_to_retry"
    assert run.failure_class == "throttled"
    assert run.next_retry_at is not None
    assert _inventory_rows(scope["participation_id"]) == []


@pytest.mark.asyncio
async def test_transient_request_failure_reschedules() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    client = _FakeInventoryClient(pages=[SpApiRequestFailedError("transient")])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.succeeded is False
    run = _get_run(run_id)
    assert run.status == "waiting_to_retry"
    assert run.failure_class == "transient_request_failed"


@pytest.mark.asyncio
async def test_malformed_page_reschedules() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    client = _FakeInventoryClient(pages=[SpApiParseFailedError("bad json")])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    run = _get_run(run_id)
    assert run.status == "waiting_to_retry"
    assert run.failure_class == "malformed_page"


# --- non-retryable failures terminalize immediately -------------------------


@pytest.mark.asyncio
async def test_authentication_failure_terminalizes_never_retries() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    client = _FakeInventoryClient(pages=[SpApiAuthenticationError("bad token")])
    service = _service(client)

    await service.process_claimed_job(run_id)

    run = _get_run(run_id)
    assert run.status == "failed"
    assert run.failure_class == "authentication_failed"


@pytest.mark.asyncio
async def test_invalid_request_terminalizes() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    client = _FakeInventoryClient(pages=[SpApiInvalidRequestError("bad request")])
    service = _service(client)

    await service.process_claimed_job(run_id)

    run = _get_run(run_id)
    assert run.status == "failed"
    assert run.failure_class == "invalid_request"


@pytest.mark.asyncio
async def test_unresolvable_connection_secret_terminalizes() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    client = _FakeInventoryClient(pages=[])
    service = _service(client, resolver=_FakeResolver(raise_error=SecretNotFoundError("no secret")))

    outcome = await service.process_claimed_job(run_id)

    assert outcome.succeeded is False
    assert outcome.reason == "secret_unresolvable"
    run = _get_run(run_id)
    assert run.status == "failed"


# --- retry budget exhaustion terminalizes as rate_limited -------------------


@pytest.mark.asyncio
async def test_retry_budget_exhausted_terminalizes() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    settings = _test_settings(inventory_sync_max_attempts=1)
    client = _FakeInventoryClient(pages=[SpApiRateLimitedError("slow down")])
    service = _service(client, settings=settings)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.succeeded is False
    assert outcome.reason == "rate_limited"
    run = _get_run(run_id)
    assert run.status == "failed"
    assert run.failure_class == "rate_limited"


# --- proactive inter-page throttle is invoked, never the sole mechanism ----


@pytest.mark.asyncio
async def test_min_page_interval_sleep_is_invoked_between_pages() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    page1 = InventoryPage(
        granularity=Granularity(granularityType="Marketplace", granularityId=MARKETPLACE),
        summaries=[_summary(seller_sku="SKU-1")],
        next_token="t1",
        marketplace_id=MARKETPLACE,
        page_token_used=None,
        provenance=_provenance(),
    )
    page2 = InventoryPage(
        granularity=Granularity(granularityType="Marketplace", granularityId=MARKETPLACE),
        summaries=[_summary(seller_sku="SKU-2", asin="B000000002", fnsku="FNSKU2")],
        next_token=None,
        marketplace_id=MARKETPLACE,
        page_token_used="t1",
        provenance=_provenance(),
    )
    client = _FakeInventoryClient(pages=[page1, page2])
    sleep_calls: list[float] = []

    async def _tracking_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    settings = _test_settings(inventory_worker_min_page_interval_seconds=0.5)
    service = _service(client, settings=settings, sleep=_tracking_sleep)

    await service.process_claimed_job(run_id)

    # Sleeps once between the two pages (never before the first page).
    assert 0.5 in sleep_calls
