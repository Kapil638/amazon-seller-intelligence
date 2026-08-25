"""Factory selection for SecretProvider. No cloud SDKs. No secret leakage."""

from __future__ import annotations

import inspect
from typing import get_type_hints

import pytest
from pydantic import SecretStr

from app.amazon.secrets import (
    PRODUCTION_SECRET_BACKEND_UNAVAILABLE_MESSAGE,
    UNKNOWN_SECRET_BACKEND_MESSAGE,
    DevelopmentSecretProvider,
    SecretAccessError,
    SecretProvider,
    SecretProviderFactory,
    get_secret_provider,
    reset_secret_provider,
    resolve_amazon_secret_backend,
)
from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID, Settings

REFRESH_TOKEN = "Atzr|test-sandbox-refresh-token"


def _assert_no_secrets(text: str) -> None:
    assert REFRESH_TOKEN not in text
    assert "Atzr|" not in text
    assert "Atza|" not in text


def _settings(backend: str, *, refresh_token: str | None = REFRESH_TOKEN) -> Settings:
    token = SecretStr(refresh_token) if refresh_token is not None else None
    return Settings(
        amazon_secret_backend=backend,
        sp_api_sandbox_refresh_token=token,
        default_organization_id=DEFAULT_DEVELOPMENT_ORGANIZATION_ID,
    )


def test_factory_selects_development_provider() -> None:
    reset_secret_provider()
    try:
        settings = _settings("development")
        assert resolve_amazon_secret_backend(settings) == "development"
        provider = SecretProviderFactory().create(settings)
        assert isinstance(provider, DevelopmentSecretProvider)
        assert isinstance(provider, SecretProvider)
        assert repr(provider) == "SecretProvider(backend=development)"
        cached = get_secret_provider(settings)
        assert isinstance(cached, DevelopmentSecretProvider)
        _assert_no_secrets(repr(provider))
    finally:
        reset_secret_provider()


def test_unknown_provider_fails_safely() -> None:
    reset_secret_provider()
    try:
        settings = _settings("aws_secrets_manager")
        with pytest.raises(SecretAccessError) as exc_info:
            SecretProviderFactory().create(settings)
        assert str(exc_info.value) == UNKNOWN_SECRET_BACKEND_MESSAGE
        _assert_no_secrets(str(exc_info.value))
        with pytest.raises(SecretAccessError):
            get_secret_provider(_settings("vault"))
    finally:
        reset_secret_provider()


def test_production_selection_does_not_fallback_to_development() -> None:
    reset_secret_provider()
    try:
        get_secret_provider(_settings("development"))
        settings = _settings("production", refresh_token=REFRESH_TOKEN)
        assert resolve_amazon_secret_backend(settings) == "production"
        try:
            provider = SecretProviderFactory().create(settings)
        except SecretAccessError as exc:
            assert str(exc) == PRODUCTION_SECRET_BACKEND_UNAVAILABLE_MESSAGE
            _assert_no_secrets(str(exc))
        else:
            raise AssertionError(f"production backend must not return {type(provider)!r}")
        with pytest.raises(SecretAccessError) as fetched:
            get_secret_provider(settings)
        assert str(fetched.value) == PRODUCTION_SECRET_BACKEND_UNAVAILABLE_MESSAGE
        _assert_no_secrets(str(fetched.value))
    finally:
        reset_secret_provider()


def test_factory_development_file_store_survives_reset(tmp_path) -> None:
    reset_secret_provider()
    try:
        store = tmp_path / "amazon-development-secrets.json"
        settings = Settings(
            amazon_secret_backend="development",
            amazon_development_secret_store=str(store),
            sp_api_sandbox_refresh_token=None,
            default_organization_id=DEFAULT_DEVELOPMENT_ORGANIZATION_ID,
        )
        first = SecretProviderFactory().create(settings)
        reference = (
            f"asi/amazon/SP_API/PRODUCTION/{DEFAULT_DEVELOPMENT_ORGANIZATION_ID}/"
            "22222222-2222-2222-2222-222222222222"
        )
        first.put_secret(reference, SecretStr(REFRESH_TOKEN))
        reset_secret_provider()
        second = SecretProviderFactory().create(settings)
        assert second.get_secret(reference).get_secret_value() == REFRESH_TOKEN
        _assert_no_secrets(repr(second))
    finally:
        reset_secret_provider()
    required = ("put_secret", "get_secret", "exists", "delete_secret")
    for name in required:
        assert hasattr(SecretProvider, name)
    put_hints = get_type_hints(SecretProvider.put_secret)
    get_hints = get_type_hints(SecretProvider.get_secret)
    assert put_hints["value"].__name__ == "SecretStr" or put_hints["value"] is SecretStr
    assert get_hints["return"] is SecretStr or getattr(get_hints["return"], "__name__", "") == "SecretStr"


def test_factory_errors_never_include_secret_values() -> None:
    reset_secret_provider()
    try:
        with pytest.raises(SecretAccessError) as production:
            SecretProviderFactory().create(_settings("production"))
        with pytest.raises(SecretAccessError) as unknown:
            SecretProviderFactory().create(_settings("encrypted_db"))
        _assert_no_secrets(str(production.value))
        _assert_no_secrets(str(unknown.value))
        _assert_no_secrets(repr(SecretProviderFactory()))
        source = inspect.getsource(SecretProviderFactory)
        assert "boto3" not in source
        assert "botocore" not in source
        assert "azure" not in source.lower()
        assert "hvac" not in source
    finally:
        reset_secret_provider()
