"""Disposable PostgreSQL validation for migration 0020
(`ads_campaign_state_enum`) — PR B1.

Opt-in only. See `_guard.py` for the two conditions that must both hold
before anything here runs. Proves, against genuine PostgreSQL (not just
the offline-SQL/ORM-metadata static check in
`tests/test_migration_chain_matches_orm_metadata.py`):

- `amazon_ads_campaigns.state` accepts the full officially documented
  `SponsoredProductsCampaign` enum after upgrading to 0020, and still
  rejects a value outside that enum (the constraint was widened, not
  removed);
- existing campaign rows survive the upgrade unchanged;
- the four sibling entity tables' state constraints are left exactly as
  0019 shipped them — 0020 must not widen anything but campaigns;
- `downgrade()` refuses when any campaign row holds a state the pre-0020
  three-value constraint cannot represent, and
- a clean 0020 -> 0019 -> 0020 round trip succeeds when every campaign's
  state is representable by both constraints.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from tests.postgres import _guard

pytestmark = pytest.mark.skipif(bool(_guard.skip_reason()), reason=_guard.skip_reason() or "")

API_ROOT = Path(__file__).resolve().parents[2]

_SIBLING_STATE_CONSTRAINTS = {
    "amazon_ads_ad_groups": "ck_amazon_ads_ad_groups_state",
    "amazon_ads_advertised_products": "ck_amazon_ads_advertised_products_state",
    "amazon_ads_keywords": "ck_amazon_ads_keywords_state",
    "amazon_ads_product_targets": "ck_amazon_ads_product_targets_state",
}


def _alembic_config(url: str) -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@contextmanager
def _alembic_environment(url: str):
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


@pytest.fixture
def disposable_engine():
    url = _guard.disposable_url()
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        if existing_tables - {"alembic_version"}:
            pytest.fail(
                "POSTGRES_DISPOSABLE_TEST_URL points at a non-empty database "
                f"({len(existing_tables)} existing table(s)) — refusing to run "
                "destructive migration tests against it. Use a genuinely fresh "
                "disposable instance."
            )
        yield engine
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        engine.dispose()


def _seed_profile_scope(engine) -> tuple[str, str]:
    """Returns (organization_id, ads_profile_id) — the minimum parent
    chain a campaign row's foreign keys require."""
    org_id = str(uuid.uuid4())
    connection_id = str(uuid.uuid4())
    profile_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO organizations (id, name) VALUES (:id, 'PR B1 Migration Test Org')"), {"id": org_id})
        conn.execute(
            text(
                "INSERT INTO amazon_ads_connections (id, organization_id, status) "
                "VALUES (:id, :org_id, 'not_connected')"
            ),
            {"id": connection_id, "org_id": org_id},
        )
        conn.execute(
            text(
                "INSERT INTO amazon_ads_profiles "
                "(id, organization_id, connection_id, profile_id, marketplace_country_code, "
                "currency_code, timezone, region) "
                "VALUES (:id, :org_id, :connection_id, '111', 'US', 'USD', 'America/Los_Angeles', 'NA')"
            ),
            {"id": profile_id, "org_id": org_id, "connection_id": connection_id},
        )
    return org_id, profile_id


def _insert_campaign(engine, *, org_id: str, profile_id: str, external_id: str, state: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO amazon_ads_campaigns "
                "(id, organization_id, ads_profile_id, external_campaign_id, name, state) "
                "VALUES (:id, :org_id, :profile_id, :external_id, 'Test Campaign', :state)"
            ),
            {
                "id": str(uuid.uuid4()),
                "org_id": org_id,
                "profile_id": profile_id,
                "external_id": external_id,
                "state": state,
            },
        )


def test_upgrade_to_0020_widens_the_campaign_state_check_constraint(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0019_amazon_ads_foundation")

    org_id, profile_id = _seed_profile_scope(disposable_engine)

    # Before 0020: PROPOSED is rejected by the narrower 0019 constraint.
    with pytest.raises(IntegrityError):
        _insert_campaign(disposable_engine, org_id=org_id, profile_id=profile_id, external_id="c-proposed", state="PROPOSED")

    with _alembic_environment(url):
        command.upgrade(cfg, "0020_ads_campaign_state_enum")

    # After 0020: every officially documented value is accepted.
    for state in ("ENABLED", "PAUSED", "ARCHIVED", "PROPOSED", "ENABLING", "USER_DELETED", "OTHER"):
        _insert_campaign(disposable_engine, org_id=org_id, profile_id=profile_id, external_id=f"c-{state.lower()}", state=state)

    with disposable_engine.begin() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM amazon_ads_campaigns WHERE ads_profile_id = :profile_id"),
            {"profile_id": profile_id},
        ).scalar_one()
    assert count == 7

    # The constraint was widened, not removed — a value outside even the
    # documented seven is still rejected.
    with pytest.raises(IntegrityError):
        _insert_campaign(disposable_engine, org_id=org_id, profile_id=profile_id, external_id="c-bogus", state="BOGUS")


def test_0020_upgrade_preserves_existing_campaign_rows(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0019_amazon_ads_foundation")

    org_id, profile_id = _seed_profile_scope(disposable_engine)
    _insert_campaign(disposable_engine, org_id=org_id, profile_id=profile_id, external_id="c-preexisting", state="ENABLED")

    with _alembic_environment(url):
        command.upgrade(cfg, "0020_ads_campaign_state_enum")

    with disposable_engine.begin() as conn:
        row = conn.execute(
            text("SELECT state FROM amazon_ads_campaigns WHERE external_campaign_id = 'c-preexisting'")
        ).one()
    assert row.state == "ENABLED"


def test_0020_leaves_sibling_entity_state_constraints_unchanged(disposable_engine) -> None:
    """0020 must widen ONLY the campaign state constraint — every sibling
    entity table's constraint must reject the same wider values exactly
    as it did under 0019."""
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0020_ads_campaign_state_enum")

    inspector = inspect(disposable_engine)
    for table, constraint_name in _SIBLING_STATE_CONSTRAINTS.items():
        check_constraints = {c["name"]: c["sqltext"] for c in inspector.get_check_constraints(table)}
        assert constraint_name in check_constraints, f"{table} lost its state check constraint"
        sqltext = check_constraints[constraint_name]
        assert "PROPOSED" not in sqltext, f"{table}'s state constraint was unexpectedly widened"
        for expected in ("ENABLED", "PAUSED", "ARCHIVED"):
            assert expected in sqltext


def test_downgrade_refuses_when_a_campaign_holds_an_unrepresentable_state(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0020_ads_campaign_state_enum")

        org_id, profile_id = _seed_profile_scope(disposable_engine)
        _insert_campaign(disposable_engine, org_id=org_id, profile_id=profile_id, external_id="c-proposed", state="PROPOSED")

        with pytest.raises(RuntimeError, match="Refusing to downgrade 0020"):
            command.downgrade(cfg, "0019_amazon_ads_foundation")

    # The refusal must leave the wider (0020) constraint in place — not a
    # half-applied downgrade.
    inspector = inspect(disposable_engine)
    check_constraints = {c["name"]: c["sqltext"] for c in inspector.get_check_constraints("amazon_ads_campaigns")}
    assert "PROPOSED" in check_constraints["ck_amazon_ads_campaigns_state"]


def test_0020_to_0019_to_0020_round_trip_when_states_are_representable(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0020_ads_campaign_state_enum")

        org_id, profile_id = _seed_profile_scope(disposable_engine)
        _insert_campaign(disposable_engine, org_id=org_id, profile_id=profile_id, external_id="c-enabled", state="ENABLED")

        command.downgrade(cfg, "0019_amazon_ads_foundation")

        inspector = inspect(disposable_engine)
        check_constraints = {c["name"]: c["sqltext"] for c in inspector.get_check_constraints("amazon_ads_campaigns")}
        assert "PROPOSED" not in check_constraints["ck_amazon_ads_campaigns_state"]

        with disposable_engine.begin() as conn:
            row = conn.execute(
                text("SELECT state FROM amazon_ads_campaigns WHERE external_campaign_id = 'c-enabled'")
            ).one()
        assert row.state == "ENABLED"

        command.upgrade(cfg, "0020_ads_campaign_state_enum")

    inspector = inspect(disposable_engine)
    check_constraints = {c["name"]: c["sqltext"] for c in inspector.get_check_constraints("amazon_ads_campaigns")}
    assert "PROPOSED" in check_constraints["ck_amazon_ads_campaigns_state"]
