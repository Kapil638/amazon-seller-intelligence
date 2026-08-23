"""External-provider DTOs for SP-API Sellers v1. Not the ASI canonical data model."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class LwaTokenResponse(BaseModel):
    """Short-lived LWA token. `access_token` is SecretStr so repr/JSON dump do not leak it."""

    model_config = ConfigDict(extra="ignore")

    access_token: SecretStr
    token_type: str
    expires_in: int


class Marketplace(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    country_code: str = Field(alias="countryCode")
    default_currency_code: str = Field(alias="defaultCurrencyCode")
    default_language_code: str = Field(alias="defaultLanguageCode")
    domain_name: str = Field(alias="domainName")


class Participation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    is_participating: bool = Field(alias="isParticipating")
    has_suspended_listings: bool = Field(alias="hasSuspendedListings")


class MarketplaceParticipation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    marketplace: Marketplace
    participation: Participation
    store_name: str = Field(alias="storeName")


class GetMarketplaceParticipationsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    payload: list[MarketplaceParticipation] | None = None
    errors: list[dict] | None = None


class SpApiSandboxProvenance(BaseModel):
    """Non-secret connectivity metadata. Never include tokens or authorization headers."""

    provider: Literal["amazon_sp_api"] = "amazon_sp_api"
    environment: Literal["sandbox"] = "sandbox"
    api: Literal["sellers"] = "sellers"
    operation: str
    region: str
    endpoint_host: str
    fetched_at: datetime
    http_status: int
    api_model_version: str


class MarketplaceParticipationsSandboxResult(BaseModel):
    payload: list[MarketplaceParticipation]
    provenance: SpApiSandboxProvenance
    participation_count: int
