from app.parsers.rainforest_product_mapper import map_rainforest_product
from tests.test_rainforest import load_fixture


def test_amazon_sold_is_distinct_from_missing_seller() -> None:
    product = map_rainforest_product(load_fixture("amazon_sold.json"), "B0AMZSOLD1", "amazon.in")
    assert product.seller is None
    assert product.is_sold_by_amazon is True
    assert product.availability == "In Stock"
    assert product.availability_type == "in_stock"
    assert product.price is not None
    assert product.price.amount == 499.0


def test_third_party_seller_is_not_treated_as_amazon_sold() -> None:
    product = map_rainforest_product(load_fixture("product.json"), "B07J4TNYV8", "amazon.in")
    assert product.seller is not None
    assert product.seller.name == "AKASO OUTDOOR"
    assert product.is_sold_by_amazon is False


def test_videos_count_without_video_objects() -> None:
    product = map_rainforest_product(load_fixture("videos_count_only.json"), "B0VIDCOUNT1", "amazon.in")
    assert product.videos == []
    assert product.videos_count == 3


def test_docs_only_optional_fields_map_when_present() -> None:
    product = map_rainforest_product(load_fixture("docs_only_enrichment.json"), "B0DOCSENR1", "amazon.in")
    assert product.a_plus is not None
    assert product.a_plus.has_a_plus_content is True
    assert product.a_plus.has_brand_story is True
    assert product.a_plus.third_party is False
    assert product.a_plus.company_description == "Northlane makes outdoor electronics."
    assert product.a_plus.body_text and "weather-sealed" in product.a_plus.body_text
    assert product.a_plus.images[0].url.endswith("module-1.jpg")
    assert product.a_plus.images[0].alt == "Weather-sealed housing"
    assert product.a_plus.brand_story is not None
    assert product.a_plus.brand_story.hero_image.endswith("hero.jpg")
    assert product.a_plus.brand_story.images[1].endswith("card.png")
    assert [item.name for item in product.specifications] == ["Brand Name", "Item Weight", "Color Name"]
    assert product.specifications[2].value == "Black"
    assert product.specifications_flat and "Item Weight" in product.specifications_flat
    assert product.attributes is not None
    assert product.attributes.manufacturer == "Northlane Labs"
    assert product.attributes.ingredients == ["Whey protein isolate", "Cocoa powder"]
    assert product.attributes.diet_type == ["Vegetarian"]
    assert product.attributes.listed[0].name == "Capacity"
    assert product.rating_breakdown is not None
    assert product.rating_breakdown.five_star is not None
    assert product.rating_breakdown.five_star.percentage == 70
    assert product.rating_breakdown.five_star.count == 350
    assert product.rating_breakdown.one_star is not None
    assert product.rating_breakdown.one_star.count == 30
    assert len(product.featured_reviews) == 1
    review = product.featured_reviews[0]
    assert review.id == "RTESTFEATURE1"
    assert review.title == "Works as described"
    assert review.body and "weekend hike" in review.body
    assert review.rating == 5
    assert review.verified_purchase is True
    assert review.profile_name == "Demo Reviewer"
    assert review.date_utc == "2026-01-01T00:00:00.000Z"
    assert product.recent_sales_text == "50+ bought in past month"
    assert product.videos_count == 2
    assert product.videos == []
    assert product.images[0].width == 2000
    assert product.images[0].height == 2000
    assert product.images[1].width == 1500


def test_docs_only_fixture_does_not_invent_sales_numbers() -> None:
    product = map_rainforest_product(load_fixture("docs_only_enrichment.json"), "B0DOCSENR1", "amazon.in")
    assert product.recent_sales_text == "50+ bought in past month"
    assert not hasattr(product, "sales")


def test_legacy_bsr_stays_first_row_when_later_rows_exist() -> None:
    payload = {
        "product": {
            "title": "BSR order listing",
            "asin": "B0BSRONLY1",
            "bestsellers_rank": [
                {"rank": 32614, "category": "Electronics"},
                {"rank": 161, "category": "Sports & Action Video Cameras"},
            ],
        }
    }
    product = map_rainforest_product(payload, "B0BSRONLY1", "amazon.in")
    assert product.bsr is not None
    assert product.bsr.rank == 32614
    assert product.bsr.category == "Electronics"
    assert [item.rank for item in product.bsr_ranks] == [32614, 161]
