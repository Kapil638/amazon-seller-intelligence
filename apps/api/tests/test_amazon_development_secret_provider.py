"""Unit tests for DevelopmentSecretProvider. No AWS, no Amazon API calls."""

from __future__ import annotations

import inspect
import logging

import pytest
from pydantic import SecretStr

from app.amazon.secrets import (
    SECRET_NOT_FOUND_MESSAGE,
    DevelopmentSecretProvider,
    InvalidSecretReferenceError,
    SecretAccessError,
    SecretNotFoundError,
    SecretProvider,
    development_sandbox_token_reference,
    get_secret_provider,
    reset_secret_provider,
)
from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID, Settings

REFRESH_TOKEN = "Atzr|test-sandbox-refresh-token"
OTHER_TOKEN = "Atzr|test-overlay-refresh-token"
ACCESS_TOKEN = "Atza|test-sandbox-access-token"
CLIENT_SECRET = "test-lwa-client-secret-value"
CLIENT_ID = "amzn1.application-oa2-client.test"
MEMORY_REFERENCE = (
    "asi/amazon/SP_API/SANDBOX/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/"
    "22222222-2222-2222-2222-222222222222"
)
OTHER_ORG_REFERENCE = (
    "asi/amazon/SP_API/SANDBOX/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/"
    "22222222-2222-2222-2222-222222222222"
)
PRODUCTION_REFERENCE = (
    f"asi/amazon/SP_API/PRODUCTION/{DEFAULT_DEVELOPMENT_ORGANIZATION_ID}/"
    "22222222-2222-2222-2222-222222222222"
)
SANDBOX_ENV_REFERENCE = development_sandbox_token_reference()


def _provider(token: str | None = REFRESH_TOKEN) -> DevelopmentSecretProvider:
    sandbox = SecretStr(token) if token is not None else None
    return DevelopmentSecretProvider(
        sandbox_refresh_token=sandbox,
        default_organization_id=DEFAULT_DEVELOPMENT_ORGANIZATION_ID,
    )


def _assert_no_secrets(text: str) -> None:
    assert REFRESH_TOKEN not in text
    assert OTHER_TOKEN not in text
    assert ACCESS_TOKEN not in text
    assert CLIENT_SECRET not in text
    assert "Atzr|" not in text
    assert "Atza|" not in text


def test_development_provider_implements_secret_provider() -> None:
    provider = _provider()
    assert isinstance(provider, SecretProvider)
    assert callable(provider.put_secret)
    assert callable(provider.get_secret)
    assert callable(provider.exists)
    assert callable(provider.delete_secret)


def test_put_secret_stores_secret_str_safely() -> None:
    provider = _provider()
    stored = SecretStr(OTHER_TOKEN)
    provider.put_secret(MEMORY_REFERENCE, stored)
    retrieved = provider.get_secret(MEMORY_REFERENCE)
    assert isinstance(retrieved, SecretStr)
    assert retrieved.get_secret_value() == OTHER_TOKEN
    _assert_no_secrets(repr(stored))
    _assert_no_secrets(repr(provider))
    with pytest.raises(TypeError):
        provider.put_secret(MEMORY_REFERENCE, OTHER_TOKEN)  # type: ignore[arg-type]


def test_get_secret_returns_secret_str() -> None:
    provider = _provider()
    provider.put_secret(MEMORY_REFERENCE, SecretStr(OTHER_TOKEN))
    value = provider.get_secret(MEMORY_REFERENCE)
    assert isinstance(value, SecretStr)
    assert value.get_secret_value() == OTHER_TOKEN
    _assert_no_secrets(repr(value))
    _assert_no_secrets(str(value))


def test_exists_returns_correct_state() -> None:
    provider = _provider(token=None)
    assert provider.exists(MEMORY_REFERENCE) is False
    provider.put_secret(MEMORY_REFERENCE, SecretStr(OTHER_TOKEN))
    assert provider.exists(MEMORY_REFERENCE) is True
    assert isinstance(provider.exists(MEMORY_REFERENCE), bool)


def test_delete_secret_removes_stored_secret() -> None:
    provider = _provider(token=None)
    provider.put_secret(MEMORY_REFERENCE, SecretStr(OTHER_TOKEN))
    provider.delete_secret(MEMORY_REFERENCE)
    assert provider.exists(MEMORY_REFERENCE) is False
    provider.delete_secret(MEMORY_REFERENCE)
    with pytest.raises(SecretNotFoundError):
        provider.get_secret(MEMORY_REFERENCE)


def test_missing_secret_raises_safe_error() -> None:
    provider = _provider(token=None)
    with pytest.raises(SecretNotFoundError) as exc_info:
        provider.get_secret(MEMORY_REFERENCE)
    assert str(exc_info.value) == SECRET_NOT_FOUND_MESSAGE
    _assert_no_secrets(str(exc_info.value))


def test_environment_fallback_works_for_sandbox_token() -> None:
    provider = _provider()
    assert provider.exists(SANDBOX_ENV_REFERENCE) is True
    value = provider.get_secret(SANDBOX_ENV_REFERENCE)
    assert isinstance(value, SecretStr)
    assert value.get_secret_value() == REFRESH_TOKEN
    other_sandbox = (
        f"asi/amazon/SP_API/SANDBOX/{DEFAULT_DEVELOPMENT_ORGANIZATION_ID}/"
        "22222222-2222-2222-2222-222222222222"
    )
    assert provider.get_secret(other_sandbox).get_secret_value() == REFRESH_TOKEN


def test_environment_fallback_does_not_apply_to_production_or_other_orgs() -> None:
    provider = _provider()
    with pytest.raises(SecretNotFoundError) as production:
        provider.get_secret(PRODUCTION_REFERENCE)
    with pytest.raises(SecretNotFoundError) as other_org:
        provider.get_secret(OTHER_ORG_REFERENCE)
    _assert_no_secrets(str(production.value))
    _assert_no_secrets(str(other_org.value))
    assert provider.exists(PRODUCTION_REFERENCE) is False
    assert provider.exists(OTHER_ORG_REFERENCE) is False


def test_memory_overlay_overrides_environment_fallback() -> None:
    provider = _provider()
    provider.put_secret(SANDBOX_ENV_REFERENCE, SecretStr(OTHER_TOKEN))
    assert provider.get_secret(SANDBOX_ENV_REFERENCE).get_secret_value() == OTHER_TOKEN
    provider.delete_secret(SANDBOX_ENV_REFERENCE)
    assert provider.get_secret(SANDBOX_ENV_REFERENCE).get_secret_value() == REFRESH_TOKEN


def test_secrets_never_appear_in_logs_repr_or_exceptions(caplog: pytest.LogCaptureFixture) -> None:
    provider = _provider()
    with caplog.at_level(logging.DEBUG):
        provider.put_secret(MEMORY_REFERENCE, SecretStr(OTHER_TOKEN))
        provider.exists(MEMORY_REFERENCE)
        provider.get_secret(MEMORY_REFERENCE)
        provider.delete_secret(MEMORY_REFERENCE)
        with pytest.raises(InvalidSecretReferenceError) as invalid:
            provider.get_secret(REFRESH_TOKEN)
        with pytest.raises(SecretNotFoundError) as missing:
            provider.get_secret(MEMORY_REFERENCE)
    _assert_no_secrets(repr(provider))
    _assert_no_secrets(str(provider))
    _assert_no_secrets(str(provider.__dict__))
    _assert_no_secrets(caplog.text)
    _assert_no_secrets(str(invalid.value))
    _assert_no_secrets(str(missing.value))
    assert "client_id" not in provider.__dict__
    assert "client_secret" not in provider.__dict__


def test_development_provider_does_not_call_amazon() -> None:
    source = inspect.getsource(DevelopmentSecretProvider)
    assert "sellingpartnerapi" not in source
    assert "auth/o2/token" not in source
    assert "httpx" not in source
    assert "AmazonSpApiSandboxClient" not in source
    assert "LwaClient" not in source
    assert "amazon_connections" not in source


def test_get_secret_provider_defaults_to_development() -> None:
    reset_secret_provider()
    try:
        settings = Settings(
            amazon_secret_backend="development",
            sp_api_sandbox_refresh_token=SecretStr(REFRESH_TOKEN),
            default_organization_id=DEFAULT_DEVELOPMENT_ORGANIZATION_ID,
        )
        provider = get_secret_provider(settings)
        assert isinstance(provider, DevelopmentSecretProvider)
        assert isinstance(provider, SecretProvider)
        assert provider.get_secret(SANDBOX_ENV_REFERENCE).get_secret_value() == REFRESH_TOKEN
        _assert_no_secrets(repr(provider))
    finally:
        reset_secret_provider()


def test_get_secret_provider_rejects_unimplemented_backends() -> None:
    reset_secret_provider()
    try:
        settings = Settings(
            amazon_secret_backend="aws_secrets_manager",
            sp_api_sandbox_refresh_token=SecretStr(REFRESH_TOKEN),
        )
        with pytest.raises(SecretAccessError) as exc_info:
            get_secret_provider(settings)
        _assert_no_secrets(str(exc_info.value))
    finally:
        reset_secret_provider()
