from __future__ import annotations

import asyncio

from app.analytics.competitor_rules import compare_listings
from app.analytics.listing_rules import SCORE_VERSION
from app.core.exceptions import (
    CompetitorValidationError,
    NoCompetitorsRetrievedError,
    ProductFetchBlockedError,
    ProductFetchFailedError,
    ProductNotFoundError,
    ProductParseFailedError,
    ProviderConfigurationError,
    UnsupportedMarketplaceError,
)
from app.core.validation import is_valid_asin, normalize_asin
from app.models.competitor_comparison import (
    COMPARISON_VERSION,
    ComparedListing,
    CompetitorComparisonMeta,
    CompetitorComparisonResponse,
    FailedCompetitor,
)
from app.models.product import Product, ProductSource
from app.services.listing_analysis_service import ListingAnalysisService
from app.services.product_service import ProductService


class CompetitorComparisonService:
    """Fetch competitor Products, score them with ListingAnalysisService, then compare."""

    def __init__(
        self,
        products: ProductService,
        analysis: ListingAnalysisService | None = None,
    ) -> None:
        self._products = products
        self._analysis = analysis or ListingAnalysisService()

    async def compare(
        self,
        target_product: Product,
        competitor_asins: list[str],
        marketplace: str | None = None,
        source: ProductSource | None = None,
    ) -> CompetitorComparisonResponse:
        requested = self._validate_asins(target_product.asin, competitor_asins)
        resolved_marketplace = marketplace or target_product.marketplace

        results = await asyncio.gather(
            *[self._fetch_one(asin, resolved_marketplace) for asin in requested],
        )

        competitors: list[ComparedListing] = []
        failed: list[FailedCompetitor] = []
        sources: list[str] = []
        for item in results:
            if isinstance(item, FailedCompetitor):
                failed.append(item)
                continue
            product, product_source = item
            competitors.append(
                ComparedListing(
                    product=product,
                    analysis=self._analysis.analyze(product),
                )
            )
            sources.append(product_source)

        if not competitors:
            if failed and all(_is_configuration_failure(item.reason) for item in failed):
                raise ProviderConfigurationError(failed[0].reason)
            raise NoCompetitorsRetrievedError(
                _total_failure_message(requested, failed)
            )

        target = ComparedListing(
            product=target_product,
            analysis=self._analysis.analyze(target_product),
        )
        comparison = compare_listings(target, competitors)
        comparison.summary.requested_count = len(requested)

        meta_source = source.value if source is not None else _dominant_source(sources)
        return CompetitorComparisonResponse(
            target=target,
            competitors=competitors,
            comparison=comparison,
            failed_competitors=failed,
            meta=CompetitorComparisonMeta(
                source=meta_source,
                comparison_version=COMPARISON_VERSION,
                score_version=SCORE_VERSION,
            ),
        )

    def _validate_asins(self, target_asin: str, competitor_asins: list[str]) -> list[str]:
        if not competitor_asins:
            raise CompetitorValidationError("Enter at least one competitor ASIN.")
        if len(competitor_asins) > 3:
            raise CompetitorValidationError("Compare at most three competitor ASINs.")

        normalized_target = normalize_asin(target_asin)
        seen: set[str] = set()
        resolved: list[str] = []
        for raw in competitor_asins:
            asin = normalize_asin(raw)
            if not is_valid_asin(asin):
                raise CompetitorValidationError("Invalid ASIN format")
            if asin == normalized_target:
                raise CompetitorValidationError(
                    "The target ASIN cannot be entered as a competitor."
                )
            if asin in seen:
                raise CompetitorValidationError("Competitor ASINs must be unique.")
            seen.add(asin)
            resolved.append(asin)
        return resolved

    async def _fetch_one(
        self,
        asin: str,
        marketplace: str,
    ) -> tuple[Product, str] | FailedCompetitor:
        try:
            return await self._products.fetch_product(asin, marketplace)
        except ProductNotFoundError:
            return FailedCompetitor(asin=asin, reason="Product was not found.")
        except ProductFetchBlockedError:
            return FailedCompetitor(asin=asin, reason="Lookup was blocked or throttled.")
        except ProviderConfigurationError as exc:
            return FailedCompetitor(asin=asin, reason=str(exc))
        except UnsupportedMarketplaceError as exc:
            return FailedCompetitor(asin=asin, reason=str(exc))
        except (ProductFetchFailedError, ProductParseFailedError):
            return FailedCompetitor(asin=asin, reason="Could not retrieve this listing.")
        except ValueError:
            return FailedCompetitor(asin=asin, reason="Invalid ASIN format.")


def _dominant_source(sources: list[str]) -> str | None:
    if not sources:
        return None
    unique = list(dict.fromkeys(sources))
    if len(unique) == 1:
        return unique[0]
    return unique[0]


def _is_configuration_failure(reason: str) -> bool:
    lowered = reason.lower()
    return "not configured" in lowered or "authentication failed" in lowered


def _total_failure_message(requested: list[str], failed: list[FailedCompetitor]) -> str:
    if len(requested) == 1 and failed:
        return failed[0].reason
    asins = ", ".join(item.asin for item in failed) or ", ".join(requested)
    return f"No competitor listings could be retrieved. Failed ASINs: {asins}."
