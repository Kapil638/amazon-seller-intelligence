"""SecretProvider contract. Internal Amazon credential boundary only.

Application code depends on this interface, not on secret storage.

Allowed future callers:
- Amazon connection flow
- SP-API credential resolution

Forbidden callers:
- Frontend
- API payloads
- Copilot
- Skills
- EvidenceEnvelope
- Reports
- Analytics engines

DevelopmentSecretProvider is the local/dev implementation. The production
backend is reserved and fails closed until a cloud SecretProvider is
implemented. Application code depends on SecretProvider, not the backend.
Never log, print, or repr secret material.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import SecretStr

from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID, Settings, get_settings

MAX_SECRET_REFERENCE_LENGTH = 128
AMAZON_SECRET_BACKEND_DEVELOPMENT = "development"
AMAZON_SECRET_BACKEND_PRODUCTION = "production"
DEVELOPMENT_SANDBOX_ENV_CONNECTION_ID = UUID("00000000-0000-4000-8000-000000000001")
ASI_SECRET_REFERENCE_PREFIX = "asi/amazon"
ASI_SECRET_PROVIDERS = frozenset({"SP_API", "ADS_API"})
ASI_SECRET_ENVIRONMENTS = frozenset({"SANDBOX", "PRODUCTION"})

SECRET_NOT_FOUND_MESSAGE = "Requested Amazon secret was not found."
SECRET_ACCESS_FAILURE_MESSAGE = "Amazon secret could not be retrieved."
INVALID_SECRET_REFERENCE_MESSAGE = "Amazon secret reference is invalid."
PRODUCTION_SECRET_BACKEND_UNAVAILABLE_MESSAGE = (
    "Amazon production secret backend is not implemented."
)
UNKNOWN_SECRET_BACKEND_MESSAGE = "Amazon secret backend is not available."

_SECRET_VALUE_PATTERNS = (
    re.compile(r"Atzr\|[^\s'\"]+"),
    re.compile(r"Atza\|[^\s'\"]+"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)


def redact_secret_material(text: str, *, default: str | None = None) -> str:
    """Return text with credential-shaped values removed. Never keep token material."""
    redacted = text
    for pattern in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    stripped = redacted.strip()
    if default is not None and stripped in {"", "[redacted]"}:
        return default
    return redacted


def secret_provider_repr(*, backend: str) -> str:
    """Safe provider representation. Must never include tokens or credentials."""
    return f"SecretProvider(backend={backend})"


def validate_secret_reference(reference: str) -> str:
    """Reject empty, oversized, or credential-shaped references.

    `token_reference` is an opaque pointer (max 128 chars), not secret material.
    Implementations must call this before storage I/O.
    """
    if not isinstance(reference, str):
        raise InvalidSecretReferenceError(INVALID_SECRET_REFERENCE_MESSAGE)
    candidate = reference.strip()
    if not candidate:
        raise InvalidSecretReferenceError(INVALID_SECRET_REFERENCE_MESSAGE)
    if len(candidate) > MAX_SECRET_REFERENCE_LENGTH:
        raise InvalidSecretReferenceError(INVALID_SECRET_REFERENCE_MESSAGE)
    if "\n" in candidate or "\r" in candidate:
        raise InvalidSecretReferenceError(INVALID_SECRET_REFERENCE_MESSAGE)
    if redact_secret_material(candidate) != candidate:
        raise InvalidSecretReferenceError(INVALID_SECRET_REFERENCE_MESSAGE)
    return candidate


@dataclass(frozen=True)
class AsiAmazonSecretReference:
    """Parsed ASI secret pointer. Never contains secret material."""

    provider: str
    environment: str
    organization_id: str
    connection_id: str
    value: str


def parse_asi_amazon_secret_reference(reference: str) -> AsiAmazonSecretReference:
    """Require `asi/amazon/{provider}/{environment}/{organization_id}/{connection_id}`."""
    candidate = validate_secret_reference(reference)
    parts = candidate.split("/")
    if len(parts) != 6 or parts[0] != "asi" or parts[1] != "amazon":
        raise InvalidSecretReferenceError(INVALID_SECRET_REFERENCE_MESSAGE)
    provider = parts[2].upper()
    environment = parts[3].upper()
    organization_id = parts[4]
    connection_id = parts[5]
    if provider not in ASI_SECRET_PROVIDERS or environment not in ASI_SECRET_ENVIRONMENTS:
        raise InvalidSecretReferenceError(INVALID_SECRET_REFERENCE_MESSAGE)
    if not organization_id or not connection_id:
        raise InvalidSecretReferenceError(INVALID_SECRET_REFERENCE_MESSAGE)
    return AsiAmazonSecretReference(
        provider=provider,
        environment=environment,
        organization_id=organization_id,
        connection_id=connection_id,
        value=candidate,
    )


def build_asi_secret_reference(
    *,
    provider: str,
    environment: str,
    organization_id: UUID | str,
    connection_id: UUID | str,
) -> str:
    """Build a validated ASI pointer. Does not persist or create secret material."""
    return parse_asi_amazon_secret_reference(
        f"{ASI_SECRET_REFERENCE_PREFIX}/{provider}/{environment}/{organization_id}/{connection_id}"
    ).value


class SecretNotFoundError(Exception):
    """Raised when `get_secret` has no material for a valid reference."""

    def __init__(self, message: str = SECRET_NOT_FOUND_MESSAGE) -> None:
        super().__init__(redact_secret_material(message, default=SECRET_NOT_FOUND_MESSAGE))


class SecretAccessError(Exception):
    """Raised when secret storage cannot be reached or read."""

    def __init__(self, message: str = SECRET_ACCESS_FAILURE_MESSAGE) -> None:
        super().__init__(
            redact_secret_material(message, default=SECRET_ACCESS_FAILURE_MESSAGE)
        )


class InvalidSecretReferenceError(Exception):
    """Raised when a secret reference is empty, oversized, or credential-shaped."""

    def __init__(self, message: str = INVALID_SECRET_REFERENCE_MESSAGE) -> None:
        super().__init__(
            redact_secret_material(message, default=INVALID_SECRET_REFERENCE_MESSAGE)
        )


@runtime_checkable
class SecretProvider(Protocol):
    """Retrieve and manage seller secret material by opaque reference.

    Values in and out are `SecretStr`. Implementations must not log values
    and must use `secret_provider_repr` (or equivalent) for `__repr__`.
    """

    def put_secret(self, reference: str, value: SecretStr) -> None:
        """Store secret material for later retrieval. Overwrites the same reference.

        Future use: OAuth/token onboarding. Accept `SecretStr` only.
        """

    def get_secret(self, reference: str) -> SecretStr:
        """Return secret material as `SecretStr`.

        Missing material raises `SecretNotFoundError` without the secret value.
        """

    def exists(self, reference: str) -> bool:
        """Return whether secret material exists. Never returns the secret."""

    def delete_secret(self, reference: str) -> None:
        """Remove secret material. Missing references are a safe no-op."""


def development_sandbox_token_reference(
    organization_id: UUID | str | None = None,
) -> str:
    """Opaque pointer used for the default-org sandbox .env refresh token."""
    org = organization_id or DEFAULT_DEVELOPMENT_ORGANIZATION_ID
    return build_asi_secret_reference(
        provider="SP_API",
        environment="SANDBOX",
        organization_id=org,
        connection_id=DEVELOPMENT_SANDBOX_ENV_CONNECTION_ID,
    )


def _secret_or_none(value: SecretStr | None) -> SecretStr | None:
    if value is None:
        return None
    if not value.get_secret_value().strip():
        return None
    return value


def _allows_sandbox_env_fallback(reference: str, organization_id: UUID) -> bool:
    """Env token is development-only and never used for PRODUCTION or other orgs."""
    parts = reference.split("/")
    if len(parts) != 6:
        return False
    if parts[0:3] != ["asi", "amazon", "SP_API"]:
        return False
    if parts[3].upper() != "SANDBOX":
        return False
    return parts[4].lower() == str(organization_id).lower()


def _store_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text)


class DevelopmentSecretProvider:
    """Local SecretProvider: file-backed map plus sandbox .env fallback.

    File persistence is development-only so uvicorn reload does not drop
    seller refresh tokens. Production backend stays fail-closed. This is not
    a database table, not seller authorization, and not a production vault.
    App LWA client_id/client_secret stay in process settings and are never
    stored here.
    """

    def __init__(
        self,
        *,
        sandbox_refresh_token: SecretStr | None = None,
        default_organization_id: UUID | None = None,
        store_path: str | Path | None = None,
    ) -> None:
        self._lock = Lock()
        self._values: dict[str, SecretStr] = {}
        self._sandbox_refresh_token = sandbox_refresh_token
        self._default_organization_id = (
            default_organization_id or DEFAULT_DEVELOPMENT_ORGANIZATION_ID
        )
        self._store_path = _store_path(store_path)
        if self._store_path is not None:
            self._values = self._read_store()

    def __repr__(self) -> str:
        return secret_provider_repr(backend=AMAZON_SECRET_BACKEND_DEVELOPMENT)

    def __str__(self) -> str:
        return self.__repr__()

    def put_secret(self, reference: str, value: SecretStr) -> None:
        if not isinstance(value, SecretStr):
            raise TypeError("put_secret requires SecretStr")
        if _secret_or_none(value) is None:
            raise SecretAccessError(SECRET_ACCESS_FAILURE_MESSAGE)
        key = validate_secret_reference(reference)
        with self._lock:
            self._values[key] = value
            self._write_store_locked()

    def get_secret(self, reference: str) -> SecretStr:
        key = validate_secret_reference(reference)
        with self._lock:
            stored = self._values.get(key)
            if stored is not None:
                return stored
        env_token = self._env_sandbox_token(key)
        if env_token is not None:
            return env_token
        raise SecretNotFoundError(SECRET_NOT_FOUND_MESSAGE)

    def exists(self, reference: str) -> bool:
        key = validate_secret_reference(reference)
        with self._lock:
            if key in self._values:
                return True
        return self._env_sandbox_token(key) is not None

    def delete_secret(self, reference: str) -> None:
        key = validate_secret_reference(reference)
        with self._lock:
            self._values.pop(key, None)
            self._write_store_locked()

    def _env_sandbox_token(self, reference: str) -> SecretStr | None:
        if not _allows_sandbox_env_fallback(reference, self._default_organization_id):
            return None
        return _secret_or_none(self._sandbox_refresh_token)

    def _read_store(self) -> dict[str, SecretStr]:
        path = self._store_path
        if path is None or not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SecretAccessError(SECRET_ACCESS_FAILURE_MESSAGE) from exc
        if not isinstance(payload, dict):
            raise SecretAccessError(SECRET_ACCESS_FAILURE_MESSAGE)
        loaded: dict[str, SecretStr] = {}
        for raw_key, raw_value in payload.items():
            if not isinstance(raw_key, str) or not isinstance(raw_value, str):
                raise SecretAccessError(SECRET_ACCESS_FAILURE_MESSAGE)
            try:
                key = validate_secret_reference(raw_key)
            except InvalidSecretReferenceError as exc:
                raise SecretAccessError(SECRET_ACCESS_FAILURE_MESSAGE) from exc
            secret = SecretStr(raw_value)
            if _secret_or_none(secret) is None:
                raise SecretAccessError(SECRET_ACCESS_FAILURE_MESSAGE)
            loaded[key] = secret
        return loaded

    def _write_store_locked(self) -> None:
        path = self._store_path
        if path is None:
            return
        snapshot = {
            key: value.get_secret_value()
            for key, value in self._values.items()
            if _secret_or_none(value) is not None
        }
        tmp_path = path.with_name(f".{path.name}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(snapshot), encoding="utf-8")
            os.replace(tmp_path, path)
            os.chmod(path, 0o600)
        except OSError as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise SecretAccessError(SECRET_ACCESS_FAILURE_MESSAGE) from exc


_PROVIDER: SecretProvider | None = None
_PROVIDER_LOCK = Lock()


def resolve_amazon_secret_backend(settings: Settings | None = None) -> str:
    """Normalize AMAZON_SECRET_BACKEND. Default is development."""
    cfg = settings or get_settings()
    return (cfg.amazon_secret_backend or AMAZON_SECRET_BACKEND_DEVELOPMENT).strip().lower()


class SecretProviderFactory:
    """Select a SecretProvider from configuration. Fail closed except development.

    Production is a reserved backend. It must be selected explicitly and must
    not fall back to DevelopmentSecretProvider or sandbox .env tokens.
    A future cloud implementation must still satisfy SecretProvider:
    put_secret, get_secret, exists, delete_secret; SecretStr only; no logs.
    """

    def __repr__(self) -> str:
        return "SecretProviderFactory()"

    def create(self, settings: Settings | None = None) -> SecretProvider:
        cfg = settings or get_settings()
        backend = resolve_amazon_secret_backend(cfg)
        if backend == AMAZON_SECRET_BACKEND_DEVELOPMENT:
            return DevelopmentSecretProvider(
                sandbox_refresh_token=cfg.sp_api_sandbox_refresh_token,
                default_organization_id=cfg.default_organization_id,
                store_path=cfg.amazon_development_secret_store,
            )
        if backend == AMAZON_SECRET_BACKEND_PRODUCTION:
            raise SecretAccessError(PRODUCTION_SECRET_BACKEND_UNAVAILABLE_MESSAGE)
        raise SecretAccessError(UNKNOWN_SECRET_BACKEND_MESSAGE)


def get_secret_provider(settings: Settings | None = None) -> SecretProvider:
    """Return the configured SecretProvider. Development is the only live backend."""
    global _PROVIDER
    factory = SecretProviderFactory()
    cfg = settings or get_settings()
    backend = resolve_amazon_secret_backend(cfg)
    if backend != AMAZON_SECRET_BACKEND_DEVELOPMENT:
        return factory.create(cfg)
    with _PROVIDER_LOCK:
        if _PROVIDER is None:
            _PROVIDER = factory.create(cfg)
        return _PROVIDER


def reset_secret_provider() -> None:
    """Drop the process-local provider. Used by tests."""
    global _PROVIDER
    with _PROVIDER_LOCK:
        _PROVIDER = None
