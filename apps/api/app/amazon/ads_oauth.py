"""Amazon Ads OAuth start helpers. 12C read-only foundation, inactive by
configuration (see `app.core.config.Settings`'s `ads_*` fields).

Mirrors `app.amazon.oauth`'s SP-API state design exactly (single-use
hashed state, TTL, replay prevention) but is a wholly separate module and
database table (`amazon_ads_oauth_states`) — never the SP-API
`amazon_oauth_states` table, whose `provider` CHECK constraint is pinned
to `'SP_API'` only and is not widened by this iteration (see
`docs/AI_HANDOVER/20_PILOT_DEPLOYMENT_EWISE.md` §7).

Consent URL: Login with Amazon's standard authorize endpoint
(`https://www.amazon.com/ap/oa`), not a Seller Central-specific page —
Amazon Ads authorization is a generic LWA consent screen, distinct from
SP-API's Seller Central "Website Authorization Workflow" consent page.
Query parameters (`client_id`, `scope`, `response_type=code`,
`redirect_uri`, `state`) and the `advertising::campaign_management`
scope string are as documented in Amazon's Ads API authorization guide
(consulted this pass; see the handover doc for exact sources — the
official docs site did not render server-side in this environment, so
this was corroborated from Amazon's own GitHub issue tracker and
developer-forum threads rather than a single fetched page).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from app.core.exceptions import AdsConfigurationError

OAUTH_STATE_BYTES = 32
CONSENT_RESPONSE_TYPE = "code"


def generate_ads_oauth_state() -> str:
    """URL-safe raw state token, >=128 bits entropy. Callers persist only the hash."""
    return secrets.token_urlsafe(OAUTH_STATE_BYTES)


def hash_ads_oauth_state(raw_state: str) -> str:
    return hashlib.sha256(raw_state.encode("utf-8")).hexdigest()


def new_ads_oauth_state() -> tuple[str, str]:
    raw_state = generate_ads_oauth_state()
    return raw_state, hash_ads_oauth_state(raw_state)


def ads_oauth_state_expiry(*, ttl_seconds: int, now: datetime | None = None) -> datetime:
    if ttl_seconds <= 0:
        raise AdsConfigurationError("Amazon Ads OAuth state expiry is not configured.")
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment + timedelta(seconds=ttl_seconds)


def ads_oauth_state_is_usable(
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


# Frontend return locations the Ads OAuth flow may send the browser back
# to on completion. A closed allowlist, not an arbitrary caller-supplied
# path — prevents the OAuth state from being used as an open redirect.
ALLOWED_RETURN_PATHS = frozenset({"/seller/advertising"})
DEFAULT_RETURN_PATH = "/seller/advertising"


def validate_return_path(path: str | None) -> str:
    candidate = (path or "").strip()
    if candidate in ALLOWED_RETURN_PATHS:
        return candidate
    return DEFAULT_RETURN_PATH


def build_ads_consent_url(
    *,
    base_url: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
) -> str:
    """Build the LWA authorize URL. Never includes a client secret."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise AdsConfigurationError("Amazon Ads consent URL is not configured.")
    cid = (client_id or "").strip()
    if not cid:
        raise AdsConfigurationError("Amazon Ads application is not configured.")
    redirect = (redirect_uri or "").strip()
    if not redirect:
        raise AdsConfigurationError("Amazon Ads OAuth redirect URI is not configured.")
    if not state:
        raise AdsConfigurationError("Amazon Ads authorization state is missing.")
    params = {
        "client_id": cid,
        "scope": scope,
        "response_type": CONSENT_RESPONSE_TYPE,
        "redirect_uri": redirect,
        "state": state,
    }
    return f"{base}?{urlencode(params)}"
