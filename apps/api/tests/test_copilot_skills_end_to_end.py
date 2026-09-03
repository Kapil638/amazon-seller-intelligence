"""12B.5A — end-to-end pipeline tests: plan -> execute (real ToolRegistry)
-> synthesize (deterministic template, no LLM attached), for each of the
five Listings/Orders skills, against real SQLite-persisted data. Proves
the full wiring (planner routing, tool registration, evidence-to-claims
conversion, synthesis's skill-aware template) works together, not just
each piece in isolation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.amazon.listings_normalization import NormalizedListing
from app.copilot import default_registry
from app.copilot.budget import BudgetTracker
from app.copilot.conversation.service import ConversationService
from app.copilot.planner.service import PlannerService
from app.copilot.synthesis.schemas import SynthesisRequest
from app.copilot.synthesis.service import SynthesisService
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


def _listing(sku: str, **overrides) -> NormalizedListing:
    base = dict(
        seller_sku=sku, asin="B0TEST00001", product_type="TOY", condition_type=None, item_name="Widget",
        main_image_url=None, amazon_created_at=None, amazon_last_updated_at=None,
        status=["BUYABLE"], is_buyable=True, is_discoverable=True, offers=[],
        price_amount=Decimal("9.99"), price_currency="USD", fulfillment_availability=[],
        issues=[], issue_count=0, highest_issue_severity=None, product_types=[],
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


def _seed_orders_run(scope: dict):
    with session_scope() as session:
        run_repo = AmazonIngestionRunMarketplaceParticipationRepository(session)
        claim = run_repo.enqueue_orders_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            connection_id=scope["connection_id"], marketplace_participation_ids=[scope["participation_id"]],
            region="na", environment="PRODUCTION",
        )
        assert claim.claimed
        claimed = run_repo.claim_orders_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"], region="na",
            environment="PRODUCTION", lease_owner="test-lease", lease_duration_seconds=300,
        )
        assert claimed.claimed
        return claimed.run_id


def _seed_order(scope: dict, run_id, amazon_order_id: str, *, seller_sku: str, created_at: datetime, was_cancelled: bool = False) -> None:
    status = "CANCELLED" if was_cancelled else "SHIPPED"
    with session_scope() as session:
        order = AmazonSellerOrderRepository(session).upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            amazon_order_id=amazon_order_id, fulfillment_status=status, fulfilled_by="MERCHANT",
            sales_channel_name="AMAZON", sales_channel_marketplace_id=MARKETPLACE,
            sales_channel_marketplace_name="Amazon.com", items_shipped_count=0, items_unshipped_count=0,
            order_total_amount=Decimal("10.00"), order_total_currency="USD",
            is_business_order=False, is_prime=False, was_cancelled=was_cancelled,
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


async def _run_pipeline(message: str, participation_id) -> tuple:
    conversations = ConversationService()
    created = conversations.create_conversation()
    planner = PlannerService(conversations=conversations, registry=default_registry())
    plan = await planner.plan_turn(created.id, message, marketplace_participation_id=participation_id)
    assert plan.tool_calls, f"expected at least one tool call, got plan={plan}"

    registry = default_registry()
    budget = BudgetTracker()
    evidence = []
    for call in plan.tool_calls:
        result = await registry.execute(call.name, call.arguments, budget=budget, confirmed=True)
        evidence.append(result)

    synthesis = SynthesisService()
    response = await synthesis.synthesize(
        SynthesisRequest(user_message=message, intent=plan.intent, evidence=evidence, compact_context={})
    )
    return plan, response


@pytest.mark.asyncio
async def test_listing_health_pipeline_end_to_end() -> None:
    scope = _seed_scope()
    _reconcile_listings(
        scope,
        [_listing("SKU-ERR", is_buyable=False, issues=[{"code": "X", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR")],
    )
    plan, response = await _run_pipeline("Which listings should I fix first?", scope["participation_id"])
    assert plan.intent == "prioritize_listing_health"
    assert response.source == "template_fallback"
    assert "SKU-ERR" in " ".join(response.findings)
    assert response.confidence != "none"


@pytest.mark.asyncio
async def test_order_trends_pipeline_never_says_revenue() -> None:
    scope = _seed_scope()
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    _seed_order(scope, run_id, "111-1", seller_sku="SKU-A", created_at=now - timedelta(days=1))
    plan, response = await _run_pipeline("How are my orders trending?", scope["participation_id"])
    assert plan.intent == "analyze_order_trends"
    full_text = response.message.lower()
    assert "revenue" not in full_text
    assert "profit" not in full_text
    assert "order value" in full_text or "usd" in full_text


@pytest.mark.asyncio
async def test_cancellation_pipeline_does_not_call_small_sample_anomalous() -> None:
    scope = _seed_scope()
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    _seed_order(scope, run_id, "222-1", seller_sku="SKU-A", created_at=now - timedelta(days=1), was_cancelled=True)
    plan, response = await _run_pipeline("Are cancellations unusually high?", scope["participation_id"])
    assert plan.intent == "detect_cancellation_anomalies"
    assert "not labeled anomalous" in response.message.lower() or "sample too small" in response.message.lower()


@pytest.mark.asyncio
async def test_listing_risk_pipeline_never_claims_causation() -> None:
    scope = _seed_scope()
    _reconcile_listings(
        scope, [_listing("SKU-RISK", issues=[{"code": "Y", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR")]
    )
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    _seed_order(scope, run_id, "333-1", seller_sku="SKU-RISK", created_at=now - timedelta(days=1))
    plan, response = await _run_pipeline(
        "Which listing issues affect the most orders?", scope["participation_id"]
    )
    assert plan.intent == "rank_listing_risk_by_order_exposure"
    lowered = response.message.lower()
    assert "will be lost" not in lowered
    assert "already lost" not in lowered


@pytest.mark.asyncio
async def test_non_buyable_pipeline_end_to_end() -> None:
    scope = _seed_scope()
    _reconcile_listings(
        scope,
        [_listing("SKU-NB", asin="B01MD1SKLL", is_buyable=False, issues=[{"code": "Z", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR")],
    )
    plan, response = await _run_pipeline("Why is B01MD1SKLL not buyable?", scope["participation_id"])
    assert plan.intent == "investigate_non_buyable_listing"
    assert response.source == "template_fallback"
    assert "buyable" in response.message.lower()


@pytest.mark.asyncio
async def test_non_buyable_pipeline_returns_selection_when_no_listing_named() -> None:
    """The launch card's general question ("Why are my listings not
    buyable?") names no SKU/ASIN — must route through and answer with a
    prioritized selection, never a `clarify` degrade and never a guess."""
    scope = _seed_scope()
    _reconcile_listings(
        scope,
        [
            _listing("SKU-NB-1", is_buyable=False, issues=[{"code": "Z", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR"),
            _listing("SKU-NB-2", is_buyable=False, issues=[{"code": "Y", "severity": "WARNING"}], issue_count=1, highest_issue_severity="WARNING"),
        ],
    )
    plan, response = await _run_pipeline("Why are my listings not buyable?", scope["participation_id"])
    assert plan.intent == "investigate_non_buyable_listing"
    assert plan.tool_calls[0].arguments.get("asin") is None
    assert response.source == "template_fallback"
    assert "SKU-NB-1" in response.message
