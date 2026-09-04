"""12B.5B final safety/bounded-evidence review — regression tests for the
fail-closed production-database guard in `app.persistence.database`.

Background: an ad-hoc `uv run python -c "..."` diagnostic script
accidentally resolved this project's real Supabase `DATABASE_URL`
(inherited the same way any script importing this module does, from the
developer's own shell/`.env`) and attempted a write; `session_scope()`'s
rollback-on-exception only absorbed it because that specific write
happened to collide with an existing row.

**Design history, relevant to why these tests are shaped the way they
are:** the first version of this guard authorized a database connection
by checking whether `app.main` had been imported (an in-memory flag set
at import time). That was replaced, not patched, because it broke the
Listings/Orders workers and the Listings job admin CLI (each a separate
OS process that never imports `app.main`) while simultaneously being
disable-able by any script that merely imported `app.main` for an
unrelated reason (e.g. `tests/conftest.py`'s own `TestClient` usage).
The replacement is `ASI_DB_RUNTIME_CONTEXT`, an environment variable
each legitimate process's own launcher/entry point sets explicitly —
never inferred from an import.

None of these tests ever open a real network connection or touch
Supabase — `create_engine`/`_bootstrap_organization` are monkeypatched
out wherever a test needs to exercise `get_engine()`'s real body past
the guard check, and every URL used is a synthetic placeholder.
"""

from __future__ import annotations

import os

import pytest

from app.persistence import database as database_module
from app.persistence.database import (
    ProductionDatabaseGuardError,
    _guard_engine_creation,
    _looks_like_production_database,
    _production_database_access_authorized,
    _runtime_context,
)

_CONTEXT_ENV_VAR = "ASI_DB_RUNTIME_CONTEXT"
_OVERRIDE_ENV_VAR = "ASI_ALLOW_PRODUCTION_DB_ACCESS"
_FAKE_REMOTE_URL = "postgresql://user:pass@db.example-project-ref.supabase.co:5432/postgres"


@pytest.fixture(autouse=True)
def _clean_guard_env(monkeypatch):
    """Every test in this file gets a clean `ASI_DB_RUNTIME_CONTEXT`/
    `ASI_ALLOW_PRODUCTION_DB_ACCESS` slate via `monkeypatch`, which
    reverts both automatically at teardown — no test here can leak
    authorization state into another test or into the rest of the
    pytest session."""
    monkeypatch.delenv(_CONTEXT_ENV_VAR, raising=False)
    monkeypatch.delenv(_OVERRIDE_ENV_VAR, raising=False)


# --- _looks_like_production_database ----------------------------------------


def test_sqlite_is_never_treated_as_production_like() -> None:
    assert _looks_like_production_database("sqlite://") is False
    assert _looks_like_production_database("sqlite:////tmp/some.db") is False


def test_empty_url_is_never_treated_as_production_like() -> None:
    assert _looks_like_production_database("") is False


def test_loopback_postgres_is_not_treated_as_production_like() -> None:
    assert _looks_like_production_database("postgresql://user:pass@localhost:5432/asi_test") is False
    assert _looks_like_production_database("postgresql://user:pass@127.0.0.1:5432/asi_test") is False


def test_remote_postgres_host_is_treated_as_production_like() -> None:
    # A representative shape for a hosted Postgres/Supabase URL — the
    # exact host is a synthetic placeholder, never a real project
    # reference, and is asserted never to leak into any exception text
    # below.
    assert _looks_like_production_database(_FAKE_REMOTE_URL) is True


def test_unparseable_url_fails_closed_as_production_like() -> None:
    # A malformed URL cannot be affirmatively proven safe, so it is
    # treated the same as any other non-loopback host.
    assert _looks_like_production_database("postgresql://[::not-a-valid-host") is True


# --- _guard_engine_creation: context-based authorization --------------------


def test_guard_raises_for_a_remote_database_with_no_context_set() -> None:
    with pytest.raises(ProductionDatabaseGuardError):
        _guard_engine_creation(_FAKE_REMOTE_URL)


def test_guard_raises_for_a_remote_database_with_an_unrecognized_context(monkeypatch) -> None:
    monkeypatch.setenv(_CONTEXT_ENV_VAR, "some_unrelated_diagnostic_script")
    with pytest.raises(ProductionDatabaseGuardError):
        _guard_engine_creation(_FAKE_REMOTE_URL)


@pytest.mark.parametrize("context", ["api", "listings_worker", "orders_worker"])
def test_guard_allows_a_remote_database_for_each_non_admin_recognized_context(monkeypatch, context) -> None:
    monkeypatch.setenv(_CONTEXT_ENV_VAR, context)
    # Does not raise.
    _guard_engine_creation(_FAKE_REMOTE_URL)


def test_guard_admin_context_alone_is_not_sufficient_for_a_remote_database(monkeypatch) -> None:
    """The remediation review's explicit requirement: a controlled
    production administrative operation needs the *additional* narrow
    opt-in, not just the "admin" context label — unlike the API and the
    two workers, which are authorized by context alone."""
    monkeypatch.setenv(_CONTEXT_ENV_VAR, "admin")
    with pytest.raises(ProductionDatabaseGuardError):
        _guard_engine_creation(_FAKE_REMOTE_URL)


def test_guard_admin_context_with_explicit_override_is_sufficient(monkeypatch) -> None:
    monkeypatch.setenv(_CONTEXT_ENV_VAR, "admin")
    monkeypatch.setenv(_OVERRIDE_ENV_VAR, "1")
    _guard_engine_creation(_FAKE_REMOTE_URL)  # does not raise


def test_guard_allows_a_remote_database_with_explicit_narrow_override_and_no_context(monkeypatch) -> None:
    assert _production_database_access_authorized() is False
    monkeypatch.setenv(_OVERRIDE_ENV_VAR, "1")
    assert _production_database_access_authorized() is True
    # An unclassified process may still opt in explicitly for one
    # reviewed, intentional production operation.
    _guard_engine_creation(_FAKE_REMOTE_URL)  # does not raise


def test_guard_override_requires_the_exact_value(monkeypatch) -> None:
    monkeypatch.setenv(_OVERRIDE_ENV_VAR, "true")  # not the exact required value "1"
    assert _production_database_access_authorized() is False
    with pytest.raises(ProductionDatabaseGuardError):
        _guard_engine_creation(_FAKE_REMOTE_URL)


def test_guard_never_blocks_a_local_sqlite_url_regardless_of_context() -> None:
    # The guard must never interfere with the sqlite:// path every test
    # in this suite already depends on, context or no context.
    _guard_engine_creation("sqlite://")


def test_guard_exception_never_contains_the_database_url() -> None:
    url = "postgresql://user:secret-password@db.example-project-ref.supabase.co:5432/postgres"
    with pytest.raises(ProductionDatabaseGuardError) as excinfo:
        _guard_engine_creation(url)
    message = str(excinfo.value)
    assert url not in message
    assert "secret-password" not in message
    assert "example-project-ref" not in message


def test_runtime_context_reads_directly_from_the_environment(monkeypatch) -> None:
    assert _runtime_context() == ""
    monkeypatch.setenv(_CONTEXT_ENV_VAR, "  api  ")
    assert _runtime_context() == "api"  # stripped, never raw whitespace-padded


# --- Real engine-resolution path: `get_engine()` itself, for every -----------
# --- legitimate runtime context, and for an unclassified diagnostic ---------
# --- process. No network connection is ever made — `create_engine` and -----
# --- `_bootstrap_organization` are replaced with no-ops so this exercises --
# --- `get_engine()`'s own real body (URL resolution -> guard -> engine -----
# --- construction) without ever reaching real I/O. --------------------------


@pytest.fixture
def _fake_remote_engine_resolution(monkeypatch):
    """Makes `get_engine()`'s real, unmodified code resolve to the
    synthetic remote URL above and construct a fake, inert "engine"
    object instead of a real SQLAlchemy engine — so calling the actual
    `get_engine()` function proves the guard's decision without any
    network I/O, against Supabase or anything else."""
    monkeypatch.setattr(database_module, "sqlalchemy_database_url", lambda *a, **k: _FAKE_REMOTE_URL)
    monkeypatch.setattr(database_module, "create_engine", lambda *a, **k: object())
    monkeypatch.setattr(database_module, "_bootstrap_organization", lambda engine: None)
    database_module.get_engine.cache_clear()
    yield
    database_module.get_engine.cache_clear()


def test_real_get_engine_succeeds_for_the_api_context(monkeypatch, _fake_remote_engine_resolution) -> None:
    monkeypatch.setenv(_CONTEXT_ENV_VAR, "api")
    engine = database_module.get_engine()
    assert engine is not None


def test_real_get_engine_succeeds_for_the_listings_worker_context(monkeypatch, _fake_remote_engine_resolution) -> None:
    monkeypatch.setenv(_CONTEXT_ENV_VAR, "listings_worker")
    engine = database_module.get_engine()
    assert engine is not None


def test_real_get_engine_succeeds_for_the_orders_worker_context(monkeypatch, _fake_remote_engine_resolution) -> None:
    monkeypatch.setenv(_CONTEXT_ENV_VAR, "orders_worker")
    engine = database_module.get_engine()
    assert engine is not None


def test_real_get_engine_succeeds_for_the_admin_context_with_override(
    monkeypatch, _fake_remote_engine_resolution
) -> None:
    monkeypatch.setenv(_CONTEXT_ENV_VAR, "admin")
    monkeypatch.setenv(_OVERRIDE_ENV_VAR, "1")
    engine = database_module.get_engine()
    assert engine is not None


def test_real_get_engine_fails_closed_for_an_unclassified_diagnostic_process(
    monkeypatch, _fake_remote_engine_resolution
) -> None:
    # No context, no override — exactly the shape of the original
    # incident's ad-hoc `uv run python -c "..."` script.
    with pytest.raises(ProductionDatabaseGuardError):
        database_module.get_engine()


def test_real_get_engine_fails_closed_for_admin_context_alone(monkeypatch, _fake_remote_engine_resolution) -> None:
    monkeypatch.setenv(_CONTEXT_ENV_VAR, "admin")
    with pytest.raises(ProductionDatabaseGuardError):
        database_module.get_engine()
