from app.media.selector import select_media_evidence
from app.media.url_validator import MediaUrlValidator
from app.models.media_evidence import MediaSourceType
from app.models.product import APlusContent, APlusImage, BrandStory, Image, ProductVideo
from tests.test_listing_analysis import make_product

AMZ = "https://m.media-amazon.com/images/I"


def test_main_image_is_selected_first() -> None:
    product = make_product(
        images=[
            Image(url=f"{AMZ}/gallery1.jpg"),
            Image(url=f"{AMZ}/main.jpg", is_main=True),
            Image(url=f"{AMZ}/gallery2.jpg"),
        ]
    )
    result = select_media_evidence(product, max_images=8)
    assert result.selected[0].source_type == MediaSourceType.MAIN_IMAGE
    assert result.selected[0].url.endswith("main.jpg")
    assert result.images_selected >= 1


def test_duplicate_urls_are_removed() -> None:
    product = make_product(
        images=[
            Image(url=f"{AMZ}/same.jpg", is_main=True),
            Image(url=f"{AMZ}/same.jpg"),
            Image(url=f"{AMZ}/other.jpg"),
        ]
    )
    result = select_media_evidence(product, max_images=8)
    urls = [item.url for item in result.selected]
    assert urls.count(f"{AMZ}/same.jpg") == 1
    assert any(item.reason == "duplicate_url" for item in result.skipped)


def test_maximum_image_limit() -> None:
    product = make_product(
        images=[Image(url=f"{AMZ}/{index}.jpg", is_main=index == 0) for index in range(20)]
    )
    result = select_media_evidence(product, max_images=8)
    assert result.images_available == 20
    assert result.images_selected == 8
    assert result.images_skipped == 12
    assert any(item.reason == "over_image_limit" for item in result.skipped)


def test_a_plus_and_brand_story_are_selected() -> None:
    product = make_product(
        images=[Image(url=f"{AMZ}/main.jpg", is_main=True)],
        a_plus=APlusContent(
            has_a_plus_content=True,
            images=[APlusImage(url=f"{AMZ}/aplus.jpg", alt="Module")],
            brand_story=BrandStory(hero_image=f"{AMZ}/hero.jpg", description="Brand"),
        ),
    )
    result = select_media_evidence(product, max_images=8)
    types = {item.source_type for item in result.selected}
    assert MediaSourceType.MAIN_IMAGE in types
    assert MediaSourceType.A_PLUS in types
    assert MediaSourceType.BRAND_STORY in types


def test_invalid_urls_are_skipped_without_blocking_valid_ones() -> None:
    product = make_product(
        images=[
            Image(url="http://127.0.0.1/private.jpg", is_main=True),
            Image(url=f"{AMZ}/ok.jpg"),
        ]
    )
    result = select_media_evidence(product, max_images=8)
    assert result.images_selected == 1
    assert result.selected[0].url.endswith("ok.jpg")
    assert result.images_skipped >= 1


def test_zero_images_selects_nothing() -> None:
    result = select_media_evidence(make_product(images=[]), max_images=8)
    assert result.images_available == 0
    assert result.images_selected == 0
    assert result.selected == []


def test_video_thumbnails_are_not_selected() -> None:
    product = make_product(
        images=[Image(url=f"{AMZ}/main.jpg", is_main=True)],
        videos=[ProductVideo(title="Clip", thumbnail_url=f"{AMZ}/thumb.jpg", video_url=f"{AMZ}/clip.mp4")],
        videos_count=1,
    )
    result = select_media_evidence(product, max_images=8)
    assert all("thumb" not in item.url for item in result.selected)
    assert result.video.video_present is True
    assert result.video.frames_not_analyzed is True
    assert result.video.titles == ["Clip"]


def test_custom_validator_can_allow_test_hosts() -> None:
    product = make_product(images=[Image(url="https://cdn.example.test/img.jpg", is_main=True)])
    blocked = select_media_evidence(product, max_images=8)
    assert blocked.images_selected == 0
    allowed = select_media_evidence(
        product,
        max_images=8,
        validator=MediaUrlValidator(frozenset({"cdn.example.test"})),
    )
    assert allowed.images_selected == 1
