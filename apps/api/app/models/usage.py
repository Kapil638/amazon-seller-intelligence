from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

WarningLevel = Literal["normal", "warning", "critical", "unknown"]
AccountStatus = Literal["ok", "unavailable", "not_configured"]


class RainforestUsagePoint(BaseModel):
    date: str
    credits_used: int


class RainforestAccountUsage(BaseModel):
    """Authoritative Rainforest Account API credits. Never includes api_key or identity."""

    source: Literal["rainforest_account_api"] = "rainforest_account_api"
    available: bool
    status: AccountStatus
    credits_used: int | None = None
    credits_limit: int | None = None
    credits_remaining: int | None = None
    usage_percentage: float | None = None
    warning_level: WarningLevel = "unknown"
    reset_at: datetime | None = None
    usage_history: list[RainforestUsagePoint] = Field(default_factory=list)
    last_updated: datetime | None = None
    message: str | None = None


class RainforestAppUsage(BaseModel):
    """This application's Rainforest call ledger. Not the Rainforest bill."""

    source: Literal["application_ledger"] = "application_ledger"
    product_calls: int = 0
    search_calls: int = 0
    cache_hits: int = 0
    calls_saved: int = 0
    failed_calls: int = 0


class RainforestUsageBlock(BaseModel):
    account: RainforestAccountUsage
    app: RainforestAppUsage


class OpenAIAccountUsage(BaseModel):
    """Authoritative OpenAI organization spend, when an Admin API key is configured."""

    source: Literal["openai_organization_costs_api"] = "openai_organization_costs_api"
    available: bool
    status: AccountStatus
    spend_usd: float | None = None
    budget_usd: float | None = None
    usage_percentage: float | None = None
    warning_level: WarningLevel = "unknown"
    period_start: datetime | None = None
    last_updated: datetime | None = None
    message: str | None = None


class OpenAIAppUsage(BaseModel):
    """This application's OpenAI token ledger and estimated cost. Not the OpenAI invoice."""

    source: Literal["application_ledger"] = "application_ledger"
    estimated_spend_usd: float | None = None
    cost_status: Literal["ok", "unavailable", "partial"] = "unavailable"
    requests: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_hits: int = 0
    calls_saved: int = 0
    failed_calls: int = 0
    unpriced_requests: int = 0


class OpenAIUsageBlock(BaseModel):
    account: OpenAIAccountUsage
    app: OpenAIAppUsage


class UsageDashboardResponse(BaseModel):
    rainforest: RainforestUsageBlock
    openai: OpenAIUsageBlock
