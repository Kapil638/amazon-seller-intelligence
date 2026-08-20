from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings
from app.media.url_validator import MediaUrlValidator, allowed_hosts_from_settings
from app.models.media_evidence import (
    MediaEvidenceItem,
    MediaSelectionResult,
    MediaSourceType,
    SkippedMediaItem,
    VideoEvidenceSummary,
)
from app.models.product import Product

SELECTION_REASON = (
    "Deterministic priority: main image, gallery, A+ images, then Brand Story. "
    "Duplicate URLs and invalid hosts are skipped. Video files and thumbnails are not sent."
)


@dataclass(frozen=True)
class _Candidate:
    source_type: MediaSourceType
    url: str
    alt_text: str | None
    width: int | None
    height: int | None
    position: int


def select_media_evidence(
    product: Product,
    *,
    max_images: int | None = None,
    validator: MediaUrlValidator | None = None,
) -> MediaSelectionResult:
    settings = get_settings()
    limit = max_images if max_images is not None else settings.openai_vision_max_images
    checker = validator or MediaUrlValidator(allowed_hosts_from_settings(settings.openai_vision_allowed_hosts))
    candidates = _collect_candidates(product)
    selected: list[MediaEvidenceItem] = []
    skipped: list[SkippedMediaItem] = []
    seen: set[str] = set()

    buckets: dict[MediaSourceType, list[_Candidate]] = {
        MediaSourceType.MAIN_IMAGE: [],
        MediaSourceType.GALLERY: [],
        MediaSourceType.A_PLUS: [],
        MediaSourceType.BRAND_STORY: [],
    }
    for item in candidates:
        buckets[item.source_type].append(item)

    reserve_aplus = min(2, len(buckets[MediaSourceType.A_PLUS]))
    reserve_brand = 1 if buckets[MediaSourceType.BRAND_STORY] else 0

    def try_add(candidate: _Candidate) -> bool:
        normalized = candidate.url.strip()
        if normalized in seen:
            skipped.append(
                SkippedMediaItem(
                    source_type=candidate.source_type,
                    reason="duplicate_url",
                    host=_host_hint(normalized),
                )
            )
            return False
        ok, reason, host = checker.validate(normalized)
        if not ok:
            skipped.append(
                SkippedMediaItem(
                    source_type=candidate.source_type,
                    reason=reason or "invalid_url",
                    host=host,
                )
            )
            return False
        if len(selected) >= limit:
            skipped.append(
                SkippedMediaItem(
                    source_type=candidate.source_type,
                    reason="over_image_limit",
                    host=host,
                )
            )
            return False
        seen.add(normalized)
        selected.append(
            MediaEvidenceItem(
                id=_next_id(candidate.source_type, selected),
                source_type=candidate.source_type,
                url=normalized,
                alt_text=candidate.alt_text,
                width=candidate.width,
                height=candidate.height,
                position=candidate.position,
            )
        )
        return True

    for item in buckets[MediaSourceType.MAIN_IMAGE]:
        try_add(item)

    gallery_budget = max(0, limit - len(selected) - reserve_aplus - reserve_brand)
    added_gallery = 0
    remaining_gallery: list[_Candidate] = []
    for item in buckets[MediaSourceType.GALLERY]:
        if added_gallery < gallery_budget:
            if try_add(item):
                added_gallery += 1
            # invalid/duplicate already recorded
        else:
            remaining_gallery.append(item)

    remaining_aplus: list[_Candidate] = []
    added_aplus = 0
    for item in buckets[MediaSourceType.A_PLUS]:
        if added_aplus < 2:
            if try_add(item):
                added_aplus += 1
        else:
            remaining_aplus.append(item)

    remaining_brand: list[_Candidate] = []
    added_brand = 0
    for item in buckets[MediaSourceType.BRAND_STORY]:
        if added_brand < 1:
            if try_add(item):
                added_brand += 1
        else:
            remaining_brand.append(item)

    for item in remaining_gallery + remaining_aplus + remaining_brand:
        try_add(item)

    warnings = [f"{item.source_type or 'unknown'}: {item.reason}" for item in skipped]
    return MediaSelectionResult(
        images_available=len(candidates),
        images_selected=len(selected),
        images_skipped=len(skipped),
        selection_reason=SELECTION_REASON,
        selected=selected,
        skipped=skipped,
        warnings=warnings,
        video=_video_summary(product),
    )


def _collect_candidates(product: Product) -> list[_Candidate]:
    items: list[_Candidate] = []
    mains = [image for image in product.images if image.is_main]
    gallery = [image for image in product.images if not image.is_main]
    if not mains and product.images:
        mains = [product.images[0]]
        gallery = list(product.images[1:])
    for index, image in enumerate(mains):
        items.append(
            _Candidate(MediaSourceType.MAIN_IMAGE, image.url, image.alt, image.width, image.height, index)
        )
    for index, image in enumerate(gallery):
        items.append(
            _Candidate(MediaSourceType.GALLERY, image.url, image.alt, image.width, image.height, index)
        )

    payload = product.a_plus
    if payload is not None:
        for index, image in enumerate(payload.images):
            items.append(_Candidate(MediaSourceType.A_PLUS, image.url, image.alt, None, None, index))
        story = payload.brand_story
        brand_index = 0
        if story is not None:
            if story.hero_image:
                items.append(
                    _Candidate(MediaSourceType.BRAND_STORY, story.hero_image, "Brand Story hero", None, None, brand_index)
                )
                brand_index += 1
            if story.brand_logo:
                items.append(
                    _Candidate(MediaSourceType.BRAND_STORY, story.brand_logo, "Brand Story logo", None, None, brand_index)
                )
                brand_index += 1
            for url in story.images:
                items.append(_Candidate(MediaSourceType.BRAND_STORY, url, None, None, None, brand_index))
                brand_index += 1
        if payload.company_logo:
            items.append(
                _Candidate(MediaSourceType.BRAND_STORY, payload.company_logo, "Company logo", None, None, brand_index)
            )
    return items


def _video_summary(product: Product) -> VideoEvidenceSummary:
    titles = [item.title.strip() for item in product.videos if item.title and item.title.strip()][:8]
    object_count = len(product.videos)
    reported = product.videos_count
    present = object_count > 0 or (reported is not None and reported > 0)
    return VideoEvidenceSummary(
        video_present=present,
        video_details_available=object_count > 0,
        video_count_reported=reported,
        video_object_count=object_count,
        titles=titles,
        frames_not_analyzed=True,
    )


def _next_id(source_type: MediaSourceType, selected: list[MediaEvidenceItem]) -> str:
    count = sum(1 for item in selected if item.source_type == source_type) + 1
    prefix = {
        MediaSourceType.MAIN_IMAGE: "main",
        MediaSourceType.GALLERY: "gallery",
        MediaSourceType.A_PLUS: "aplus",
        MediaSourceType.BRAND_STORY: "brand",
    }[source_type]
    return f"img-{prefix}-{count}"


def _host_hint(url: str) -> str | None:
    from urllib.parse import urlparse

    try:
        return urlparse(url).hostname
    except Exception:
        return None
