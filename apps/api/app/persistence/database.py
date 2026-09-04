from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache
from urllib.parse import urlsplit

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.persistence.models import Base, Organization

_SessionLocal: sessionmaker[Session] | None = None

# **This is a safety interlock against accidental misuse, not an
# authentication mechanism or a security boundary.** `ASI_DB_RUNTIME_
# CONTEXT`/`ASI_ALLOW_PRODUCTION_DB_ACCESS` are plain environment
# variables: anyone with shell access to a process — the same access
# already required to read `DATABASE_URL` itself, or `.env`, or any
# other secret this process can see — can set either one trivially, with
# no password, token, or cryptographic proof involved. This guard does
# not, and is not intended to, stop a determined or malicious actor who
# already has that level of access; it exists purely to make the
# specific failure mode from the incident below (an *unintentional*
# ad-hoc script silently reusing a real production connection) require
# a deliberate, legible action instead of happening by default. Treat it
# the same way this codebase already treats `ASI_LISTINGS_WORKER_
# ENABLED`/`ASI_ORDERS_WORKER_ENABLED` (the same class of interlock,
# not a credential) — never as a substitute for real access control
# over who can reach this machine/process/shell at all.
#
# 12B.5B remediation (Section 8, then corrected in the follow-up
# safety/bounded-evidence review): an ad-hoc `uv run python -c "..."`
# diagnostic script accidentally resolved this project's real Supabase
# `DATABASE_URL` (inherited from the developer's own shell/`.env`, the
# same way any script importing this module does) and attempted a
# write. `session_scope()`'s rollback-on-exception behavior absorbed it
# only because that particular write happened to collide with an
# existing row — a script whose write did not collide would have
# committed for real.
#
# **The first fix for this (an `_api_process_started` flag set at
# import time by `app.main`) was itself unsafe and was replaced, not
# patched:** it broke the Listings/Orders workers and the Listings job
# admin CLI — each is a *separate OS process* that never imports
# `app.main` at all, so the flag could never be true for them even when
# legitimately, deliberately run against a real database — and in the
# other direction, any diagnostic-style script that merely did
# `from app.main import app` (exactly what `tests/conftest.py` already
# does, for its own unrelated `TestClient` needs) would have silently
# *disabled* the protection, the opposite of what a fail-closed guard
# must do. A Python import is not a legitimacy signal: it is transitive,
# easy to trigger by accident, and says nothing about how or why a
# process was actually started.
#
# **Replacement design: an explicit environment variable the process's
# own launcher sets, never a side effect of what any module happens to
# import.** `ASI_DB_RUNTIME_CONTEXT` must be set, by the command that
# starts the process, to one of the values in
# `_RECOGNIZED_DB_RUNTIME_CONTEXTS` below, before that process ever
# calls `get_engine()` against a non-loopback database:
#
# - `"api"` — set by the actual launch command (`./scripts/dev.sh` and
#   the documented manual `uv run uvicorn app.main:app ...` command in
#   `docs/AI_HANDOVER/14_LOCAL_DEVELOPMENT_SETUP.md`), never by
#   `app/main.py`'s own code. `app.main` performs no authorization side
#   effect at all — merely importing it (for a `TestClient`, a
#   diagnostic script, anything) changes nothing here.
# - `"listings_worker"` / `"orders_worker"` — set by each worker's own
#   `main()`, and only *after* that worker's own pre-existing, already-
#   fail-closed `ASI_LISTINGS_WORKER_ENABLED`/`ASI_ORDERS_WORKER_ENABLED`
#   gate has already been confirmed true. This is not a new opt-in
#   surface: a worker that was not already explicitly authorized to run
#   at all never reaches the line that sets this.
# - `"admin"` — set by `app.amazon.listings_job_admin`'s own `main()`,
#   which only ever runs when an operator deliberately invokes it with
#   required, non-defaulted `--organization-id`/`--run-id` arguments.
#   Administrative operations additionally still require the narrow
#   `ASI_ALLOW_PRODUCTION_DB_ACCESS` override below when the target is a
#   real remote database — self-declaring as `"admin"` alone is not
#   sufficient for this one context, matching the explicit-opt-in
#   requirement for controlled production administrative operations.
#
# Tests never need to set this: `tests/conftest.py` forces
# `DATABASE_URL=sqlite://`, and SQLite is always exempt from this guard
# regardless of context. Alembic never needs it either: `migrations/
# env.py` builds its own engine directly from `sqlalchemy_database_url
# ()` via `engine_from_config` and never calls `get_engine()`/
# `session_scope()` at all, so this guard cannot affect a migration
# either way. An unrecognized or unset value is treated as "unclassified
# diagnostic process" and fails closed against any non-loopback
# database, exactly like the original incident's ad-hoc script should
# have.
_DB_RUNTIME_CONTEXT_ENV_VAR = "ASI_DB_RUNTIME_CONTEXT"
_RECOGNIZED_DB_RUNTIME_CONTEXTS = frozenset({"api", "listings_worker", "orders_worker", "admin"})

# Narrow, explicit, session-scoped opt-in for a genuinely authorized
# one-off production operation (e.g. a reviewed on-call script, or the
# `"admin"` context above) that needs to reach a real remote database.
# Deliberately an environment variable the operator sets on their own
# shell for that one invocation — never written to `.env`, never a
# persistent default, never inferred from any other setting.
_PRODUCTION_DB_OVERRIDE_ENV_VAR = "ASI_ALLOW_PRODUCTION_DB_ACCESS"


class ProductionDatabaseGuardError(RuntimeError):
    """Raised when an unclassified process tries to open a database
    engine against what looks like a real (non-local, non-SQLite)
    database without explicit authorization. Never carries the URL,
    host, or any credential — see `_looks_like_production_database`'s
    own docstring for why."""


def _runtime_context() -> str:
    return os.environ.get(_DB_RUNTIME_CONTEXT_ENV_VAR, "").strip()


def _looks_like_production_database(url: str) -> bool:
    """SQLite is always disposable/local by construction. A Postgres (or
    other network) URL is treated as "production-like" unless its host
    is a loopback address — a locally running Postgres (e.g. a
    docker-compose instance for local integration testing) is the one
    non-SQLite case this project already trusts a developer to run
    disposable data against directly. Never returns or logs any part of
    `url` itself — only a boolean — so a caller of this function can
    never accidentally leak the URL through this check's own return
    value or an exception message built from it."""
    if not url or url.startswith("sqlite"):
        return False
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        # An unparseable URL is not affirmatively "safe" — treat it the
        # same as any other non-loopback host and require authorization.
        return True
    return host not in {"localhost", "127.0.0.1", "::1", ""}


def _production_database_access_authorized() -> bool:
    return os.environ.get(_PRODUCTION_DB_OVERRIDE_ENV_VAR, "").strip() == "1"


def _guard_engine_creation(url: str) -> None:
    if not _looks_like_production_database(url):
        return
    context = _runtime_context()
    if context in _RECOGNIZED_DB_RUNTIME_CONTEXTS and context != "admin":
        return
    if _production_database_access_authorized():
        # Covers two cases identically: an unclassified process with the
        # narrow override, and the "admin" context, which always
        # additionally requires this override — self-declaring as
        # "admin" is never sufficient on its own for a controlled
        # production administrative operation.
        return
    raise ProductionDatabaseGuardError(
        "Refusing to open a database connection from an unrecognized or "
        f"unauthorized runtime context. Set {_DB_RUNTIME_CONTEXT_ENV_VAR} to "
        "one of the recognized launcher contexts, or, for a reviewed, "
        f"intentional production operation, set {_PRODUCTION_DB_OVERRIDE_ENV_VAR}"
        "=1 in this shell for this invocation only — never in a persisted "
        ".env file. The database URL itself is never included in this "
        "message."
    )


def sqlalchemy_database_url(raw: str | None = None) -> str:
    """Accept a dashboard postgresql:// URI and use psycopg3."""
    url = (raw if raw is not None else get_settings().database_url).strip()
    if url.startswith("postgresql://") and not url.startswith("postgresql+"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://") and not url.startswith("postgres+"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    return url


@lru_cache
def get_engine() -> Engine | None:
    url = sqlalchemy_database_url()
    if not url:
        return None
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        _bootstrap_organization(engine)
        return engine
    _guard_engine_creation(url)
    engine = create_engine(url, pool_pre_ping=True)
    _bootstrap_organization(engine)
    return engine


def get_session_factory() -> sessionmaker[Session] | None:
    global _SessionLocal
    engine = get_engine()
    if engine is None:
        return None
    if _SessionLocal is None or _SessionLocal.kw.get("bind") is not engine:
        _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    return _SessionLocal


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    factory = get_session_factory()
    if factory is None:
        raise RuntimeError("DATABASE_URL is not configured.")
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def persistence_enabled() -> bool:
    return bool((get_settings().database_url or "").strip())


def reset_persistence() -> None:
    global _SessionLocal
    get_engine.cache_clear()
    _SessionLocal = None


def reset_sqlite_schema() -> None:
    engine = get_engine()
    if engine is None or engine.dialect.name != "sqlite":
        return
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    _bootstrap_organization(engine)


def _bootstrap_organization(engine: Engine) -> None:
    settings = get_settings()
    with Session(engine) as session:
        existing = session.get(Organization, settings.default_organization_id)
        if existing is None:
            session.add(
                Organization(id=settings.default_organization_id, name=settings.default_organization_name)
            )
            session.commit()


def current_organization_id():
    return get_settings().default_organization_id
