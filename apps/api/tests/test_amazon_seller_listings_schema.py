"""12B.3B — schema foundation for `amazon_seller_listings` and the extended
`amazon_ingestion_runs` ledger. Schema-level proof only: no SP-API client,
reconciliation service, read API, or UI code exists yet.

Uses a dedicated file-based SQLite engine (`Base.metadata.create_all()`),
the same pattern already used by `test_amazon_seller_account_reconcile_
concurrency.py`, so these tests run everywhere without Docker or a real
PostgreSQL instance. SQLite is not the real migration target (see
`test_amazon_seller_identity_schema.py` for why `0010` itself cannot be
run via Alembic against SQLite), but `Base.metadata.create_all()` uses the
same `Guid`/`JsonPayload` dialect-aware ORM types the application actually
runs on in every other test in this suite, so SQLite genuinely proves these
constraints, defaults, and the partial unique index all work — real
PostgreSQL proof of the *migration itself* lives in
`tests/postgres/test_disposable_postgres_seller_listings_migration.py`
(guarded, not executed in this environment) and the dependency-free offline
compile check in `tests/test_migration_chain_matches_orm_metadata.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.persistence.models import (
    AmazonIngestionRun,
    AmazonMarketplaceParticipation,
    AmazonSellerAccount,
    AmazonSellerListing,
    Base,
    Organization,
)
from app.persistence.repositories import AmazonIngestionRunRepository

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _dedicated_engine(tmp_path: Path, name: str):
    db_path = tmp_path / f"{name}.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return engine


def _dedicated_engine_with_fk_enforcement(tmp_path: Path, name: str):
    """SQLite does not enforce foreign keys (including ON DELETE actions)
    unless `PRAGMA foreign_keys=ON` is set per-connection — and nothing in
    `app.persistence.database` enables it, so the application's own SQLite
    test engine (and every other test in this file) never actually proves
    FK-enforcement behavior. This dedicated engine turns it on explicitly,
    for this file's referential-action and provenance tests only — no
    production code is touched."""
    db_path = tmp_path / f"{name}.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def _seed_org_seller_account_and_participation(engine) -> tuple:
    org_id = uuid4()
    seller_account_id = uuid4()
    participation_id = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_id, name="12B.3B Schema Test Org"))
        session.add(
            AmazonSellerAccount(
                id=seller_account_id,
                organization_id=org_id,
                selling_partner_id=f"A{uuid4().hex[:14].upper()}",
                status="active",
            )
        )
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_id,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_id="ATVPDKIKX0DER",
                region="na",
            )
        )
        session.commit()
    return org_id, seller_account_id, participation_id


# --- amazon_seller_listings ---------------------------------------------


def test_seller_listing_natural_key_uniqueness_enforced(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "natural_key")
    _, _, participation_id = _seed_org_seller_account_and_participation(engine)

    with Session(engine) as session:
        session.add(
            AmazonSellerListing(
                marketplace_participation_id=participation_id,
                seller_sku="SKU-DUP",
                status=["BUYABLE"],
                offers=[],
                fulfillment_availability=[],
                issues=[],
                product_types=[],
            )
        )
        session.commit()

    with Session(engine) as session:
        session.add(
            AmazonSellerListing(
                marketplace_participation_id=participation_id,
                seller_sku="SKU-DUP",
                status=[],
                offers=[],
                fulfillment_availability=[],
                issues=[],
                product_types=[],
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_seller_listing_asin_is_nullable_and_round_trips(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "asin_nullable")
    _, _, participation_id = _seed_org_seller_account_and_participation(engine)

    listing_id = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonSellerListing(
                id=listing_id,
                marketplace_participation_id=participation_id,
                seller_sku="SKU-DRAFT-NO-ASIN",
                asin=None,
                status=[],
                offers=[],
                fulfillment_availability=[],
                issues=[],
                product_types=[],
            )
        )
        session.commit()

    with Session(engine) as session:
        row = session.get(AmazonSellerListing, listing_id)
        assert row is not None
        assert row.asin is None


def test_seller_listing_json_fields_are_not_null_and_require_explicit_values(tmp_path) -> None:
    """Matches the existing repo convention (`ProfitSnapshot.inputs_json`,
    `AdvertisingSnapshot.outputs_json`, ...): JSON payload columns are
    NOT NULL with no Python-level or server-side default. The application
    must always supply a value — omitting one is a bug, not a valid state."""
    engine = _dedicated_engine(tmp_path, "json_not_null")
    _, _, participation_id = _seed_org_seller_account_and_participation(engine)

    with Session(engine) as session:
        session.add(
            AmazonSellerListing(
                marketplace_participation_id=participation_id,
                seller_sku="SKU-MISSING-STATUS",
                offers=[],
                fulfillment_availability=[],
                issues=[],
                product_types=[],
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_seller_listing_derived_and_json_fields_round_trip(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "round_trip")
    _, _, participation_id = _seed_org_seller_account_and_participation(engine)

    listing_id = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonSellerListing(
                id=listing_id,
                marketplace_participation_id=participation_id,
                seller_sku="SKU-FULL",
                asin="B0TESTASIN99",
                product_type="LUGGAGE",
                status=["BUYABLE", "DISCOVERABLE"],
                is_buyable=True,
                is_discoverable=True,
                offers=[{"marketplaceId": "ATVPDKIKX0DER", "offerType": "B2C", "price": {"amount": "9.99"}}],
                price_amount="9.99",
                price_currency="USD",
                fulfillment_availability=[{"fulfillmentChannelCode": "DEFAULT", "quantity": 5}],
                issues=[{"code": "X", "message": "m", "severity": "WARNING", "categories": ["LISTING"]}],
                issue_count=1,
                highest_issue_severity="WARNING",
                product_types=[{"marketplaceId": "ATVPDKIKX0DER", "productType": "LUGGAGE"}],
            )
        )
        session.commit()

    with Session(engine) as session:
        row = session.get(AmazonSellerListing, listing_id)
        assert row is not None
        assert row.is_buyable is True
        assert row.is_discoverable is True
        assert row.offers[0]["offerType"] == "B2C"
        assert row.fulfillment_availability[0]["fulfillmentChannelCode"] == "DEFAULT"
        assert row.issue_count == 1
        assert row.highest_issue_severity == "WARNING"
        assert row.product_types[0]["productType"] == "LUGGAGE"


def test_seller_listing_highest_issue_severity_check_constraint(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "severity_check")
    _, _, participation_id = _seed_org_seller_account_and_participation(engine)

    with Session(engine) as session:
        session.add(
            AmazonSellerListing(
                marketplace_participation_id=participation_id,
                seller_sku="SKU-BAD-SEVERITY",
                status=[],
                offers=[],
                fulfillment_availability=[],
                issues=[],
                product_types=[],
                highest_issue_severity="CRITICAL",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_seller_listing_ownership_derives_through_marketplace_participation_only(tmp_path) -> None:
    """No `organization_id`/`seller_account_id` column exists on
    `amazon_seller_listings` — ownership is derived solely by joining
    through `marketplace_participation_id`. This test proves that join
    correctly isolates two organizations' listings from each other, which
    is the actual guarantee the "no contradictory ownership" requirement
    reduces to when there is only one recorded ownership value in the
    system."""
    engine = _dedicated_engine(tmp_path, "ownership_join")
    org_a, _, participation_a = _seed_org_seller_account_and_participation(engine)
    org_b, _, participation_b = _seed_org_seller_account_and_participation(engine)
    assert org_a != org_b

    with Session(engine) as session:
        session.add_all(
            [
                AmazonSellerListing(
                    marketplace_participation_id=participation_a,
                    seller_sku="ORG-A-SKU",
                    status=[],
                    offers=[],
                    fulfillment_availability=[],
                    issues=[],
                    product_types=[],
                ),
                AmazonSellerListing(
                    marketplace_participation_id=participation_b,
                    seller_sku="ORG-B-SKU",
                    status=[],
                    offers=[],
                    fulfillment_availability=[],
                    issues=[],
                    product_types=[],
                ),
            ]
        )
        session.commit()

    with Session(engine) as session:
        org_a_skus = {
            listing.seller_sku
            for listing, participation in session.query(AmazonSellerListing, AmazonMarketplaceParticipation)
            .join(
                AmazonMarketplaceParticipation,
                AmazonSellerListing.marketplace_participation_id == AmazonMarketplaceParticipation.id,
            )
            .filter(AmazonMarketplaceParticipation.organization_id == org_a)
            .all()
        }
        assert org_a_skus == {"ORG-A-SKU"}


# --- referential-action and provenance integrity (real FK enforcement) ----
#
# These tests use `_dedicated_engine_with_fk_enforcement`, not
# `_dedicated_engine`: FK-driven behavior (ON DELETE actions, and the
# composite FK below) cannot be observed at all with SQLite's default
# `PRAGMA foreign_keys=OFF`, which is what every other test in this file —
# and the application's own SQLite test engine — actually runs with.


def test_deleting_a_referenced_marketplace_participation_is_restricted_by_a_listings_run(
    tmp_path,
) -> None:
    """Confirms the fix: marketplace_participation_id's FK is ON DELETE
    RESTRICT, not SET NULL. Before this fix, SET NULL would have been
    attempted and then failed a totally different constraint
    (ck_amazon_ingestion_runs_listings_scope_required) with a confusing
    CHECK-violation error; RESTRICT makes the enforced behavior match the
    declared behavior with a direct FK-violation error instead."""
    engine = _dedicated_engine_with_fk_enforcement(tmp_path, "restrict_participation_delete")
    _, seller_account_id, participation_id = _seed_org_seller_account_and_participation(engine)

    with Session(engine) as session:
        participation = session.get(AmazonMarketplaceParticipation, participation_id)
        organization_id = participation.organization_id
        session.add(
            AmazonIngestionRun(
                organization_id=organization_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                run_type="listings",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="started",
            )
        )
        session.commit()

    with Session(engine) as session:
        participation = session.get(AmazonMarketplaceParticipation, participation_id)
        session.delete(participation)
        with pytest.raises(IntegrityError):
            session.commit()


def test_deleting_an_ingestion_run_referenced_as_last_ingestion_run_is_restricted(tmp_path) -> None:
    engine = _dedicated_engine_with_fk_enforcement(tmp_path, "restrict_run_delete")
    org_id, seller_account_id, participation_id = _seed_org_seller_account_and_participation(engine)

    run_id = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                id=run_id,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                run_type="listings",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="succeeded",
            )
        )
        session.commit()

    with Session(engine) as session:
        session.add(
            AmazonSellerListing(
                marketplace_participation_id=participation_id,
                seller_sku="SKU-WITH-PROVENANCE",
                status=[],
                offers=[],
                fulfillment_availability=[],
                issues=[],
                product_types=[],
                last_ingestion_run_id=run_id,
            )
        )
        session.commit()

    with Session(engine) as session:
        run = session.get(AmazonIngestionRun, run_id)
        session.delete(run)
        with pytest.raises(IntegrityError):
            session.commit()


def test_last_ingestion_run_must_belong_to_the_same_marketplace_participation(tmp_path) -> None:
    """The actual provenance guarantee: a listing's `last_ingestion_run_id`
    cannot point to a run scoped to a DIFFERENT marketplace participation.
    Enforced by the composite FK (last_ingestion_run_id,
    marketplace_participation_id) -> amazon_ingestion_runs(id,
    marketplace_participation_id) — not by any organization/seller-account
    duplication."""
    engine = _dedicated_engine_with_fk_enforcement(tmp_path, "provenance_same_participation")
    org_id, seller_account_id, participation_a = _seed_org_seller_account_and_participation(engine)
    participation_b = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_b,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_id="A1F83G8C2ARO7P",
                region="eu",
            )
        )
        session.commit()

    run_for_a = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                id=run_for_a,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_a,
                run_type="listings",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="succeeded",
            )
        )
        session.commit()

    # Rejected: a participation-B listing claiming a participation-A run.
    with Session(engine) as session:
        session.add(
            AmazonSellerListing(
                marketplace_participation_id=participation_b,
                seller_sku="SKU-CROSS-PARTICIPATION",
                status=[],
                offers=[],
                fulfillment_availability=[],
                issues=[],
                product_types=[],
                last_ingestion_run_id=run_for_a,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    # Accepted: a participation-A listing claiming the participation-A run.
    with Session(engine) as session:
        session.add(
            AmazonSellerListing(
                marketplace_participation_id=participation_a,
                seller_sku="SKU-SAME-PARTICIPATION",
                status=[],
                offers=[],
                fulfillment_availability=[],
                issues=[],
                product_types=[],
                last_ingestion_run_id=run_for_a,
            )
        )
        session.commit()  # must not raise


# --- amazon_ingestion_runs extensions -------------------------------------


def test_ingestion_run_run_type_defaults_to_marketplace_participations(tmp_path) -> None:
    """The existing, unmodified repository call site still works: `start()`
    never passes `run_type`, and the ORM-level default backfills it —
    the same deterministic default the real migration applies to
    pre-existing rows via `server_default`."""
    engine = _dedicated_engine(tmp_path, "run_type_default")
    org_id, _, _ = _seed_org_seller_account_and_participation(engine)

    with Session(engine) as session:
        run = AmazonIngestionRunRepository(session).start(
            organization_id=org_id, domain="amazon.com", region="na", environment="PRODUCTION"
        )
        session.commit()
        run_id = run.id

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, run_id)
        assert row is not None
        assert row.run_type == "marketplace_participations"
        assert row.marketplace_participation_id is None
        assert row.pages_fetched == 0
        assert row.reported_total_results is None


def test_ingestion_run_run_type_check_constraint(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "run_type_check")
    org_id, _, _ = _seed_org_seller_account_and_participation(engine)

    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                organization_id=org_id,
                run_type="not_a_real_type",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="started",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_ingestion_run_listings_scope_required_check_constraint(tmp_path) -> None:
    """A `run_type='listings'` row must carry both `seller_account_id` and
    `marketplace_participation_id` — the single-writer scope this
    constraint (and the partial unique index below) is keyed on."""
    engine = _dedicated_engine(tmp_path, "listings_scope_required")
    org_id, seller_account_id, participation_id = _seed_org_seller_account_and_participation(engine)

    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=None,
                run_type="listings",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="started",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                run_type="listings",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="started",
            )
        )
        session.commit()  # must not raise


def test_active_listings_run_uniqueness_enforced_and_scope_never_permanently_locked(tmp_path) -> None:
    """Proves the single-writer guarantee end to end on SQLite (partial
    unique index via `sqlite_where`): a second concurrent 'started' listings
    run for the same scope is rejected, but completing the first frees the
    scope for a new claim — it is never permanently locked."""
    engine = _dedicated_engine(tmp_path, "active_run_uniqueness")
    org_id, seller_account_id, participation_id = _seed_org_seller_account_and_participation(engine)

    def _new_listings_run() -> AmazonIngestionRun:
        return AmazonIngestionRun(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id,
            run_type="listings",
            domain="amazon.com",
            region="na",
            environment="PRODUCTION",
            status="started",
        )

    with Session(engine) as session:
        session.add(_new_listings_run())
        session.commit()

    with Session(engine) as session:
        session.add(_new_listings_run())
        with pytest.raises(IntegrityError):
            session.commit()

    # A second, unrelated 'marketplace_participations' run for the same
    # seller account (marketplace_participation_id is NULL) must never be
    # blocked by this constraint — the partial predicate only matches
    # run_type='listings'.
    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=None,
                run_type="marketplace_participations",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="started",
            )
        )
        session.commit()  # must not raise

    with Session(engine) as session:
        first = (
            session.query(AmazonIngestionRun)
            .filter_by(
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                run_type="listings",
            )
            .one()
        )
        first.status = "succeeded"
        session.commit()

    with Session(engine) as session:
        session.add(_new_listings_run())
        session.commit()  # must not raise — the scope was freed


def test_ingestion_run_stale_lease_fields_support_future_recovery(tmp_path) -> None:
    """Schema-only proof: a run can carry a lease owner/expiry in the past,
    and once a later process (not implemented here — that is 12B.3D)
    transitions it out of 'started', the scope is claimable again. This
    test does not implement stale-run detection or recovery; it proves the
    columns exist and behave correctly under manual transition.

    Also proves the negative case explicitly: an expired `lease_expires_at`
    does NOT, by itself, release the scope. The partial unique index's
    predicate (`run_type = 'listings' AND status = 'started'`) is evaluated
    against a row's own columns when written, not continuously against
    wall-clock time — it has no way to know a lease has "expired" until
    something writes a new status to that row. "Scope released after a
    terminal status" is the only true statement; "lease expiry
    auto-releases the scope" is not, and this test pins that distinction
    with a real failing claim attempt before the manual transition below."""
    engine = _dedicated_engine(tmp_path, "stale_lease")
    org_id, seller_account_id, participation_id = _seed_org_seller_account_and_participation(engine)

    run_id = uuid4()
    expired = datetime.now(UTC) - timedelta(hours=2)
    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                id=run_id,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                run_type="listings",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="started",
                lease_owner="worker-that-crashed",
                lease_expires_at=expired,
            )
        )
        session.commit()

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, run_id)
        assert row is not None
        assert row.lease_owner == "worker-that-crashed"
        assert row.lease_expires_at is not None
        # SQLite round-trips DateTime(timezone=True) as naive; compare in a
        # single, consistently-naive frame rather than assuming tzinfo survives.
        stored_expiry = row.lease_expires_at
        if stored_expiry.tzinfo is None:
            stored_expiry = stored_expiry.replace(tzinfo=UTC)
        assert stored_expiry < datetime.now(UTC)

    # Negative case: the lease is already expired, but the row is still
    # 'started' — a concurrent claim attempt for the same scope must still
    # be rejected. Expiry alone changes nothing until something writes a
    # new status to the stale row.
    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                run_type="listings",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="started",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, run_id)
        # A future recovery service would transition this row here.
        row.status = "timed_out"
        session.commit()

    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                run_type="listings",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="started",
            )
        )
        session.commit()  # must not raise — the abandoned run no longer holds the scope


def test_reported_total_results_persists_values_above_the_documented_pagination_ceiling(tmp_path) -> None:
    """Amazon documents a hard ceiling of 1000 items actually paginatable
    through `searchListingsItems`, even when `numberOfResults` (stored here
    as `reported_total_results`) reports a larger true match count. This
    test only proves the column persists such a value faithfully; comparing
    it against the 1000-item ceiling to override `pagination_complete` is
    explicitly deferred to the 12B.3D reconciliation service."""
    engine = _dedicated_engine(tmp_path, "pagination_ceiling")
    org_id, seller_account_id, participation_id = _seed_org_seller_account_and_participation(engine)

    run_id = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                id=run_id,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                run_type="listings",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="succeeded",
                records_received=1000,
                reported_total_results=5000,
                pages_fetched=100,
                pagination_complete=True,
            )
        )
        session.commit()

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, run_id)
        assert row is not None
        assert row.reported_total_results == 5000
        assert row.records_received == 1000
        # Naive signal only — a future service must not trust this alone.
        assert row.pagination_complete is True


# --- static, no-database checks -------------------------------------------


def test_migration_0010_source_contains_no_secret_material() -> None:
    source = (MIGRATIONS_DIR / "0010_amazon_seller_listings.py").read_text()
    forbidden_columns = {"refresh_token", "access_token", "client_secret", "token_reference"}
    for column in forbidden_columns:
        assert column not in source
    for marker in ("Atza|", "Atzr|", "sk-", "AKIA", "-----BEGIN", "supabase.co"):
        assert marker not in source
