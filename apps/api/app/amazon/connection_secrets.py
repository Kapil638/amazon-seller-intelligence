"""Resolve seller secret references from Amazon connection metadata.

Allowed future callers:
- Amazon connection flow
- SP-API credential resolution

Forbidden callers:
- Frontend
- API payloads
- Copilot
- Skills
- EvidenceEnvelope

This module does not create secrets, persist token_reference, call Amazon,
or perform OAuth. It only maps org-scoped connection metadata to a
SecretProvider lookup.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import SecretStr

from app.amazon.secrets import (
    InvalidSecretReferenceError,
    SecretProvider,
    development_sandbox_token_reference,
    get_secret_provider,
    parse_asi_amazon_secret_reference,
)


class AmazonConnectionSecretSource(Protocol):
    """Minimal connection metadata needed to resolve a secret pointer."""

    organization_id: UUID
    id: UUID
    provider: str
    environment: str
    token_reference: str | None


def _id_text(value: UUID | str) -> str:
    return str(value).lower()


class AmazonConnectionSecretResolver:
    """Org-scoped connection → SecretProvider resolution. Read-only."""

    def __init__(self, secret_provider: SecretProvider | None = None) -> None:
        self._secret_provider = secret_provider

    def __repr__(self) -> str:
        return "AmazonConnectionSecretResolver()"

    def __str__(self) -> str:
        return self.__repr__()

    def reference_for(
        self,
        *,
        organization_id: UUID | str,
        connection: AmazonConnectionSecretSource,
    ) -> str:
        """Return the secret pointer for this org-scoped connection. Never a token."""
        self._require_same_organization(organization_id, connection)
        stored = (connection.token_reference or "").strip()
        if stored:
            parsed = parse_asi_amazon_secret_reference(stored)
            if _id_text(parsed.organization_id) != _id_text(connection.organization_id):
                raise InvalidSecretReferenceError()
            if _id_text(parsed.connection_id) != _id_text(connection.id):
                raise InvalidSecretReferenceError()
            if parsed.provider != connection.provider.upper():
                raise InvalidSecretReferenceError()
            if parsed.environment != connection.environment.upper():
                raise InvalidSecretReferenceError()
            return parsed.value
        if connection.provider.upper() == "SP_API" and connection.environment.upper() == "SANDBOX":
            return development_sandbox_token_reference(connection.organization_id)
        raise InvalidSecretReferenceError()

    def resolve_refresh_token(
        self,
        *,
        organization_id: UUID | str,
        connection: AmazonConnectionSecretSource,
    ) -> SecretStr:
        """Fetch seller refresh material for the connection. Does not persist."""
        reference = self.reference_for(
            organization_id=organization_id,
            connection=connection,
        )
        provider = self._secret_provider or get_secret_provider()
        return provider.get_secret(reference)

    def _require_same_organization(
        self,
        organization_id: UUID | str,
        connection: AmazonConnectionSecretSource,
    ) -> None:
        if _id_text(organization_id) != _id_text(connection.organization_id):
            raise InvalidSecretReferenceError()
