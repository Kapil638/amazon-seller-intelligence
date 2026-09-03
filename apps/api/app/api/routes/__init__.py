from fastapi import APIRouter

from app.api.routes.analysis import router as analysis_router
from app.api.routes.competitors import router as competitors_router
from app.api.routes.copilot import router as copilot_router
from app.api.routes.products import router as products_router
from app.api.routes.reports import router as reports_router
from app.api.routes.scoring_profiles import router as scoring_profiles_router
from app.api.routes.usage import router as usage_router
from app.api.routes.bulk import router as bulk_router
from app.api.routes.profit import router as profit_router
from app.api.routes.advertising import router as advertising_router
from app.api.routes.amazon_connection import router as amazon_connection_router
from app.api.routes.amazon_listings import router as amazon_listings_router
from app.api.routes.amazon_listings_sync import router as amazon_listings_sync_router
from app.api.routes.amazon_orders import router as amazon_orders_router
from app.api.routes.amazon_orders_sync import router as amazon_orders_sync_router

api_router = APIRouter()
api_router.include_router(products_router)
api_router.include_router(analysis_router)
api_router.include_router(scoring_profiles_router)
api_router.include_router(competitors_router)
api_router.include_router(reports_router)
api_router.include_router(usage_router)
api_router.include_router(bulk_router)
api_router.include_router(copilot_router)
api_router.include_router(profit_router)
api_router.include_router(advertising_router)
api_router.include_router(amazon_connection_router)
api_router.include_router(amazon_listings_router)
api_router.include_router(amazon_listings_sync_router)
api_router.include_router(amazon_orders_router)
api_router.include_router(amazon_orders_sync_router)
