from fastapi import APIRouter, Depends, HTTPException

from app.ai.factory import get_ai_provider
from app.analytics.listing_rules import SCORE_VERSION
from app.analytics.listing_rules_v2 import SCORE_VERSION as SCORE_VERSION_V2
from app.core.exceptions import (
    AIAuthenticationError,
    AIConfigurationError,
    AIRateLimitedError,
    AIRequestFailedError,
    AISafetyRefusalError,
    AIStructuredOutputError,
    CompetitorValidationError,
    NoCompetitorsRetrievedError,
    ProviderConfigurationError,
)
from app.models.ai_competitive_intelligence import (
    AICompetitiveIntelligenceMeta,
    AICompetitiveIntelligenceRequest,
    AICompetitiveIntelligenceResponse,
)
from app.models.ai_listing_intelligence import (
    AIListingIntelligenceMeta,
    AIListingIntelligenceRequest,
    AIListingIntelligenceResponse,
)
from app.models.ai_listing_intelligence_v2 import (
    AIListingIntelligenceV2Meta,
    AIListingIntelligenceV2Request,
    AIListingIntelligenceV2Response,
)
from app.models.competitor_comparison import (
    CompetitorComparisonRequest,
    CompetitorComparisonResponse,
)
from app.models.listing_analysis import (
    AnalysisMeta,
    ListingAnalysisRequest,
    ListingAnalysisResponse,
)
from app.models.listing_analysis_v2 import ListingAnalysisV2Meta, ListingAnalysisV2Response
from app.prompts.competitive_intelligence import PROMPT_VERSION as COMPETITIVE_PROMPT_VERSION
from app.prompts.listing_intelligence import PROMPT_VERSION
from app.prompts.listing_intelligence_v2 import PROMPT_VERSION as PROMPT_VERSION_V2
from app.providers.factory import get_product_provider
from app.services.ai_competitive_intelligence_service import AICompetitiveIntelligenceService
from app.services.ai_listing_intelligence_service import AIListingIntelligenceService
from app.services.ai_listing_intelligence_v2_service import AIListingIntelligenceV2Service
from app.services.competitor_comparison_service import CompetitorComparisonService
from app.services.listing_analysis_service import ListingAnalysisService
from app.services.listing_analysis_v2_service import ListingAnalysisV2Service
from app.services.product_service import ProductService

router = APIRouter(prefix="/api/v1/analysis", tags=["analysis"])


def get_listing_analysis_service() -> ListingAnalysisService:
    return ListingAnalysisService()


def get_listing_analysis_v2_service() -> ListingAnalysisV2Service:
    return ListingAnalysisV2Service()


def get_ai_listing_intelligence_service() -> AIListingIntelligenceService:
    return AIListingIntelligenceService(provider=get_ai_provider())


def get_ai_listing_intelligence_v2_service() -> AIListingIntelligenceV2Service:
    return AIListingIntelligenceV2Service(provider=get_ai_provider())


def get_competitor_comparison_service() -> CompetitorComparisonService:
    return CompetitorComparisonService(
        products=ProductService(provider=get_product_provider()),
        analysis=ListingAnalysisService(),
    )


def get_ai_competitive_intelligence_service() -> AICompetitiveIntelligenceService:
    return AICompetitiveIntelligenceService(provider=get_ai_provider())


def _ai_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AIConfigurationError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, AIAuthenticationError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, AIRateLimitedError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, AISafetyRefusalError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, (AIRequestFailedError, AIStructuredOutputError)):
        return HTTPException(status_code=502, detail=str(exc))
    raise exc


@router.post("/listing", response_model=ListingAnalysisResponse)
def analyze_listing(
    payload: ListingAnalysisRequest,
    service: ListingAnalysisService = Depends(get_listing_analysis_service),
) -> ListingAnalysisResponse:
    analysis = service.analyze(payload.product)
    return ListingAnalysisResponse(
        product=payload.product,
        analysis=analysis,
        meta=AnalysisMeta(
            engine="deterministic",
            score_version=SCORE_VERSION,
            source=payload.source,
        ),
    )


@router.post("/listing/v2", response_model=ListingAnalysisV2Response)
def analyze_listing_v2(
    payload: ListingAnalysisRequest,
    service: ListingAnalysisV2Service = Depends(get_listing_analysis_v2_service),
) -> ListingAnalysisV2Response:
    analysis = service.analyze(payload.product)
    return ListingAnalysisV2Response(
        product=payload.product,
        analysis=analysis,
        meta=ListingAnalysisV2Meta(
            engine="deterministic",
            score_version=SCORE_VERSION_V2,
            source=payload.source,
        ),
    )


@router.post("/listing/ai", response_model=AIListingIntelligenceResponse)
async def analyze_listing_ai(
    payload: AIListingIntelligenceRequest,
    service: AIListingIntelligenceService = Depends(get_ai_listing_intelligence_service),
) -> AIListingIntelligenceResponse:
    try:
        result = await service.generate(payload.product, payload.analysis)
    except (
        AIConfigurationError,
        AIAuthenticationError,
        AIRateLimitedError,
        AIRequestFailedError,
        AIStructuredOutputError,
        AISafetyRefusalError,
    ) as exc:
        raise _ai_http_error(exc) from exc
    return AIListingIntelligenceResponse(
        product=payload.product,
        analysis=payload.analysis,
        ai_intelligence=result.payload,
        meta=AIListingIntelligenceMeta(
            engine="ai",
            provider=result.provider,
            model=result.model,
            prompt_version=result.prompt_version or PROMPT_VERSION,
            source=payload.source,
            usage=result.usage,
            latency_ms=result.latency_ms,
        ),
    )


@router.post("/listing/v2/ai", response_model=AIListingIntelligenceV2Response)
async def analyze_listing_v2_ai(
    payload: AIListingIntelligenceV2Request,
    service: AIListingIntelligenceV2Service = Depends(get_ai_listing_intelligence_v2_service),
) -> AIListingIntelligenceV2Response:
    try:
        result = await service.generate(payload.product, payload.analysis)
    except (
        AIConfigurationError,
        AIAuthenticationError,
        AIRateLimitedError,
        AIRequestFailedError,
        AIStructuredOutputError,
        AISafetyRefusalError,
    ) as exc:
        raise _ai_http_error(exc) from exc
    return AIListingIntelligenceV2Response(
        product=payload.product,
        analysis=payload.analysis,
        ai_intelligence=result.payload,
        meta=AIListingIntelligenceV2Meta(
            engine="ai",
            provider=result.provider,
            model=result.model,
            prompt_version=result.prompt_version or PROMPT_VERSION_V2,
            source=payload.source,
            usage=result.usage,
            latency_ms=result.latency_ms,
        ),
    )


@router.post("/competitors", response_model=CompetitorComparisonResponse)
async def compare_competitors(
    payload: CompetitorComparisonRequest,
    service: CompetitorComparisonService = Depends(get_competitor_comparison_service),
) -> CompetitorComparisonResponse:
    try:
        return await service.compare(
            target_product=payload.target_product,
            competitor_asins=payload.competitor_asins,
            marketplace=payload.marketplace,
            source=payload.source,
        )
    except CompetitorValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except NoCompetitorsRetrievedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/competitors/ai", response_model=AICompetitiveIntelligenceResponse)
async def compare_competitors_ai(
    payload: AICompetitiveIntelligenceRequest,
    service: AICompetitiveIntelligenceService = Depends(get_ai_competitive_intelligence_service),
) -> AICompetitiveIntelligenceResponse:
    try:
        result = await service.generate(payload.comparison)
    except (
        AIConfigurationError,
        AIAuthenticationError,
        AIRateLimitedError,
        AIRequestFailedError,
        AIStructuredOutputError,
        AISafetyRefusalError,
    ) as exc:
        raise _ai_http_error(exc) from exc
    return AICompetitiveIntelligenceResponse(
        comparison=payload.comparison,
        ai_intelligence=result.payload,
        meta=AICompetitiveIntelligenceMeta(
            engine="ai",
            provider=result.provider,
            model=result.model,
            prompt_version=result.prompt_version or COMPETITIVE_PROMPT_VERSION,
            comparison_version=payload.comparison.meta.comparison_version,
            usage=result.usage,
            latency_ms=result.latency_ms,
        ),
    )
