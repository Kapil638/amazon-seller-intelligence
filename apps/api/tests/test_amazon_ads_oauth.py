from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.amazon.ads_oauth import (
    ads_oauth_state_expiry,
    ads_oauth_state_is_usable,
    build_ads_consent_url,
    hash_ads_oauth_state,
    new_ads_oauth_state,
    validate_return_path,
)
from app.core.exceptions import AdsConfigurationError


def test_new_state_is_single_use_hash_pair() -> None:
    raw, hashed = new_ads_oauth_state()
    assert raw != hashed
    assert hashed == hash_ads_oauth_state(raw)
    assert len(hashed) == 64


def test_two_generated_states_never_collide() -> None:
    raw1, _ = new_ads_oauth_state()
    raw2, _ = new_ads_oauth_state()
    assert raw1 != raw2


def test_state_expiry_requires_positive_ttl() -> None:
    with pytest.raises(AdsConfigurationError):
        ads_oauth_state_expiry(ttl_seconds=0)
    with pytest.raises(AdsConfigurationError):
        ads_oauth_state_expiry(ttl_seconds=-5)


def test_state_is_usable_before_expiry_and_unconsumed() -> None:
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=60)
    assert ads_oauth_state_is_usable(expires_at=expires, consumed_at=None, now=now) is True


def test_state_is_not_usable_once_expired() -> None:
    now = datetime.now(UTC)
    expires = now - timedelta(seconds=1)
    assert ads_oauth_state_is_usable(expires_at=expires, consumed_at=None, now=now) is False


def test_state_is_not_usable_once_consumed() -> None:
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=60)
    assert ads_oauth_state_is_usable(expires_at=expires, consumed_at=now, now=now) is False


def test_validate_return_path_allows_only_the_closed_allowlist() -> None:
    assert validate_return_path("/seller/advertising") == "/seller/advertising"
    assert validate_return_path("https://evil.example.com/") == "/seller/advertising"
    assert validate_return_path("/seller/../../etc/passwd") == "/seller/advertising"
    assert validate_return_path(None) == "/seller/advertising"
    assert validate_return_path("") == "/seller/advertising"


def test_build_ads_consent_url_shape_and_no_secret() -> None:
    url = build_ads_consent_url(
        base_url="https://www.amazon.com/ap/oa",
        client_id="amzn1.application-oa2-client.abc",
        redirect_uri="https://api.ewiseintelligence.com/api/v1/amazon/ads-connection/callback",
        scope="advertising::campaign_management",
        state="raw-state-value",
    )
    assert url.startswith("https://www.amazon.com/ap/oa?")
    assert "client_id=amzn1.application-oa2-client.abc" in url
    assert "response_type=code" in url
    assert "scope=advertising%3A%3Acampaign_management" in url
    assert "state=raw-state-value" in url
    assert "client_secret" not in url


def test_build_ads_consent_url_fails_closed_on_missing_config() -> None:
    with pytest.raises(AdsConfigurationError):
        build_ads_consent_url(base_url="", client_id="x", redirect_uri="y", scope="z", state="s")
    with pytest.raises(AdsConfigurationError):
        build_ads_consent_url(base_url="https://x", client_id="", redirect_uri="y", scope="z", state="s")
    with pytest.raises(AdsConfigurationError):
        build_ads_consent_url(base_url="https://x", client_id="c", redirect_uri="", scope="z", state="s")
    with pytest.raises(AdsConfigurationError):
        build_ads_consent_url(base_url="https://x", client_id="c", redirect_uri="y", scope="z", state="")
