"""pilot-deployment-ewise, correction 1 — tests for
`app.amazon.secret_migration_admin`, the operator-only, one-off
development-store -> production-backend secret migration CLI. Every
value used here is a synthetic test token, never a real one — this
milestone documents and tests the migration path without executing it
against the real local seller token.
"""

from __future__ import annotations

import base64
import json
import os

import pytest
from pydantic import SecretStr

from app.amazon.secret_migration_admin import (
    DRY_RUN_FOUND_MESSAGE,
    MIGRATED_MESSAGE,
    NOT_FOUND_MESSAGE,
    main,
    migrate_reference,
)
from app.amazon.secrets import DevelopmentSecretProvider, SecretProviderFactory, build_asi_secret_reference
from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID, Settings, get_settings

TEST_TOKEN = "Atzr|test-migration-token"
KEY_V1 = base64.b64encode(b"9" * 32).decode("ascii")
REFERENCE = build_asi_secret_reference(
    provider="SP_API",
    environment="PRODUCTION",
    organization_id=DEFAULT_DEVELOPMENT_ORGANIZATION_ID,
    connection_id="c1111111-1111-1111-1111-111111111111",
)


@pytest.fixture(autouse=True)
def _clean_db_runtime_context():
    os.environ.pop("ASI_DB_RUNTIME_CONTEXT", None)
    yield
    os.environ.pop("ASI_DB_RUNTIME_CONTEXT", None)


def _seed_development_store(path) -> None:
    provider = DevelopmentSecretProvider(store_path=str(path))
    provider.put_secret(REFERENCE, SecretStr(TEST_TOKEN))


def _set_production_settings(monkeypatch) -> None:
    monkeypatch.setenv("AMAZON_SECRET_BACKEND", "production")
    monkeypatch.setenv("AMAZON_SECRET_ENCRYPTION_KEYS", json.dumps({"v1": KEY_V1}))
    monkeypatch.setenv("AMAZON_SECRET_ACTIVE_KEY_VERSION", "v1")
    get_settings.cache_clear()


def test_migrate_reference_not_found_when_store_is_empty(tmp_path) -> None:
    store = tmp_path / "secrets.json"
    result = migrate_reference(REFERENCE, development_store=str(store), dry_run=True)
    assert result == NOT_FOUND_MESSAGE


def test_migrate_reference_dry_run_writes_nothing(tmp_path, monkeypatch) -> None:
    store = tmp_path / "secrets.json"
    _seed_development_store(store)
    _set_production_settings(monkeypatch)
    try:
        result = migrate_reference(REFERENCE, development_store=str(store), dry_run=True)
        assert result == DRY_RUN_FOUND_MESSAGE
        target = SecretProviderFactory().create(get_settings())
        assert target.exists(REFERENCE) is False
    finally:
        get_settings.cache_clear()


def test_migrate_reference_writes_to_the_configured_production_backend(tmp_path, monkeypatch) -> None:
    store = tmp_path / "secrets.json"
    _seed_development_store(store)
    _set_production_settings(monkeypatch)
    try:
        result = migrate_reference(REFERENCE, development_store=str(store), dry_run=False)
        assert result == MIGRATED_MESSAGE
        target = SecretProviderFactory().create(get_settings())
        assert target.get_secret(REFERENCE).get_secret_value() == TEST_TOKEN
    finally:
        get_settings.cache_clear()


def test_main_declares_the_admin_db_runtime_context_before_parsing_args() -> None:
    exit_code = main(["migrate", "--reference", "", "--development-store", "/nonexistent"])
    assert exit_code == 1
    assert os.environ.get("ASI_DB_RUNTIME_CONTEXT") == "admin"


def test_main_dry_run_never_prints_the_secret_value(tmp_path, monkeypatch, capsys) -> None:
    store = tmp_path / "secrets.json"
    _seed_development_store(store)
    _set_production_settings(monkeypatch)
    try:
        exit_code = main(
            ["migrate", "--reference", REFERENCE, "--development-store", str(store), "--dry-run"]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert TEST_TOKEN not in captured.out
        assert "Atzr|" not in captured.out
        assert REFERENCE in captured.out
        assert DRY_RUN_FOUND_MESSAGE in captured.out
    finally:
        get_settings.cache_clear()


def test_main_migrate_never_prints_the_secret_value(tmp_path, monkeypatch, capsys) -> None:
    store = tmp_path / "secrets.json"
    _seed_development_store(store)
    _set_production_settings(monkeypatch)
    try:
        exit_code = main(["migrate", "--reference", REFERENCE, "--development-store", str(store)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert TEST_TOKEN not in captured.out
        assert "Atzr|" not in captured.out
        assert MIGRATED_MESSAGE in captured.out
    finally:
        get_settings.cache_clear()


def test_main_reports_failure_without_leaking_details_when_backend_misconfigured(
    tmp_path, monkeypatch, capsys
) -> None:
    store = tmp_path / "secrets.json"
    _seed_development_store(store)
    # production selected but no encryption keys configured — must fail
    # closed, never fall back to development or silently succeed.
    monkeypatch.setenv("AMAZON_SECRET_BACKEND", "production")
    monkeypatch.delenv("AMAZON_SECRET_ENCRYPTION_KEYS", raising=False)
    get_settings.cache_clear()
    try:
        exit_code = main(["migrate", "--reference", REFERENCE, "--development-store", str(store)])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert TEST_TOKEN not in captured.out
        assert "Migration failed" in captured.out
    finally:
        get_settings.cache_clear()
