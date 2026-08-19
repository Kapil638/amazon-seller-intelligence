from datetime import UTC, datetime

from app.models.product import BSR, Image, Price, Product, Seller, Variation
from app.providers.base import ProductDataProvider, ProviderCapabilities

MOCK_CAPABILITIES = ProviderCapabilities(
    product_details=True,
    pricing=True,
    ratings=True,
    reviews=False,
    bsr=True,
    seller=True,
    variations=True,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _catalog() -> dict[tuple[str, str], Product]:
    """Fictional catalog keyed by (asin, marketplace)."""
    products = [
        Product(
            asin="B0TEST0001",
            marketplace="amazon.in",
            title="AuroraGlow Vitamin D3 Softgels, 60 Count",
            brand="Lumora Wellness",
            price=Price(amount=449.0, currency="INR"),
            rating=4.4,
            review_count=1284,
            bullet_points=[
                "60 vegetarian softgels with 600 IU vitamin D3 per serving",
                "Formulated for daily immune and bone-health support",
                "Third-party tested for purity; no artificial colors",
                "Easy-to-swallow size with a mild citrus coating",
                "Packed in a moisture-resistant bottle for Indian climates",
            ],
            description=(
                "AuroraGlow Vitamin D3 is a daily wellness supplement designed for "
                "adults who want a simple, consistent source of vitamin D. Each "
                "bottle contains 60 vegetarian softgels. This is fictional product "
                "data for local development only."
            ),
            images=[
                Image(
                    url="https://placehold.co/800x800/1e3a5f/ffffff?text=AuroraGlow+D3",
                    alt="AuroraGlow Vitamin D3 bottle",
                ),
                Image(
                    url="https://placehold.co/800x800/2d5a87/ffffff?text=Supplement+Facts",
                    alt="AuroraGlow supplement facts",
                ),
            ],
            category="Health & Personal Care",
            bsr=BSR(rank=1842, category="Health & Personal Care"),
            availability="In Stock",
            seller=Seller(
                name="Lumora Retail India",
                id="MOCKSELLER01",
                is_fba=True,
                rating=4.7,
            ),
            variations=[
                Variation(
                    asin="B0TEST0001",
                    label="60 Count",
                    attributes={"count": "60"},
                ),
                Variation(
                    asin="B0TEST0101",
                    label="120 Count",
                    attributes={"count": "120"},
                ),
            ],
            last_fetched_at=_now(),
        ),
        Product(
            asin="B0TEST0002",
            marketplace="amazon.in",
            title="NimbusFoam Memory Contour Pillow, Standard Size",
            brand="Restora Home",
            price=Price(amount=1299.0, currency="INR"),
            rating=4.2,
            review_count=856,
            bullet_points=[
                "Contoured memory foam designed for side and back sleepers",
                "Removable, machine-washable bamboo-blend cover",
                "Medium-firm support that keeps its shape overnight",
                "Standard size fits most Indian pillow covers",
                "Ships vacuum-packed; expands fully within 24 hours",
            ],
            description=(
                "The NimbusFoam contour pillow uses slow-recovery memory foam to "
                "support neck alignment. Cover is zippered and washable. Fictional "
                "catalog item for development."
            ),
            images=[
                Image(
                    url="https://placehold.co/800x800/3d4a3a/ffffff?text=NimbusFoam+Pillow",
                    alt="NimbusFoam contour pillow",
                ),
                Image(
                    url="https://placehold.co/800x800/5a6b52/ffffff?text=Cover+Detail",
                    alt="Bamboo-blend pillow cover",
                ),
            ],
            category="Home & Kitchen",
            bsr=BSR(rank=512, category="Home & Kitchen > Bedding"),
            availability="In Stock",
            seller=Seller(
                name="Restora Home Store",
                id="MOCKSELLER02",
                is_fba=True,
                rating=4.5,
            ),
            variations=[
                Variation(
                    asin="B0TEST0002",
                    label="Standard",
                    attributes={"size": "standard"},
                ),
                Variation(
                    asin="B0TEST0202",
                    label="Queen",
                    attributes={"size": "queen"},
                ),
            ],
            last_fetched_at=_now(),
        ),
        Product(
            asin="B0TEST0003",
            marketplace="amazon.in",
            title="PeakPulse Resistance Bands Set, 5 Levels",
            brand="StrideForge",
            price=Price(amount=799.0, currency="INR"),
            rating=4.6,
            review_count=2103,
            bullet_points=[
                "Five latex-free bands from extra-light to extra-heavy",
                "Includes door anchor, handles, and a carry pouch",
                "Printed tension rating on each band for quick setup",
                "Suitable for home workouts, travel, and physiotherapy",
                "Comes with a printed 20-move starter routine",
            ],
            description=(
                "PeakPulse is a compact resistance-training kit for home fitness. "
                "Five color-coded bands cover a range of tension levels. Fictional "
                "product used only in the mock data provider."
            ),
            images=[
                Image(
                    url="https://placehold.co/800x800/7a2e1f/ffffff?text=PeakPulse+Bands",
                    alt="PeakPulse resistance bands set",
                ),
                Image(
                    url="https://placehold.co/800x800/9a4a32/ffffff?text=Accessories",
                    alt="Handles and door anchor",
                ),
                Image(
                    url="https://placehold.co/800x800/c46a48/ffffff?text=Carry+Pouch",
                    alt="Carry pouch",
                ),
            ],
            category="Sports, Fitness & Outdoors",
            bsr=BSR(rank=96, category="Sports, Fitness & Outdoors > Strength Training"),
            availability="In Stock",
            seller=Seller(
                name="StrideForge Official",
                id="MOCKSELLER03",
                is_fba=False,
                rating=4.8,
            ),
            variations=[
                Variation(
                    asin="B0TEST0003",
                    label="5-Band Set",
                    attributes={"pack": "5"},
                ),
            ],
            last_fetched_at=_now(),
        ),
    ]
    return {(item.asin, item.marketplace): item for item in products}


class MockProductDataProvider(ProductDataProvider):
    """In-memory catalog of fictional products. No external APIs."""

    def __init__(self) -> None:
        self._products = _catalog()

    @property
    def name(self) -> str:
        return "mock"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return MOCK_CAPABILITIES

    async def get_product(self, asin: str, marketplace: str) -> Product | None:
        product = self._products.get((asin, marketplace))
        if product is None:
            return None
        return product.model_copy(update={"last_fetched_at": _now()})

    def has_product(self, asin: str, marketplace: str) -> bool:
        return (asin, marketplace) in self._products
