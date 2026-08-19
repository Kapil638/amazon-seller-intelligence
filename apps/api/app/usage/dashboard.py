from __future__ import annotations

from app.models.usage import (
    OpenAIUsageBlock,
    RainforestUsageBlock,
    UsageDashboardResponse,
)
from app.usage.ledger import get_usage_ledger
from app.usage.openai_account import OpenAIAccountClient
from app.usage.rainforest_account import RainforestAccountClient


class UsageDashboardService:
    def __init__(
        self,
        rainforest_client: RainforestAccountClient | None = None,
        openai_client: OpenAIAccountClient | None = None,
    ) -> None:
        self._rainforest = rainforest_client or RainforestAccountClient()
        self._openai = openai_client or OpenAIAccountClient()

    async def get_dashboard(self, *, force_refresh: bool = False) -> UsageDashboardResponse:
        rainforest_account = await self._rainforest.get_usage(force_refresh=force_refresh)
        openai_account = await self._openai.get_usage(force_refresh=force_refresh)
        ledger = get_usage_ledger()
        return UsageDashboardResponse(
            rainforest=RainforestUsageBlock(
                account=rainforest_account,
                app=ledger.rainforest_app_snapshot(),
            ),
            openai=OpenAIUsageBlock(
                account=openai_account,
                app=ledger.openai_app_snapshot(),
            ),
        )


def get_usage_dashboard_service() -> UsageDashboardService:
    return UsageDashboardService()
