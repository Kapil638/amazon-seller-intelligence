"""Unit tests for the SecretProvider abstraction. No storage providers. No Amazon calls."""

from __future__ import annotations

import inspect
from typing import get_type_hints

import pytest
from pydantic import SecretStr

from app.amazon.secrets import (
    INVALID_SECRET_REFERENCE_MESSAGE,
    SECRET_ACCESS_FAILURE_MESSAGE,
    SECRET_NOT_FOUND_MESSAGE,
    InvalidSecretReferenceError,
    SecretAccessError,
    SecretNotFoundError,
    SecretProvider,
    redact_secret_material,
    secret_provider_repr,
    validate_secret_reference,
)

REFRESH_TOKEN = "Atzr|test-sandbox-refresh-token"
ACCESS_TOKEN = "Atza|test-sandbox-access-token"
CLIENT_SECRET = "test-lwa-client-secret-value"
PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\nMIIBOgIBAAJBAK8=\n-----END PRIVATE KEY-----"
SAFE_REFERENCE = "asi/amazon/SP_API/SANDBOX/11111111-1111-1111-1111-111111111111/22222222-2222-2222-2222-222222222222"


class _ContractProbe:
    """Test double for the Protocol only. Not a development or production provider."""

    def __init__(self, backend: str = "probe") -> None:
        self._backend = backend
        self._values: dict[str, SecretStr] = {}

    def __repr__(self) -> str:
        return secret_provider_repr(backend=self._backend)

    def put_secret(self, reference: str, value: SecretStr) -> None:
        if not isinstance(value, SecretStr):
            raise TypeError("put_secret requires SecretStr")
        key = validate_secret_reference(reference)
        self._values[key] = value

    def get_secret(self, reference: str) -> SecretStr:
        key = validate_secret_reference(reference)
        try:
            return self._values[key]
        except KeyError:
            raise SecretNotFoundError(SECRET_NOT_FOUND_MESSAGE) from None

    def exists(self, reference: str) -> bool:
        key = validate_secret_reference(reference)
        return key in self._values

    def delete_secret(self, reference: str) -> None:
        key = validate_secret_reference(reference)
        self._values.pop(key, None)


def test_secret_provider_protocol_contract_exists() -> None:
    required = ("put_secret", "get_secret", "exists", "delete_secret")
    for name in required:
        assert hasattr(SecretProvider, name)
        assert callable(getattr(SecretProvider, name))
    probe = _ContractProbe()
    assert isinstance(probe, SecretProvider)


def test_secret_values_use_secret_str() -> None:
    put_hints = get_type_hints(SecretProvider.put_secret)
    get_hints = get_type_hints(SecretProvider.get_secret)
    exists_hints = get_type_hints(SecretProvider.exists)
    assert put_hints["value"] is SecretStr
    assert get_hints["return"] is SecretStr
    assert exists_hints["return"] is bool

    probe = _ContractProbe()
    stored = SecretStr(REFRESH_TOKEN)
    probe.put_secret(SAFE_REFERENCE, stored)
    retrieved = probe.get_secret(SAFE_REFERENCE)
    assert isinstance(retrieved, SecretStr)
    assert retrieved.get_secret_value() == REFRESH_TOKEN
    assert REFRESH_TOKEN not in repr(retrieved)
    assert REFRESH_TOKEN not in str(retrieved)


def test_exceptions_do_not_leak_secret_values() -> None:
    leaked = (
        SecretNotFoundError(f"missing {REFRESH_TOKEN}"),
        SecretAccessError(f"failed {ACCESS_TOKEN}"),
        InvalidSecretReferenceError(REFRESH_TOKEN),
        SecretAccessError(PRIVATE_KEY),
    )
    for exc in leaked:
        message = str(exc)
        assert REFRESH_TOKEN not in message
        assert ACCESS_TOKEN not in message
        assert "Atzr|" not in message
        assert "Atza|" not in message
        assert "BEGIN PRIVATE KEY" not in message
        assert "MIIBOgIBAAJBAK8=" not in message


def test_provider_representations_do_not_expose_secrets() -> None:
    probe = _ContractProbe(backend="development")
    probe.put_secret(SAFE_REFERENCE, SecretStr(REFRESH_TOKEN))
    rendered = repr(probe)
    assert rendered == "SecretProvider(backend=development)"
    assert REFRESH_TOKEN not in rendered
    assert ACCESS_TOKEN not in rendered
    assert CLIENT_SECRET not in rendered
    assert "Atzr|" not in secret_provider_repr(backend="development")


def test_missing_secret_error_is_safe() -> None:
    probe = _ContractProbe()
    with pytest.raises(SecretNotFoundError) as exc_info:
        probe.get_secret(SAFE_REFERENCE)
    message = str(exc_info.value)
    assert message == SECRET_NOT_FOUND_MESSAGE
    assert REFRESH_TOKEN not in message
    assert ACCESS_TOKEN not in message
    assert probe.exists(SAFE_REFERENCE) is False


def test_invalid_reference_rejects_credential_shaped_values() -> None:
    with pytest.raises(InvalidSecretReferenceError) as exc_info:
        validate_secret_reference(REFRESH_TOKEN)
    assert REFRESH_TOKEN not in str(exc_info.value)
    assert str(exc_info.value) == INVALID_SECRET_REFERENCE_MESSAGE
    with pytest.raises(InvalidSecretReferenceError):
        validate_secret_reference("   ")
    with pytest.raises(InvalidSecretReferenceError):
        validate_secret_reference("x" * 129)
    assert validate_secret_reference(f"  {SAFE_REFERENCE}  ") == SAFE_REFERENCE


def test_exists_and_delete_never_return_secret_material() -> None:
    probe = _ContractProbe()
    probe.put_secret(SAFE_REFERENCE, SecretStr(REFRESH_TOKEN))
    assert probe.exists(SAFE_REFERENCE) is True
    probe.delete_secret(SAFE_REFERENCE)
    assert probe.exists(SAFE_REFERENCE) is False
    probe.delete_secret(SAFE_REFERENCE)
    assert isinstance(probe.exists(SAFE_REFERENCE), bool)


def test_redact_and_access_failure_messages_are_safe() -> None:
    assert REFRESH_TOKEN not in redact_secret_material(f"error {REFRESH_TOKEN}")
    access = SecretAccessError()
    assert str(access) == SECRET_ACCESS_FAILURE_MESSAGE
    assert inspect.signature(SecretProvider.get_secret).return_annotation in {SecretStr, "SecretStr"}


def test_abstraction_does_not_call_amazon() -> None:
    import app.amazon.secrets as secrets_module

    source = inspect.getsource(secrets_module)
    assert "sellingpartnerapi" not in source
    assert "auth/o2/token" not in source
    assert "httpx" not in source
    assert "AmazonSpApiSandboxClient" not in source
    assert "LwaClient" not in source
    assert not any(
        name in secrets_module.__dict__
        for name in ("LwaClient", "AmazonSpApiSandboxClient", "httpx")
    )
