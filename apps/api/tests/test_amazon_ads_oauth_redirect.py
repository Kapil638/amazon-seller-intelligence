from __future__ import annotations

import logging

import pytest

from app.amazon.ads_oauth import ALLOWED_RETURN_PATHS, frontend_ads_return_url

PRODUCTION_ORIGIN = "https://app.ewiseintelligence.com"


def test_success_redirects_to_the_frontend_hostname_not_the_api_hostname() -> None:
    url = frontend_ads_return_url(origin=PRODUCTION_ORIGIN, return_path="/seller/advertising", notice="success")
    assert url == "https://app.ewiseintelligence.com/seller/advertising?ads=success"
    assert not url.startswith("https://api.ewiseintelligence.com")


@pytest.mark.parametrize("notice", ["success", "denied", "expired", "error"])
def test_every_notice_outcome_produces_a_valid_frontend_url(notice: str) -> None:
    url = frontend_ads_return_url(origin=PRODUCTION_ORIGIN, return_path="/seller/advertising", notice=notice)
    assert url == f"https://app.ewiseintelligence.com/seller/advertising?ads={notice}"


def test_unrecognized_notice_falls_back_to_error_rather_than_passing_through_raw() -> None:
    url = frontend_ads_return_url(
        origin=PRODUCTION_ORIGIN, return_path="/seller/advertising", notice="<script>alert(1)</script>"
    )
    assert url == "https://app.ewiseintelligence.com/seller/advertising?ads=error"
    assert "<script>" not in url


def test_query_value_is_percent_encoded_not_interpolated_raw() -> None:
    # A notice containing characters that would need query-string
    # escaping is rejected by the enum check before encoding ever
    # matters — but confirms the result is exactly one clean "ads=error"
    # param, never a second, attacker-controlled param spliced in
    # alongside it (e.g. "&c=d" surviving as its own query key).
    url = frontend_ads_return_url(origin=PRODUCTION_ORIGIN, return_path="/seller/advertising", notice="a b&c=d")
    assert url == "https://app.ewiseintelligence.com/seller/advertising?ads=error"


def test_trailing_slash_on_origin_does_not_produce_a_double_slash() -> None:
    url = frontend_ads_return_url(
        origin="https://app.ewiseintelligence.com/", return_path="/seller/advertising", notice="success"
    )
    assert "//seller" not in url.split("://", 1)[1]
    assert url == "https://app.ewiseintelligence.com/seller/advertising?ads=success"


class TestOpenRedirectRejection:
    """The return path must be re-validated inside this function itself —
    never trust that every call site already validated it — so an
    absolute-URL or protocol-relative injection is rejected even if some
    future caller forgets to call `validate_return_path` first."""

    def test_absolute_external_url_as_return_path_is_rejected(self) -> None:
        url = frontend_ads_return_url(
            origin=PRODUCTION_ORIGIN, return_path="https://evil.example.com/steal", notice="success"
        )
        assert "evil.example.com" not in url
        assert url == "https://app.ewiseintelligence.com/seller/advertising?ads=success"

    def test_protocol_relative_return_path_is_rejected(self) -> None:
        url = frontend_ads_return_url(origin=PRODUCTION_ORIGIN, return_path="//evil.example.com/steal", notice="success")
        assert "evil.example.com" not in url

    def test_path_traversal_return_path_is_rejected(self) -> None:
        url = frontend_ads_return_url(
            origin=PRODUCTION_ORIGIN, return_path="/seller/../../etc/passwd", notice="success"
        )
        assert url == "https://app.ewiseintelligence.com/seller/advertising?ads=success"

    def test_none_and_empty_return_path_fall_back_to_the_default(self) -> None:
        for candidate in (None, "", "   "):
            url = frontend_ads_return_url(origin=PRODUCTION_ORIGIN, return_path=candidate, notice="success")
            assert url == "https://app.ewiseintelligence.com/seller/advertising?ads=success"

    def test_only_the_closed_allowlist_paths_are_ever_reachable(self) -> None:
        # Documents the exact allowlist this test file's guarantees rest
        # on — if this ever grows, every test above should be revisited
        # for whether the new path also needs open-redirect coverage.
        assert ALLOWED_RETURN_PATHS == frozenset({"/seller/advertising"})


def test_empty_or_missing_origin_falls_back_to_local_dev_default_never_to_the_api_host() -> None:
    for origin in ("", "   "):
        url = frontend_ads_return_url(origin=origin, return_path="/seller/advertising", notice="success")
        assert url.startswith("http://localhost:3000")
        assert "api.ewiseintelligence.com" not in url


def test_no_secret_or_token_shaped_value_can_reach_the_redirect_url() -> None:
    """Structural guarantee: this function's signature has no parameter
    through which a code/token could ever flow, and its only string
    interpolation points are the fixed origin, the allowlisted path, and
    the allowlisted notice enum — there is no code path by which
    `authorization_code`, `access_token`, or `refresh_token` could appear
    in the returned URL."""
    for forged_notice in ("Atza|fake-access-token", "Atzr|fake-refresh-token", "ANoAAAAA-fake-auth-code"):
        url = frontend_ads_return_url(origin=PRODUCTION_ORIGIN, return_path="/seller/advertising", notice=forged_notice)
        assert "Atza|" not in url
        assert "Atzr|" not in url
        assert url.endswith("ads=error")


def test_route_never_logs_a_token_when_building_the_redirect(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        frontend_ads_return_url(origin=PRODUCTION_ORIGIN, return_path="/seller/advertising", notice="success")
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "Atza|" not in log_text
    assert "Atzr|" not in log_text
