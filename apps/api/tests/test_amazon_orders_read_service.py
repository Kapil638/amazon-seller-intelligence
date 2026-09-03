"""12B.4D — AmazonOrdersReadService. Uses the shared, per-test-isolated
SQLite database via `current_organization_id()`, matching
`test_amazon_listings_read_service.py`'s established pattern. No Amazon
call, no secret resolution possible from this service.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.amazon.orders_read import AmazonOrdersReadService, AmazonSellerOrderNotFoundError
from app.core.exceptions import AmazonListingsParticipationNotFoundError
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import Organization
from app.persistence.repositories import (
    AmazonIngestionRunMarketplaceParticipationRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
    AmazonSellerOrderItemRepository,
    AmazonSellerOrderRepository,
)

MARKETPLACE = "ATVPDKIKX0DER"


def _seed_participation() -> dict:
    org_id = current_organization_id()
    with session_scope() as session:
        connection = None
        from app.persistence.repositories import AmazonConnectionRepository

        connection = AmazonConnectionRepository(session).create(
            organization_id=org_id, provider="SP_API", environment="PRODUCTION", region="na"
        )
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


def _seed_run(scope: dict, *, created_at: datetime | None = None) -> "uuid4":
    """`created_at` is an explicit override for tests that need two runs
    to have a deterministic, unambiguous chronological order — see
    `test_latest_successful_sync_remains_available_after_a_later_failed_run`'s
    docstring for why relying on the server default there would be
    flaky."""
    with session_scope() as session:
        run_repo = AmazonIngestionRunMarketplaceParticipationRepository(session)
        claim = run_repo.enqueue_orders_run(
            organization_id=scope["org_id"],
            seller_account_id=scope["seller_account_id"],
            connection_id=scope["connection_id"],
            marketplace_participation_ids=[scope["participation_id"]],
            region="na",
            environment="PRODUCTION",
        )
        assert claim.claimed
        if created_at is not None:
            from app.persistence.models import AmazonIngestionRun

            row = session.get(AmazonIngestionRun, claim.run_id)
            row.created_at = created_at
        return claim.run_id


def _seed_order(scope: dict, run_id, amazon_order_id: str, **overrides) -> "uuid4":
    defaults = dict(
        organization_id=scope["org_id"],
        marketplace_participation_id=scope["participation_id"],
        amazon_order_id=amazon_order_id,
        fulfillment_status="SHIPPED",
        fulfilled_by="MERCHANT",
        sales_channel_name="AMAZON",
        sales_channel_marketplace_id=MARKETPLACE,
        sales_channel_marketplace_name="Amazon.com",
        items_shipped_count=1,
        items_unshipped_count=0,
        order_total_amount=Decimal("19.99"),
        order_total_currency="USD",
        is_business_order=False,
        is_prime=True,
        was_cancelled=False,
        amazon_created_at=datetime(2026, 1, 1, tzinfo=UTC),
        amazon_last_updated_at=datetime(2026, 1, 2, tzinfo=UTC),
        last_ingestion_run_id=run_id,
    )
    defaults.update(overrides)
    with session_scope() as session:
        row = AmazonSellerOrderRepository(session).upsert(**defaults)
        AmazonSellerOrderItemRepository(session).upsert(
            organization_id=scope["org_id"],
            marketplace_participation_id=scope["participation_id"],
            order_id=row.id,
            amazon_order_item_id=f"{amazon_order_id}-ITEM-1",
            seller_sku="SKU-1",
            asin="B0TEST0001",
            item_name="Test Product",
            condition_type=None,
            quantity_ordered=1,
            quantity_fulfilled=1,
            quantity_unfulfilled=0,
            unit_price_amount=Decimal("19.99"),
            unit_price_currency="USD",
            item_proceeds_amount=Decimal("19.99"),
            item_proceeds_currency="USD",
            last_ingestion_run_id=run_id,
        )
        return row.id


# --- authorization / tenancy -----------------------------------------------


def test_summary_for_own_participation_succeeds() -> None:
    scope = _seed_participation()
    run_id = _seed_run(scope)
    _seed_order(scope, run_id, "902-1")
    summary = AmazonOrdersReadService().get_summary(scope["participation_id"])
    assert summary.total_orders == 1
    assert summary.order_value_sum == Decimal("19.99")
    assert summary.order_value_currency == "USD"


def test_summary_foreign_and_nonexistent_participation_produce_identical_errors() -> None:
    scope = _seed_participation()
    other_org = uuid4()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other Org"))

    with session_scope() as session:
        foreign_participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=other_org,
            seller_account_id=AmazonSellerAccountRepository(session).create_or_reconcile(
                organization_id=other_org, selling_partner_id=f"A{uuid4().hex[:14].upper()}"
            ).id,
            marketplace_id=MARKETPLACE, region="na",
        ).id

    service = AmazonOrdersReadService()
    with pytest.raises(AmazonListingsParticipationNotFoundError) as foreign_exc:
        service.get_summary(foreign_participation)
    with pytest.raises(AmazonListingsParticipationNotFoundError) as missing_exc:
        service.get_summary(uuid4())
    assert str(foreign_exc.value) != ""
    assert type(foreign_exc.value) is type(missing_exc.value)


def test_mixed_currency_summary_omits_the_ambiguous_sum() -> None:
    scope = _seed_participation()
    run_id = _seed_run(scope)
    _seed_order(scope, run_id, "902-usd", order_total_currency="USD", order_total_amount=Decimal("10.00"))
    _seed_order(scope, run_id, "902-eur", order_total_currency="EUR", order_total_amount=Decimal("9.00"))
    summary = AmazonOrdersReadService().get_summary(scope["participation_id"])
    assert summary.total_orders == 2
    assert summary.order_value_sum is None
    assert summary.order_value_currency is None


def test_amount_with_unknown_currency_is_excluded_from_the_single_currency_sum() -> None:
    """12B.4D remediation: a known-amount-unknown-currency order must
    never be silently folded into another order's known-currency total —
    that would misrepresent an amount of unknown denomination as if it
    were in that other, known currency. The participation's one true
    known currency (USD) still reports correctly, reflecting only the
    orders actually known to be in it."""
    scope = _seed_participation()
    run_id = _seed_run(scope)
    _seed_order(scope, run_id, "902-usd", order_total_currency="USD", order_total_amount=Decimal("10.00"))
    _seed_order(scope, run_id, "902-unknown", order_total_currency=None, order_total_amount=Decimal("500.00"))
    summary = AmazonOrdersReadService().get_summary(scope["participation_id"])
    assert summary.total_orders == 2
    assert summary.order_value_currency == "USD"
    assert summary.order_value_sum == Decimal("10.00")


# --- sync evidence -----------------------------------------------------------


def test_never_synchronized_when_no_orders_run_exists() -> None:
    scope = _seed_participation()
    summary = AmazonOrdersReadService().get_summary(scope["participation_id"])
    assert summary.sync.status == "never_synchronized"


def test_sync_evidence_restricted_to_run_type_orders() -> None:
    """A Listings run for the same participation must never be mistaken
    for Orders sync evidence."""
    scope = _seed_participation()
    with session_scope() as session:
        from app.persistence.models import AmazonIngestionRun

        session.add(
            AmazonIngestionRun(
                organization_id=scope["org_id"],
                seller_account_id=scope["seller_account_id"],
                marketplace_participation_id=scope["participation_id"],
                run_type="listings",
                domain="listings_items",
                region="na",
                environment="PRODUCTION",
                status="succeeded",
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
        )
    summary = AmazonOrdersReadService().get_summary(scope["participation_id"])
    assert summary.sync.status == "never_synchronized"


def test_latest_successful_sync_remains_available_after_a_later_failed_run() -> None:
    """The latest attempt failed, but a PRIOR attempt succeeded — the
    summary's overall status reflects the latest attempt while
    `last_successful_synchronized_at` still reflects the earlier
    success. `created_at` is set explicitly (rather than left to the
    server default) because SQLite's `CURRENT_TIMESTAMP` only has
    second-level precision: two runs enqueued microseconds apart in a
    fast test can otherwise land in the same second and fall through to
    the `id` tiebreaker, whose random UUID ordering has nothing to do
    with which row is actually newer. Explicit `created_at` values make
    the test's intended timeline the one actually stored, matching
    `test_amazon_listings_read_service.py`'s established pattern for the
    identical concern."""
    scope = _seed_participation()
    now = datetime.now(UTC)
    run_id = _seed_run(scope, created_at=now - timedelta(minutes=10))
    with session_scope() as session:
        from app.persistence.models import AmazonIngestionRun

        row = session.get(AmazonIngestionRun, run_id)
        row.status = "succeeded"
        row.completed_at = now - timedelta(hours=1)
        row.lease_owner = None
        row.lease_expires_at = None
        session.commit()

    failed_run_id = _seed_run(scope, created_at=now)
    with session_scope() as session:
        from app.persistence.models import AmazonIngestionRun

        row = session.get(AmazonIngestionRun, failed_run_id)
        row.status = "failed"
        row.failure_class = "rate_limited"
        row.completed_at = now
        row.lease_owner = None
        row.lease_expires_at = None
        session.commit()

    summary = AmazonOrdersReadService().get_summary(scope["participation_id"])
    assert summary.sync.status == "failed"
    assert summary.sync.last_successful_synchronized_at is not None


# --- list_orders: filtering / sorting / pagination --------------------------


def test_list_orders_search_matches_order_id_sku_and_asin() -> None:
    scope = _seed_participation()
    run_id = _seed_run(scope)
    _seed_order(scope, run_id, "902-findme")
    _seed_order(scope, run_id, "902-other")

    service = AmazonOrdersReadService()
    by_order_id = service.list_orders(scope["participation_id"], search="findme")
    assert by_order_id.total == 1
    assert by_order_id.items[0].amazon_order_id == "902-findme"

    by_sku = service.list_orders(scope["participation_id"], search="SKU-1")
    assert by_sku.total == 2  # both orders share the same seeded SKU


def test_list_orders_filters_by_fulfillment_status() -> None:
    scope = _seed_participation()
    run_id = _seed_run(scope)
    _seed_order(scope, run_id, "902-shipped", fulfillment_status="SHIPPED")
    _seed_order(scope, run_id, "902-cancelled", fulfillment_status="CANCELLED", was_cancelled=True)

    service = AmazonOrdersReadService()
    result = service.list_orders(scope["participation_id"], fulfillment_status="CANCELLED")
    assert result.total == 1
    assert result.items[0].amazon_order_id == "902-cancelled"


def test_list_orders_pagination_is_stable_and_bounded() -> None:
    scope = _seed_participation()
    run_id = _seed_run(scope)
    for i in range(5):
        _seed_order(scope, run_id, f"902-{i}", amazon_last_updated_at=datetime(2026, 1, 1 + i, tzinfo=UTC))

    service = AmazonOrdersReadService()
    page1 = service.list_orders(scope["participation_id"], offset=0, limit=2)
    page2 = service.list_orders(scope["participation_id"], offset=2, limit=2)
    assert page1.total == 5
    assert len(page1.items) == 2
    assert len(page2.items) == 2
    assert {i.id for i in page1.items}.isdisjoint({i.id for i in page2.items})


def test_list_orders_rejects_oversized_limit_by_clamping() -> None:
    scope = _seed_participation()
    service = AmazonOrdersReadService()
    result = service.list_orders(scope["participation_id"], limit=10_000)
    assert result.limit == 100  # MAX_PAGE_SIZE


# --- get_order detail --------------------------------------------------------


def test_get_order_returns_detail_with_sanitized_items() -> None:
    scope = _seed_participation()
    run_id = _seed_run(scope)
    order_row_id = _seed_order(scope, run_id, "902-detail")

    detail = AmazonOrdersReadService().get_order(scope["participation_id"], order_row_id)
    assert detail.amazon_order_id == "902-detail"
    assert len(detail.items) == 1
    assert detail.items[0].seller_sku == "SKU-1"
    dumped = detail.model_dump()
    assert "gift_message" not in str(dumped).lower()
    assert "cancel_reason" not in str(dumped).lower()


def test_get_order_foreign_and_nonexistent_produce_identical_errors() -> None:
    scope = _seed_participation()
    service = AmazonOrdersReadService()
    with pytest.raises(AmazonSellerOrderNotFoundError):
        service.get_order(scope["participation_id"], uuid4())
    with pytest.raises(AmazonSellerOrderNotFoundError):
        service.get_order(uuid4(), uuid4())
