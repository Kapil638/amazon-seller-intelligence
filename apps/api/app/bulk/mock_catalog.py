from datetime import UTC, datetime

from app.models.product import BSR, Image, Price, Product, Seller

TRANSIENT_ASIN = "B0BLKTRN01"
MARKETPLACE = "amazon.in"

STRONG_TITLE = (
    "AuroraGlow Vitamin D3 Softgels Daily Immune and Bone Health Support, "
    "Vegetarian 60 Count Bottle"
)
STRONG_BULLETS = [
    "Supports daily immune and bone health with 600 IU vitamin D3 per serving",
    "Designed for adults who want a simple, consistent supplement routine",
    "Includes 60 vegetarian softgels in a moisture-resistant bottle",
    "Third-party tested for purity with no artificial colors added",
    "Easy-to-swallow size with a mild citrus coating for daily use",
]
STRONG_DESCRIPTION = (
    "AuroraGlow Vitamin D3 is a daily wellness supplement designed for adults "
    "who want a simple, consistent source of vitamin D. Each bottle contains "
    "60 vegetarian softgels intended for everyday use. This fictional listing "
    "is used only to test deterministic listing analysis. The copy is long "
    "enough to sit inside the preferred description range used by score version v1."
)


def _now() -> datetime:
    return datetime.now(UTC)


def _images(count: int, label: str) -> list[Image]:
    return [
        Image(url=f"https://placehold.co/800x800?text={label}+{index}", alt=f"{label} {index}")
        for index in range(1, count + 1)
    ]


def _product(**overrides: object) -> Product:
    data: dict[str, object] = {
        "asin": "B0BLKSTR01",
        "marketplace": MARKETPLACE,
        "title": STRONG_TITLE,
        "brand": "Lumora Wellness",
        "price": Price(amount=449, currency="INR"),
        "rating": 4.6,
        "review_count": 2100,
        "bullet_points": list(STRONG_BULLETS),
        "description": STRONG_DESCRIPTION,
        "images": _images(6, "Strong"),
        "category": "Health & Personal Care",
        "bsr": BSR(rank=1842, category="Health & Personal Care"),
        "availability": "In Stock",
        "seller": Seller(name="Lumora Retail India", id="MOCKBULK01", is_fba=True, rating=4.7),
        "variations": [],
        "last_fetched_at": _now(),
    }
    data.update(overrides)
    return Product.model_validate(data)


def bulk_fixture_catalog() -> dict[tuple[str, str], Product]:
    """Fictional bulk fixtures. No copyrighted Amazon listing copy."""

    products = [
        _product(asin="B0BLKSTR01"),
        _product(
            asin="B0BLKTTL02",
            title="Glow D3",
            brand="Northvale Labs",
        ),
        _product(
            asin="B0BLKDES03",
            title="CedarPath Desk Organizer Tray For Home Office Supplies, Bamboo Finish",
            brand="CedarPath Home",
            description=None,
            category="Home & Kitchen",
        ),
        _product(
            asin="B0BLKIMG04",
            title="Riverstone Insulated Water Bottle 750ml For Daily Commute And Gym",
            brand="Riverstone Gear",
            images=_images(1, "Bottle"),
            category="Sports, Fitness & Outdoors",
        ),
        _product(
            asin="B0BLKBLT05",
            title="Maple&Co Cotton Bath Towel Set Soft Absorbent 4 Piece Pack",
            brand="Maple & Co",
            bullet_points=[],
            category="Home & Kitchen",
        ),
        _product(
            asin="B0BLKRAT06",
            title="BrightNest LED Desk Lamp With Adjustable Neck For Study Tables",
            brand="BrightNest",
            rating=3.1,
            review_count=88,
            category="Home & Kitchen",
        ),
        _product(
            asin="B0BLKREV07",
            title="HarborLeaf Green Tea Bags 100 Count Unflavored Everyday Brew",
            brand="HarborLeaf Tea",
            rating=4.4,
            review_count=4,
            category="Grocery",
        ),
        _product(
            asin="B0BLKINC08",
            title="Pocket item",
            brand=None,
            price=None,
            rating=None,
            review_count=None,
            bullet_points=["Small"],
            description=None,
            images=_images(1, "Incomplete"),
            category=None,
            bsr=None,
            availability=None,
            seller=None,
        ),
        _product(
            asin="B0BLKHGH09",
            title="X",
            brand=None,
            price=None,
            bullet_points=[],
            description=None,
            images=[],
            category=None,
            bsr=None,
        ),
        _product(
            asin="B0BLKMID10",
            title="SummitWire USB-C Cable",
            brand="SummitWire",
            rating=4.0,
            review_count=40,
            bullet_points=["Charges phones", "1 metre length", "Black cable"],
            description="A short USB-C cable listing used as a medium-quality fixture.",
            images=_images(3, "Cable"),
            category="Electronics",
        ),
        _product(
            asin="B0BLKLOW11",
            title=STRONG_TITLE.replace("AuroraGlow", "HelioPure"),
            brand="HelioPure",
            bullet_points=[item.replace("AuroraGlow", "HelioPure") for item in STRONG_BULLETS],
            description=STRONG_DESCRIPTION.replace("AuroraGlow", "HelioPure"),
        ),
        _product(
            asin=TRANSIENT_ASIN,
            title="NimbusFoam Travel Neck Pillow Compact Memory Foam For Flights",
            brand="Restora Home",
            category="Home & Kitchen",
        ),
        _product(
            asin="B0BLKSOC12",
            title="Pinecroft Ceramic Mug 350ml Matte Finish For Coffee And Tea",
            brand="Pinecroft",
            rating=2.8,
            review_count=12,
            category="Home & Kitchen",
        ),
    ]
    return {(item.asin, item.marketplace): item for item in products}
