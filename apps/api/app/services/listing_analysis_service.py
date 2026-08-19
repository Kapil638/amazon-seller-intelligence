from app.analytics.listing_rules import analyze_listing
from app.models.listing_analysis import ListingAnalysis
from app.models.product import Product


class ListingAnalysisService:
    """Application service for deterministic listing intelligence.

    Routes, jobs, and future MCP tools should call this layer rather than
    importing scoring rules directly.
    """

    def analyze(self, product: Product) -> ListingAnalysis:
        return analyze_listing(product)
