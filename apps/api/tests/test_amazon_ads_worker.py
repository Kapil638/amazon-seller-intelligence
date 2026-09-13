from __future__ import annotations

import os

import pytest

from app.amazon import ads_worker
from app.amazon.ads_client import DisabledAmazonAdsApiClient, HttpAmazonAdsApiClient
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ASI_ADS_WORKER_ENABLED", raising=False)
    monkeypatch.delenv("ADS_API_BACKEND", raising=False)
    monkeypatch.delenv("ASI_DB_RUNTIME_CONTEXT", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_disabled_by_default_never_constructs_a_service(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def _spy_run_forever(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(ads_worker, "run_forever", _spy_run_forever)
    assert ads_worker.main() == ads_worker.EXIT_DISABLED
    assert called is False


def test_enabled_but_backend_not_http_refuses_with_distinct_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASI_ADS_WORKER_ENABLED", "true")
    # ADS_API_BACKEND deliberately left unset -> defaults to "disabled".
    called = False

    async def _spy_run_forever(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(ads_worker, "run_forever", _spy_run_forever)
    assert ads_worker.main() == ads_worker.EXIT_BACKEND_NOT_HTTP
    assert called is False


def test_enabled_with_mock_backend_still_refuses_to_run_for_real(monkeypatch: pytest.MonkeyPatch) -> None:
    """`mock` is a valid client backend for tests/local dev, but this
    worker specifically must only ever run for real against `http` —
    proves the two flags aren't just "both truthy," the backend value is
    checked exactly."""
    monkeypatch.setenv("ASI_ADS_WORKER_ENABLED", "true")
    monkeypatch.setenv("ADS_API_BACKEND", "mock")
    called = False

    async def _spy_run_forever(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(ads_worker, "run_forever", _spy_run_forever)
    assert ads_worker.main() == ads_worker.EXIT_BACKEND_NOT_HTTP
    assert called is False


def test_enabled_with_http_backend_constructs_the_real_client_and_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASI_ADS_WORKER_ENABLED", "true")
    monkeypatch.setenv("ADS_API_BACKEND", "http")
    captured: dict = {}

    async def _spy_run_forever(service, *, idle_poll_seconds, stop_after=None):
        captured["client"] = service._client  # noqa: SLF001 - white-box check this test exists to make

    monkeypatch.setattr(ads_worker, "run_forever", _spy_run_forever)
    exit_code = ads_worker.main()
    assert exit_code == ads_worker.EXIT_OK
    assert isinstance(captured["client"], HttpAmazonAdsApiClient)
    assert not isinstance(captured["client"], DisabledAmazonAdsApiClient)


def test_is_worker_enabled_true_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for value in ("1", "true", "True", "yes", "on"):
        monkeypatch.setenv("ASI_ADS_WORKER_ENABLED", value)
        assert ads_worker.is_worker_enabled() is True
    for value in ("0", "false", "", "no"):
        monkeypatch.setenv("ASI_ADS_WORKER_ENABLED", value)
        assert ads_worker.is_worker_enabled() is False
