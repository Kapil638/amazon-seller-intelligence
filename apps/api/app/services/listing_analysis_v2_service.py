from app.analytics.listing_rules_v2 import analyze_listing_v2
from app.models.listing_analysis_v2 import ListingAnalysisV2
from app.models.product import Product


class ListingAnalysisV2Service:
    """Deterministic listing-quality analysis (score version v2)."""

    def analyze(self, product: Product) -> ListingAnalysisV2:
        return analyze_listing_v2(product)
