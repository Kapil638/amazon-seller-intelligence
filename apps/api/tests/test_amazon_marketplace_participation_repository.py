"""12B.2A — AmazonMarketplaceParticipationRepository. Schema foundation only."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import Organization
from app.persistence.repositories import (
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
)

FORBIDDEN_SECRET_FIELDS = ("refresh_token", "access_token", "client_secret", "client_id", "token_reference")


def test_multiple_marketplaces_can_belong_to_one_seller_account() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id="A1SELLERID"
        )
        repo = AmazonMarketplaceParticipationRepository(session)
        repo.create_or_reconcile(
            organization_id=org_id,
            seller_account_id=seller_account.id,
            marketplace_id="ATVPDKIKX0DER",
            region="na",
            country_code="US",
        )
        repo.create_or_reconcile(
            organization_id=org_id,
            seller_account_id=seller_account.id,
            marketplace_id="A2EUQ1WTGCTBG2",
            region="na",
            country_code="CA",
        )
        rows = repo.list_for_seller_account(org_id, seller_account.id)
        assert {row.marketplace_id for row in rows} == {"ATVPDKIKX0DER", "A2EUQ1WTGCTBG2"}
        for row in rows:
            for column in row.__table__.c.keys():
                assert column not in FORBIDDEN_SECRET_FIELDS


def test_same_marketplace_is_idempotently_unique_for_one_seller_account() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id="A1SELLERID"
        )
        repo = AmazonMarketplaceParticipationRepository(session)
        first = repo.create_or_reconcile(
            organization_id=org_id,
            seller_account_id=seller_account.id,
            marketplace_id="ATVPDKIKX0DER",
            region="na",
            store_name="Original Store",
        )
        second = repo.create_or_reconcile(
            organization_id=org_id,
            seller_account_id=seller_account.id,
            marketplace_id="ATVPDKIKX0DER",
            region="na",
            store_name="Renamed Store",
        )
        assert first.id == second.id
        assert second.store_name == "Renamed Store"
        rows = repo.list_for_seller_account(org_id, seller_account.id)
        assert len(rows) == 1


def test_same_marketplace_may_belong_to_different_seller_accounts() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        seller_repo = AmazonSellerAccountRepository(session)
        account_one = seller_repo.create_or_reconcile(
            organization_id=org_id, selling_partner_id="A1SELLERID"
        )
        account_two = seller_repo.create_or_reconcile(
            organization_id=org_id, selling_partner_id="A2SELLERID"
        )
        repo = AmazonMarketplaceParticipationRepository(session)
        repo.create_or_reconcile(
            organization_id=org_id,
            seller_account_id=account_one.id,
            marketplace_id="ATVPDKIKX0DER",
            region="na",
        )
        repo.create_or_reconcile(
            organization_id=org_id,
            seller_account_id=account_two.id,
            marketplace_id="ATVPDKIKX0DER",
            region="na",
        )
        assert len(repo.list_for_seller_account(org_id, account_one.id)) == 1
        assert len(repo.list_for_seller_account(org_id, account_two.id)) == 1


def test_cross_organization_seller_account_binding_is_rejected() -> None:
    org_a = current_organization_id()
    org_b = uuid4()
    with session_scope() as session:
        session.add(Organization(id=org_b, name="Other"))
        account_b = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_b, selling_partner_id="ABSELLERID"
        )
    with session_scope() as session:
        repo = AmazonMarketplaceParticipationRepository(session)
        with pytest.raises(TypeError):
            repo.create_or_reconcile(
                organization_id=org_a,
                seller_account_id=account_b.id,
                marketplace_id="ATVPDKIKX0DER",
                region="na",
            )


def test_display_domain_is_not_used_as_identity() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id="A1SELLERID"
        )
        repo = AmazonMarketplaceParticipationRepository(session)
        row = repo.create_or_reconcile(
            organization_id=org_id,
            seller_account_id=seller_account.id,
            marketplace_id="ATVPDKIKX0DER",
            region="na",
            domain_name="amazon.com",
        )
        assert row.marketplace_id == "ATVPDKIKX0DER"
        # domain_name is descriptive metadata; the unique constraint is on
        # (seller_account_id, marketplace_id), never on the display domain.
        column_names = {col.name for col in row.__table__.columns}
        assert "domain_name" in column_names
        unique_columns = {
            frozenset(constraint.columns.keys())
            for constraint in row.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert frozenset({"seller_account_id", "marketplace_id"}) in unique_columns
        assert not any("domain_name" in cols for cols in unique_columns)
