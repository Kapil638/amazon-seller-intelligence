"""Amazon Ads OAuth + connection/profile management service. 12C
read-only foundation — complete but inactive (see
`app.core.config.Settings`'s `ads_*` fields and this module's own
`AdsConfigurationError` fail-closed checks below).

No `users` table exists yet in this codebase (`current_organization_id()`
resolves from `Settings.default_organization_id`, not a request-scoped
session — see `app.persistence.database`). "Initiating user" binding
(item 1's requirement) is therefore captured on a best-effort basis as
the verified Cloudflare Access identity (`request.state.
cloudflare_access_identity`, see `app.core.cloudflare_access`) at
state-creation time — a string, never a foreign key to a user that does
not exist. This is a recorded architectural gap, not an invented
substitute: see `docs/AI_HANDOVER/22_AMAZON_ADS_READONLY_FOUNDATION.md`.

Connection-hijack prevention: every callback re-derives organization_id
and connection_id from the OAuth state row itself (`AmazonAdsOAuthState`,
looked up by hash only), never from a caller-supplied value — a
forged/replayed `state` cannot attach a token exchange to a different
organization's connection, because there is no code path that ever lets
a caller specify which organization/connection a callback affects.
"""

from __future__ import annotations

import logging
import secrets as _secrets
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr

from app.amazon.ads_client import AdsRequestContext, AmazonAdsApiClient, MockAmazonAdsApiClient
from app.amazon.ads_lwa_token import AdsLwaTokenService
from app.amazon.ads_models import AdsProfileResponse
from app.amazon.ads_oauth import (
    ads_oauth_state_expiry,
    build_ads_consent_url,
    new_ads_oauth_state,
    validate_return_path,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AdsConfigurationError,
    AdsConnectionHijackError,
    AdsOAuthStateInvalidError,
    AdsProfileNotFoundError,
)
from app.persistence.database import current_organization_id, persistence_enabled, session_scope
from app.persistence.repositories import (
    AmazonAdsConnectionRepository,
    AmazonAdsOAuthStateRepository,
    AmazonAdsProfileRepository,
    AmazonSellerAccountRepository,
)
from app.amazon.secrets import (
    SecretProvider,
    build_asi_secret_reference,
    get_secret_provider,
)
from app.core.exceptions import PersistenceNotConfiguredError

logger = logging.getLogger(__name__)

ADS_PROVIDER = "ADS_API"
ADS_ENVIRONMENT = "PRODUCTION"

ConnectionStatus = Literal["not_connected", "pending_authorization", "connected", "revoked", "error"]
CallbackNotice = Literal["success", "denied", "error", "expired"]


class AdsAuthorizationStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization_url: str


class AdsProfileRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    profile_id: str
    account_id: str | None
    account_type: str | None
    marketplace_country_code: str
    currency_code: str
    timezone: str
    region: str
    display_name: str | None
    is_selected: bool
    sync_state: str


class AdsConnectionOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ConnectionStatus
    persisted: bool
    organization_id: str
    configured: bool
    authorized_at: datetime | None = None
    profiles: list[AdsProfileRead] = []
    selected_profile_id: str | None = None


class AdsCallbackResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notice: CallbackNotice
    return_path: str


def _correlation_id() -> str:
    return _secrets.token_hex(8)


class AmazonAdsConnectionService:
    """Ads OAuth start/callback + profile listing/selection. Never issues
    a live Amazon call this iteration except through an injected
    `AmazonAdsApiClient` (default: `MockAmazonAdsApiClient`, so this
    service is exercised end-to-end in tests without ever touching a
    socket) and an injectable LWA token exchanger."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        secret_provider: SecretProvider | None = None,
        ads_client: AmazonAdsApiClient | None = None,
        lwa_token_service: AdsLwaTokenService | None = None,
    ) -> None:
        self._settings = settings
        self._secret_provider = secret_provider
        self._ads_client = ads_client or MockAmazonAdsApiClient()
        self._lwa_token_service = lwa_token_service

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def _org_id(self) -> UUID:
        return current_organization_id()

    def _secrets(self) -> SecretProvider:
        return self._secret_provider or get_secret_provider(self._cfg())

    def _require_persistence(self) -> None:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError("Amazon Ads connection persistence is not configured.")

    def _require_configured(self, cfg: Settings) -> None:
        if not (cfg.ads_lwa_client_id and cfg.ads_lwa_client_secret and cfg.ads_oauth_redirect_uri.strip()):
            raise AdsConfigurationError("Amazon Ads is not configured.")

    def is_configured(self, cfg: Settings | None = None) -> bool:
        c = cfg or self._cfg()
        return bool(c.ads_lwa_client_id and c.ads_lwa_client_secret and c.ads_oauth_redirect_uri.strip())

    # ------------------------------------------------------------------
    # Start authorization
    # ------------------------------------------------------------------
    def start_authorization(
        self, *, return_path: str | None = None, initiating_user_identity: str | None = None
    ) -> AdsAuthorizationStart:
        self._require_persistence()
        cfg = self._cfg()
        self._require_configured(cfg)
        org_id = self._org_id()
        raw_state, hashed_state = new_ads_oauth_state()
        expires_at = ads_oauth_state_expiry(ttl_seconds=cfg.ads_oauth_state_ttl_seconds)
        safe_return_path = validate_return_path(return_path)
        with session_scope() as session:
            connection = AmazonAdsConnectionRepository(session).get_or_create_for_org(org_id)
            seller_account_id = None
            seller_accounts = AmazonSellerAccountRepository(session).list_for_org(org_id)
            if seller_accounts:
                seller_account_id = seller_accounts[0].id
            AmazonAdsOAuthStateRepository(session).create(
                organization_id=org_id,
                connection_id=connection.id,
                state_hash=hashed_state,
                expires_at=expires_at,
                return_path=safe_return_path,
                initiating_user_identity=initiating_user_identity,
                amazon_seller_account_id=seller_account_id,
            )
            AmazonAdsConnectionRepository(session).mark_pending_authorization(org_id, connection.id)
        consent_url = build_ads_consent_url(
            base_url=cfg.ads_oauth_consent_base_url,
            client_id=cfg.ads_lwa_client_id.get_secret_value() if cfg.ads_lwa_client_id else "",
            redirect_uri=cfg.ads_oauth_redirect_uri,
            scope=cfg.ads_oauth_scope,
            state=raw_state,
        )
        return AdsAuthorizationStart(authorization_url=consent_url)

    # ------------------------------------------------------------------
    # Callback
    # ------------------------------------------------------------------
    async def complete_authorization_callback(
        self, *, state: str | None, code: str | None, error: str | None
    ) -> AdsCallbackResult:
        self._require_persistence()
        cfg = self._cfg()
        default_return = "/seller/advertising"
        if error:
            logger.info("ads oauth callback denied")
            return AdsCallbackResult(notice="denied", return_path=default_return)
        if not state:
            return AdsCallbackResult(notice="error", return_path=default_return)
        state_hash = _hash_state(state)
        with session_scope() as session:
            state_repo = AmazonAdsOAuthStateRepository(session)
            row, classification = state_repo.classify(state_hash)
            if row is None or classification != "usable":
                logger.info("ads oauth callback state rejected reason=%s", classification)
                return AdsCallbackResult(
                    notice="expired" if classification == "expired" else "error", return_path=default_return
                )
            return_path = row.return_path
            connection_id = row.connection_id
            organization_id = row.organization_id
            consumed = state_repo.consume(row.id)
            if consumed is None:
                # Lost a concurrent-consume race — treat identically to
                # "already used," never distinguish from a fresh replay.
                raise AdsOAuthStateInvalidError()
            connection = AmazonAdsConnectionRepository(session).get_by_id(organization_id, connection_id)
            if connection is None:
                raise AdsConnectionHijackError()

        if not code:
            return AdsCallbackResult(notice="error", return_path=return_path)
        if not self.is_configured(cfg):
            return AdsCallbackResult(notice="error", return_path=return_path)

        try:
            token_service = self._lwa_token_service or AdsLwaTokenService.from_settings(cfg)
            grant = token_service.exchange_authorization_code(SecretStr(code))
        except Exception:
            logger.warning("ads oauth token exchange failed")
            with session_scope() as session:
                AmazonAdsConnectionRepository(session).mark_error(
                    organization_id, connection_id, error_code="token_exchange_failed", now=datetime.now(UTC)
                )
            return AdsCallbackResult(notice="error", return_path=return_path)

        reference = build_asi_secret_reference(
            provider=ADS_PROVIDER,
            environment=ADS_ENVIRONMENT,
            organization_id=organization_id,
            connection_id=connection_id,
        )
        self._secrets().put_secret(reference, grant.refresh_token)

        try:
            ctx = AdsRequestContext(
                access_token=grant.access_token,
                client_id=cfg.ads_lwa_client_id.get_secret_value() if cfg.ads_lwa_client_id else "",
                region="NA",
                profile_id=None,
                correlation_id=_correlation_id(),
            )
            profiles = await self._ads_client.list_profiles(ctx)
        except Exception:
            logger.warning("ads profile discovery failed after successful token exchange")
            profiles = []

        with session_scope() as session:
            AmazonAdsConnectionRepository(session).mark_connected(
                organization_id, connection_id, token_reference=reference, authorized_at=datetime.now(UTC)
            )
            if profiles:
                AmazonAdsProfileRepository(session).upsert_many(
                    organization_id,
                    connection_id,
                    [_profile_to_row_data(p) for p in profiles],
                )
        return AdsCallbackResult(notice="success", return_path=return_path)

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------
    def list_profiles(self) -> list[AdsProfileRead]:
        self._require_persistence()
        org_id = self._org_id()
        with session_scope() as session:
            connection = AmazonAdsConnectionRepository(session).get_for_org(org_id)
            if connection is None:
                return []
            rows = AmazonAdsProfileRepository(session).list_for_connection(org_id, connection.id)
            return [_row_to_profile_read(r) for r in rows]

    def select_profile(self, ads_profile_id: str) -> AdsProfileRead:
        self._require_persistence()
        org_id = self._org_id()
        try:
            profile_uuid = UUID(ads_profile_id)
        except ValueError:
            raise AdsProfileNotFoundError(ads_profile_id) from None
        with session_scope() as session:
            connection = AmazonAdsConnectionRepository(session).get_for_org(org_id)
            if connection is None:
                raise AdsProfileNotFoundError(ads_profile_id)
            try:
                row = AmazonAdsProfileRepository(session).select_profile(org_id, connection.id, profile_uuid)
            except ValueError:
                raise AdsProfileNotFoundError(ads_profile_id) from None
            return _row_to_profile_read(row)

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------
    def overview(self) -> AdsConnectionOverview:
        cfg = self._cfg()
        org_id = self._org_id()
        if not persistence_enabled():
            return AdsConnectionOverview(
                status="not_connected", persisted=False, organization_id=str(org_id), configured=self.is_configured(cfg)
            )
        with session_scope() as session:
            connection = AmazonAdsConnectionRepository(session).get_for_org(org_id)
            if connection is None:
                return AdsConnectionOverview(
                    status="not_connected",
                    persisted=True,
                    organization_id=str(org_id),
                    configured=self.is_configured(cfg),
                )
            profiles = AmazonAdsProfileRepository(session).list_for_connection(org_id, connection.id)
            selected = next((p for p in profiles if p.is_selected), None)
            status: ConnectionStatus = connection.status  # type: ignore[assignment]
            return AdsConnectionOverview(
                status=status,
                persisted=True,
                organization_id=str(org_id),
                configured=self.is_configured(cfg),
                authorized_at=connection.authorized_at,
                profiles=[_row_to_profile_read(p) for p in profiles],
                selected_profile_id=str(selected.id) if selected else None,
            )


def _hash_state(raw_state: str) -> str:
    from app.amazon.ads_oauth import hash_ads_oauth_state

    return hash_ads_oauth_state(raw_state)


def _profile_to_row_data(profile: AdsProfileResponse) -> dict:
    return {
        "profile_id": str(profile.profile_id),
        "account_id": profile.account_info.id,
        "account_type": profile.account_info.type,
        "marketplace_country_code": profile.country_code,
        "currency_code": profile.currency_code,
        "timezone": profile.timezone,
        "region": _region_for_country(profile.country_code),
        "display_name": profile.account_info.name,
    }


_EU_COUNTRIES = {"UK", "GB", "FR", "IT", "ES", "DE", "NL", "AE", "SE", "PL", "TR"}
_FE_COUNTRIES = {"JP", "AU", "SG"}


def _region_for_country(country_code: str) -> str:
    code = (country_code or "").strip().upper()
    if code in _FE_COUNTRIES:
        return "FE"
    if code in _EU_COUNTRIES:
        return "EU"
    return "NA"


def _row_to_profile_read(row) -> AdsProfileRead:
    return AdsProfileRead(
        id=str(row.id),
        profile_id=row.profile_id,
        account_id=row.account_id,
        account_type=row.account_type,
        marketplace_country_code=row.marketplace_country_code,
        currency_code=row.currency_code,
        timezone=row.timezone,
        region=row.region,
        display_name=row.display_name,
        is_selected=row.is_selected,
        sync_state=row.sync_state,
    )


def get_amazon_ads_connection_service() -> AmazonAdsConnectionService:
    return AmazonAdsConnectionService()
