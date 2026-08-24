"""Unit tests for connection-to-secret resolution. No OAuth, no Amazon API calls."""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from uuid import UUID

import pytest
from pydantic import SecretStr

from app.amazon.connection_secrets import AmazonConnectionSecretResolver
from app.amazon.secrets import (
    DevelopmentSecretProvider,
    InvalidSecretReferenceError,
    SecretNotFoundError,
    build_asi_secret_reference,
    development_sandbox_token_reference,
    parse_asi_amazon_secret_reference,
    validate_secret_reference,
)
from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID

REFRESH_TOKEN = "Atzr|test-sandbox-refresh-token"
OTHER_TOKEN = "Atzr|org-b-refresh-token"
ACCESS_TOKEN = "Atza|test-sandbox-access-token"
CLIENT_SECRET = "test-lwa-client-secret-value"
PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\nMIIBOgIBAAJBAK8=\n-----END PRIVATE KEY-----"
ORG_A = DEFAULT_DEVELOPMENT_ORGANIZATION_ID
ORG_B = UUID("22222222-2222-4222-8222-222222222222")
CONNECTION_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CONNECTION_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


@dataclass
class _Connection:
    organization_id: UUID
    id: UUID
    provider: str = "SP_API"
    environment: str = "SANDBOX"
    token_reference: str | None = None


class _RecordingProvider:
    def __init__(self, inner: DevelopmentSecretProvider) -> None:
        self._inner = inner
        self.gets: list[str] = []

    def put_secret(self, reference: str, value: SecretStr) -> None:
        self._inner.put_secret(reference, value)

    def get_secret(self, reference: str) -> SecretStr:
        self.gets.append(reference)
        return self._inner.get_secret(reference)

    def exists(self, reference: str) -> bool:
        return self._inner.exists(reference)

    def delete_secret(self, reference: str) -> None:
        self._inner.delete_secret(reference)


def _assert_no_secrets(text: str) -> None:
    assert REFRESH_TOKEN not in text
    assert OTHER_TOKEN not in text
    assert ACCESS_TOKEN not in text
    assert CLIENT_SECRET not in text
    assert "Atzr|" not in text
    assert "Atza|" not in text
    assert "BEGIN PRIVATE KEY" not in text


def test_valid_secret_reference_accepted() -> None:
    reference = build_asi_secret_reference(
        provider="SP_API",
        environment="PRODUCTION",
        organization_id="org123",
        connection_id="connection456",
    )
    parsed = parse_asi_amazon_secret_reference(reference)
    assert parsed.value == "asi/amazon/SP_API/PRODUCTION/org123/connection456"
    assert parsed.provider == "SP_API"
    assert parsed.environment == "PRODUCTION"
    assert validate_secret_reference(reference) == reference
    sandbox = development_sandbox_token_reference(ORG_A)
    assert parse_asi_amazon_secret_reference(sandbox).environment == "SANDBOX"


def test_invalid_token_shaped_reference_rejected() -> None:
    for bad in (REFRESH_TOKEN, ACCESS_TOKEN, PRIVATE_KEY):
        with pytest.raises(InvalidSecretReferenceError) as exc_info:
            parse_asi_amazon_secret_reference(bad)
        _assert_no_secrets(str(exc_info.value))


def test_raw_secret_value_rejected_as_reference() -> None:
    with pytest.raises(InvalidSecretReferenceError) as exc_info:
        parse_asi_amazon_secret_reference("raw-refresh-token-value")
    _assert_no_secrets(str(exc_info.value))
    with pytest.raises(InvalidSecretReferenceError):
        parse_asi_amazon_secret_reference("asi/amazon/SP_API/PRODUCTION")
    with pytest.raises(InvalidSecretReferenceError):
        parse_asi_amazon_secret_reference("")


def test_connection_resolves_correct_reference() -> None:
    reference = build_asi_secret_reference(
        provider="SP_API",
        environment="SANDBOX",
        organization_id=ORG_A,
        connection_id=CONNECTION_A,
    )
    provider = DevelopmentSecretProvider(sandbox_refresh_token=SecretStr(REFRESH_TOKEN))
    provider.put_secret(reference, SecretStr(OTHER_TOKEN))
    connection = _Connection(
        organization_id=ORG_A,
        id=CONNECTION_A,
        token_reference=reference,
    )
    resolver = AmazonConnectionSecretResolver(secret_provider=provider)
    assert resolver.reference_for(organization_id=ORG_A, connection=connection) == reference
    value = resolver.resolve_refresh_token(organization_id=ORG_A, connection=connection)
    assert isinstance(value, SecretStr)
    assert value.get_secret_value() == OTHER_TOKEN
    fallback = _Connection(organization_id=ORG_A, id=CONNECTION_A, token_reference=None)
    assert resolver.reference_for(
        organization_id=ORG_A, connection=fallback
    ) == development_sandbox_token_reference(ORG_A)
    assert (
        resolver.resolve_refresh_token(
            organization_id=ORG_A, connection=fallback
        ).get_secret_value()
        == REFRESH_TOKEN
    )


def test_organization_isolation_enforced() -> None:
    reference_a = build_asi_secret_reference(
        provider="SP_API",
        environment="SANDBOX",
        organization_id=ORG_A,
        connection_id=CONNECTION_A,
    )
    reference_b = build_asi_secret_reference(
        provider="SP_API",
        environment="SANDBOX",
        organization_id=ORG_B,
        connection_id=CONNECTION_B,
    )
    inner = DevelopmentSecretProvider(sandbox_refresh_token=None)
    inner.put_secret(reference_a, SecretStr(REFRESH_TOKEN))
    inner.put_secret(reference_b, SecretStr(OTHER_TOKEN))
    recorder = _RecordingProvider(inner)
    resolver = AmazonConnectionSecretResolver(secret_provider=recorder)
    connection_a = _Connection(
        organization_id=ORG_A,
        id=CONNECTION_A,
        token_reference=reference_a,
    )
    with pytest.raises(InvalidSecretReferenceError) as other_org:
        resolver.resolve_refresh_token(organization_id=ORG_B, connection=connection_a)
    _assert_no_secrets(str(other_org.value))
    assert recorder.gets == []

    spoofed = _Connection(
        organization_id=ORG_A,
        id=CONNECTION_A,
        token_reference=reference_b,
    )
    with pytest.raises(InvalidSecretReferenceError) as spoof:
        resolver.resolve_refresh_token(organization_id=ORG_A, connection=spoofed)
    _assert_no_secrets(str(spoof.value))
    assert recorder.gets == []
    assert (
        resolver.resolve_refresh_token(
            organization_id=ORG_A, connection=connection_a
        ).get_secret_value()
        == REFRESH_TOKEN
    )


def test_missing_secret_produces_safe_exception() -> None:
    reference = build_asi_secret_reference(
        provider="SP_API",
        environment="PRODUCTION",
        organization_id=ORG_A,
        connection_id=CONNECTION_A,
    )
    provider = DevelopmentSecretProvider(sandbox_refresh_token=None)
    connection = _Connection(
        organization_id=ORG_A,
        id=CONNECTION_A,
        environment="PRODUCTION",
        token_reference=reference,
    )
    resolver = AmazonConnectionSecretResolver(secret_provider=provider)
    with pytest.raises(SecretNotFoundError) as exc_info:
        resolver.resolve_refresh_token(organization_id=ORG_A, connection=connection)
    _assert_no_secrets(str(exc_info.value))
    production_unbound = _Connection(
        organization_id=ORG_A,
        id=CONNECTION_A,
        environment="PRODUCTION",
        token_reference=None,
    )
    with pytest.raises(InvalidSecretReferenceError):
        resolver.reference_for(organization_id=ORG_A, connection=production_unbound)


def test_secret_values_never_appear_in_logs_or_errors(caplog: pytest.LogCaptureFixture) -> None:
    reference = build_asi_secret_reference(
        provider="SP_API",
        environment="SANDBOX",
        organization_id=ORG_A,
        connection_id=CONNECTION_A,
    )
    provider = DevelopmentSecretProvider(sandbox_refresh_token=SecretStr(REFRESH_TOKEN))
    provider.put_secret(reference, SecretStr(OTHER_TOKEN))
    connection = _Connection(
        organization_id=ORG_A,
        id=CONNECTION_A,
        token_reference=reference,
    )
    resolver = AmazonConnectionSecretResolver(secret_provider=provider)
    with caplog.at_level(logging.DEBUG):
        value = resolver.resolve_refresh_token(organization_id=ORG_A, connection=connection)
        with pytest.raises(InvalidSecretReferenceError) as invalid:
            resolver.reference_for(
                organization_id=ORG_A,
                connection=_Connection(
                    organization_id=ORG_A,
                    id=CONNECTION_A,
                    token_reference=REFRESH_TOKEN,
                ),
            )
    _assert_no_secrets(repr(resolver))
    _assert_no_secrets(repr(value))
    _assert_no_secrets(caplog.text)
    _assert_no_secrets(str(invalid.value))


def test_resolver_does_not_call_amazon_or_persist() -> None:
    source = inspect.getsource(AmazonConnectionSecretResolver)
    assert "httpx" not in source
    assert "LwaClient" not in source
    assert "AmazonSpApiSandboxClient" not in source
    assert "session_scope" not in source
    import app.amazon.connection_secrets as module

    assert "copilot" not in inspect.getsource(module)
