"""12B.2A — AmazonIngestionRunRepository. Foundation only; no live ingestion."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import Organization
from app.persistence.repositories import (
    AmazonIngestionRunRepository,
    AmazonSellerAccountRepository,
)


def test_start_creates_a_run_scoped_to_org_and_seller_account() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id="A1SELLERID"
        )
        run = AmazonIngestionRunRepository(session).start(
            organization_id=org_id,
            domain="sellers_marketplace_participations",
            region="na",
            environment="PRODUCTION",
            seller_account_id=seller_account.id,
        )
        assert run.id is not None
        assert run.organization_id == org_id
        assert run.seller_account_id == seller_account.id
        assert run.status == "started"
        assert run.records_received == 0
        assert run.completed_at is None


def test_cross_organization_seller_account_association_is_rejected() -> None:
    org_a = current_organization_id()
    org_b = uuid4()
    with session_scope() as session:
        session.add(Organization(id=org_b, name="Other"))
        account_b = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_b, selling_partner_id="ABSELLERID"
        )
    with session_scope() as session:
        with pytest.raises(TypeError):
            AmazonIngestionRunRepository(session).start(
                organization_id=org_a,
                domain="sellers_marketplace_participations",
                region="na",
                environment="PRODUCTION",
                seller_account_id=account_b.id,
            )


def test_valid_statuses_and_counters_behave_correctly() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        run = AmazonIngestionRunRepository(session).start(
            organization_id=org_id,
            domain="sellers_marketplace_participations",
            region="na",
            environment="PRODUCTION",
        )
        run_id = run.id
    with session_scope() as session:
        repo = AmazonIngestionRunRepository(session)
        updated = repo.complete(
            org_id,
            run_id,
            status="succeeded",
            records_received=3,
            records_accepted=3,
            records_rejected=0,
        )
        assert updated is not None
        assert updated.status == "succeeded"
        assert updated.completed_at is not None
        assert updated.records_received == 3
        assert updated.records_accepted == 3
        assert updated.records_rejected == 0
    with session_scope() as session:
        with pytest.raises(TypeError):
            AmazonIngestionRunRepository(session).complete(org_id, run_id, status="bogus_status")


def test_partial_failure_can_be_represented() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        run = AmazonIngestionRunRepository(session).start(
            organization_id=org_id,
            domain="sellers_marketplace_participations",
            region="na",
            environment="PRODUCTION",
        )
        run_id = run.id
    with session_scope() as session:
        updated = AmazonIngestionRunRepository(session).complete(
            org_id,
            run_id,
            status="partial",
            records_received=5,
            records_accepted=3,
            records_rejected=2,
            retry_count=1,
            failure_class="rate_limited",
            pagination_complete=False,
        )
        assert updated is not None
        assert updated.status == "partial"
        assert updated.records_rejected == 2
        assert updated.failure_class == "rate_limited"
        assert updated.pagination_complete is False


def test_list_for_connection_is_scoped_and_ordered_latest_first() -> None:
    from datetime import UTC, datetime

    from app.persistence.repositories import AmazonConnectionRepository

    org_id = current_organization_id()
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).create(
            organization_id=org_id, provider="SP_API", environment="PRODUCTION", region="na"
        )
        other_connection = AmazonConnectionRepository(session).create(
            organization_id=org_id, provider="SP_API", environment="SANDBOX", region="na"
        )
        connection_id = connection.id
        other_connection_id = other_connection.id
    with session_scope() as session:
        repo = AmazonIngestionRunRepository(session)
        first = repo.start(
            organization_id=org_id, domain="d", region="na", environment="PRODUCTION",
            connection_id=connection_id,
        )
        first_id = first.id
        # `started_at` is a database-generated timestamp with only
        # second-level resolution on SQLite; two rows created back-to-back
        # inside one test can legitimately tie. Backdate this one explicitly
        # so the assertion below is deterministic rather than depending on
        # the (intentionally unordered, UUID-based) id tie-break that
        # `list_for_connection` falls back on for genuine ties.
        first.started_at = datetime(2020, 1, 1, tzinfo=UTC)
    with session_scope() as session:
        repo = AmazonIngestionRunRepository(session)
        second = repo.start(
            organization_id=org_id, domain="d", region="na", environment="PRODUCTION",
            connection_id=connection_id,
        )
        second_id = second.id
        repo.start(
            organization_id=org_id, domain="d", region="na", environment="SANDBOX",
            connection_id=other_connection_id,
        )
    with session_scope() as session:
        runs = AmazonIngestionRunRepository(session).list_for_connection(org_id, connection_id)
        assert [run.id for run in runs] == [second_id, first_id]


def test_organization_a_cannot_retrieve_organization_b_ingestion_run() -> None:
    org_a = current_organization_id()
    org_b = uuid4()
    with session_scope() as session:
        session.add(Organization(id=org_b, name="Other"))
        run_b = AmazonIngestionRunRepository(session).start(
            organization_id=org_b,
            domain="sellers_marketplace_participations",
            region="na",
            environment="PRODUCTION",
        )
    with session_scope() as session:
        repo = AmazonIngestionRunRepository(session)
        assert repo.get_by_id(org_a, run_b.id) is None
        assert repo.get_by_id(org_b, run_b.id) is not None
        assert repo.list_for_org(org_a) == []


def test_no_live_ingestion_occurs() -> None:
    """Starting or completing a run is bookkeeping only; it must never call SP-API."""
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "app" / "persistence" / "repositories.py"
    ).read_text()
    tree = ast.parse(source)
    ingestion_run_repo = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "AmazonIngestionRunRepository"
    )
    class_source = ast.get_source_segment(source, ingestion_run_repo) or ""
    for marker in ("httpx", "get_marketplace_participations", "sellingpartnerapi"):
        assert marker not in class_source
