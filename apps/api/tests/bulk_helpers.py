from io import BytesIO, StringIO

from openpyxl import Workbook

from app.bulk.cache import KeyedTtlCache
from app.bulk.mock_product import BulkMockProductDataProvider
from app.bulk.processor import BulkProcessor
from app.ai.mock import MockAIProvider
from app.models.bulk import BulkJobOptions
from app.services.listing_analysis_service import ListingAnalysisService


def csv_bytes(headers: list[str], rows: list[list[str]], filename: str = "asins.csv") -> tuple[str, bytes]:
    buffer = StringIO()
    buffer.write(",".join(headers) + "\n")
    for row in rows:
        buffer.write(",".join(row) + "\n")
    return filename, buffer.getvalue().encode("utf-8")


def xlsx_bytes(headers: list[str], rows: list[list[str]], filename: str = "asins.xlsx") -> tuple[str, bytes]:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    payload = BytesIO()
    workbook.save(payload)
    return filename, payload.getvalue()


def make_processor(
    *,
    product_ttl: int = 86400,
    ai_ttl: int = 604800,
    concurrency: int = 3,
    product_provider: BulkMockProductDataProvider | None = None,
    ai_provider: MockAIProvider | None = None,
) -> tuple[BulkProcessor, BulkMockProductDataProvider, MockAIProvider, KeyedTtlCache, KeyedTtlCache]:
    products = product_provider or BulkMockProductDataProvider()
    ai = ai_provider or MockAIProvider()
    product_cache = KeyedTtlCache(product_ttl)
    ai_cache = KeyedTtlCache(ai_ttl)
    processor = BulkProcessor(
        product_provider=products,
        product_cache=product_cache,
        ai_provider=ai,
        ai_cache=ai_cache,
        analysis=ListingAnalysisService(),
        concurrency=concurrency,
    )
    return processor, products, ai, product_cache, ai_cache


def standard_options(**overrides: object) -> BulkJobOptions:
    payload = {
        "analysis_mode": "standard",
        "ai_selection": "high_priority",
        "top_n": 10,
        "marketplace": "amazon.in",
    }
    payload.update(overrides)
    return BulkJobOptions.model_validate(payload)
