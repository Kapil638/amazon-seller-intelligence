from app.media.url_validator import (
    DEFAULT_ALLOWED_MEDIA_HOSTS,
    MediaUrlValidator,
    allowed_hosts_from_settings,
)


def test_default_allowlist_matches_observed_hosts() -> None:
    assert DEFAULT_ALLOWED_MEDIA_HOSTS == frozenset({"m.media-amazon.com", "placehold.co"})


def test_https_amazon_cdn_is_allowed() -> None:
    ok, reason, host = MediaUrlValidator().validate("https://m.media-amazon.com/images/I/71kM3BRnDaL.jpg")
    assert ok is True
    assert reason is None
    assert host == "m.media-amazon.com"


def test_https_placehold_is_allowed() -> None:
    ok, reason, _host = MediaUrlValidator().validate("https://placehold.co/800x800/ffffff/000000?text=Demo")
    assert ok is True
    assert reason is None


def test_http_is_rejected() -> None:
    ok, reason, _host = MediaUrlValidator().validate("http://m.media-amazon.com/images/I/71x.jpg")
    assert ok is False
    assert reason == "https_required"


def test_private_and_local_urls_are_rejected() -> None:
    validator = MediaUrlValidator()
    rejected = [
        "https://127.0.0.1/img.jpg",
        "https://localhost/img.jpg",
        "https://10.0.0.8/img.jpg",
        "https://192.168.1.10/img.jpg",
        "https://169.254.1.1/img.jpg",
        "https://[::1]/img.jpg",
        "file:///tmp/img.jpg",
        "data:image/png;base64,abc",
        "ftp://m.media-amazon.com/img.jpg",
    ]
    for url in rejected:
        ok, reason, _host = validator.validate(url)
        assert ok is False, url
        assert reason is not None


def test_non_allowlisted_host_is_rejected() -> None:
    ok, reason, host = MediaUrlValidator().validate("https://evil.example/steal.jpg")
    assert ok is False
    assert reason == "host_not_allowlisted"
    assert host == "evil.example"


def test_userinfo_and_non_443_port_are_rejected() -> None:
    validator = MediaUrlValidator()
    ok, reason, _host = validator.validate("https://user:pass@m.media-amazon.com/img.jpg")
    assert ok is False
    assert reason == "userinfo_not_allowed"
    ok, reason, _host = validator.validate("https://m.media-amazon.com:8080/img.jpg")
    assert ok is False
    assert reason == "blocked_port"


def test_extra_hosts_from_settings_are_allowlisted() -> None:
    hosts = allowed_hosts_from_settings("cdn.example.test, Another.CDN.test.")
    validator = MediaUrlValidator(hosts)
    ok, reason, host = validator.validate("https://cdn.example.test/img.jpg")
    assert ok is True
    assert reason is None
    assert host == "cdn.example.test"
    ok, reason, _host = validator.validate("https://another.cdn.test/img.jpg")
    assert ok is True
