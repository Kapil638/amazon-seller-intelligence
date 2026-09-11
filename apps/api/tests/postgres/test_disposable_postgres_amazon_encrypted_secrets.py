"""pilot-deployment-ewise, correction 1 — Disposable PostgreSQL
validation for migration 0018 (`amazon_encrypted_secrets`).

Opt-in only. See `_guard.py` for the two conditions that must both hold
before anything here runs. Proves the real DDL (column types, primary
key, not-null) against genuine PostgreSQL — the AES-256-GCM encryption
logic itself is covered exhaustively against SQLite in
`tests/test_amazon_production_secrets.py`, which is dialect-independent;
this module only proves the schema PostgreSQL actually accepts and
enforces.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings
from tests.postgres import _guard

pytestmark = pytest.mark.skipif(bool(_guard.skip_reason()), reason=_guard.skip_reason() or "")

API_ROOT = Path(__file__).resolve().parents[2]


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


def test_empty_postgres_upgrade_produces_expected_secrets_schema(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0018_amazon_encrypted_secrets")

    inspector = inspect(disposable_engine)
    assert "amazon_encrypted_secrets" in set(inspector.get_table_names())
    columns = {c["name"]: c for c in inspector.get_columns("amazon_encrypted_secrets")}
    assert {"reference", "key_version", "nonce", "ciphertext", "created_at", "updated_at"}.issubset(columns)
    assert columns["reference"]["nullable"] is False
    assert columns["key_version"]["nullable"] is False
    assert columns["nonce"]["nullable"] is False
    assert columns["ciphertext"]["nullable"] is False
    pk = inspector.get_pk_constraint("amazon_encrypted_secrets")
    assert pk["constrained_columns"] == ["reference"]


def test_reference_primary_key_rejects_a_duplicate_insert(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0018_amazon_encrypted_secrets")

    insert = text(
        "INSERT INTO amazon_encrypted_secrets (reference, key_version, nonce, ciphertext) "
        "VALUES (:reference, 'v1', :nonce, :ciphertext)"
    )
    params = {"reference": "asi/amazon/SP_API/PRODUCTION/org/conn", "nonce": b"\x00" * 12, "ciphertext": b"\x01" * 32}
    with disposable_engine.begin() as conn:
        conn.execute(insert, params)
    with disposable_engine.begin() as conn:
        with pytest.raises(Exception):  # noqa: B017 - real IntegrityError, dialect-raised
            conn.execute(insert, params)


def test_ciphertext_and_nonce_round_trip_raw_bytes_exactly(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0018_amazon_encrypted_secrets")

    nonce = bytes(range(12))
    ciphertext = bytes(range(200, 232)) + b"\x00\xff"
    with disposable_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO amazon_encrypted_secrets (reference, key_version, nonce, ciphertext) "
                "VALUES ('asi/amazon/SP_API/PRODUCTION/org/conn2', 'v1', :nonce, :ciphertext)"
            ),
            {"nonce": nonce, "ciphertext": ciphertext},
        )
    with disposable_engine.begin() as conn:
        row = conn.execute(
            text("SELECT nonce, ciphertext FROM amazon_encrypted_secrets WHERE reference = 'asi/amazon/SP_API/PRODUCTION/org/conn2'")
        ).one()
        assert bytes(row.nonce) == nonce
        assert bytes(row.ciphertext) == ciphertext


def test_downgrade_0018_to_0017_drops_the_table_cleanly(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0018_amazon_encrypted_secrets")
        command.downgrade(cfg, "0017_inventory_heartbeat")

    inspector = inspect(disposable_engine)
    assert "amazon_encrypted_secrets" not in set(inspector.get_table_names())
