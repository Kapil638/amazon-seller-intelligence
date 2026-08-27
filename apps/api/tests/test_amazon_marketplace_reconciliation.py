"""12B.2B — AmazonMarketplaceReconciliationService. No live SP-API calls."""

from __future__ import annotations

from uuid import uuid4

from app.amazon.marketplace_reconciliation import (
    AmazonMarketplaceReconciliationService,
    INGESTION_DOMAIN,
)
from app.amazon.seller_validation import NormalizedMarketplaceParticipation
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import Organization
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
)

FORBIDDEN_SECRET_FIELDS = ("refresh_token", "access_token", "client_secret", "client_id", "token_reference")


def _connection_id(organization_id) -> object:
    with session_scope() as session:
        row = AmazonConnectionRepository(session).create(
            organization_id=organization_id,
            provider="SP_API",
            environment="PRODUCTION",
            region="na",
        )
        return row.id


def _participation(
    marketplace_id: str = "ATVPDKIKX0DER",
    *,
    is_participating: bool = True,
    has_suspended_listings: bool = False,
    store_name: str | None = "BestSellerStore",
    country_code: str = "US",
    domain_name: str = "www.amazon.com",
    name: str = "Amazon.com",
) -> NormalizedMarketplaceParticipation:
    return NormalizedMarketplaceParticipation(
        marketplace_id=marketplace_id,
        name=name,
        country_code=country_code,
        default_currency_code="USD",
        default_language_code="en_US",
        domain_name=domain_name,
        store_name=store_name,
        is_participating=is_participating,
        has_suspended_listings=has_suspended_listings,
    )


def test_first_reconciliation_creates_account_and_participations() -> None:
    org_id = current_organization_id()
    connection_id = _connection_id(org_id)
    service = AmazonMarketplaceReconciliationService()

    outcome = service.reconcile(
        organization_id=org_id,
        connection_id=connection_id,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="A1SELLERID",
        participations=[
            _participation("ATVPDKIKX0DER", country_code="US"),
            _participation("A2EUQ1WTGCTBG2", country_code="CA", store_name=None),
        ],
    )

    assert outcome.succeeded is True
    assert outcome.reason is None
    assert outcome.records_received == 2
    assert outcome.records_accepted == 2
    assert outcome.records_rejected == 0
    assert outcome.seller_account_id is not None

    with session_scope() as session:
        account = AmazonSellerAccountRepository(session).get_by_id(org_id, outcome.seller_account_id)
        assert account is not None
        assert account.selling_partner_id == "A1SELLERID"
        assert account.display_store_name == "BestSellerStore"
        assert account.last_successful_sync_at is not None
        rows = AmazonMarketplaceParticipationRepository(session).list_for_seller_account(
            org_id, account.id
        )
        assert {row.marketplace_id for row in rows} == {"ATVPDKIKX0DER", "A2EUQ1WTGCTBG2"}
        for row in rows:
            assert row.is_active is True
            for column in row.__table__.c.keys():
                assert column not in FORBIDDEN_SECRET_FIELDS
        run = AmazonIngestionRunRepository(session).get_by_id(org_id, outcome.ingestion_run_id)
        assert run is not None
        assert run.status == "succeeded"
        assert run.domain == INGESTION_DOMAIN
        assert run.records_accepted == 2
        assert run.failure_class is None


def test_repeat_reconciliation_is_idempotent() -> None:
    org_id = current_organization_id()
    connection_id = _connection_id(org_id)
    service = AmazonMarketplaceReconciliationService()
    participations = [_participation("ATVPDKIKX0DER")]

    first = service.reconcile(
        organization_id=org_id,
        connection_id=connection_id,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="A1SELLERID",
        participations=participations,
    )
    second = service.reconcile(
        organization_id=org_id,
        connection_id=connection_id,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="A1SELLERID",
        participations=participations,
    )

    assert first.seller_account_id == second.seller_account_id
    with session_scope() as session:
        accounts = AmazonSellerAccountRepository(session).list_for_org(org_id)
        assert len(accounts) == 1
        rows = AmazonMarketplaceParticipationRepository(session).list_for_seller_account(
            org_id, first.seller_account_id
        )
        assert len(rows) == 1


def test_non_participating_and_suspended_marketplaces_are_preserved() -> None:
    org_id = current_organization_id()
    connection_id = _connection_id(org_id)
    service = AmazonMarketplaceReconciliationService()

    outcome = service.reconcile(
        organization_id=org_id,
        connection_id=connection_id,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="A1SELLERID",
        participations=[
            _participation("ATVPDKIKX0DER", is_participating=True),
            _participation(
                "A2EUQ1WTGCTBG2",
                is_participating=False,
                has_suspended_listings=True,
                store_name=None,
            ),
        ],
    )
    assert outcome.succeeded is True

    with session_scope() as session:
        rows = {
            row.marketplace_id: row
            for row in AmazonMarketplaceParticipationRepository(session).list_for_seller_account(
                org_id, outcome.seller_account_id
            )
        }
        assert rows["ATVPDKIKX0DER"].is_participating is True
        assert rows["A2EUQ1WTGCTBG2"].is_participating is False
        assert rows["A2EUQ1WTGCTBG2"].has_suspended_listings is True
        # is_active tracks presence in the latest complete snapshot, not
        # Amazon's own is_participating flag — both rows were present.
        assert rows["ATVPDKIKX0DER"].is_active is True
        assert rows["A2EUQ1WTGCTBG2"].is_active is True


def test_marketplace_absent_from_later_snapshot_is_deactivated_not_deleted() -> None:
    org_id = current_organization_id()
    connection_id = _connection_id(org_id)
    service = AmazonMarketplaceReconciliationService()

    service.reconcile(
        organization_id=org_id,
        connection_id=connection_id,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="A1SELLERID",
        participations=[
            _participation("ATVPDKIKX0DER"),
            _participation("A2EUQ1WTGCTBG2", store_name=None),
        ],
    )
    outcome = service.reconcile(
        organization_id=org_id,
        connection_id=connection_id,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="A1SELLERID",
        participations=[_participation("ATVPDKIKX0DER")],
    )
    assert outcome.succeeded is True

    with session_scope() as session:
        rows = {
            row.marketplace_id: row
            for row in AmazonMarketplaceParticipationRepository(session).list_for_seller_account(
                org_id, outcome.seller_account_id
            )
        }
        assert set(rows) == {"ATVPDKIKX0DER", "A2EUQ1WTGCTBG2"}
        assert rows["ATVPDKIKX0DER"].is_active is True
        assert rows["A2EUQ1WTGCTBG2"].is_active is False


def test_display_store_name_choice_is_deterministic_not_last_row_wins() -> None:
    org_id = current_organization_id()
    connection_id = _connection_id(org_id)
    service = AmazonMarketplaceReconciliationService()

    outcome = service.reconcile(
        organization_id=org_id,
        connection_id=connection_id,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="A1SELLERID",
        participations=[
            _participation("Z9LAST", store_name="LastRowStore"),
            _participation("A1FIRST", store_name="FirstRowStore"),
        ],
    )
    with session_scope() as session:
        account = AmazonSellerAccountRepository(session).get_by_id(org_id, outcome.seller_account_id)
        assert account is not None
        # Deterministic by lowest marketplace_id among participating
        # entries, not payload order.
        assert account.display_store_name == "FirstRowStore"
        rows = {
            row.marketplace_id: row
            for row in AmazonMarketplaceParticipationRepository(session).list_for_seller_account(
                org_id, outcome.seller_account_id
            )
        }
        assert rows["Z9LAST"].store_name == "LastRowStore"
        assert rows["A1FIRST"].store_name == "FirstRowStore"


def test_ownership_conflict_fails_closed_and_does_not_disclose_owner() -> None:
    org_a = current_organization_id()
    org_b = uuid4()
    with session_scope() as session:
        session.add(Organization(id=org_b, name="Other"))
        AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_a, selling_partner_id="ASHAREDID"
        )
    connection_id = _connection_id(org_b)
    service = AmazonMarketplaceReconciliationService()

    outcome = service.reconcile(
        organization_id=org_b,
        connection_id=connection_id,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="ASHAREDID",
        participations=[_participation("ATVPDKIKX0DER")],
    )

    assert outcome.succeeded is False
    assert outcome.reason == "ownership_conflict"
    assert str(org_a) not in str(outcome)
    with session_scope() as session:
        assert AmazonSellerAccountRepository(session).list_for_org(org_b) == []
        run = AmazonIngestionRunRepository(session).get_by_id(org_b, outcome.ingestion_run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.failure_class == "ownership_conflict"


def test_missing_identity_is_rejected_before_any_write() -> None:
    org_id = current_organization_id()
    connection_id = _connection_id(org_id)
    service = AmazonMarketplaceReconciliationService()

    outcome = service.reconcile(
        organization_id=org_id,
        connection_id=connection_id,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="   ",
        participations=[_participation("ATVPDKIKX0DER")],
    )
    assert outcome.succeeded is False
    assert outcome.reason == "identity_missing"
    with session_scope() as session:
        assert AmazonSellerAccountRepository(session).list_for_org(org_id) == []
        assert AmazonIngestionRunRepository(session).list_for_org(org_id) == []


def test_malformed_participations_reject_the_whole_snapshot_with_a_visible_failed_run() -> None:
    """A response mixing valid and malformed entries must not be partially
    reconciled — a malformed entry is not evidence the seller left any
    marketplace, so the whole snapshot is rejected for absence/deactivation
    safety. This must still be observable: a failed ingestion run is
    recorded with `failure_class="malformed_participations"`, not silently
    dropped as an INFO-only, unpersisted event."""
    org_id = current_organization_id()
    connection_id = _connection_id(org_id)
    service = AmazonMarketplaceReconciliationService()

    outcome = service.reconcile(
        organization_id=org_id,
        connection_id=connection_id,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="A1SELLERID",
        participations=[_participation("ATVPDKIKX0DER"), _participation("   ")],
    )
    assert outcome.succeeded is False
    assert outcome.reason == "malformed_participations"
    assert outcome.records_received == 2
    assert outcome.records_rejected == 2
    assert outcome.ingestion_run_id is not None
    with session_scope() as session:
        # No canonical writes at all — not even the one structurally valid entry.
        assert AmazonSellerAccountRepository(session).list_for_org(org_id) == []
        runs = AmazonIngestionRunRepository(session).list_for_org(org_id)
        assert len(runs) == 1
        assert runs[0].status == "failed"
        assert runs[0].failure_class == "malformed_participations"
        assert runs[0].records_received == 2
        assert runs[0].records_accepted == 0
        assert runs[0].records_rejected == 2


def test_fully_malformed_snapshot_also_records_a_visible_failed_run() -> None:
    org_id = current_organization_id()
    connection_id = _connection_id(org_id)
    service = AmazonMarketplaceReconciliationService()

    outcome = service.reconcile(
        organization_id=org_id,
        connection_id=connection_id,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="A1SELLERID",
        participations=[_participation("   ")],
    )
    assert outcome.succeeded is False
    assert outcome.reason == "malformed_participations"
    with session_scope() as session:
        assert AmazonSellerAccountRepository(session).list_for_org(org_id) == []
        runs = AmazonIngestionRunRepository(session).list_for_org(org_id)
        assert len(runs) == 1
        assert runs[0].status == "failed"
        assert runs[0].failure_class == "malformed_participations"


def test_empty_payload_is_distinguished_from_malformed_and_never_deactivates() -> None:
    """A structurally valid but empty payload is not treated as 'the seller
    now has zero marketplaces' — existing product behavior already treats
    zero participating marketplaces as a validation failure upstream
    (AmazonSellerValidationService.validate's seller_identity_unavailable
    gate), so this stays consistent: rejected outright, distinctly reasoned
    from malformed data, and never deactivates prior rows."""
    org_id = current_organization_id()
    connection_id = _connection_id(org_id)
    service = AmazonMarketplaceReconciliationService()

    first = service.reconcile(
        organization_id=org_id,
        connection_id=connection_id,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="A1SELLERID",
        participations=[_participation("ATVPDKIKX0DER")],
    )
    assert first.succeeded is True

    outcome = service.reconcile(
        organization_id=org_id,
        connection_id=connection_id,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="A1SELLERID",
        participations=[],
    )
    assert outcome.succeeded is False
    assert outcome.reason == "empty_snapshot"
    assert outcome.reason != "malformed_participations"
    assert outcome.records_received == 0

    with session_scope() as session:
        # The prior successful synchronization's row is untouched, not deactivated.
        rows = AmazonMarketplaceParticipationRepository(session).list_for_seller_account(
            org_id, first.seller_account_id
        )
        assert len(rows) == 1
        assert rows[0].is_active is True

        # (Two runs created back-to-back can tie at SQLite's timestamp
        # resolution, so this checks by status rather than by position.)
        runs = AmazonIngestionRunRepository(session).list_for_connection(org_id, connection_id)
        assert len(runs) == 2
        assert {run.status for run in runs} == {"succeeded", "failed"}
        failed_run = next(run for run in runs if run.status == "failed")
        assert failed_run.failure_class == "empty_snapshot"
        assert failed_run.records_received == 0


def test_malformed_payload_does_not_deactivate_previously_synced_marketplaces() -> None:
    """A fully malformed response on a later sync attempt must never be
    treated as proof the seller left every marketplace — the earlier,
    successfully-synced rows must remain exactly as they were."""
    org_id = current_organization_id()
    connection_id = _connection_id(org_id)
    service = AmazonMarketplaceReconciliationService()

    first = service.reconcile(
        organization_id=org_id,
        connection_id=connection_id,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="A1SELLERID",
        participations=[_participation("ATVPDKIKX0DER"), _participation("A2EUQ1WTGCTBG2", store_name=None)],
    )
    assert first.succeeded is True

    outcome = service.reconcile(
        organization_id=org_id,
        connection_id=connection_id,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="A1SELLERID",
        participations=[_participation("   ")],
    )
    assert outcome.succeeded is False
    assert outcome.reason == "malformed_participations"

    with session_scope() as session:
        rows = {
            row.marketplace_id: row
            for row in AmazonMarketplaceParticipationRepository(session).list_for_seller_account(
                org_id, first.seller_account_id
            )
        }
        assert set(rows) == {"ATVPDKIKX0DER", "A2EUQ1WTGCTBG2"}
        assert rows["ATVPDKIKX0DER"].is_active is True
        assert rows["A2EUQ1WTGCTBG2"].is_active is True

        # The failed attempt is visible as a distinct, sanitized ingestion run —
        # not silently dropped as an INFO-only event with no persisted trace.
        # (Two runs created back-to-back can tie at SQLite's timestamp
        # resolution, so this checks statuses as a set rather than by
        # position — see the identical lesson in
        # test_amazon_ingestion_run_repository.py.)
        runs = AmazonIngestionRunRepository(session).list_for_connection(org_id, connection_id)
        assert len(runs) == 2
        statuses = {run.status for run in runs}
        assert statuses == {"succeeded", "failed"}
        failed_run = next(run for run in runs if run.status == "failed")
        assert failed_run.failure_class == "malformed_participations"


def test_organization_isolation_is_preserved_across_reconciliation() -> None:
    org_a = current_organization_id()
    org_b = uuid4()
    with session_scope() as session:
        session.add(Organization(id=org_b, name="Other"))
    connection_a = _connection_id(org_a)
    connection_b = _connection_id(org_b)
    service = AmazonMarketplaceReconciliationService()

    service.reconcile(
        organization_id=org_a,
        connection_id=connection_a,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="AORGAID",
        participations=[_participation("ATVPDKIKX0DER")],
    )
    service.reconcile(
        organization_id=org_b,
        connection_id=connection_b,
        region="na",
        environment="PRODUCTION",
        selling_partner_id="AORGBID",
        participations=[_participation("A2EUQ1WTGCTBG2")],
    )

    with session_scope() as session:
        assert len(AmazonSellerAccountRepository(session).list_for_org(org_a)) == 1
        assert len(AmazonSellerAccountRepository(session).list_for_org(org_b)) == 1
        assert AmazonSellerAccountRepository(session).list_for_org(org_a)[0].selling_partner_id == (
            "AORGAID"
        )


def test_reconciliation_never_calls_sp_api() -> None:
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "app" / "amazon" / "marketplace_reconciliation.py"
    ).read_text()
    tree = ast.parse(source)
    assert "httpx" not in {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    for marker in ("get_marketplace_participations", "sellingpartnerapi", "AmazonSpApiSellersClient"):
        assert marker not in source
