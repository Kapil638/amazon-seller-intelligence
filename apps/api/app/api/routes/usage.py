from fastapi import APIRouter, Query

from app.models.usage import UsageDashboardResponse
from app.usage.dashboard import get_usage_dashboard_service

router = APIRouter(prefix="/api/v1/usage", tags=["usage"])


@router.get("/dashboard", response_model=UsageDashboardResponse)
async def usage_dashboard(
    refresh: bool = Query(False, description="Bypass provider-account usage cache."),
) -> UsageDashboardResponse:
    return await get_usage_dashboard_service().get_dashboard(force_refresh=refresh)
