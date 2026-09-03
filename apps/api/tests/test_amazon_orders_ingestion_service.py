"""12B.4D — AmazonOrdersIngestionService. No live Amazon call: the Orders
client is fully faked via `orders_client_factory` (its actual HTTP
behavior is already covered by 12B.4C's own test suite). Uses the shared,
per-test-isolated SQLite database, matching
`test_amazon_listings_ingestion_service.py`'s established pattern.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.amazon.orders_client import GetOrderRequest, SearchOrdersPageRequest
from app.amazon.orders_ingestion import (
    AmazonOrdersIngestionService,
    OrdersIngestionOutcome,
    _ClaimedOrdersRun,
    _ClaimFailure,
    _ConnectionSnapshot,
    _TraversalResult,
)
from app.amazon.orders_models import Order, OrdersPage, OrdersPageProvenance
from app.core.config import Settings
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiInvalidRequestError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonIngestionRun, AmazonSellerOrder
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunMarketplaceParticipationRepository,
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonOrdersSyncCheckpointRepository,
    AmazonSellerAccountRepository,
    AmazonSellerOrderItemRepository,
    AmazonSellerOrderRepository,
)

MARKETPLACE = "ATVPDKIKX0DER"
MARKETPLACE_2 = "A2EUQ1WTGCTBG2"


# --- fakes -------------------------------------------------------------


class _FakeResolver:
    def __init__(self, token: str = "test-refresh-token", raise_error: Exception | None = None) -> None:
        self._token = token
        self._raise_error = raise_error

    def resolve_refresh_token(self, *, organization_id, connection):
        if self._raise_error is not None:
            raise self._raise_error
        return SecretStr(self._token)


class _FakeOrdersClient:
    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.requests: list[SearchOrdersPageRequest] = []

    async def search_orders(self, request: SearchOrdersPageRequest) -> OrdersPage:
        self.requests.append(request)
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def get_order(self, request: GetOrderRequest):  # pragma: no cover - unused by ingestion
        raise NotImplementedError


def _test_settings(**overrides) -> Settings:
    fields = dict(
        sp_api_lwa_client_id=SecretStr("test-sandbox-lwa-client-id-DO-NOT-USE"),
        sp_api_lwa_client_secret=SecretStr("test-sandbox-lwa-client-secret-DO-NOT-USE"),
        sp_api_production_lwa_client_id=SecretStr("test-production-lwa-client-id-DO-NOT-USE"),
        sp_api_production_lwa_client_secret=SecretStr("test-production-lwa-client-secret-DO-NOT-USE"),
    )
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def _service(script: list, **kwargs) -> tuple[AmazonOrdersIngestionService, _FakeOrdersClient]:
    client = _FakeOrdersClient(script)

    def factory(**_kwargs):
        return client

    resolver = kwargs.pop("resolver", None) or _FakeResolver()
    lease_owner_factory = kwargs.pop("lease_owner_factory", None) or (lambda: f"lease-{uuid4().hex[:8]}")
    settings = kwargs.pop("settings", None) or _test_settings()
    service = AmazonOrdersIngestionService(
        settings=settings,
        resolver=resolver,
        orders_client_factory=factory,
        lease_owner_factory=lease_owner_factory,
        **kwargs,
    )
    return service, client


def _item_dict(
    *,
    order_item_id: str = "ITEM-1",
    quantity_ordered: int = 1,
    seller_sku: str | None = "SKU-1",
    asin: str | None = "B0TEST0001",
) -> dict:
    return {
        "orderItemId": order_item_id,
        "quantityOrdered": quantity_ordered,
        "product": {k: v for k, v in {"asin": asin, "sellerSku": seller_sku}.items() if v is not None},
        "fulfillment": {"quantityFulfilled": quantity_ordered, "quantityUnfulfilled": 0},
    }


def _order_dict(
    order_id: str,
    *,
    marketplace_id: str = MARKETPLACE,
    quantity_ordered: int = 1,
    seller_sku: str = "SKU-1",
    asin: str | None = "B0TEST0001",
    fulfillment_status: str | None = "SHIPPED",
    last_updated_time: datetime | None = None,
    order_item_id: str = "ITEM-1",
    grand_total: str | None = "19.99",
    extra_items: list[dict] | None = None,
) -> dict:
    body: dict = {
        "orderId": order_id,
        "createdTime": "2026-01-01T00:00:00Z",
        "lastUpdatedTime": (last_updated_time or datetime(2026, 1, 2, tzinfo=UTC)).isoformat(),
        "salesChannel": {"channelName": "AMAZON", "marketplaceId": marketplace_id, "marketplaceName": "Amazon"},
        "orderItems": [
            _item_dict(order_item_id=order_item_id, quantity_ordered=quantity_ordered, seller_sku=seller_sku, asin=asin)
        ]
        + (extra_items or []),
    }
    if fulfillment_status:
        body["fulfillment"] = {"fulfillmentStatus": fulfillment_status}
    if grand_total:
        body["proceeds"] = {"grandTotal": {"amount": grand_total, "currencyCode": "USD"}}
    return body


def _order(order_id: str, **kwargs) -> Order:
    return Order.model_validate(_order_dict(order_id, **kwargs))


def _page(orders: list[Order], *, next_token: str | None = None, marketplace_ids: tuple[str, ...] = (MARKETPLACE,)) -> OrdersPage:
    return OrdersPage(
        orders=orders,
        next_token=next_token,
        marketplace_ids=marketplace_ids,
        pagination_token_used=None,
        provenance=OrdersPageProvenance(
            operation="searchOrders",
            region="na",
            endpoint_host="sellingpartnerapi-na.amazon.com",
            fetched_at=datetime.now(UTC),
            http_status=200,
            api_model_version="orders-api-model/2026-01-01",
            attempt_count=1,
        ),
    )


def _seed_scope(
    *,
    seller_account_status: str = "active",
    participation_active: bool = True,
    with_connection: bool = True,
    selling_partner_id: str = "A1B2C3D4E5F6G7",
    marketplace_id: str = MARKETPLACE,
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
            marketplace_id=marketplace_id,
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
            "region": "na",
            "environment": "PRODUCTION",
        }


def _enqueue_and_claim(scope: dict, *, participation_ids: list | None = None, lease_owner: str = "test-lease") -> AmazonIngestionRun:
    with session_scope() as session:
        run_repo = AmazonIngestionRunMarketplaceParticipationRepository(session)
        claim = run_repo.enqueue_orders_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            connection_id=scope["connection_id"],
            marketplace_participation_ids=participation_ids or [scope["marketplace_participation_id"]],
            region=scope["region"],
            environment=scope["environment"],
        )
        assert claim.claimed, claim.reason
        claimed = run_repo.claim_orders_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            region=scope["region"],
            environment=scope["environment"],
            lease_owner=lease_owner,
            lease_duration_seconds=300,
        )
        assert claimed.claimed, claimed.reason
        return session.get(AmazonIngestionRun, claimed.run_id)


def _get_run(organization_id, run_id) -> AmazonIngestionRun:
    with session_scope() as session:
        return session.get(AmazonIngestionRun, run_id)


def _get_order(organization_id, participation_id, amazon_order_id):
    with session_scope() as session:
        return AmazonSellerOrderRepository(session).get_by_natural_key(organization_id, participation_id, amazon_order_id)


def _get_items(organization_id, participation_id, order_row_id):
    with session_scope() as session:
        return AmazonSellerOrderItemRepository(session).list_for_order(organization_id, participation_id, order_row_id)


def _get_checkpoint(organization_id, participation_id):
    with session_scope() as session:
        return AmazonOrdersSyncCheckpointRepository(session).get(organization_id, participation_id)


# --- happy path: multi-page, persistence, checkpoint advancement --------


@pytest.mark.asyncio
async def test_multi_page_ingestion_persists_orders_and_advances_checkpoint() -> None:
    scope = _seed_scope()
    run = _enqueue_and_claim(scope)
    service, client = _service(
        [
            _page([_order("902-1", seller_sku="SKU-A")], next_token="TOKEN-PAGE-2"),
            _page([_order("902-2", seller_sku="SKU-B")]),
        ]
    )

    outcome = await service.process_claimed_job(run.id)

    assert outcome.succeeded is True
    assert outcome.pagination_complete is True
    assert outcome.pages_fetched == 2
    assert outcome.orders_accepted == 2
    assert outcome.items_accepted == 2
    assert len(client.requests) == 2
    assert client.requests[1].pagination_token == "TOKEN-PAGE-2"

    order1 = _get_order(scope["organization_id"], scope["marketplace_participation_id"], "902-1")
    order2 = _get_order(scope["organization_id"], scope["marketplace_participation_id"], "902-2")
    assert order1 is not None and order2 is not None
    assert order1.order_total_amount == Decimal("19.99")

    checkpoint = _get_checkpoint(scope["organization_id"], scope["marketplace_participation_id"])
    assert checkpoint is not None
    assert checkpoint.synced_through_at is not None

    final_run = _get_run(scope["organization_id"], run.id)
    assert final_run.status == "succeeded"
    assert final_run.pagination_complete is True


@pytest.mark.asyncio
async def test_multiple_embedded_items_all_persisted() -> None:
    scope = _seed_scope()
    run = _enqueue_and_claim(scope)
    second_item = _item_dict(order_item_id="ITEM-2", quantity_ordered=3, seller_sku="SKU-2", asin="B0TEST0002")
    order = _order("902-multi", extra_items=[second_item])
    service, client = _service([_page([order])])

    outcome = await service.process_claimed_job(run.id)

    assert outcome.succeeded is True
    order_row = _get_order(scope["organization_id"], scope["marketplace_participation_id"], "902-multi")
    items = _get_items(scope["organization_id"], scope["marketplace_participation_id"], order_row.id)
    assert {i.seller_sku for i in items} == {"SKU-1", "SKU-2"}


# --- idempotency / update behavior --------------------------------------


@pytest.mark.asyncio
async def test_repeated_order_upsert_is_idempotent_and_reflects_update() -> None:
    scope = _seed_scope()

    run1 = _enqueue_and_claim(scope)
    service, _ = _service([_page([_order("902-repeat", fulfillment_status="UNSHIPPED")])])
    outcome1 = await service.process_claimed_job(run1.id)
    assert outcome1.succeeded is True

    run2 = _enqueue_and_claim(scope, lease_owner="lease-2")
    service2, _ = _service(
        [_page([_order("902-repeat", fulfillment_status="SHIPPED", last_updated_time=datetime(2026, 1, 3, tzinfo=UTC))])]
    )
    outcome2 = await service2.process_claimed_job(run2.id)
    assert outcome2.succeeded is True

    order_row = _get_order(scope["organization_id"], scope["marketplace_participation_id"], "902-repeat")
    assert order_row.fulfillment_status == "SHIPPED"

    with session_scope() as session:
        count = session.execute(select(func.count()).select_from(AmazonSellerOrder)).scalar_one()
    assert count == 1  # never a duplicate row for the same natural key


# --- retry / rate-limit / waiting_to_retry (no real sleeps) -------------


@pytest.mark.asyncio
async def test_throttled_response_reschedules_waiting_to_retry_honoring_retry_after() -> None:
    scope = _seed_scope()
    run = _enqueue_and_claim(scope)
    service, client = _service([SpApiRateLimitedError("throttled", retry_after_seconds=42.0)])

    outcome = await service.process_claimed_job(run.id)

    assert outcome.succeeded is False
    assert outcome.reason == "waiting_to_retry"
    final_run = _get_run(scope["organization_id"], run.id)
    assert final_run.status == "waiting_to_retry"
    assert final_run.lease_owner is None
    assert final_run.next_retry_at is not None
    delta = (final_run.next_retry_at.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
    assert 35 <= delta <= 45  # honors the 42s Retry-After signal, not exponential backoff


@pytest.mark.asyncio
async def test_retry_budget_exhaustion_becomes_terminal_rate_limited() -> None:
    scope = _seed_scope()
    settings = _test_settings(orders_sync_max_attempts=1)
    run = _enqueue_and_claim(scope)
    service, _ = _service([SpApiRateLimitedError("throttled")], settings=settings)

    outcome = await service.process_claimed_job(run.id)

    assert outcome.succeeded is False
    assert outcome.reason == "rate_limited"
    final_run = _get_run(scope["organization_id"], run.id)
    assert final_run.status == "failed"
    assert final_run.failure_class == "rate_limited"


@pytest.mark.asyncio
async def test_authentication_failure_is_terminal_not_retried() -> None:
    scope = _seed_scope()
    run = _enqueue_and_claim(scope)
    service, _ = _service([SpApiAuthenticationError("auth failed")])

    outcome = await service.process_claimed_job(run.id)

    assert outcome.succeeded is False
    assert outcome.reason == "authentication_failed"
    final_run = _get_run(scope["organization_id"], run.id)
    assert final_run.status == "failed"


# --- malformed page / partial rejection ---------------------------------


@pytest.mark.asyncio
async def test_malformed_page_is_retryable_not_a_hard_crash() -> None:
    scope = _seed_scope()
    settings = _test_settings(orders_sync_max_attempts=1)
    run = _enqueue_and_claim(scope)
    service, _ = _service([SpApiParseFailedError("bad json")], settings=settings)

    outcome = await service.process_claimed_job(run.id)

    assert outcome.succeeded is False
    assert outcome.reason == "rate_limited"  # exhausted after 1 attempt, sanitized terminal name
    final_run = _get_run(scope["organization_id"], run.id)
    assert final_run.status == "failed"
    # No order from a page that failed to parse should ever be persisted.
    with session_scope() as session:
        assert session.scalars(select(AmazonSellerOrder)).first() is None


@pytest.mark.asyncio
async def test_partial_page_rejection_counters_for_unattributable_order() -> None:
    """Two participations covered in one run; one returned order's
    marketplace id matches neither — it must be rejected (counted), not
    crash the traversal or the other, valid order on the same page."""
    scope1 = _seed_scope(marketplace_id=MARKETPLACE)
    org_id = scope1["organization_id"]
    with session_scope() as session:
        participation2 = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id,
            seller_account_id=scope1["seller_account_id"],
            marketplace_id=MARKETPLACE_2,
            region="na",
            connection_id=scope1["connection_id"],
        )
        participation2_id = participation2.id

    run = _enqueue_and_claim(scope1, participation_ids=[scope1["marketplace_participation_id"], participation2_id])
    valid_order = _order("902-valid", marketplace_id=MARKETPLACE)
    unattributable_order = _order("902-orphan", marketplace_id="A_UNKNOWN_MARKETPLACE", order_item_id="ITEM-ORPHAN")
    service, _ = _service([_page([valid_order, unattributable_order], marketplace_ids=(MARKETPLACE, MARKETPLACE_2))])

    outcome = await service.process_claimed_job(run.id)

    assert outcome.succeeded is True
    assert outcome.orders_received == 2
    assert outcome.orders_accepted == 1
    assert outcome.orders_rejected == 1
    assert outcome.items_rejected == 1
    assert _get_order(org_id, scope1["marketplace_participation_id"], "902-valid") is not None
    assert _get_order(org_id, scope1["marketplace_participation_id"], "902-orphan") is None
    assert _get_order(org_id, participation2_id, "902-orphan") is None


@pytest.mark.asyncio
async def test_item_missing_seller_sku_is_rejected_order_still_persisted() -> None:
    scope = _seed_scope()
    run = _enqueue_and_claim(scope)
    bad_item = _item_dict(order_item_id="ITEM-BAD", quantity_ordered=1, seller_sku=None, asin="B0X")
    order = Order.model_validate(
        {
            "orderId": "902-partial-item",
            "createdTime": "2026-01-01T00:00:00Z",
            "lastUpdatedTime": "2026-01-02T00:00:00Z",
            "salesChannel": {"channelName": "AMAZON", "marketplaceId": MARKETPLACE},
            "orderItems": [bad_item],
        }
    )
    service, _ = _service([_page([order])])

    outcome = await service.process_claimed_job(run.id)

    assert outcome.succeeded is True
    assert outcome.orders_accepted == 1
    assert outcome.items_rejected == 1
    assert outcome.items_accepted == 0
    order_row = _get_order(scope["organization_id"], scope["marketplace_participation_id"], "902-partial-item")
    assert order_row is not None
    items = _get_items(scope["organization_id"], scope["marketplace_participation_id"], order_row.id)
    assert items == []


# --- multi-marketplace ownership / provenance ---------------------------


@pytest.mark.asyncio
async def test_multi_marketplace_orders_routed_to_correct_participation() -> None:
    scope1 = _seed_scope(marketplace_id=MARKETPLACE)
    org_id = scope1["organization_id"]
    with session_scope() as session:
        participation2 = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id,
            seller_account_id=scope1["seller_account_id"],
            marketplace_id=MARKETPLACE_2,
            region="na",
            connection_id=scope1["connection_id"],
        )
        participation2_id = participation2.id

    run = _enqueue_and_claim(scope1, participation_ids=[scope1["marketplace_participation_id"], participation2_id])
    order_mp1 = _order("902-mp1", marketplace_id=MARKETPLACE)
    order_mp2 = _order("902-mp2", marketplace_id=MARKETPLACE_2, seller_sku="SKU-MP2")
    service, _ = _service([_page([order_mp1, order_mp2], marketplace_ids=(MARKETPLACE, MARKETPLACE_2))])

    outcome = await service.process_claimed_job(run.id)

    assert outcome.succeeded is True
    assert outcome.orders_accepted == 2
    assert _get_order(org_id, scope1["marketplace_participation_id"], "902-mp1") is not None
    assert _get_order(org_id, participation2_id, "902-mp2") is not None
    assert _get_order(org_id, scope1["marketplace_participation_id"], "902-mp2") is None

    cp1 = _get_checkpoint(org_id, scope1["marketplace_participation_id"])
    cp2 = _get_checkpoint(org_id, participation2_id)
    assert cp1 is not None and cp2 is not None


# --- scope validation / foreign-organization isolation ------------------


@pytest.mark.asyncio
async def test_not_claimed_run_returns_sanitized_outcome_without_calling_amazon() -> None:
    service, client = _service([])
    outcome = await service.process_claimed_job(uuid4())
    assert outcome.succeeded is False
    assert outcome.reason == "not_claimed"
    assert client.requests == []


@pytest.mark.asyncio
async def test_scope_ambiguous_when_participations_span_different_regions() -> None:
    """A run's own claimed scope enforces single (region, connection) at
    the repository layer already — this proves the ingestion-service-side
    _check_scope also rejects an inconsistent set defensively, never
    guessing which region/connection to use. Uses a second connection in
    a different *environment* (SANDBOX) to obtain a genuinely distinct
    connection_id/region — amazon_connections has a unique constraint on
    (organization_id, provider, environment), so two PRODUCTION
    connections for the same org/provider cannot coexist."""
    scope = _seed_scope()
    org_id = scope["organization_id"]
    with session_scope() as session:
        other_connection = AmazonConnectionRepository(session).create(
            organization_id=org_id, provider="SP_API", environment="SANDBOX", region="eu"
        )
        other_connection.token_reference = f"asi-amazon-secret:{uuid4().hex}"
        session.flush()
        other_participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id,
            seller_account_id=scope["seller_account_id"],
            marketplace_id="A1PA6795UKMFR9",
            region="eu",
            connection_id=other_connection.id,
        )
        other_participation_id = other_participation.id

        with pytest.raises(_ClaimFailure) as excinfo:
            AmazonOrdersIngestionService._check_scope(
                session,
                organization_id=org_id,
                seller_account_id=scope["seller_account_id"],
                marketplace_participation_ids=[scope["marketplace_participation_id"], other_participation_id],
            )
        assert excinfo.value.reason == "scope_ambiguous"


# --- 12B.4D remediation: durable pagination continuation -----------------


def _reclaim_for_retry(scope: dict, run_id, *, lease_owner: str) -> None:
    """Mirrors what the real worker loop does on its next poll: makes an
    already-`waiting_to_retry` run immediately eligible (bypassing the
    real backoff delay, irrelevant to what these tests verify) and
    re-claims it to `started`."""
    with session_scope() as session:
        row = session.get(AmazonIngestionRun, run_id)
        row.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    with session_scope() as session:
        claim = AmazonIngestionRunMarketplaceParticipationRepository(session).claim_orders_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            region=scope["region"],
            environment=scope["environment"],
            lease_owner=lease_owner,
            lease_duration_seconds=300,
        )
        assert claim.claimed, claim.reason


@pytest.mark.asyncio
async def test_interruption_after_a_page_resumes_from_the_saved_token_not_page_one() -> None:
    """A page-20-style interruption: attempt 1 durably commits one page
    (and the token to fetch the next one) before a transient failure on
    the *next* fetch reschedules the run. Attempt 2 must present the
    saved token on its very first request — the already-committed page
    is never re-fetched."""
    scope = _seed_scope()
    run = _enqueue_and_claim(scope)
    service1, client1 = _service(
        [
            _page([_order("902-p1", seller_sku="SKU-1")], next_token="TOKEN-PAGE-2"),
            SpApiRequestFailedError("transient network failure"),
        ]
    )

    outcome1 = await service1.process_claimed_job(run.id)

    assert outcome1.succeeded is False
    assert outcome1.reason == "waiting_to_retry"
    run_after_attempt1 = _get_run(scope["organization_id"], run.id)
    assert run_after_attempt1.status == "waiting_to_retry"
    assert run_after_attempt1.orders_pagination_next_token == "TOKEN-PAGE-2"
    assert run_after_attempt1.pages_fetched == 1
    frozen_window = run_after_attempt1.orders_window_last_updated_after
    assert frozen_window is not None
    # Plain ORM reads (unlike `freeze_orders_window_if_needed`'s own
    # return value) do not go through this codebase's SQLite-tzinfo
    # normalization — see that method's docstring.
    if frozen_window.tzinfo is None:
        frozen_window = frozen_window.replace(tzinfo=UTC)

    _reclaim_for_retry(scope, run.id, lease_owner="test-lease-2")
    service2, client2 = _service([_page([_order("902-p2", seller_sku="SKU-2")])])

    outcome2 = await service2.process_claimed_job(run.id)

    assert outcome2.succeeded is True
    assert outcome2.pagination_complete is True
    assert len(client2.requests) == 1
    assert client2.requests[0].pagination_token == "TOKEN-PAGE-2"
    assert client2.requests[0].last_updated_after == frozen_window  # window stayed frozen across attempts
    assert outcome2.pages_fetched == 2  # cumulative across both attempts, not attempt-local

    assert _get_order(scope["organization_id"], scope["marketplace_participation_id"], "902-p1") is not None
    assert _get_order(scope["organization_id"], scope["marketplace_participation_id"], "902-p2") is not None

    final_run = _get_run(scope["organization_id"], run.id)
    assert final_run.status == "succeeded"
    assert final_run.orders_pagination_next_token is None  # cleared on terminal


@pytest.mark.asyncio
async def test_duplicate_page_persistence_is_idempotent() -> None:
    """The exact observable effect of a crash between Amazon's response
    and this module's own transaction commit: the resumed attempt
    refetches and re-persists a page whose orders a prior attempt may
    already have committed. Persisting the identical page twice must
    never create a duplicate row."""
    scope = _seed_scope()
    run = _enqueue_and_claim(scope)
    service, _ = _service([])
    order = _order("902-dup", seller_sku="SKU-DUP")
    claimed = _ClaimedOrdersRun(
        run_id=run.id,
        organization_id=scope["organization_id"],
        seller_account_id=scope["seller_account_id"],
        lease_owner=run.lease_owner,
        region=scope["region"],
        environment=scope["environment"],
        selling_partner_id="A1B2C3D4E5F6G7",
        connection=_ConnectionSnapshot(
            organization_id=scope["organization_id"],
            id=scope["connection_id"],
            provider="SP_API",
            environment="PRODUCTION",
            token_reference="asi-amazon-secret:test",
        ),
        marketplace_ids=(MARKETPLACE,),
        participation_by_marketplace_id={MARKETPLACE: scope["marketplace_participation_id"]},
        single_participation_id=scope["marketplace_participation_id"],
        participation_checkpoints={scope["marketplace_participation_id"]: None},
        orders_window_last_updated_after=datetime(2026, 1, 1, tzinfo=UTC),
        orders_window_captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        resume_pagination_token=None,
        resume_pages_committed=0,
    )
    result = _TraversalResult()

    first_ok = service._persist_page(claimed=claimed, orders=[order], next_token="TOKEN-NEXT", result=result)
    second_ok = service._persist_page(claimed=claimed, orders=[order], next_token="TOKEN-NEXT", result=result)

    assert first_ok is True
    assert second_ok is True
    with session_scope() as session:
        count = session.execute(select(func.count()).select_from(AmazonSellerOrder)).scalar_one()
    assert count == 1
    assert _get_order(scope["organization_id"], scope["marketplace_participation_id"], "902-dup") is not None
    run_row = _get_run(scope["organization_id"], run.id)
    assert run_row.orders_pagination_next_token == "TOKEN-NEXT"


class _LeaseStealingClient:
    """Simulates another worker reclaiming this run's lease while this
    attempt's request to Amazon is still in flight — a fully realistic
    race, not a contrived one: `search_orders` is exactly the long
    (~178.6s-paced) await this scenario needs to happen during."""

    def __init__(self, page: OrdersPage, run_id) -> None:
        self._page = page
        self._run_id = run_id
        self.requests: list[SearchOrdersPageRequest] = []

    async def search_orders(self, request: SearchOrdersPageRequest) -> OrdersPage:
        self.requests.append(request)
        with session_scope() as session:
            row = session.get(AmazonIngestionRun, self._run_id)
            row.lease_owner = "someone-else-entirely"
            session.commit()
        return self._page

    async def get_order(self, request: GetOrderRequest):  # pragma: no cover - unused by ingestion
        raise NotImplementedError


@pytest.mark.asyncio
async def test_lease_lost_mid_flight_does_not_advance_the_durable_cursor() -> None:
    """If this worker no longer verifiably holds the lease by the time a
    page's persist transaction runs, the run's own durable continuation
    pointer must not advance to reflect work this attempt no longer has
    authority to claim credit for — whoever now holds the lease resumes
    from the pre-attempt state, safely re-fetching (idempotently) this
    same page."""
    scope = _seed_scope()
    run = _enqueue_and_claim(scope)
    page = _page([_order("902-lost", seller_sku="SKU-LOST")], next_token="TOKEN-SHOULD-NOT-PERSIST")
    client = _LeaseStealingClient(page, run.id)
    service = AmazonOrdersIngestionService(
        settings=_test_settings(),
        resolver=_FakeResolver(),
        orders_client_factory=lambda **_kwargs: client,
        lease_owner_factory=lambda: "unused",
    )

    outcome = await service.process_claimed_job(run.id)

    assert outcome.succeeded is False
    assert outcome.reason == "lease_lost"
    final_run = _get_run(scope["organization_id"], run.id)
    assert final_run.lease_owner == "someone-else-entirely"
    assert final_run.orders_pagination_next_token is None
    assert final_run.pages_fetched == 0


@pytest.mark.asyncio
async def test_invalid_request_while_resuming_a_token_falls_back_to_page_one_within_the_frozen_window() -> None:
    """Amazon documents no distinguishing error code for an expired/
    invalid `paginationToken` — see the module docstring. Presenting a
    continuation token and getting an invalid-request response back must
    be classified as `pagination_token_rejected` (retryable, explicit,
    truthfully recorded), falling back to a page-one restart *within the
    still-frozen window* — never a silent one, and never a full window
    recompute."""
    scope = _seed_scope()
    run = _enqueue_and_claim(scope)
    service1, _ = _service(
        [
            _page([_order("902-a", seller_sku="SKU-A")], next_token="TOKEN-2"),
            SpApiInvalidRequestError("token no longer valid"),
        ]
    )

    outcome1 = await service1.process_claimed_job(run.id)

    assert outcome1.succeeded is False
    assert outcome1.reason == "waiting_to_retry"
    run_after = _get_run(scope["organization_id"], run.id)
    assert run_after.status == "waiting_to_retry"
    assert run_after.failure_class == "pagination_token_rejected"
    assert run_after.orders_pagination_next_token is None
    assert run_after.pages_fetched == 0
    assert run_after.orders_window_last_updated_after is not None  # window itself untouched

    _reclaim_for_retry(scope, run.id, lease_owner="test-lease-2")
    service2, client2 = _service([_page([_order("902-a", seller_sku="SKU-A")])])

    outcome2 = await service2.process_claimed_job(run.id)

    assert outcome2.succeeded is True
    assert client2.requests[0].pagination_token is None  # restarted at page one, not resumed


@pytest.mark.asyncio
async def test_invalid_request_on_first_page_with_no_token_is_a_genuine_terminal_failure() -> None:
    """The identical error code, with no continuation token in play,
    cannot be a token-expiry symptom by definition and must stay a
    genuine terminal `invalid_request` — never retried."""
    scope = _seed_scope()
    run = _enqueue_and_claim(scope)
    service, _ = _service([SpApiInvalidRequestError("bad request")])

    outcome = await service.process_claimed_job(run.id)

    assert outcome.succeeded is False
    assert outcome.reason == "invalid_request"
    final_run = _get_run(scope["organization_id"], run.id)
    assert final_run.status == "failed"
    assert final_run.orders_pagination_next_token is None


@pytest.mark.asyncio
async def test_terminal_failure_clears_a_previously_saved_durable_token() -> None:
    scope = _seed_scope()
    run = _enqueue_and_claim(scope)
    service1, _ = _service(
        [
            _page([_order("902-term-1", seller_sku="SKU-1")], next_token="TOKEN-ABANDONED"),
            SpApiRequestFailedError("transient"),
        ]
    )
    outcome1 = await service1.process_claimed_job(run.id)
    assert outcome1.reason == "waiting_to_retry"
    assert _get_run(scope["organization_id"], run.id).orders_pagination_next_token == "TOKEN-ABANDONED"

    _reclaim_for_retry(scope, run.id, lease_owner="test-lease-2")
    service2, _ = _service([SpApiAuthenticationError("nope")])

    outcome2 = await service2.process_claimed_job(run.id)

    assert outcome2.succeeded is False
    assert outcome2.reason == "authentication_failed"
    final_run = _get_run(scope["organization_id"], run.id)
    assert final_run.status == "failed"
    assert final_run.orders_pagination_next_token is None


@pytest.mark.asyncio
async def test_multi_marketplace_run_keeps_one_immutable_request_scope_across_a_resume() -> None:
    """A run covering several participations freezes one shared
    marketplace-id set and window start at first claim; a resumed
    attempt must present the exact same set on its resumed request, not
    a recomputed one — even though nothing in this codebase currently
    ever changes a participation's marketplace_id after creation, the
    resumed request is proven to reuse the frozen values from the run's
    own claimed state, not any fresh lookup."""
    scope1 = _seed_scope(marketplace_id=MARKETPLACE)
    org_id = scope1["organization_id"]
    with session_scope() as session:
        participation2 = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id,
            seller_account_id=scope1["seller_account_id"],
            marketplace_id=MARKETPLACE_2,
            region="na",
            connection_id=scope1["connection_id"],
        )
        participation2_id = participation2.id

    run = _enqueue_and_claim(scope1, participation_ids=[scope1["marketplace_participation_id"], participation2_id])
    service1, client1 = _service(
        [
            _page(
                [_order("902-mp1", marketplace_id=MARKETPLACE)],
                next_token="TOKEN-2",
                marketplace_ids=(MARKETPLACE, MARKETPLACE_2),
            ),
            SpApiRequestFailedError("transient"),
        ]
    )
    outcome1 = await service1.process_claimed_job(run.id)
    assert outcome1.reason == "waiting_to_retry"
    first_request_marketplaces = client1.requests[0].marketplace_ids

    _reclaim_for_retry(scope1, run.id, lease_owner="test-lease-2")
    service2, client2 = _service(
        [_page([_order("902-mp2", marketplace_id=MARKETPLACE_2, seller_sku="SKU-MP2")])]
    )

    outcome2 = await service2.process_claimed_job(run.id)

    assert outcome2.succeeded is True
    assert client2.requests[0].marketplace_ids == first_request_marketplaces
    assert set(client2.requests[0].marketplace_ids) == {MARKETPLACE, MARKETPLACE_2}


# --- 12B.4D remediation: token secrecy -------------------------------------


def test_ingestion_outcome_never_carries_the_pagination_token() -> None:
    field_names = {f.name for f in dataclasses.fields(OrdersIngestionOutcome)}
    assert not any("token" in name for name in field_names)


@pytest.mark.asyncio
async def test_pagination_token_never_appears_in_log_output(caplog: pytest.LogCaptureFixture) -> None:
    scope = _seed_scope()
    run = _enqueue_and_claim(scope)
    secret_token = "SECRET-CONTINUATION-TOKEN-MUST-NEVER-BE-LOGGED"
    service, _ = _service(
        [
            _page([_order("902-log", seller_sku="SKU-LOG")], next_token=secret_token),
            SpApiRequestFailedError("transient"),
        ]
    )

    with caplog.at_level(logging.DEBUG):
        outcome = await service.process_claimed_job(run.id)

    assert outcome.reason == "waiting_to_retry"
    assert _get_run(scope["organization_id"], run.id).orders_pagination_next_token == secret_token
    for record in caplog.records:
        assert secret_token not in record.getMessage()


@pytest.mark.asyncio
async def test_repeated_pagination_token_rejection_is_bounded_and_terminalizes_safely() -> None:
    """A pathological `token -> page one -> new token -> rejected -> page
    one -> ...` chain must be mathematically bounded, never an infinite
    loop. `pagination_token_rejected` is a plain member of
    `RETRYABLE_ORDERS_FAILURE_CLASSES`, so it is already governed by the
    exact same durable retry-budget machinery already proven for
    throttled/transient/malformed_page — no special-case bound was added
    or is needed. `attempt_number` (`run_row.retry_count + 1`) and
    `first_started_at` (`run_row.started_at`) are both re-read fresh from
    the database on every attempt, never carried in memory, so the
    budget survives any number of process restarts between attempts —
    simulated here by constructing a brand-new `AmazonOrdersIngestionService`
    with no shared state for every single attempt."""
    scope = _seed_scope()
    settings = _test_settings(orders_sync_max_attempts=3)
    run = _enqueue_and_claim(scope)

    # Attempt 1: one page commits durably first — this is what proves
    # exhaustion later never touches already-committed data — then the
    # next fetch (presenting the token that page committed) is rejected.
    service1, _ = _service(
        [
            _page([_order("902-committed", seller_sku="SKU-1")], next_token="TOKEN-1"),
            SpApiInvalidRequestError("token rejected"),
        ],
        settings=settings,
    )
    outcome1 = await service1.process_claimed_job(run.id)
    assert outcome1.reason == "waiting_to_retry"
    assert _get_run(scope["organization_id"], run.id).failure_class == "pagination_token_rejected"

    # Attempt 2: restarts at page one (no token) as designed; page one
    # succeeds *again* (an idempotent re-upsert of the same order),
    # minting a brand-new token — which is again rejected. This
    # "page one always succeeds, the next page always gets rejected"
    # shape is the actual pathological loop the review asked to bound,
    # not a single-shot page-one failure with no token ever reissued.
    _reclaim_for_retry(scope, run.id, lease_owner="lease-2")
    service2, client2 = _service(
        [
            _page([_order("902-committed", seller_sku="SKU-1")], next_token="TOKEN-2"),
            SpApiInvalidRequestError("token rejected again"),
        ],
        settings=settings,
    )
    outcome2 = await service2.process_claimed_job(run.id)
    assert client2.requests[0].pagination_token is None  # restarted at page one, not resumed
    assert outcome2.reason == "waiting_to_retry"
    assert _get_run(scope["organization_id"], run.id).failure_class == "pagination_token_rejected"

    # Attempt 3 == orders_sync_max_attempts: the budget is now exhausted
    # — this must terminalize regardless of page one succeeding yet
    # again, never reschedule a 4th attempt.
    _reclaim_for_retry(scope, run.id, lease_owner="lease-3")
    service3, client3 = _service(
        [
            _page([_order("902-committed", seller_sku="SKU-1")], next_token="TOKEN-3"),
            SpApiInvalidRequestError("token rejected a third time"),
        ],
        settings=settings,
    )
    outcome3 = await service3.process_claimed_job(run.id)

    assert outcome3.succeeded is False
    # Truthful terminal name: Amazon rejected the continuation token
    # repeatedly — it never throttled the request, so this must not be
    # reported as "rate_limited" (the shared name reserved for genuine
    # throttled/transient/malformed-page exhaustion).
    assert outcome3.reason == "pagination_token_retry_exhausted"
    final_run = _get_run(scope["organization_id"], run.id)
    assert final_run.status == "failed"
    assert final_run.failure_class == "pagination_token_retry_exhausted"
    assert final_run.orders_pagination_next_token is None
    assert final_run.retry_count == 2  # two reclaims from waiting_to_retry, both durably accounted for

    # The scope is free again (terminal), but this exact exhausted run
    # can never be reclaimed for a further attempt.
    with session_scope() as session:
        claim = AmazonIngestionRunMarketplaceParticipationRepository(session).claim_orders_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            region=scope["region"],
            environment=scope["environment"],
            lease_owner="lease-4",
            lease_duration_seconds=300,
        )
        assert claim.claimed is False
        assert claim.reason == "no_eligible_job"

    # Checkpoints were never advanced by the exhausted run...
    assert _get_checkpoint(scope["organization_id"], scope["marketplace_participation_id"]) is None
    # ...but the order committed during attempt 1, before any rejection
    # ever happened, is still exactly there, untouched by the eventual
    # exhaustion.
    assert _get_order(scope["organization_id"], scope["marketplace_participation_id"], "902-committed") is not None
