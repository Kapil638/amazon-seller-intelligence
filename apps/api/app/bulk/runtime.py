from functools import lru_cache

from app.bulk.cache import KeyedTtlCache
from app.bulk.jobs import BulkJobService, InMemoryJobStore, InProcessJobBackend
from app.bulk.processor import BulkProcessor
from app.bulk.providers import get_bulk_ai_provider, get_bulk_product_provider
from app.core.config import get_settings

_STORE = InMemoryJobStore()
_BACKEND = InProcessJobBackend()
_PRODUCT_CACHE: KeyedTtlCache | None = None
_AI_CACHE: KeyedTtlCache | None = None


def get_bulk_job_store() -> InMemoryJobStore:
    return _STORE


def get_bulk_product_cache() -> KeyedTtlCache:
    global _PRODUCT_CACHE
    if _PRODUCT_CACHE is None:
        _PRODUCT_CACHE = KeyedTtlCache(get_settings().product_cache_ttl_seconds)
    return _PRODUCT_CACHE


def get_bulk_ai_cache() -> KeyedTtlCache:
    global _AI_CACHE
    if _AI_CACHE is None:
        _AI_CACHE = KeyedTtlCache(get_settings().ai_analysis_cache_ttl_seconds)
    return _AI_CACHE


def make_bulk_processor() -> BulkProcessor:
    settings = get_settings()
    product_provider = get_bulk_product_provider()
    ai_provider = get_bulk_ai_provider()
    return BulkProcessor(
        product_provider=product_provider,
        product_cache=get_bulk_product_cache(),
        ai_provider=ai_provider,
        ai_cache=get_bulk_ai_cache(),
        concurrency=settings.bulk_product_concurrency,
    )


@lru_cache
def get_bulk_job_service() -> BulkJobService:
    return BulkJobService(_STORE, _BACKEND, make_bulk_processor)


def reset_bulk_runtime() -> None:
    global _PRODUCT_CACHE, _AI_CACHE
    _STORE.clear()
    _PRODUCT_CACHE = KeyedTtlCache(get_settings().product_cache_ttl_seconds)
    _AI_CACHE = KeyedTtlCache(get_settings().ai_analysis_cache_ttl_seconds)
    get_bulk_job_service.cache_clear()
