"""Seller connection validation. Handshake only; no ingest, Copilot, or Skills.

Resolves the org-scoped token_reference through SecretProvider, calls
GET /sellers/v1/marketplaceParticipations, and returns allowed metadata.
Does not persist connection rows; AmazonConnectionService applies status.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.amazon.connection_secrets import AmazonConnectionSecretResolver
from app.amazon.lwa_token import oauth_application_credentials
from app.amazon.sandbox import GET_MARKETPLACE_PARTICIPATIONS
from app.amazon.secrets import (
    InvalidSecretReferenceError,
    SecretAccessError,
    SecretNotFoundError,
    SecretProvider,
    get_secret_provider,
)
from app.amazon.sellers import (
    AmazonSpApiSellersClient,
    participating_marketplaces,
    sp_api_base_url,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)

logger = logging.getLogger(__name__)

SAFE_VALIDATION_MESSAGE = "Amazon seller connection could not be validated."
INVALID_REFRESH_MESSAGE = "Amazon seller authorization is no longer valid."
SECRET_ACCESS_MESSAGE = "Amazon seller credentials could not be retrieved."
UNAVAILABLE_MESSAGE = "Amazon SP-API is temporarily unavailable."
IDENTITY_UNAVAILABLE_MESSAGE = "Amazon seller marketplace participation was not found."
IDENTITY_CONFLICT_MESSAGE = "Amazon seller identity could not be confirmed."
IDENTITY_MISSING_MESSAGE = "Amazon seller identity is not available for this connection. Connect Amazon again to restore access."

SellersClientFactory = Callable[..., AmazonSpApiSellersClient]


def _normalize_participations(
    payload: list,
) -> list[NormalizedMarketplaceParticipation]:
    """Build the typed, normalized collection for reconciliation.

    Preserves every entry Amazon returned, including non-participating and
    suspended marketplaces — `participating_marketplaces()` filters those
    out for the (unrelated) connection-status gate, but 12B.2B reconciliation
    must not silently drop them. Entries with a blank marketplace id after
    stripping are malformed and are skipped; only a count is logged, never
    the payload itself.
    """
    normalized: list[NormalizedMarketplaceParticipation] = []
    skipped = 0
    for item in payload:
        marketplace_id = (item.marketplace.id or "").strip()
        if not marketplace_id:
            skipped += 1
            continue
        normalized.append(
            NormalizedMarketplaceParticipation(
                marketplace_id=marketplace_id,
                name=(item.marketplace.name or "").strip() or None,
                country_code=(item.marketplace.country_code or "").strip() or None,
                default_currency_code=(item.marketplace.default_currency_code or "").strip() or None,
                default_language_code=(item.marketplace.default_language_code or "").strip() or None,
                domain_name=(item.marketplace.domain_name or "").strip() or None,
                store_name=(item.store_name or "").strip() or None,
                is_participating=item.participation.is_participating,
                has_suspended_listings=item.participation.has_suspended_listings,
            )
        )
    if skipped:
        logger.info("amazon seller validation skipped malformed participations count=%s", skipped)
    return normalized


class SellerValidationTarget(Protocol):
    organization_id: UUID
    id: UUID
    provider: str
    environment: str
    region: str
    token_reference: str | None
    status: str
    selling_partner_id: str | None


class SellerMarketplace(BaseModel):
    """Allowed marketplace metadata. Not a canonical identity table."""

    model_config = ConfigDict(extra="forbid")

    marketplace_id: str
    country_code: str


class NormalizedMarketplaceParticipation(BaseModel):
    """Typed, normalized participation for 12B.2B reconciliation.

    Built field-by-field from the parsed Sellers response — never a raw
    Amazon payload passthrough. Includes non-participating and suspended
    entries, unlike `SellerMarketplace`/`participating_marketplaces()`,
    which exist only to gate connection-status and stay unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    marketplace_id: str
    name: str | None = None
    country_code: str | None = None
    default_currency_code: str | None = None
    default_language_code: str | None = None
    domain_name: str | None = None
    store_name: str | None = None
    is_participating: bool
    has_suspended_listings: bool


class SellerValidationResult(BaseModel):
    """Handshake result. Never includes tokens, credentials, or Amazon payloads."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    selling_partner_id: str | None = None
    marketplaces: list[SellerMarketplace] = Field(default_factory=list)
    participations: list[NormalizedMarketplaceParticipation] = Field(default_factory=list)
    connection_status: str
    reason: str
    operation: str = GET_MARKETPLACE_PARTICIPATIONS
    message: str | None = None
    revoke_secret: bool = False


class AmazonSellerValidationService:
    """Validate an authorized seller connection using SP-API Sellers."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        secret_provider: SecretProvider | None = None,
        resolver: AmazonConnectionSecretResolver | None = None,
        sellers_client_factory: SellersClientFactory | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._secret_provider = secret_provider
        self._resolver = resolver
        self._sellers_client_factory = sellers_client_factory
        self._transport = transport

    def __repr__(self) -> str:
        return "AmazonSellerValidationService()"

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def _secrets(self) -> SecretProvider:
        return self._secret_provider or get_secret_provider(self._cfg())

    def _secret_resolver(self) -> AmazonConnectionSecretResolver:
        return self._resolver or AmazonConnectionSecretResolver(secret_provider=self._secrets())

    def _client(
        self,
        *,
        refresh_token: SecretStr,
        environment: str,
        region: str,
    ) -> AmazonSpApiSellersClient:
        cfg = self._cfg()
        client_id, client_secret = oauth_application_credentials(cfg)
        factory = self._sellers_client_factory or AmazonSpApiSellersClient
        return factory(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=cfg.sp_api_lwa_token_url,
            base_url=sp_api_base_url(
                region=region,
                environment=environment,
                sandbox_override=cfg.sp_api_sandbox_base_url,
                production_override=cfg.sp_api_production_base_url,
            ),
            region=region,
            timeout_seconds=cfg.sp_api_timeout_seconds,
            user_agent=cfg.sp_api_user_agent,
            transport=self._transport,
        )

    def _failed(
        self,
        *,
        connection_status: str,
        reason: str,
        message: str,
        revoke_secret: bool = False,
    ) -> SellerValidationResult:
        return SellerValidationResult(
            valid=False,
            connection_status=connection_status,
            reason=reason,
            message=message,
            revoke_secret=revoke_secret,
        )

    async def validate(
        self,
        *,
        organization_id: UUID,
        connection: SellerValidationTarget,
    ) -> SellerValidationResult:
        """Call Sellers for this org-scoped connection. Never uses selling_partner_id as tenant."""
        if not (connection.token_reference or "").strip():
            logger.info(
                "amazon seller validation rejected reason=secret_reference_invalid connection_id=%s",
                connection.id,
            )
            return self._failed(
                connection_status="pending_validation",
                reason="secret_reference_invalid",
                message=SECRET_ACCESS_MESSAGE,
            )
        resolver = self._secret_resolver()
        try:
            refresh_token = resolver.resolve_refresh_token(
                organization_id=organization_id,
                connection=connection,
            )
        except InvalidSecretReferenceError:
            logger.info(
                "amazon seller validation rejected reason=secret_reference_invalid connection_id=%s",
                connection.id,
            )
            return self._failed(
                connection_status="pending_validation",
                reason="secret_reference_invalid",
                message=SECRET_ACCESS_MESSAGE,
            )
        except (SecretNotFoundError, SecretAccessError):
            logger.info(
                "amazon seller validation failed reason=secret_access_failed connection_id=%s",
                connection.id,
            )
            return self._failed(
                connection_status="pending_validation",
                reason="secret_access_failed",
                message=SECRET_ACCESS_MESSAGE,
            )
        except SpApiConfigurationError:
            logger.info(
                "amazon seller validation failed reason=lwa_configuration connection_id=%s",
                connection.id,
            )
            return self._failed(
                connection_status="pending_validation",
                reason="lwa_configuration",
                message=SAFE_VALIDATION_MESSAGE,
            )

        try:
            parsed = await self._client(
                refresh_token=refresh_token,
                environment=connection.environment,
                region=connection.region,
            ).get_marketplace_participations()
        except SpApiConfigurationError:
            logger.info(
                "amazon seller validation failed reason=lwa_configuration connection_id=%s",
                connection.id,
            )
            return self._failed(
                connection_status="pending_validation",
                reason="lwa_configuration",
                message=SAFE_VALIDATION_MESSAGE,
            )
        except SpApiAuthenticationError:
            logger.info(
                "amazon seller validation failed reason=requires_reauth connection_id=%s",
                connection.id,
            )
            return self._failed(
                connection_status="error",
                reason="requires_reauth",
                message=INVALID_REFRESH_MESSAGE,
                revoke_secret=True,
            )
        except (SpApiRequestFailedError, SpApiRateLimitedError, SpApiParseFailedError):
            logger.info(
                "amazon seller validation failed reason=sp_api_unavailable connection_id=%s",
                connection.id,
            )
            return self._failed(
                connection_status="degraded",
                reason="sp_api_unavailable",
                message=UNAVAILABLE_MESSAGE,
            )
        finally:
            del refresh_token

        marketplaces = [
            SellerMarketplace(marketplace_id=marketplace_id, country_code=country_code)
            for marketplace_id, country_code in participating_marketplaces(parsed.payload or [])
        ]
        participations = _normalize_participations(parsed.payload or [])

        # `getMarketplaceParticipations` does not define a `sellingPartnerId`
        # field on its response — the official schema's `payload` is a plain
        # `MarketplaceParticipationList`. The only authoritative seller
        # identity is the one captured during the OAuth callback and already
        # persisted on this connection row. Never infer identity from store
        # name, marketplace id, domain, or token, and never expect Amazon to
        # supply it again here.
        stored_selling_partner_id = (connection.selling_partner_id or "").strip() or None
        if not stored_selling_partner_id:
            logger.info(
                "amazon seller validation rejected reason=identity_missing connection_id=%s",
                connection.id,
            )
            return self._failed(
                connection_status="error",
                reason="identity_missing",
                message=IDENTITY_MISSING_MESSAGE,
            )
        if not marketplaces:
            logger.info(
                "amazon seller validation failed reason=seller_identity_unavailable connection_id=%s",
                connection.id,
            )
            return self._failed(
                connection_status="pending_validation",
                reason="seller_identity_unavailable",
                message=IDENTITY_UNAVAILABLE_MESSAGE,
            )
        logger.info(
            "amazon seller validation succeeded connection_id=%s operation=%s marketplaces=%s",
            connection.id,
            GET_MARKETPLACE_PARTICIPATIONS,
            len(marketplaces),
        )
        return SellerValidationResult(
            valid=True,
            selling_partner_id=stored_selling_partner_id,
            marketplaces=marketplaces,
            participations=participations,
            connection_status="connected",
            reason="validated",
        )
