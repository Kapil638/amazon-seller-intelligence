from app.analytics.competitor_search_query import generate_search_query, normalize_search_query
from app.models.product import Product


class CompetitorSearchQueryService:
    """Derives a concise Amazon search phrase from a normalized Product."""

    def generate(self, product: Product) -> str:
        return generate_search_query(product)

    def resolve(self, product: Product, search_query: str | None) -> tuple[str, bool]:
        if search_query is None:
            return self.generate(product), True
        return normalize_search_query(search_query), False
