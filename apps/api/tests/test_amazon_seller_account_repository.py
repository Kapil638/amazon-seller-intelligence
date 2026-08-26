"""12B.2A — AmazonSellerAccountRepository. Schema foundation only; no ingest."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import Organization
from app.persistence.repositories import (
    AmazonSellerAccountRepository,
    SellerAccountOwnershipConflict,
)

FORBIDDEN_SECRET_FIELDS = ("refresh_token", "access_token", "client_secret", "client_id", "token_reference")


def test_create_seller_account_succeeds() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        repo = AmazonSellerAccountRepository(session)
        row = repo.create_or_reconcile(organization_id=org_id, selling_partner_id="A1SELLERID")
        assert row.id is not None
        assert row.organization_id == org_id
        assert row.selling_partner_id == "A1SELLERID"
        assert row.status == "active"
        assert row.first_seen_at is not None
        for column in row.__table__.c.keys():
            assert column not in FORBIDDEN_SECRET_FIELDS


def test_one_organization_can_own_multiple_seller_accounts() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        repo = AmazonSellerAccountRepository(session)
        first = repo.create_or_reconcile(organization_id=org_id, selling_partner_id="A1FIRSTID")
        second = repo.create_or_reconcile(organization_id=org_id, selling_partner_id="A2SECONDID")
        assert first.id != second.id
        accounts = repo.list_for_org(org_id)
        assert {account.selling_partner_id for account in accounts} == {"A1FIRSTID", "A2SECONDID"}


def test_reconcile_same_identifier_same_organization_reuses_account() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        repo = AmazonSellerAccountRepository(session)
        created = repo.create_or_reconcile(organization_id=org_id, selling_partner_id="A1SELLERID")
        reconciled = repo.create_or_reconcile(
            organization_id=org_id,
            selling_partner_id="A1SELLERID",
            display_store_name="Updated Store Name",
        )
        assert reconciled.id == created.id
        assert reconciled.display_store_name == "Updated Store Name"
        accounts = repo.list_for_org(org_id)
        assert len(accounts) == 1


def test_selling_partner_id_is_globally_unique_across_organizations() -> None:
    org_a = current_organization_id()
    org_b = uuid4()
    with session_scope() as session:
        session.add(Organization(id=org_b, name="Other"))
        AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_a, selling_partner_id="ASHAREDID"
        )
    with session_scope() as session:
        repo = AmazonSellerAccountRepository(session)
        with pytest.raises(SellerAccountOwnershipConflict) as excinfo:
            repo.create_or_reconcile(organization_id=org_b, selling_partner_id="ASHAREDID")
    # The conflict must never disclose which organization already owns it.
    assert str(org_a) not in str(excinfo.value)
    with session_scope() as session:
        accounts = AmazonSellerAccountRepository(session).list_for_org(org_b)
        assert accounts == []
        owner_accounts = AmazonSellerAccountRepository(session).list_for_org(org_a)
        assert len(owner_accounts) == 1


def test_organization_a_cannot_retrieve_organization_b_seller_account() -> None:
    org_a = current_organization_id()
    org_b = uuid4()
    with session_scope() as session:
        session.add(Organization(id=org_b, name="Other"))
        row_b = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_b, selling_partner_id="ABSELLERID"
        )
    with session_scope() as session:
        repo = AmazonSellerAccountRepository(session)
        assert repo.get_by_id(org_a, row_b.id) is None
        assert repo.get_by_id(org_b, row_b.id) is not None
        assert repo.list_for_org(org_a) == []


def test_seller_account_requires_non_empty_identifier() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        repo = AmazonSellerAccountRepository(session)
        with pytest.raises(TypeError):
            repo.create_or_reconcile(organization_id=org_id, selling_partner_id="")
        with pytest.raises(TypeError):
            repo.create_or_reconcile(organization_id=org_id, selling_partner_id="   ")
