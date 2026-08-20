from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.persistence.models import Base, Organization

_SessionLocal: sessionmaker[Session] | None = None


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
