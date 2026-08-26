"""Amazon Connection Beta service. Connectivity metadata only. No Amazon data ingest."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.exc import IntegrityError

from app.amazon.ads_api import ADS_API_PROVIDER, ADS_API_STATUS
from app.amazon.lwa import MISSING_CREDENTIALS_MESSAGE, credentials_configured
from app.amazon.lwa_token import AmazonLwaTokenService
from app.amazon.models import LwaAuthorizationGrant, MarketplaceParticipationsSandboxResult
from app.amazon.oauth import (
    build_seller_central_consent_url,
    hash_oauth_state,
    new_oauth_state,
    oauth_state_expiry,
    seller_central_consent_origin,
    seller_connection_marketplace,
)
from app.amazon.oauth_callback import (
    AuthorizationCodeReceived,
    is_amazon_access_denied,
    normalize_selling_partner_id,
    wrap_authorization_code,
)
from app.amazon.sandbox import GET_MARKETPLACE_PARTICIPATIONS, AmazonSpApiSandboxClient
from app.amazon.secrets import (
    InvalidSecretReferenceError,
    SecretAccessError,
    SecretNotFoundError,
    SecretProvider,
    build_asi_secret_reference,
    get_secret_provider,
)
from app.amazon.seller_validation import AmazonSellerValidationService, SellerValidationResult
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    PersistenceError,
    PersistenceNotConfiguredError,
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)
from app.persistence.database import current_organization_id, persistence_enabled, session_scope
from app.persistence.models import AmazonConnection
from app.persistence.repositories import AmazonConnectionRepository, AmazonOAuthStateRepository

logger = logging.getLogger(__name__)

ConnectionStatus = Literal["CONNECTED", "NOT_CONNECTED", "FAILED"]
ConnectionLifecycleStatus = Literal[
    "not_connected",
    "pending_authorization",
    "pending_validation",
    "connected",
    "degraded",
    "revoked",
    "error",
]
SpApiProvider = Literal["SP_API"]
AdsApiProvider = Literal["ADS_API"]
ConnectionEnvironment = Literal["SANDBOX", "PRODUCTION"]
_SELLER_VALIDATION_STATUSES = frozenset({"pending_validation", "connected", "degraded"})

_SECRET_FIELDS = frozenset(
    {
        "refresh_token",
        "access_token",
        "client_secret",
        "client_id",
        "token_reference",
        "authorization_code",
        "spapi_oauth_code",
        "state",
    }
)


class SpApiConnectivityChecker(Protocol):
    async def get_marketplace_participations(self) -> MarketplaceParticipationsSandboxResult: ...


SandboxClientFactory = Callable[[], SpApiConnectivityChecker]


class LwaAuthorizationCodeExchanger(Protocol):
    def exchange_authorization_code(self, authorization_code: SecretStr) -> LwaAuthorizationGrant: ...


class _PendingLwaExchange:
    """In-memory callback handoff. Authorization code is never logged or persisted."""

    __slots__ = ("connection_id", "provider", "environment", "authorization_code")

    def __init__(
        self,
        *,
        connection_id: UUID,
        provider: str,
        environment: str,
        authorization_code: SecretStr,
    ) -> None:
        self.connection_id = connection_id
        self.provider = provider
        self.environment = environment
        self.authorization_code = authorization_code

    def __repr__(self) -> str:
        return "_PendingLwaExchange()"


@dataclass(frozen=True)
class _SellerConnectionSnapshot:
    """Detached connection fields for handshake. Never includes token material."""

    organization_id: UUID
    id: UUID
    provider: str
    environment: str
    region: str
    token_reference: str | None
    status: str
    selling_partner_id: str | None


class AdsApiConnectionPlaceholder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: AdsApiProvider = "ADS_API"
    status: Literal["NOT_CONNECTED"] = "NOT_CONNECTED"


class AmazonConnectionOverview(BaseModel):
    """Sanitized connection view. Never includes tokens, secrets, or token_reference."""

    model_config = ConfigDict(extra="forbid")

    status: ConnectionStatus
    connection_status: ConnectionLifecycleStatus = "not_connected"
    persisted: bool = False
    provider: SpApiProvider = "SP_API"
    environment: ConnectionEnvironment = "PRODUCTION"
    region: str = "na"
    marketplace: str
    application: str
    credentials_configured: bool
    selling_partner_id: str | None = None
    authorized_at: datetime | None = None
    last_successful_validation_at: datetime | None = None
    last_successful_sync_at: datetime | None = None
    last_error_code: str | None = None
    last_test_at: datetime | None = None
    organization_id: str
    ads_api: AdsApiConnectionPlaceholder = Field(default_factory=AdsApiConnectionPlaceholder)


class AmazonConnectionTestResult(BaseModel):
    """Sanitized connectivity result. Never includes tokens, secrets, or Amazon payloads."""

    model_config = ConfigDict(extra="forbid")

    status: ConnectionStatus
    provider: SpApiProvider = "SP_API"
    environment: ConnectionEnvironment = "SANDBOX"
    marketplace: str
    operation: str
    tested_at: datetime
    message: str | None = None


class AmazonAuthorizationStart(BaseModel):
    """Seller Central consent URL for Connect Amazon. Raw state is only in the URL."""

    model_config = ConfigDict(extra="forbid")

    authorization_url: str
    expires_at: datetime
    connection_status: ConnectionLifecycleStatus
    provider: SpApiProvider = "SP_API"
    environment: ConnectionEnvironment = "PRODUCTION"
    organization_id: str


class AmazonConnectionService:
    """Connection metadata overlay, sandbox Test Connection, and seller handshake.

    GET/overview never calls Amazon. Sandbox env-token test never persists
    `connected`. Seller validation may persist `connected` after Sellers 200.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        sandbox_client_factory: SandboxClientFactory | None = None,
        secret_provider: SecretProvider | None = None,
        lwa_token_service: LwaAuthorizationCodeExchanger | None = None,
        seller_validator: AmazonSellerValidationService | None = None,
        sp_api_transport: Any | None = None,
    ) -> None:
        self._settings = settings
        self._sandbox_client_factory = sandbox_client_factory
        self._secret_provider = secret_provider
        self._lwa_token_service = lwa_token_service
        self._seller_validator = seller_validator
        self._sp_api_transport = sp_api_transport

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def _org_id(self) -> UUID:
        return current_organization_id()

    def _credentials_ready(self, cfg: Settings) -> bool:
        return credentials_configured(
            cfg.sp_api_lwa_client_id,
            cfg.sp_api_lwa_client_secret,
            cfg.sp_api_sandbox_refresh_token,
        )

    def _require_persistence(self) -> None:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError("Amazon connection persistence is not configured.")

    def _reject_secrets(self, fields: dict[str, Any]) -> None:
        if _SECRET_FIELDS.intersection(fields):
            raise PersistenceError("Amazon connection service cannot store secret fields.")

    def _connection_marketplace(self, cfg: Settings, region: str) -> str:
        return seller_connection_marketplace(
            region=region,
            default_marketplace=cfg.default_marketplace,
        )

    def _env_overview(self, cfg: Settings) -> AmazonConnectionOverview:
        region = (cfg.sp_api_region or "na").strip().lower() or "na"
        return AmazonConnectionOverview(
            status="NOT_CONNECTED",
            connection_status="not_connected",
            persisted=False,
            environment="PRODUCTION",
            marketplace=self._connection_marketplace(cfg, region),
            application=cfg.sp_api_application_name,
            region=region,
            credentials_configured=self._credentials_ready(cfg),
            last_test_at=None,
            organization_id=str(self._org_id()),
            ads_api=AdsApiConnectionPlaceholder(provider=ADS_API_PROVIDER, status=ADS_API_STATUS),
        )

    def _from_row(self, row: AmazonConnection, cfg: Settings) -> AmazonConnectionOverview:
        environment: ConnectionEnvironment = "PRODUCTION" if row.environment == "PRODUCTION" else "SANDBOX"
        lifecycle: ConnectionLifecycleStatus
        if row.status in (
            "not_connected",
            "pending_authorization",
            "pending_validation",
            "connected",
            "degraded",
            "revoked",
            "error",
        ):
            lifecycle = row.status  # type: ignore[assignment]
        else:
            lifecycle = "not_connected"
        logger.info(
            "amazon connection overview persisted=true id=%s status=%s",
            row.id,
            row.status,
        )
        return AmazonConnectionOverview(
            status="NOT_CONNECTED",
            connection_status=lifecycle,
            persisted=True,
            provider="SP_API",
            environment=environment,
            region=row.region,
            marketplace=self._connection_marketplace(cfg, row.region),
            application=cfg.sp_api_application_name,
            credentials_configured=self._credentials_ready(cfg),
            selling_partner_id=row.selling_partner_id,
            authorized_at=row.authorized_at,
            last_successful_validation_at=row.last_successful_validation_at,
            last_successful_sync_at=row.last_successful_sync_at,
            last_error_code=row.last_error_code,
            last_test_at=row.last_successful_validation_at,
            organization_id=str(row.organization_id),
            ads_api=AdsApiConnectionPlaceholder(provider=ADS_API_PROVIDER, status=ADS_API_STATUS),
        )

    def overview(
        self,
        *,
        provider: str = "SP_API",
        environment: str | None = None,
    ) -> AmazonConnectionOverview:
        cfg = self._cfg()
        if not persistence_enabled():
            return self._env_overview(cfg)
        with session_scope() as session:
            repo = AmazonConnectionRepository(session)
            if environment:
                row = repo.get(self._org_id(), provider=provider, environment=environment)
                if row is None:
                    return self._env_overview(cfg)
                return self._from_row(row, cfg)
            production = repo.get(self._org_id(), provider=provider, environment="PRODUCTION")
            if production is not None:
                return self._from_row(production, cfg)
            # Connect Amazon is PRODUCTION. Do not surface leftover SANDBOX
            # Test Connection rows on the seller-authorization card.
            return self._env_overview(cfg)

    def create_connection(
        self,
        *,
        provider: str = "SP_API",
        environment: str = "SANDBOX",
        region: str | None = None,
        status: str = "not_connected",
        selling_partner_id: str | None = None,
        application_id: str | None = None,
    ) -> AmazonConnectionOverview:
        self._require_persistence()
        cfg = self._cfg()
        try:
            with session_scope() as session:
                repo = AmazonConnectionRepository(session)
                row = repo.create(
                    organization_id=self._org_id(),
                    provider=provider,
                    environment=environment,
                    region=region or cfg.sp_api_region,
                    status=status,
                    selling_partner_id=selling_partner_id,
                    application_id=application_id,
                )
                logger.info("amazon connection created id=%s status=%s", row.id, row.status)
                return self._from_row(row, cfg)
        except IntegrityError as exc:
            raise PersistenceError(
                "An Amazon connection already exists for this organization, provider, and environment."
            ) from exc

    def update_connection(self, connection_id: UUID, **fields: Any) -> AmazonConnectionOverview:
        self._require_persistence()
        self._reject_secrets(fields)
        cfg = self._cfg()
        try:
            with session_scope() as session:
                repo = AmazonConnectionRepository(session)
                row = repo.update(self._org_id(), connection_id, **fields)
                if row is None:
                    raise PersistenceError("Amazon connection was not found.")
                logger.info("amazon connection updated id=%s status=%s", row.id, row.status)
                return self._from_row(row, cfg)
        except TypeError as exc:
            raise PersistenceError("Invalid Amazon connection update.") from exc

    def delete_connection(self, connection_id: UUID) -> bool:
        self._require_persistence()
        with session_scope() as session:
            repo = AmazonConnectionRepository(session)
            deleted = repo.delete(self._org_id(), connection_id)
        if not deleted:
            raise PersistenceError("Amazon connection was not found.")
        logger.info("amazon connection deleted id=%s", connection_id)
        return True

    def _ensure_pending_connection(
        self,
        session,
        *,
        provider: str,
        environment: str,
        region: str,
        application_id: str,
    ) -> AmazonConnection:
        repo = AmazonConnectionRepository(session)
        row = repo.get(self._org_id(), provider=provider, environment=environment)
        if row is None:
            return repo.create(
                organization_id=self._org_id(),
                provider=provider,
                environment=environment,
                region=region,
                status="pending_authorization",
                application_id=application_id or None,
            )
        fields: dict[str, Any] = {}
        if row.status != "pending_authorization":
            fields["status"] = "pending_authorization"
        if region and row.region != region:
            fields["region"] = region
        if application_id and row.application_id != application_id:
            fields["application_id"] = application_id
        if not fields:
            return row
        updated = repo.update(self._org_id(), row.id, **fields)
        if updated is None:
            raise PersistenceError("Amazon connection was not found.")
        return updated

    def start_authorization(
        self,
        *,
        environment: ConnectionEnvironment = "PRODUCTION",
        provider: SpApiProvider = "SP_API",
    ) -> AmazonAuthorizationStart:
        """Create hashed OAuth state and return a Seller Central consent URL.

        Does not redirect, exchange codes, write secrets, or mark the connection
        connected / pending_validation.
        """
        self._require_persistence()
        self._reject_secrets({"environment": environment, "provider": provider})
        cfg = self._cfg()
        application_id = cfg.consent_application_id()
        if not application_id:
            raise SpApiConfigurationError("Amazon application is not configured.")
        raw_state, state_hash = new_oauth_state()
        expires_at = oauth_state_expiry(ttl_seconds=cfg.sp_api_oauth_state_ttl_seconds)
        region = (cfg.sp_api_region or "na").strip().lower() or "na"
        marketplace = self._connection_marketplace(cfg, region)
        origin = seller_central_consent_origin(
            marketplace=marketplace,
            region=region,
            override=cfg.sp_api_oauth_consent_base_url,
        )
        authorization_url = build_seller_central_consent_url(
            origin=origin,
            application_id=application_id,
            state=raw_state,
            version_beta=cfg.sp_api_consent_version_beta,
        )
        del raw_state
        try:
            with session_scope() as session:
                connection = self._ensure_pending_connection(
                    session,
                    provider=provider,
                    environment=environment,
                    region=region,
                    application_id=application_id,
                )
                AmazonOAuthStateRepository(session).create(
                    organization_id=self._org_id(),
                    provider=provider,
                    environment=environment,
                    connection_id=connection.id,
                    state_hash=state_hash,
                    expires_at=expires_at,
                )
                logger.info(
                    "amazon authorization started connection_id=%s status=%s",
                    connection.id,
                    connection.status,
                )
                return AmazonAuthorizationStart(
                    authorization_url=authorization_url,
                    expires_at=expires_at,
                    connection_status="pending_authorization",
                    provider="SP_API",
                    environment=environment,
                    organization_id=str(self._org_id()),
                )
        except IntegrityError as exc:
            raise PersistenceError("Amazon authorization could not be started.") from exc

    def _secrets(self) -> SecretProvider:
        return self._secret_provider or get_secret_provider(self._cfg())

    def _lwa(self) -> LwaAuthorizationCodeExchanger:
        if self._lwa_token_service is not None:
            return self._lwa_token_service
        return AmazonLwaTokenService.from_settings(self._cfg())

    def _mark_callback_error(self, connection_id: UUID, code: str) -> None:
        with session_scope() as session:
            AmazonConnectionRepository(session).update(
                self._org_id(),
                connection_id,
                last_error_code=code,
                last_error_at=datetime.now(UTC),
            )

    def complete_authorization_callback(
        self,
        *,
        state: str | None = None,
        spapi_oauth_code: str | None = None,
        code: str | None = None,
        error: str | None = None,
        error_description: str | None = None,
        selling_partner_id: str | None = None,
    ) -> AuthorizationCodeReceived:
        """Validate state, exchange the authorization code, store the refresh token.

        Organization is ASI context plus the hashed state row. Amazon's Website
        Authorization Workflow includes `selling_partner_id` on every redirect;
        it is captured as seller-account metadata onto the connection row, but
        it is never used for tenancy, org identity, or authorization. Does not
        call SP-API or mark the connection connected.
        """
        normalized_selling_partner_id = normalize_selling_partner_id(selling_partner_id)
        del selling_partner_id
        del error_description
        self._require_persistence()
        org_id = self._org_id()
        raw_state = (state or "").strip()
        held_code = wrap_authorization_code(spapi_oauth_code=spapi_oauth_code, code=code)
        code_present = held_code is not None
        denied = is_amazon_access_denied(error)

        def _result(
            *,
            connection_id: str,
            connection_status: str,
            outcome: str,
            notice: str,
            reason: str,
            authorization_code_present: bool = False,
        ) -> AuthorizationCodeReceived:
            return AuthorizationCodeReceived(
                connection_id=connection_id,
                organization_id=str(org_id),
                authorization_code_present=authorization_code_present,
                connection_status=connection_status,
                outcome=outcome,  # type: ignore[arg-type]
                notice=notice,  # type: ignore[arg-type]
                reason=reason,
            )

        try:
            pending = self._consume_usable_oauth_callback(
                org_id=org_id,
                raw_state=raw_state,
                held_code=held_code,
                code_present=code_present,
                denied=denied,
                result=_result,
            )
            if not isinstance(pending, _PendingLwaExchange):
                return pending
            return self._store_refresh_token_from_authorization_code(
                pending, _result, selling_partner_id=normalized_selling_partner_id
            )
        finally:
            del held_code
            del raw_state

    def _consume_usable_oauth_callback(
        self,
        *,
        org_id: UUID,
        raw_state: str,
        held_code: SecretStr | None,
        code_present: bool,
        denied: bool,
        result: Callable[..., AuthorizationCodeReceived],
    ) -> AuthorizationCodeReceived | _PendingLwaExchange:
        if not raw_state:
            logger.info("amazon oauth callback rejected reason=oauth_state_missing")
            return result(
                connection_id="",
                connection_status="pending_authorization",
                outcome="denied" if denied else "invalid",
                notice="denied" if denied else "error",
                reason="oauth_state_missing",
            )

        digest = hash_oauth_state(raw_state)
        with session_scope() as session:
            states = AmazonOAuthStateRepository(session)
            connections = AmazonConnectionRepository(session)
            row, classification = states.classify(org_id, digest)
            if row is None or classification == "missing":
                logger.info("amazon oauth callback rejected reason=oauth_state_invalid")
                return result(
                    connection_id="",
                    connection_status="pending_authorization",
                    outcome="invalid",
                    notice="denied" if denied else "error",
                    reason="oauth_state_invalid",
                )
            connection = connections.get_by_id(org_id, row.connection_id)
            if connection is None or connection.organization_id != org_id:
                logger.info("amazon oauth callback rejected reason=oauth_state_unbound")
                return result(
                    connection_id=str(row.connection_id),
                    connection_status="pending_authorization",
                    outcome="invalid",
                    notice="error",
                    reason="oauth_state_unbound",
                )
            if classification == "expired":
                connections.update(
                    org_id,
                    connection.id,
                    last_error_code="oauth_state_expired",
                    last_error_at=datetime.now(UTC),
                )
                logger.info(
                    "amazon oauth callback rejected reason=oauth_state_expired connection_id=%s",
                    connection.id,
                )
                return result(
                    connection_id=str(connection.id),
                    connection_status=connection.status,
                    outcome="invalid",
                    notice="error",
                    reason="oauth_state_expired",
                )
            if classification == "consumed":
                connections.update(
                    org_id,
                    connection.id,
                    last_error_code="oauth_state_consumed",
                    last_error_at=datetime.now(UTC),
                )
                logger.info(
                    "amazon oauth callback rejected reason=oauth_state_consumed connection_id=%s",
                    connection.id,
                )
                return result(
                    connection_id=str(connection.id),
                    connection_status=connection.status,
                    outcome="invalid",
                    notice="error",
                    reason="oauth_state_consumed",
                )
            if denied:
                states.consume(org_id, row.id)
                connections.update(
                    org_id,
                    connection.id,
                    last_error_code="access_denied",
                    last_error_at=datetime.now(UTC),
                )
                logger.info(
                    "amazon oauth callback denied connection_id=%s status=%s",
                    connection.id,
                    connection.status,
                )
                return result(
                    connection_id=str(connection.id),
                    connection_status=connection.status,
                    outcome="denied",
                    notice="denied",
                    reason="access_denied",
                )
            if not code_present or held_code is None:
                logger.info(
                    "amazon oauth callback rejected reason=missing_code connection_id=%s",
                    connection.id,
                )
                return result(
                    connection_id=str(connection.id),
                    connection_status=connection.status,
                    outcome="invalid",
                    notice="error",
                    reason="missing_code",
                )
            consumed = states.consume(org_id, row.id)
            if consumed is None:
                logger.info(
                    "amazon oauth callback rejected reason=oauth_state_consumed connection_id=%s",
                    connection.id,
                )
                return result(
                    connection_id=str(connection.id),
                    connection_status=connection.status,
                    outcome="invalid",
                    notice="error",
                    reason="oauth_state_consumed",
                )
            return _PendingLwaExchange(
                connection_id=connection.id,
                provider=connection.provider,
                environment=connection.environment,
                authorization_code=held_code,
            )

    def _store_refresh_token_from_authorization_code(
        self,
        pending: _PendingLwaExchange,
        result: Callable[..., AuthorizationCodeReceived],
        *,
        selling_partner_id: str | None = None,
    ) -> AuthorizationCodeReceived:
        connection_id = pending.connection_id
        org_id = self._org_id()

        if not selling_partner_id:
            # Fail closed. Amazon's Website Authorization Workflow guarantees
            # `selling_partner_id` on every redirect for a self-authorized
            # app; its absence here (missing, or rejected by normalization as
            # invalid — oversized, token-shaped, control characters, etc.) is
            # not a normal omission to tolerate. Proceeding would exchange a
            # grant for an unverified seller and could overwrite the active
            # secret behind this connection's deterministic reference. No
            # Amazon call, no SecretProvider access, no identity/status
            # change occurs — applies equally to first authorization,
            # reauthorization, reconnect, and concurrent attempts, since none
            # of that logic is reached before this return. The rejected value
            # itself is never included in the log line, the result, or any
            # exception — only connection_id.
            self._mark_callback_error(connection_id, "seller_identity_missing")
            logger.info(
                "amazon oauth callback rejected reason=seller_identity_missing connection_id=%s",
                connection_id,
            )
            return result(
                connection_id=str(connection_id),
                connection_status="pending_authorization",
                outcome="invalid",
                notice="error",
                reason="seller_identity_missing",
                authorization_code_present=True,
            )

        # Identity conflict is checked next, using only values already known
        # (the callback-supplied identifier and the connection's own current
        # selling_partner_id) — before the LWA code is ever exchanged and
        # before SecretProvider is touched. The active secret reference is
        # derived from (org, connection), not from seller identity, so a
        # conflicting reauthorization must never reach put_secret: doing so
        # would silently replace the prior seller's grant at that reference
        # while the database still names the prior seller, an unsafe
        # credential/identity mismatch.
        with session_scope() as session:
            current = AmazonConnectionRepository(session).get_by_id(org_id, connection_id)
        if current is None:
            self._mark_callback_error(connection_id, "token_bind_failed")
            logger.info(
                "amazon oauth callback failed reason=token_bind_failed connection_id=%s",
                connection_id,
            )
            return result(
                connection_id=str(connection_id),
                connection_status="pending_authorization",
                outcome="invalid",
                notice="error",
                reason="token_bind_failed",
                authorization_code_present=True,
            )
        existing_selling_partner_id = (current.selling_partner_id or "").strip() or None
        if (
            selling_partner_id
            and existing_selling_partner_id
            and selling_partner_id != existing_selling_partner_id
        ):
            self._mark_callback_error(connection_id, "identity_conflict")
            logger.info(
                "amazon oauth callback rejected reason=identity_conflict connection_id=%s",
                connection_id,
            )
            return result(
                connection_id=str(connection_id),
                connection_status="pending_authorization",
                outcome="invalid",
                notice="error",
                reason="identity_conflict",
                authorization_code_present=True,
            )

        try:
            grant = self._lwa().exchange_authorization_code(pending.authorization_code)
        except SpApiConfigurationError:
            self._mark_callback_error(connection_id, "lwa_configuration")
            logger.info(
                "amazon oauth callback failed reason=lwa_configuration connection_id=%s",
                connection_id,
            )
            return result(
                connection_id=str(connection_id),
                connection_status="pending_authorization",
                outcome="invalid",
                notice="error",
                reason="lwa_configuration",
                authorization_code_present=True,
            )
        except SpApiAuthenticationError:
            self._mark_callback_error(connection_id, "lwa_authentication")
            logger.info(
                "amazon oauth callback failed reason=lwa_authentication connection_id=%s",
                connection_id,
            )
            return result(
                connection_id=str(connection_id),
                connection_status="pending_authorization",
                outcome="invalid",
                notice="error",
                reason="lwa_authentication",
                authorization_code_present=True,
            )
        except (SpApiRequestFailedError, SpApiRateLimitedError, SpApiParseFailedError):
            self._mark_callback_error(connection_id, "lwa_unavailable")
            logger.info(
                "amazon oauth callback failed reason=lwa_unavailable connection_id=%s",
                connection_id,
            )
            return result(
                connection_id=str(connection_id),
                connection_status="pending_authorization",
                outcome="invalid",
                notice="error",
                reason="lwa_unavailable",
                authorization_code_present=True,
            )

        refresh = grant.refresh_token
        del grant

        # Atomic identity claim — the invariant-enforcing step. This single
        # conditional UPDATE is the only thing allowed to change
        # selling_partner_id, and it must complete, successfully, strictly
        # before this attempt is allowed to touch SecretProvider at all. Two
        # concurrent callbacks with different identifiers can never both
        # claim: the database serializes concurrent writers of the same row,
        # so whichever commits second re-evaluates against the already-
        # updated value and affects zero rows. See
        # AmazonConnectionRepository.claim_identity_for_authorization for why
        # this holds on SQLite and PostgreSQL alike, and the 12B.2A
        # concurrency report for the rejected alternatives.
        try:
            with session_scope() as session:
                claimed = AmazonConnectionRepository(session).claim_identity_for_authorization(
                    org_id, connection_id, selling_partner_id=selling_partner_id
                )
        except (PersistenceError, TypeError, IntegrityError):
            del refresh
            self._mark_callback_error(connection_id, "token_bind_failed")
            logger.info(
                "amazon oauth callback failed reason=token_bind_failed connection_id=%s",
                connection_id,
            )
            return result(
                connection_id=str(connection_id),
                connection_status="pending_authorization",
                outcome="invalid",
                notice="error",
                reason="token_bind_failed",
                authorization_code_present=True,
            )
        if not claimed:
            # Lost the identity claim (or the connection vanished). No secret
            # was ever written for this attempt — SecretProvider was never
            # touched, so there is nothing to clean up.
            del refresh
            self._mark_callback_error(connection_id, "identity_conflict")
            logger.info(
                "amazon oauth callback rejected reason=identity_conflict connection_id=%s",
                connection_id,
            )
            return result(
                connection_id=str(connection_id),
                connection_status="pending_authorization",
                outcome="invalid",
                notice="error",
                reason="identity_conflict",
                authorization_code_present=True,
            )

        reference = build_asi_secret_reference(
            provider=pending.provider,
            environment=pending.environment,
            organization_id=org_id,
            connection_id=connection_id,
        )
        secrets = self._secrets()
        stored = False
        try:
            try:
                secrets.put_secret(reference, refresh)
                stored = True
            except (SecretAccessError, InvalidSecretReferenceError, TypeError):
                # The identity claim above is deliberately left in place: this
                # connection's identity was legitimately won by this attempt,
                # and reverting it would let a different seller claim the slot
                # after a merely transient storage failure. The same seller
                # retrying (or Connect Amazon again) will match the already-
                # claimed identifier and proceed normally.
                self._mark_callback_error(connection_id, "secret_storage_failed")
                logger.info(
                    "amazon oauth callback failed reason=secret_storage_failed connection_id=%s",
                    connection_id,
                )
                return result(
                    connection_id=str(connection_id),
                    connection_status="pending_authorization",
                    outcome="invalid",
                    notice="error",
                    reason="secret_storage_failed",
                    authorization_code_present=True,
                )
            try:
                with session_scope() as session:
                    repo = AmazonConnectionRepository(session)
                    bound = repo.bind_token_reference(org_id, connection_id, reference)
                    if bound is None:
                        raise PersistenceError("Amazon connection was not found.")
                    repo.update(
                        org_id,
                        connection_id,
                        status="pending_validation",
                        authorized_at=datetime.now(UTC),
                        last_error_code=None,
                        last_error_at=None,
                    )
            except (PersistenceError, TypeError, InvalidSecretReferenceError, IntegrityError):
                if stored:
                    # The bind+update above ran in one transaction; on failure
                    # session_scope() rolled it back, so this attempt's own
                    # write to `token_reference` was undone regardless. A
                    # fresh read now tells us whether anything is legitimately
                    # relying on this reference already — either a
                    # pre-existing grant from before this attempt, or a
                    # concurrent same-seller attempt that won and committed
                    # while this one was failing. Only delete when nothing is:
                    # deleting an orphan no one has bound yet is harmless
                    # cleanup, but deleting a reference something else already
                    # depends on would destroy a still-valid grant with no way
                    # to recover it (put_secret already overwrote whatever was
                    # there before we ever reached this point).
                    with session_scope() as session:
                        post_failure = AmazonConnectionRepository(session).get_by_id(
                            org_id, connection_id
                        )
                    reference_already_relied_on = bool(
                        post_failure is not None and (post_failure.token_reference or "").strip()
                    )
                    if not reference_already_relied_on:
                        try:
                            secrets.delete_secret(reference)
                        except (SecretAccessError, InvalidSecretReferenceError, TypeError):
                            logger.warning(
                                "amazon oauth callback secret cleanup failed connection_id=%s",
                                connection_id,
                            )
                self._mark_callback_error(connection_id, "token_bind_failed")
                logger.info(
                    "amazon oauth callback failed reason=token_bind_failed connection_id=%s",
                    connection_id,
                )
                return result(
                    connection_id=str(connection_id),
                    connection_status="pending_authorization",
                    outcome="invalid",
                    notice="error",
                    reason="token_bind_failed",
                    authorization_code_present=True,
                )
        finally:
            del refresh

        logger.info(
            "amazon oauth callback token stored connection_id=%s status=pending_validation",
            connection_id,
        )
        return result(
            connection_id=str(connection_id),
            connection_status="pending_validation",
            outcome="token_stored",
            notice="success",
            reason="token_stored",
            authorization_code_present=True,
        )

    def _checker(self) -> SpApiConnectivityChecker:
        factory = self._sandbox_client_factory or AmazonSpApiSandboxClient
        return factory()

    def _validator(self) -> AmazonSellerValidationService:
        if self._seller_validator is not None:
            return self._seller_validator
        return AmazonSellerValidationService(
            settings=self._cfg(),
            secret_provider=self._secrets(),
            transport=self._sp_api_transport,
        )

    def _seller_handshake_snapshot(
        self,
        *,
        provider: str,
        environment: str,
    ) -> _SellerConnectionSnapshot | None:
        if not persistence_enabled():
            return None
        with session_scope() as session:
            row = AmazonConnectionRepository(session).get(
                self._org_id(),
                provider=provider,
                environment=environment,
            )
            if row is None or not (row.token_reference or "").strip():
                return None
            if row.status not in _SELLER_VALIDATION_STATUSES:
                return None
            return _SellerConnectionSnapshot(
                organization_id=row.organization_id,
                id=row.id,
                provider=row.provider,
                environment=row.environment,
                region=row.region,
                token_reference=row.token_reference,
                status=row.status,
                selling_partner_id=row.selling_partner_id,
            )

    def _apply_seller_validation(
        self,
        snapshot: _SellerConnectionSnapshot,
        result: SellerValidationResult,
    ) -> None:
        now = datetime.now(UTC)
        if result.revoke_secret and snapshot.token_reference:
            try:
                self._secrets().delete_secret(snapshot.token_reference)
            except (SecretAccessError, SecretNotFoundError, InvalidSecretReferenceError, TypeError):
                logger.warning(
                    "amazon seller validation secret cleanup failed connection_id=%s",
                    snapshot.id,
                )
        with session_scope() as session:
            repo = AmazonConnectionRepository(session)
            fields: dict[str, Any] = {
                "status": result.connection_status,
                "last_error_code": None if result.valid else result.reason,
                "last_error_at": None if result.valid else now,
            }
            if result.valid:
                fields["last_successful_validation_at"] = now
                if result.selling_partner_id:
                    fields["selling_partner_id"] = result.selling_partner_id
            repo.update(snapshot.organization_id, snapshot.id, **fields)
            if result.revoke_secret:
                repo.clear_token_reference(snapshot.organization_id, snapshot.id)

    async def validate_seller_connection(
        self,
        *,
        provider: SpApiProvider = "SP_API",
        environment: ConnectionEnvironment = "SANDBOX",
    ) -> SellerValidationResult:
        """Handshake an authorized seller grant. Does not ingest seller data."""
        self._require_persistence()
        snapshot = self._seller_handshake_snapshot(provider=provider, environment=environment)
        if snapshot is None:
            logger.info("amazon seller validation skipped reason=not_ready")
            return SellerValidationResult(
                valid=False,
                connection_status="pending_validation",
                reason="not_ready",
                message="Amazon seller connection is not ready to validate.",
            )
        result = await self._validator().validate(
            organization_id=self._org_id(),
            connection=snapshot,
        )
        self._apply_seller_validation(snapshot, result)
        return result

    async def test_sp_api(self) -> AmazonConnectionTestResult:
        cfg = self._cfg()
        tested_at = datetime.now(UTC)
        snapshot = self._seller_handshake_snapshot(provider="SP_API", environment="PRODUCTION")
        if snapshot is None:
            snapshot = self._seller_handshake_snapshot(provider="SP_API", environment="SANDBOX")
        if snapshot is not None:
            result = await self._validator().validate(
                organization_id=self._org_id(),
                connection=snapshot,
            )
            self._apply_seller_validation(snapshot, result)
            environment: ConnectionEnvironment = (
                "PRODUCTION" if snapshot.environment == "PRODUCTION" else "SANDBOX"
            )
            return AmazonConnectionTestResult(
                status="CONNECTED" if result.valid else "FAILED",
                environment=environment,
                marketplace=cfg.default_marketplace,
                operation=result.operation,
                tested_at=tested_at,
                message=result.message,
            )
        if not self._credentials_ready(cfg):
            logger.info("amazon connection test skipped reason=missing_credentials")
            return AmazonConnectionTestResult(
                status="NOT_CONNECTED",
                marketplace=cfg.default_marketplace,
                operation=GET_MARKETPLACE_PARTICIPATIONS,
                tested_at=tested_at,
                message=MISSING_CREDENTIALS_MESSAGE,
            )
        try:
            await self._checker().get_marketplace_participations()
        except SpApiConfigurationError as exc:
            logger.info("amazon connection test skipped reason=configuration")
            return AmazonConnectionTestResult(
                status="NOT_CONNECTED",
                marketplace=cfg.default_marketplace,
                operation=GET_MARKETPLACE_PARTICIPATIONS,
                tested_at=tested_at,
                message=str(exc),
            )
        except SpApiAuthenticationError as exc:
            logger.warning("amazon connection test failed reason=authentication")
            return self._failed(cfg, tested_at, str(exc))
        except SpApiRateLimitedError as exc:
            logger.warning("amazon connection test failed reason=rate_limited")
            return self._failed(cfg, tested_at, str(exc))
        except SpApiRequestFailedError as exc:
            logger.warning("amazon connection test failed reason=sandbox_unavailable")
            return self._failed(cfg, tested_at, str(exc))
        except SpApiParseFailedError as exc:
            logger.warning("amazon connection test failed reason=parse")
            return self._failed(cfg, tested_at, str(exc))
        logger.info("amazon connection test succeeded provider=SP_API environment=SANDBOX")
        return AmazonConnectionTestResult(
            status="CONNECTED",
            marketplace=cfg.default_marketplace,
            operation=GET_MARKETPLACE_PARTICIPATIONS,
            tested_at=tested_at,
        )

    def _failed(
        self,
        cfg: Settings,
        tested_at: datetime,
        message: str,
    ) -> AmazonConnectionTestResult:
        return AmazonConnectionTestResult(
            status="FAILED",
            marketplace=cfg.default_marketplace,
            operation=GET_MARKETPLACE_PARTICIPATIONS,
            tested_at=tested_at,
            message=message,
        )


def get_amazon_connection_service() -> AmazonConnectionService:
    return AmazonConnectionService()
