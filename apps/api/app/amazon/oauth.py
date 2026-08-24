"""Amazon seller authorization start helpers.

Generates OAuth CSRF state and Seller Central consent URLs. Callback intake
lives in `oauth_callback.py`. Does not exchange codes, call LWA, or store secrets.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from app.core.exceptions import SpApiConfigurationError

OAUTH_STATE_BYTES = 32
OAUTH_STATE_HASH_LENGTH = 64
CONSENT_PATH = "/apps/authorize/consent"

# Seller Central consent origins keyed by marketplace domain.
# Do not treat any single marketplace as a global default in call sites.
SELLER_CENTRAL_ORIGINS_BY_MARKETPLACE: dict[str, str] = {
    "amazon.in": "https://sellercentral.amazon.in",
    "amazon.com": "https://sellercentral.amazon.com",
    "amazon.ca": "https://sellercentral.amazon.ca",
    "amazon.com.mx": "https://sellercentral.amazon.com.mx",
    "amazon.com.br": "https://sellercentral.amazon.com.br",
    "amazon.co.uk": "https://sellercentral.amazon.co.uk",
    "amazon.de": "https://sellercentral.amazon.de",
    "amazon.fr": "https://sellercentral.amazon.fr",
    "amazon.it": "https://sellercentral.amazon.it",
    "amazon.es": "https://sellercentral.amazon.es",
    "amazon.nl": "https://sellercentral.amazon.nl",
    "amazon.se": "https://sellercentral.amazon.se",
    "amazon.pl": "https://sellercentral.amazon.pl",
    "amazon.com.be": "https://sellercentral.amazon.com.be",
    "amazon.ie": "https://sellercentral.amazon.ie",
    "amazon.ae": "https://sellercentral.amazon.ae",
    "amazon.sa": "https://sellercentral.amazon.sa",
    "amazon.eg": "https://sellercentral.amazon.eg",
    "amazon.co.jp": "https://sellercentral.amazon.co.jp",
    "amazon.com.au": "https://sellercentral.amazon.com.au",
    "amazon.sg": "https://sellercentral.amazon.sg",
}

# Regional fallback when a marketplace is not in the map and no override is set.
# India V1 uses the marketplace map (`amazon.in`), not this table.
SELLER_CENTRAL_ORIGINS_BY_REGION: dict[str, str] = {
    "na": "https://sellercentral.amazon.com",
    "eu": "https://sellercentral.amazon.co.uk",
    "fe": "https://sellercentral.amazon.co.jp",
}


def generate_oauth_state() -> str:
    """Return a URL-safe raw state token with at least 128 bits of entropy.

    Callers must persist only `hash_oauth_state(raw)` and put `raw` in the
    Amazon consent URL. Do not log or store the raw value.
    """
    return secrets.token_urlsafe(OAUTH_STATE_BYTES)


def hash_oauth_state(raw_state: str) -> str:
    return hashlib.sha256(raw_state.encode("utf-8")).hexdigest()


def new_oauth_state() -> tuple[str, str]:
    raw_state = generate_oauth_state()
    return raw_state, hash_oauth_state(raw_state)


def oauth_state_expiry(*, ttl_seconds: int, now: datetime | None = None) -> datetime:
    if ttl_seconds <= 0:
        raise SpApiConfigurationError("Amazon OAuth state expiry is not configured.")
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment + timedelta(seconds=ttl_seconds)


def oauth_state_is_usable(
    *,
    expires_at: datetime,
    consumed_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    if consumed_at is not None:
        return False
    moment = now or datetime.now(UTC)
    expires = expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return expires > moment


def seller_central_consent_origin(
    *,
    marketplace: str,
    region: str = "",
    override: str = "",
) -> str:
    configured = override.strip().rstrip("/")
    if configured:
        return configured
    key = marketplace.strip().lower()
    origin = SELLER_CENTRAL_ORIGINS_BY_MARKETPLACE.get(key)
    if origin:
        return origin
    region_key = region.strip().lower()
    origin = SELLER_CENTRAL_ORIGINS_BY_REGION.get(region_key)
    if origin:
        return origin
    raise SpApiConfigurationError("Amazon consent URL is not configured for this marketplace.")


def build_seller_central_consent_url(
    *,
    origin: str,
    application_id: str,
    state: str,
    version_beta: bool = False,
) -> str:
    """Build the documented Website Authorization consent URL.

    Query parameters are only those Amazon documents for this URI:
    `application_id`, `state`, and optional `version=beta` for draft apps.
    Redirect URI is registered with Amazon and is not added here.
    """
    app_id = application_id.strip()
    if not app_id:
        raise SpApiConfigurationError("Amazon application is not configured.")
    if not state:
        raise SpApiConfigurationError("Amazon authorization state is missing.")
    base = origin.strip().rstrip("/")
    if not base:
        raise SpApiConfigurationError("Amazon consent URL is not configured.")
    params: dict[str, str] = {
        "application_id": app_id,
        "state": state,
    }
    if version_beta:
        params["version"] = "beta"
    return f"{base}{CONSENT_PATH}?{urlencode(params)}"
