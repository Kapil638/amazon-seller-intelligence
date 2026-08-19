from statistics import median

from app.models.bulk import BulkASINProductResult, BulkFailure, BulkPortfolioSummary


def aggregate_portfolio(
    *,
    submitted: int,
    results: list[BulkASINProductResult],
    failures: list[BulkFailure],
) -> BulkPortfolioSummary:
    scores = [item.listing_analysis.overall_score for item in results]
    lookup_failures = [item for item in failures if item.kind != "invalid"]
    ratings = [item.product.rating for item in results if item.product.rating is not None]
    reviews = [item.product.review_count for item in results if item.product.review_count is not None]
    images = [len(item.product.images) for item in results]

    def _count_code(*codes: str) -> int:
        wanted = set(codes)
        return sum(
            1
            for item in results
            if any(finding.code in wanted for finding in item.listing_analysis.findings)
        )

    return BulkPortfolioSummary(
        products_submitted=submitted,
        products_analyzed=len(results),
        products_failed=len(lookup_failures),
        average_listing_score=round(sum(scores) / len(scores), 1) if scores else None,
        median_listing_score=float(median(scores)) if scores else None,
        high_priority_count=sum(1 for item in results if item.priority == "high"),
        medium_priority_count=sum(1 for item in results if item.priority == "medium"),
        low_priority_count=sum(1 for item in results if item.priority == "low"),
        missing_description_count=_count_code("NO_DESCRIPTION"),
        low_image_count=_count_code("NO_IMAGES", "FEW_IMAGES"),
        weak_bullet_count=_count_code("NO_BULLETS", "FEW_BULLETS"),
        low_completeness_count=_count_code("COMPLETENESS_GAPS"),
        average_rating=round(sum(ratings) / len(ratings), 2) if ratings else None,
        average_review_count=round(sum(reviews) / len(reviews), 1) if reviews else None,
        average_image_count=round(sum(images) / len(images), 1) if images else None,
    )


PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def sort_results(results: list[BulkASINProductResult]) -> list[BulkASINProductResult]:
    return sorted(
        results,
        key=lambda item: (PRIORITY_ORDER[item.priority], item.listing_analysis.overall_score, item.asin),
    )


def attention_results(results: list[BulkASINProductResult], limit: int = 20) -> list[BulkASINProductResult]:
    ordered = sort_results(results)
    weakest = [item for item in ordered if item.priority in {"high", "medium"}]
    chosen = weakest[:limit] if weakest else ordered[:limit]
    return chosen
