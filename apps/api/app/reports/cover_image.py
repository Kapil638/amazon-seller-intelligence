from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings
from app.media.url_validator import MediaUrlValidator, allowed_hosts_from_settings

logger = logging.getLogger("app.reports.cover_image")

_MAX_BYTES = 2_000_000
_TIMEOUT = 2.5
_JPEG = (b"\xff\xd8\xff",)
_PNG = (b"\x89PNG\r\n\x1a\n",)


def load_cover_image_bytes(url: str | None) -> bytes | None:
    """Fetch a cover image only from allowlisted HTTPS hosts. Never raise."""
    if not url:
        return None
    settings = get_settings()
    extra = getattr(settings, "allowed_media_hosts", None)
    validator = MediaUrlValidator(
        allowed_hosts_from_settings(extra if isinstance(extra, str) else None)
    )
    ok, _reason, _host = validator.validate(url)
    if not ok:
        return None
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            response = client.get(url, headers={"Accept": "image/jpeg,image/png,image/*"})
        if response.status_code != 200:
            return None
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith("image/"):
            return None
        payload = response.content
        if not payload or len(payload) > _MAX_BYTES:
            return None
        if not (payload.startswith(_JPEG[0]) or payload.startswith(_PNG[0])):
            return None
        return payload
    except Exception:
        logger.info("Cover image could not be loaded; PDF will continue without it.")
        return None
