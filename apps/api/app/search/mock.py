from app.search.base import AmazonSearchHit, AmazonSearchProvider


MOCK_HITS = (
    AmazonSearchHit(
        asin="B0TEST0002",
        title="NimbusFoam Memory Contour Pillow, Standard Size",
        brand="Restora Home",
        price=1299.0,
        currency="INR",
        rating=4.2,
        review_count=856,
        image="https://placehold.co/800x800/3d4a3a/ffffff?text=NimbusFoam+Pillow",
        position=1,
        is_sponsored=False,
        category="Home & Kitchen",
    ),
    AmazonSearchHit(
        asin="B0TEST0003",
        title="PeakPulse Resistance Bands Set, 5 Levels",
        brand="StrideForge",
        price=799.0,
        currency="INR",
        rating=4.6,
        review_count=2103,
        image="https://placehold.co/800x800/7a2e1f/ffffff?text=PeakPulse+Bands",
        position=2,
        is_sponsored=True,
        category="Sports, Fitness & Outdoors",
    ),
    AmazonSearchHit(
        asin="B0TEST0001",
        title="AuroraGlow Vitamin D3 Softgels, 60 Count",
        brand="Lumora Wellness",
        price=449.0,
        currency="INR",
        rating=4.4,
        review_count=1284,
        image="https://placehold.co/800x800/1e3a5f/ffffff?text=AuroraGlow+D3",
        position=3,
        is_sponsored=False,
        category="Health & Personal Care",
    ),
    AmazonSearchHit(
        asin="B0TEST0101",
        title="AuroraGlow Vitamin D3 Softgels, 120 Count",
        brand="Lumora Wellness",
        price=699.0,
        currency="INR",
        rating=4.3,
        review_count=410,
        image="https://placehold.co/800x800/1e3a5f/ffffff?text=AuroraGlow+120",
        position=4,
        is_sponsored=False,
        category="Health & Personal Care",
    ),
)


class MockAmazonSearchProvider(AmazonSearchProvider):
    """In-memory Amazon search snippets for local demo. No external APIs."""

    def __init__(self, hits: tuple[AmazonSearchHit, ...] | None = None) -> None:
        self._hits = hits or MOCK_HITS

    @property
    def name(self) -> str:
        return "mock"

    async def search(self, query: str, marketplace: str) -> list[AmazonSearchHit]:
        if not query.strip():
            return []
        return [item.model_copy() for item in self._hits]
