"""Disposable PostgreSQL test safety guard — 12B.2A.1.

Every test module under `tests/postgres/` must import `skip_reason()` from
here and set:

    pytestmark = pytest.mark.skipif(bool(skip_reason()), reason=skip_reason() or "")

Two independent conditions must both hold before any such test runs:

1. `ASI_ALLOW_DISPOSABLE_POSTGRES=1` is set — an explicit, unambiguous
   opt-in. Nothing here fires from a normal `pytest` invocation.
2. `POSTGRES_DISPOSABLE_TEST_URL` is set, is a PostgreSQL URL, and does not
   resemble the application's configured `DATABASE_URL` (compared only by
   host/port/database name — never printed, on any path, including failure
   messages, so a credential embedded in either URL is never logged).

If either condition fails, `skip_reason()` returns a human-readable string
and the caller must skip. This module never connects to anything and never
mutates any environment variable.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

_POSTGRES_SCHEMES = ("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://", "postgres://")


def _host_port_db(url: str) -> tuple[str, str, str]:
    parts = urlsplit(url)
    return (parts.hostname or "", str(parts.port or ""), (parts.path or "").lstrip("/"))


def _resembles_configured_database(disposable_url: str) -> bool:
    from app.core.config import get_settings

    configured = (get_settings().database_url or "").strip()
    if not configured:
        return False
    return _host_port_db(disposable_url) == _host_port_db(configured)


def skip_reason() -> str | None:
    """Return why the disposable-Postgres suite must be skipped, or None if it may run."""
    if os.environ.get("ASI_ALLOW_DISPOSABLE_POSTGRES") != "1":
        return "disposable PostgreSQL tests require ASI_ALLOW_DISPOSABLE_POSTGRES=1 (explicit opt-in)"
    url = os.environ.get("POSTGRES_DISPOSABLE_TEST_URL", "").strip()
    if not url:
        return "disposable PostgreSQL tests require POSTGRES_DISPOSABLE_TEST_URL to be set"
    if not url.startswith(_POSTGRES_SCHEMES):
        return "POSTGRES_DISPOSABLE_TEST_URL must be a PostgreSQL URL"
    if _resembles_configured_database(url):
        return (
            "refusing to run: POSTGRES_DISPOSABLE_TEST_URL resembles the application's "
            "configured DATABASE_URL (same host, port, and database name)"
        )
    return None


def disposable_url() -> str:
    """Only call after confirming `skip_reason() is None`.

    Returns the URL normalized to the psycopg3 driver this project actually
    depends on (`psycopg[binary]`, not `psycopg2` — see
    `app.persistence.database.sqlalchemy_database_url`, which every other
    part of the application already routes through for exactly this
    reason). A bare `postgresql://` scheme makes SQLAlchemy default to the
    `psycopg2` DBAPI, which is not installed anywhere in this project.
    """
    from app.persistence.database import sqlalchemy_database_url

    raw = os.environ["POSTGRES_DISPOSABLE_TEST_URL"].strip()
    return sqlalchemy_database_url(raw)
