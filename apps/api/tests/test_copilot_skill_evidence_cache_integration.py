"""12B.5B — integration proof that Layer A (evidence cache) is actually
wired into the registered Copilot tools, not just correct in isolation
(see `test_copilot_skill_cache.py` for the pure primitive tests). SQLite,
offline, no Amazon/AI-provider call. Every test clears the process-wide
cache singletons before and after itself, since they are shared across
the whole test session by design (see `app/copilot/tools/skills.py`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.amazon.listings_normalization import NormalizedListing
from app.copilot import default_registry
from app.copilot.budget import BudgetTracker
from app.copilot.tools import skills as skills_module
from app.persistence.database import current_organization_id, session_scope
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunMarketplaceParticipationRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
    AmazonSellerListingRepository,
    AmazonSellerOrderItemRepository,
    AmazonSellerOrderRepository,
)

MARKETPLACE = "ATVPDKIKX0DER"


@pytest.fixture(autouse=True)
def _clear_shared_evidence_cache():
    skills_module._EVIDENCE_CACHE.clear()
    yield
    skills_module._EVIDENCE_CACHE.clear()


def _listing(sku: str, **overrides) -> NormalizedListing:
    base = dict(
        seller_sku=sku, asin=None, product_type="TOY", condition_type=None, item_name="Widget",
        main_image_url=None, amazon_created_at=None, amazon_last_updated_at=None,
        status=["BUYABLE"], is_buyable=True, is_discoverable=True, offers=[],
        price_amount=Decimal("9.99"), price_currency="USD", fulfillment_availability=[],
        issues=[{"code": "X", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR",
        product_types=[],
    )
    base.update(overrides)
    return NormalizedListing(**base)


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
            "org_id": org_id, "seller_account_id": seller_account.id,
            "participation_id": participation.id, "connection_id": connection.id,
        }


def _reconcile_listings(scope: dict, listings: list[NormalizedListing]) -> None:
    with session_scope() as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            listings=listings, last_ingestion_run_id=None,
        )


def _complete_a_listings_run(scope: dict, *, completed_at: datetime | None = None) -> None:
    """Advances Listings evidence version — mirrors a real successful
    ingestion completing, using the same repository lifecycle every
    other Listings test already uses. `completed_at` is an explicit
    override for tests that need two completions to have a deterministic,
    unambiguous chronological order: SQLite's `CURRENT_TIMESTAMP` only
    has second-level precision, so two completions issued microseconds
    apart in a fast test can otherwise land in the same second and
    produce an identical evidence version — matching the same concern
    `test_amazon_orders_read_service.py`'s `_seed_run` already documents."""
    from app.persistence.models import AmazonIngestionRun
    from app.persistence.repositories import AmazonIngestionRunRepository

    with session_scope() as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["participation_id"], region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="worker-1", lease_duration_seconds=300,
        )
    with session_scope() as session:
        AmazonIngestionRunRepository(session).complete_listings_run(
            scope["org_id"], claim.run_id, lease_owner="worker-1", status="succeeded",
        )
    if completed_at is not None:
        with session_scope() as session:
            row = session.get(AmazonIngestionRun, claim.run_id)
            # `get_latest_successful_listings_run` orders by `started_at
            # DESC, id DESC` (never `completed_at`) — see the identical
            # note in `_seed_orders_run` for why both fields must be
            # overridden together.
            row.started_at = completed_at
            row.completed_at = completed_at


def _seed_orders_run(scope: dict, *, finalize: bool = True, completed_at: datetime | None = None):
    """Enqueues, claims, and — unless `finalize=False` — completes an
    Orders run for this scope. Must be finalized before a second
    `enqueue_orders_run` for the same scope can succeed (an unfinished
    `started` run correctly holds the single-writer slot — the same
    invariant Listings enforces). `completed_at` is an explicit override
    for the same SQLite second-precision reason `_complete_a_listings_
    run` documents."""
    with session_scope() as session:
        repo = AmazonIngestionRunMarketplaceParticipationRepository(session)
        claim = repo.enqueue_orders_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            connection_id=scope["connection_id"], marketplace_participation_ids=[scope["participation_id"]],
            region="na", environment="PRODUCTION",
        )
        assert claim.claimed, claim.reason
        claimed = repo.claim_orders_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"], region="na",
            environment="PRODUCTION", lease_owner="test-lease", lease_duration_seconds=300,
        )
        assert claimed.claimed, claimed.reason
        run_id = claimed.run_id
    if finalize:
        with session_scope() as session:
            AmazonIngestionRunMarketplaceParticipationRepository(session).finalize_successful_orders_run(
                organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
                ingestion_run_id=run_id, participation_watermarks={scope["participation_id"]: datetime.now(UTC)},
            )
        if completed_at is not None:
            from app.persistence.models import AmazonIngestionRun

            with session_scope() as session:
                row = session.get(AmazonIngestionRun, run_id)
                # `get_latest_successful_orders_run` orders by `started_at
                # DESC, id DESC` (never `completed_at`) — so `started_at`
                # must be overridden too, or two runs claimed within the
                # same SQLite CURRENT_TIMESTAMP second (near-certain in a
                # fast test) tie-break on `id`, a random UUID, making
                # which one counts as "latest successful" a coin flip
                # regardless of this override to `completed_at` alone.
                row.started_at = completed_at
                row.completed_at = completed_at
    return run_id


def _seed_order(scope: dict, run_id, amazon_order_id: str, *, seller_sku: str, created_at: datetime) -> None:
    with session_scope() as session:
        order = AmazonSellerOrderRepository(session).upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            amazon_order_id=amazon_order_id, fulfillment_status="SHIPPED", fulfilled_by="MERCHANT",
            sales_channel_name="AMAZON", sales_channel_marketplace_id=MARKETPLACE,
            sales_channel_marketplace_name="Amazon.com", items_shipped_count=0, items_unshipped_count=0,
            order_total_amount=Decimal("10.00"), order_total_currency="USD",
            is_business_order=False, is_prime=False, was_cancelled=False,
            amazon_created_at=created_at, amazon_last_updated_at=created_at, last_ingestion_run_id=run_id,
        )
        AmazonSellerOrderItemRepository(session).upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            order_id=order.id, amazon_order_item_id=f"{amazon_order_id}-ITEM", seller_sku=seller_sku,
            asin="B0TEST00001", item_name="Widget", condition_type=None, quantity_ordered=1,
            quantity_fulfilled=1, quantity_unfulfilled=0, unit_price_amount=Decimal("10.00"),
            unit_price_currency="USD", item_proceeds_amount=Decimal("10.00"), item_proceeds_currency="USD",
            last_ingestion_run_id=run_id,
        )


async def _execute_once(tool_name: str, arguments: dict):
    registry = default_registry()
    budget = BudgetTracker()
    return await registry.execute(tool_name, arguments, budget=budget, confirmed=True)


@pytest.mark.asyncio
async def test_repeated_identical_call_hits_the_evidence_cache(monkeypatch) -> None:
    scope = _seed_scope()
    _reconcile_listings(scope, [_listing("SKU-A")])
    _complete_a_listings_run(scope)

    calls = []
    from app.copilot.skills.listing_health import ListingHealthEvidenceService

    original = ListingHealthEvidenceService.evaluate

    def spy(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ListingHealthEvidenceService, "evaluate", spy)

    arguments = {"marketplace_participation_id": str(scope["participation_id"]), "period_days": 30, "limit": 25}
    result_1 = await _execute_once("prioritize_listing_health", arguments)
    result_2 = await _execute_once("prioritize_listing_health", arguments)

    assert result_1.claims and result_2.claims
    assert len(calls) == 1, "second identical call should have hit the evidence cache, not recomputed"


@pytest.mark.asyncio
async def test_new_successful_listings_run_invalidates_the_evidence_cache(monkeypatch) -> None:
    scope = _seed_scope()
    _reconcile_listings(scope, [_listing("SKU-A")])
    now = datetime.now(UTC)
    _complete_a_listings_run(scope, completed_at=now - timedelta(minutes=10))

    calls = []
    from app.copilot.skills.listing_health import ListingHealthEvidenceService

    original = ListingHealthEvidenceService.evaluate

    def spy(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ListingHealthEvidenceService, "evaluate", spy)

    arguments = {"marketplace_participation_id": str(scope["participation_id"]), "period_days": 30, "limit": 25}
    await _execute_once("prioritize_listing_health", arguments)
    assert len(calls) == 1

    # A new listing appears and a new successful Listings run completes —
    # this must change the evidence version and force a recompute, never
    # silently return the stale cached ranking.
    _reconcile_listings(scope, [_listing("SKU-A"), _listing("SKU-B", issue_count=0, highest_issue_severity=None, issues=[])])
    _complete_a_listings_run(scope, completed_at=now)

    result = await _execute_once("prioritize_listing_health", arguments)
    assert len(calls) == 2, "a new successful Listings run must invalidate the previous evidence cache entry"
    claim = result.claims[0]
    skus = {row["seller_sku"] for row in claim.value["records"]}
    assert skus == {"SKU-A", "SKU-B"}


@pytest.mark.asyncio
async def test_new_successful_orders_run_invalidates_the_evidence_cache(monkeypatch) -> None:
    scope = _seed_scope()
    now = datetime.now(UTC)
    run_id = _seed_orders_run(scope, completed_at=now - timedelta(minutes=10))
    _seed_order(scope, run_id, "700-1", seller_sku="SKU-A", created_at=now - timedelta(days=1))

    calls = []
    from app.copilot.skills.order_trends import OrderTrendEvidenceService

    original = OrderTrendEvidenceService.analyze

    def spy(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(OrderTrendEvidenceService, "analyze", spy)

    arguments = {"marketplace_participation_id": str(scope["participation_id"]), "period_days": 30}
    first = await _execute_once("analyze_order_trends", arguments)
    assert len(calls) == 1
    first_order_count = first.claims[0].value["metrics"]["order_count"]
    assert first_order_count == 1

    second_run = _seed_orders_run(scope, completed_at=now)
    _seed_order(scope, second_run, "700-2", seller_sku="SKU-B", created_at=now - timedelta(hours=1))

    second = await _execute_once("analyze_order_trends", arguments)
    assert len(calls) == 2, "a new successful Orders run must invalidate the previous evidence cache entry"
    assert second.claims[0].value["metrics"]["order_count"] == 2


@pytest.mark.asyncio
async def test_force_refresh_bypasses_the_cache_read_but_still_repopulates_it(monkeypatch) -> None:
    """"Recompute from saved data": the seller-visible affordance that
    must never trigger a sync — proven here by never touching Amazon in
    this test at all, only forcing a second read of already-synchronized
    rows."""
    scope = _seed_scope()
    _reconcile_listings(scope, [_listing("SKU-A")])
    _complete_a_listings_run(scope)

    calls = []
    from app.copilot.skills.listing_health import ListingHealthEvidenceService

    original = ListingHealthEvidenceService.evaluate

    def spy(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ListingHealthEvidenceService, "evaluate", spy)

    base_arguments = {"marketplace_participation_id": str(scope["participation_id"]), "period_days": 30, "limit": 25}
    await _execute_once("prioritize_listing_health", base_arguments)
    assert len(calls) == 1

    # An identical, non-forced call hits the cache (already proven
    # above) — a force_refresh call for the *same* scope/params must
    # still recompute even though nothing about the evidence changed.
    forced_arguments = {**base_arguments, "force_refresh": True}
    await _execute_once("prioritize_listing_health", forced_arguments)
    assert len(calls) == 2, "force_refresh must bypass the cache read"

    # The forced result repopulates the cache — the next plain call
    # (no force_refresh) must hit it again, not recompute a third time.
    await _execute_once("prioritize_listing_health", base_arguments)
    assert len(calls) == 2, "force_refresh's result should have repopulated the cache for the next plain caller"
