"""12B.3E — AmazonSellerListingRepository read methods (summary counts,
paginated/filtered/sorted collection, scoped detail lookup). SQLite,
synthetic fixtures only — never inspects real production data.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.amazon.listings_normalization import NormalizedListing
from app.persistence.models import (
    AmazonIngestionRun,
    AmazonMarketplaceParticipation,
    AmazonSellerAccount,
    Base,
    Organization,
)
from app.persistence.repositories import AmazonSellerListingRepository


def _engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def _seed(engine) -> dict:
    org_id = uuid4()
    seller_account_id = uuid4()
    participation_id = uuid4()
    other_participation_id = uuid4()
    run_id = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_id, name="Listing Reads Test Org"))
        session.flush()
        session.add(
            AmazonSellerAccount(
                id=seller_account_id, organization_id=org_id,
                selling_partner_id=f"A{uuid4().hex[:14].upper()}", status="active",
            )
        )
        session.flush()
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_id, organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_id="ATVPDKIKX0DER", region="na",
            )
        )
        session.add(
            AmazonMarketplaceParticipation(
                id=other_participation_id, organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_id="A2EUQ1WTGCTBG2", region="eu",
            )
        )
        session.flush()
        session.add(
            AmazonIngestionRun(
                id=run_id, organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id, run_type="listings",
                domain="listings_items", region="na", environment="PRODUCTION", status="succeeded",
            )
        )
        session.commit()
    return dict(
        org_id=org_id, seller_account_id=seller_account_id,
        participation_id=participation_id, other_participation_id=other_participation_id, run_id=run_id,
    )


def _listing(sku: str, **overrides) -> NormalizedListing:
    base = dict(
        seller_sku=sku, asin=f"B0{sku}TEST01"[:10], product_type="TOY", condition_type=None, item_name=f"Widget {sku}",
        main_image_url=None, amazon_created_at=None, amazon_last_updated_at=None,
        status=["BUYABLE"], is_buyable=True, is_discoverable=False, offers=[],
        price_amount=Decimal("9.99"), price_currency="USD", fulfillment_availability=[],
        issues=[], issue_count=0, highest_issue_severity=None, product_types=[],
    )
    base.update(overrides)
    return NormalizedListing(**base)


def _reconcile(engine, scope: dict, listings: list[NormalizedListing], participation_id=None) -> None:
    with Session(engine) as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=scope["org_id"],
            marketplace_participation_id=participation_id or scope["participation_id"],
            listings=listings,
            last_ingestion_run_id=scope["run_id"] if participation_id is None else None,
        )
        session.commit()


# --- tenancy --------------------------------------------------------------


def test_summary_counts_none_for_foreign_organization() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU-1")])
    with Session(engine) as session:
        result = AmazonSellerListingRepository(session).get_summary_counts(uuid4(), scope["participation_id"])
    assert result is None


def test_summary_counts_none_for_nonexistent_participation() -> None:
    engine = _engine()
    scope = _seed(engine)
    with Session(engine) as session:
        result = AmazonSellerListingRepository(session).get_summary_counts(scope["org_id"], uuid4())
    assert result is None


def test_list_page_none_for_foreign_organization() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU-1")])
    with Session(engine) as session:
        result = AmazonSellerListingRepository(session).list_page(uuid4(), scope["participation_id"])
    assert result is None


def test_get_detail_none_for_foreign_organization() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU-1")])
    with Session(engine) as session:
        repo = AmazonSellerListingRepository(session)
        listing = repo.get_by_natural_key(scope["org_id"], scope["participation_id"], "SKU-1")
        result = repo.get_detail(uuid4(), scope["participation_id"], listing.id)
    assert result is None


def test_get_detail_none_when_listing_belongs_to_a_different_participation() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU-1")])
    with Session(engine) as session:
        repo = AmazonSellerListingRepository(session)
        listing = repo.get_by_natural_key(scope["org_id"], scope["participation_id"], "SKU-1")
        # Correct organization, but the WRONG (still org-owned) participation.
        result = repo.get_detail(scope["org_id"], scope["other_participation_id"], listing.id)
    assert result is None


def test_get_detail_none_for_nonexistent_listing() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU-1")])
    with Session(engine) as session:
        result = AmazonSellerListingRepository(session).get_detail(scope["org_id"], scope["participation_id"], uuid4())
    assert result is None


def test_get_detail_succeeds_through_the_correct_owned_participation() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU-1")])
    with Session(engine) as session:
        repo = AmazonSellerListingRepository(session)
        listing = repo.get_by_natural_key(scope["org_id"], scope["participation_id"], "SKU-1")
        result = repo.get_detail(scope["org_id"], scope["participation_id"], listing.id)
    assert result is not None
    assert result.seller_sku == "SKU-1"


# --- summary counts ---------------------------------------------------


def test_summary_counts_are_independent_across_active_buyable_discoverable() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(
        engine,
        scope,
        [
            _listing("SKU-1", is_buyable=True, is_discoverable=False),
            _listing("SKU-2", is_buyable=False, is_discoverable=True),
            _listing("SKU-3", is_buyable=False, is_discoverable=False),
        ],
    )
    with Session(engine) as session:
        counts = AmazonSellerListingRepository(session).get_summary_counts(scope["org_id"], scope["participation_id"])
    assert counts.total == 3
    assert counts.active == 3
    assert counts.buyable == 1
    assert counts.not_buyable == 2
    assert counts.discoverable == 1
    assert counts.not_discoverable == 2


def test_summary_counts_issue_and_severity_distribution() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(
        engine,
        scope,
        [
            _listing("SKU-1", issue_count=1, highest_issue_severity="ERROR"),
            _listing("SKU-2", issue_count=2, highest_issue_severity="WARNING"),
            _listing("SKU-3", issue_count=0, highest_issue_severity=None),
        ],
    )
    with Session(engine) as session:
        counts = AmazonSellerListingRepository(session).get_summary_counts(scope["org_id"], scope["participation_id"])
    assert counts.with_issues == 2
    assert counts.without_issues == 1
    assert counts.severity_error == 1
    assert counts.severity_warning == 1
    assert counts.severity_info == 0


def test_summary_counts_asin_price_and_fulfillment_availability() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(
        engine,
        scope,
        [
            _listing("SKU-1", asin="B0ASINONE1", price_amount=Decimal("5.00"), fulfillment_availability=[{"fulfillmentChannelCode": "AMAZON_NA", "quantity": 3}]),
            _listing("SKU-2", asin=None, price_amount=None, fulfillment_availability=[]),
        ],
    )
    with Session(engine) as session:
        counts = AmazonSellerListingRepository(session).get_summary_counts(scope["org_id"], scope["participation_id"])
    assert counts.with_asin == 1
    assert counts.with_price == 1
    assert counts.with_fulfillment_availability == 1


def test_summary_counts_only_reflect_the_selected_participation() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU-1"), _listing("SKU-2")])
    with Session(engine) as session:
        # Seed a listing on the OTHER participation too — must not leak in.
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=scope["org_id"],
            marketplace_participation_id=scope["other_participation_id"],
            listings=[_listing("SKU-EU-1")],
            last_ingestion_run_id=None,
        )
        session.commit()
    with Session(engine) as session:
        counts = AmazonSellerListingRepository(session).get_summary_counts(scope["org_id"], scope["participation_id"])
    assert counts.total == 2


# --- collection: pagination, sorting, filters ------------------------------


def test_list_page_pagination_boundaries() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing(f"SKU-{i:02d}") for i in range(5)])
    with Session(engine) as session:
        repo = AmazonSellerListingRepository(session)
        page1 = repo.list_page(scope["org_id"], scope["participation_id"], offset=0, limit=2, sort_by="seller_sku", sort_dir="asc")
        page2 = repo.list_page(scope["org_id"], scope["participation_id"], offset=2, limit=2, sort_by="seller_sku", sort_dir="asc")
        page3 = repo.list_page(scope["org_id"], scope["participation_id"], offset=4, limit=2, sort_by="seller_sku", sort_dir="asc")
    rows1, total1 = page1
    rows2, total2 = page2
    rows3, total3 = page3
    assert total1 == total2 == total3 == 5
    assert [r.seller_sku for r in rows1] == ["SKU-00", "SKU-01"]
    assert [r.seller_sku for r in rows2] == ["SKU-02", "SKU-03"]
    assert [r.seller_sku for r in rows3] == ["SKU-04"]


def test_list_page_default_sort_is_stable_with_tie_breaker() -> None:
    """All rows share the same last_seen_at (same reconcile pass), so the
    id tie-breaker is the only thing making order deterministic."""
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing(f"SKU-{i:02d}") for i in range(5)])
    with Session(engine) as session:
        repo = AmazonSellerListingRepository(session)
        first = repo.list_page(scope["org_id"], scope["participation_id"])
        second = repo.list_page(scope["org_id"], scope["participation_id"])
    order1 = [r.id for r in first[0]]
    order2 = [r.id for r in second[0]]
    assert order1 == order2
    assert order1 == sorted(order1)  # tie-breaker is AmazonSellerListing.id ascending


def test_list_page_search_by_sku() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("ALPHA-1"), _listing("BETA-2")])
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], search="ALPHA"
        )
    assert total == 1
    assert rows[0].seller_sku == "ALPHA-1"


def test_list_page_search_by_asin() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU-1", asin="B0UNIQUE99"), _listing("SKU-2", asin="B0OTHER001")])
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], search="UNIQUE"
        )
    assert total == 1
    assert rows[0].seller_sku == "SKU-1"


def _filter_fixture_listings() -> list[NormalizedListing]:
    return [
        _listing("SKU-ACTIVE", is_buyable=False, is_discoverable=False),
        _listing("SKU-BUYABLE", is_buyable=True, is_discoverable=False),
        _listing("SKU-DISCOVERABLE", is_buyable=False, is_discoverable=True),
        _listing("SKU-ISSUE", is_buyable=False, issue_count=1, highest_issue_severity="ERROR"),
        _listing("SKU-GADGET", is_buyable=False, product_type="GADGET"),
    ]


def _seed_filter_fixture(engine, scope: dict) -> None:
    _reconcile(engine, scope, [*_filter_fixture_listings(), _listing("SKU-DROP", is_buyable=False)])
    # A second, authoritative snapshot omitting SKU-DROP deactivates it —
    # gives every filter test a genuine is_active=False row to exercise.
    with Session(engine) as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            listings=_filter_fixture_listings(),
            last_ingestion_run_id=None,
        )
        session.commit()


def test_list_page_filter_is_active_true_excludes_deactivated_row() -> None:
    engine = _engine()
    scope = _seed(engine)
    _seed_filter_fixture(engine, scope)
    with Session(engine) as session:
        rows, _ = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], is_active=True
        )
    assert "SKU-DROP" not in {r.seller_sku for r in rows}


def test_list_page_filter_is_active_false_returns_only_deactivated_row() -> None:
    engine = _engine()
    scope = _seed(engine)
    _seed_filter_fixture(engine, scope)
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], is_active=False
        )
    assert total == 1
    assert rows[0].seller_sku == "SKU-DROP"


def test_list_page_filter_is_buyable_true() -> None:
    engine = _engine()
    scope = _seed(engine)
    _seed_filter_fixture(engine, scope)
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], is_buyable=True
        )
    assert total == 1
    assert rows[0].seller_sku == "SKU-BUYABLE"


def test_list_page_filter_is_discoverable_true() -> None:
    engine = _engine()
    scope = _seed(engine)
    _seed_filter_fixture(engine, scope)
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], is_discoverable=True
        )
    assert total == 1
    assert rows[0].seller_sku == "SKU-DISCOVERABLE"


def test_list_page_filter_has_issues_true() -> None:
    engine = _engine()
    scope = _seed(engine)
    _seed_filter_fixture(engine, scope)
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], has_issues=True
        )
    assert total == 1
    assert rows[0].seller_sku == "SKU-ISSUE"


def test_list_page_filter_has_issues_false_excludes_issue_row() -> None:
    engine = _engine()
    scope = _seed(engine)
    _seed_filter_fixture(engine, scope)
    with Session(engine) as session:
        rows, _ = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], has_issues=False
        )
    assert "SKU-ISSUE" not in {r.seller_sku for r in rows}


def test_list_page_filter_highest_issue_severity() -> None:
    engine = _engine()
    scope = _seed(engine)
    _seed_filter_fixture(engine, scope)
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], highest_issue_severity="ERROR"
        )
    assert total == 1
    assert rows[0].seller_sku == "SKU-ISSUE"


def test_list_page_filter_product_type() -> None:
    engine = _engine()
    scope = _seed(engine)
    _seed_filter_fixture(engine, scope)
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], product_type="GADGET"
        )
    assert total == 1
    assert rows[0].seller_sku == "SKU-GADGET"


def test_list_page_combined_filters() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(
        engine,
        scope,
        [
            _listing("SKU-MATCH", is_buyable=True, product_type="GADGET"),
            _listing("SKU-WRONG-TYPE", is_buyable=True, product_type="TOY"),
            _listing("SKU-WRONG-BUYABLE", is_buyable=False, product_type="GADGET"),
        ],
    )
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], is_buyable=True, product_type="GADGET"
        )
    assert total == 1
    assert rows[0].seller_sku == "SKU-MATCH"


def test_list_page_invalid_sort_field_rejected() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU-1")])
    with Session(engine) as session:
        with pytest.raises(ValueError):
            AmazonSellerListingRepository(session).list_page(
                scope["org_id"], scope["participation_id"], sort_by="password"
            )


def test_list_page_invalid_sort_direction_rejected() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU-1")])
    with Session(engine) as session:
        with pytest.raises(ValueError):
            AmazonSellerListingRepository(session).list_page(
                scope["org_id"], scope["participation_id"], sort_dir="sideways"
            )


def test_list_page_never_returns_rows_from_another_participation() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU-US-1")])
    with Session(engine) as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=scope["org_id"], marketplace_participation_id=scope["other_participation_id"],
            listings=[_listing("SKU-EU-1")], last_ingestion_run_id=None,
        )
        session.commit()
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(scope["org_id"], scope["participation_id"])
    assert total == 1
    assert rows[0].seller_sku == "SKU-US-1"


# --- 12B.3E remediation: deterministic NULL ordering -----------------------


def test_list_page_asin_ascending_places_nulls_last() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(
        engine, scope,
        [
            _listing("SKU-1", asin="B0BBB00002"),
            _listing("SKU-2", asin=None),
            _listing("SKU-3", asin="B0AAA00001"),
            _listing("SKU-4", asin=None),
        ],
    )
    with Session(engine) as session:
        rows, _ = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], sort_by="asin", sort_dir="asc", limit=10,
        )
    asins = [r.asin for r in rows]
    assert asins == ["B0AAA00001", "B0BBB00002", None, None]


def test_list_page_asin_descending_still_places_nulls_last() -> None:
    """NULLS LAST is applied for *both* directions — descending must not
    flip to NULLS FIRST, which is PostgreSQL's own default for DESC."""
    engine = _engine()
    scope = _seed(engine)
    _reconcile(
        engine, scope,
        [
            _listing("SKU-1", asin="B0BBB00002"),
            _listing("SKU-2", asin=None),
            _listing("SKU-3", asin="B0AAA00001"),
            _listing("SKU-4", asin=None),
        ],
    )
    with Session(engine) as session:
        rows, _ = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], sort_by="asin", sort_dir="desc", limit=10,
        )
    asins = [r.asin for r in rows]
    assert asins == ["B0BBB00002", "B0AAA00001", None, None]


def test_list_page_price_amount_ascending_places_nulls_last() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(
        engine, scope,
        [
            _listing("SKU-1", price_amount=Decimal("30.00")),
            _listing("SKU-2", price_amount=None),
            _listing("SKU-3", price_amount=Decimal("10.00")),
        ],
    )
    with Session(engine) as session:
        rows, _ = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], sort_by="price_amount", sort_dir="asc", limit=10,
        )
    prices = [r.price_amount for r in rows]
    assert prices == [Decimal("10.00"), Decimal("30.00"), None]


def test_list_page_price_amount_descending_places_nulls_last() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(
        engine, scope,
        [
            _listing("SKU-1", price_amount=Decimal("30.00")),
            _listing("SKU-2", price_amount=None),
            _listing("SKU-3", price_amount=Decimal("10.00")),
        ],
    )
    with Session(engine) as session:
        rows, _ = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], sort_by="price_amount", sort_dir="desc", limit=10,
        )
    prices = [r.price_amount for r in rows]
    assert prices == [Decimal("30.00"), Decimal("10.00"), None]


def test_list_page_rows_sharing_the_same_sort_value_are_ordered_by_id() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(
        engine, scope,
        [
            _listing("SKU-1", price_amount=Decimal("5.00")),
            _listing("SKU-2", price_amount=Decimal("5.00")),
            _listing("SKU-3", price_amount=Decimal("5.00")),
        ],
    )
    with Session(engine) as session:
        rows, _ = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], sort_by="price_amount", sort_dir="asc", limit=10,
        )
    ids = [r.id for r in rows]
    assert ids == sorted(ids)


def test_list_page_multiple_null_rows_are_ordered_by_id() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(
        engine, scope,
        [
            _listing("SKU-1", asin=None),
            _listing("SKU-2", asin=None),
            _listing("SKU-3", asin=None),
        ],
    )
    with Session(engine) as session:
        rows, _ = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], sort_by="asin", sort_dir="asc", limit=10,
        )
    ids = [r.id for r in rows]
    assert ids == sorted(ids)


def test_list_page_stable_pagination_across_boundaries_with_nulls() -> None:
    engine = _engine()
    scope = _seed(engine)
    listings = [_listing(f"SKU-{i:02d}", asin=(f"B0{i:03d}TEST1" if i % 2 == 0 else None)) for i in range(6)]
    _reconcile(engine, scope, listings)
    with Session(engine) as session:
        repo = AmazonSellerListingRepository(session)
        full, _ = repo.list_page(scope["org_id"], scope["participation_id"], sort_by="asin", sort_dir="asc", limit=10)
        page1, _ = repo.list_page(scope["org_id"], scope["participation_id"], sort_by="asin", sort_dir="asc", offset=0, limit=3)
        page2, _ = repo.list_page(scope["org_id"], scope["participation_id"], sort_by="asin", sort_dir="asc", offset=3, limit=3)
    assert [r.id for r in full] == [r.id for r in page1] + [r.id for r in page2]


# --- 12B.3E remediation: literal (escaped) search semantics ----------------


def test_search_normal_sku_substring() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("ALPHA-1"), _listing("BETA-2")])
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], search="ALPHA"
        )
    assert total == 1
    assert rows[0].seller_sku == "ALPHA-1"


def test_search_normal_asin_substring() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU-1", asin="B0UNIQUE99"), _listing("SKU-2", asin="B0OTHER001")])
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], search="UNIQUE"
        )
    assert total == 1
    assert rows[0].seller_sku == "SKU-1"


def test_search_literal_percent_character() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(
        engine, scope,
        [_listing("SKU-100%OFF"), _listing("SKU-OTHER"), _listing("SKU-200PERCENT")],
    )
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], search="100%OFF"
        )
    assert total == 1
    assert rows[0].seller_sku == "SKU-100%OFF"


def test_search_literal_underscore_character() -> None:
    """A literal underscore must not act as a SQL single-character
    wildcard — 'SKU_1' must not also match 'SKUX1'."""
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU_1"), _listing("SKUX1")])
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], search="SKU_1"
        )
    assert total == 1
    assert rows[0].seller_sku == "SKU_1"


def test_search_literal_escape_character() -> None:
    """A literal backslash in the search term must match itself, not be
    consumed as an escape-sequence introducer."""
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU-BACK\\SLASH"), _listing("SKU-PLAIN")])
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], search="BACK\\SLASH"
        )
    assert total == 1
    assert rows[0].seller_sku == "SKU-BACK\\SLASH"


def test_search_whitespace_only_is_treated_as_no_search() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU-1"), _listing("SKU-2")])
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], search="   "
        )
    assert total == 2


def test_search_does_not_return_cross_marketplace_results() -> None:
    engine = _engine()
    scope = _seed(engine)
    _reconcile(engine, scope, [_listing("SKU-US-MATCH")])
    with Session(engine) as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=scope["org_id"], marketplace_participation_id=scope["other_participation_id"],
            listings=[_listing("SKU-EU-MATCH")], last_ingestion_run_id=None,
        )
        session.commit()
    with Session(engine) as session:
        rows, total = AmazonSellerListingRepository(session).list_page(
            scope["org_id"], scope["participation_id"], search="MATCH"
        )
    assert total == 1
    assert rows[0].seller_sku == "SKU-US-MATCH"
