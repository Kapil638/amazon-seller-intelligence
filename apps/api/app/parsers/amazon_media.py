"""Choose the best Amazon image URL from Rainforest-provided candidates.

Rainforest sometimes returns gallery thumbnails (``_SX38_SY50_``) and sometimes
``_SL1500_`` / unsized originals. We never invent new image IDs. When an Amazon
``/images/I/{id}`` URL is present, the unsized ``I/{id}.{ext}`` form is the same
shape Rainforest already uses for ``main_image.link`` and was verified as the
highest-resolution sibling (2560px vs 1500px vs 38px).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

AMAZON_IMAGE_HOSTS = (
    "m.media-amazon.com",
    "images-na.ssl-images-amazon.com",
    "images-eu.ssl-images-amazon.com",
    "images-fe.ssl-images-amazon.com",
    "images-amazon.com",
)

IMAGE_ID_RE = re.compile(r"/images/I/([^./]+)")
SIZE_RE = re.compile(r"(?:^|[._])(?:AC_)?(?:SL|SX|SY|US|SS)(\d+)", re.IGNORECASE)
EXTENSION_RE = re.compile(r"\.(jpe?g|png|webp|gif)(?:\?.*)?$", re.IGNORECASE)
VIDEO_OVERLAY_MARKERS = ("play-icon-overlay", "pkdp-play-icon", "dp-play-icon-overlay")
ORIGINAL_SCORE = 10_000
VIDEO_EXTENSIONS = (".mp4", ".m3u8", ".webm", ".mov")


def amazon_image_id(url: str) -> str | None:
    match = IMAGE_ID_RE.search(url)
    return match.group(1) if match else None


def is_amazon_media_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host.removeprefix("www.") in AMAZON_IMAGE_HOSTS


def is_video_thumbnail_url(url: str) -> bool:
    lowered = url.casefold()
    return any(marker in lowered for marker in VIDEO_OVERLAY_MARKERS)


def is_playable_video_url(url: str) -> bool:
    lowered = url.casefold().split("?", 1)[0]
    return any(lowered.endswith(ext) for ext in VIDEO_EXTENSIONS)


def quality_score(url: str) -> int:
    """Higher is better. Unsized Amazon originals outrank explicit size tokens."""
    if is_video_thumbnail_url(url):
        return 0
    sizes = [int(value) for value in SIZE_RE.findall(url)]
    if not sizes:
        return ORIGINAL_SCORE
    return max(sizes)


def unsized_amazon_url(url: str) -> str | None:
    if not is_amazon_media_url(url):
        return None
    image_id = amazon_image_id(url)
    if not image_id:
        return None
    ext_match = EXTENSION_RE.search(urlparse(url).path)
    extension = ext_match.group(1) if ext_match else "jpg"
    return f"https://m.media-amazon.com/images/I/{image_id}.{extension}"


def choose_best_image_url(urls: list[str]) -> str | None:
    candidates: list[str] = []
    seen: set[str] = set()
    for url in urls:
        text = (url or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        candidates.append(text)
        derived = unsized_amazon_url(text)
        if derived and derived not in seen:
            seen.add(derived)
            candidates.append(derived)
    if not candidates:
        return None
    return max(candidates, key=lambda item: (quality_score(item), len(item)))
