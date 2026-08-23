"""Amazon Connection Beta service. Connectivity metadata only. No Amazon data ingest."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.amazon.ads_api import ADS_API_PROVIDER, ADS_API_STATUS
from app.amazon.lwa import MISSING_CREDENTIALS_MESSAGE, credentials_configured
from app.amazon.models import MarketplaceParticipationsSandboxResult
from app.amazon.sandbox import GET_MARKETPLACE_PARTICIPATIONS, AmazonSpApiSandboxClient
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)
from app.persistence.database import current_organization_id

logger = logging.getLogger(__name__)

ConnectionStatus = Literal["CONNECTED", "NOT_CONNECTED", "FAILED"]
SpApiProvider = Literal["SP_API"]
AdsApiProvider = Literal["ADS_API"]
SandboxEnvironment = Literal["SANDBOX"]


class SpApiConnectivityChecker(Protocol):
    async def get_marketplace_participations(self) -> MarketplaceParticipationsSandboxResult: ...


SandboxClientFactory = Callable[[], SpApiConnectivityChecker]


class AdsApiConnectionPlaceholder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: AdsApiProvider = "ADS_API"
    status: Literal["NOT_CONNECTED"] = "NOT_CONNECTED"


class AmazonConnectionOverview(BaseModel):
    """Configuration-backed connection view. V1 does not persist last-test state."""

    model_config = ConfigDict(extra="forbid")

    status: ConnectionStatus
    provider: SpApiProvider = "SP_API"
    environment: SandboxEnvironment = "SANDBOX"
    marketplace: str
    application: str
    credentials_configured: bool
    last_test_at: datetime | None = None
    organization_id: str
    ads_api: AdsApiConnectionPlaceholder = Field(default_factory=AdsApiConnectionPlaceholder)


class AmazonConnectionTestResult(BaseModel):
    """Sanitized connectivity result. Never includes tokens, secrets, or Amazon payloads."""

    model_config = ConfigDict(extra="forbid")

    status: ConnectionStatus
    provider: SpApiProvider = "SP_API"
    environment: SandboxEnvironment = "SANDBOX"
    marketplace: str
    operation: str
    tested_at: datetime
    message: str | None = None


class AmazonConnectionService:
    """Validate SP-API sandbox configuration and run one connectivity check."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        sandbox_client_factory: SandboxClientFactory | None = None,
    ) -> None:
        self._settings = settings
        self._sandbox_client_factory = sandbox_client_factory

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def _credentials_ready(self, cfg: Settings) -> bool:
        return credentials_configured(
            cfg.sp_api_lwa_client_id,
            cfg.sp_api_lwa_client_secret,
            cfg.sp_api_sandbox_refresh_token,
        )

    def overview(self) -> AmazonConnectionOverview:
        cfg = self._cfg()
        return AmazonConnectionOverview(
            status="NOT_CONNECTED",
            marketplace=cfg.default_marketplace,
            application=cfg.sp_api_application_name,
            credentials_configured=self._credentials_ready(cfg),
            last_test_at=None,
            organization_id=str(current_organization_id()),
            ads_api=AdsApiConnectionPlaceholder(provider=ADS_API_PROVIDER, status=ADS_API_STATUS),
        )

    def _checker(self) -> SpApiConnectivityChecker:
        factory = self._sandbox_client_factory or AmazonSpApiSandboxClient
        return factory()

    async def test_sp_api(self) -> AmazonConnectionTestResult:
        cfg = self._cfg()
        tested_at = datetime.now(UTC)
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
