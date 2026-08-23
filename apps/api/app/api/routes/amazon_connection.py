from fastapi import APIRouter, Depends

from app.amazon.common import public_model_dump
from app.amazon.connection import (
    AmazonConnectionOverview,
    AmazonConnectionService,
    AmazonConnectionTestResult,
    get_amazon_connection_service,
)

router = APIRouter(prefix="/api/v1/amazon", tags=["amazon-connection"])


@router.get("/connection", response_model=AmazonConnectionOverview)
def get_amazon_connection(
    service: AmazonConnectionService = Depends(get_amazon_connection_service),
) -> AmazonConnectionOverview:
    overview = service.overview()
    public_model_dump(overview)
    return overview


@router.post("/connection/test", response_model=AmazonConnectionTestResult)
async def test_amazon_connection(
    service: AmazonConnectionService = Depends(get_amazon_connection_service),
) -> AmazonConnectionTestResult:
    result = await service.test_sp_api()
    public_model_dump(result)
    return result
