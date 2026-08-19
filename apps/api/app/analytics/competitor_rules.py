"""Deterministic competitor comparison rules (comparison version v1).

These thresholds are internal heuristics. They do not infer sales, conversion,
or that a lower price is automatically better.
"""

from __future__ import annotations

from statistics import median

from app.models.competitor_comparison import (
    ComparedListing,
    ComparisonMetric,
    ComparisonSummary,
    CompetitiveGap,
    CompetitorComparison,
    GapDirection,
    GapSeverity,
    PriceDelta,
)
from app.models.product import Product

SCORE_GAP_HIGH = 15
SCORE_GAP_MEDIUM = 8
IMAGE_GAP_HIGH = 3
BULLET_GAP_HIGH = 2
RATING_GAP_HIGH = 0.5
RATING_GAP_MEDIUM = 0.3
REVIEW_RATIO_HIGH = 5
REVIEW_DIFF_HIGH = 1000
REVIEW_RATIO_MEDIUM = 2
REVIEW_DIFF_MEDIUM = 200
REVIEW_DIFF_LOW = 50
PRICE_NOTE_PCT = 10.0


def compare_listings(target: ComparedListing, competitors: list[ComparedListing]) -> CompetitorComparison:
    if not competitors:
        raise ValueError("At least one competitor listing is required")

    metrics = _metrics(target, competitors)
    gaps = _gaps(target, competitors)
    price_deltas = _price_deltas(target.product, [item.product for item in competitors])
    scores = [item.analysis.overall_score for item in competitors]
    average = sum(scores) / len(scores)
    med = float(median(scores))
    summary = ComparisonSummary(
        requested_count=len(competitors),
        retrieved_count=len(competitors),
        listing_score_average=round(average, 1),
        listing_score_median=round(med, 1),
        target_listing_score=target.analysis.overall_score,
        target_vs_average=round(target.analysis.overall_score - average, 1),
    )
    return CompetitorComparison(
        metrics=metrics,
        gaps=gaps,
        price_deltas=price_deltas,
        summary=summary,
    )


def _metrics(target: ComparedListing, competitors: list[ComparedListing]) -> list[ComparisonMetric]:
    rows: list[ComparisonMetric] = []
    rows.append(_numeric_metric("price", "Price", _price_amount(target.product), {
        item.product.asin: _price_amount(item.product) for item in competitors
    }, note=_price_note(target.product, competitors)))
    rows.append(_value_metric("currency", "Currency", _price_currency(target.product), {
        item.product.asin: _price_currency(item.product) for item in competitors
    }))
    rows.append(_value_metric("rating", "Rating", target.product.rating, {
        item.product.asin: item.product.rating for item in competitors
    }))
    rows.append(_value_metric("review_count", "Reviews", target.product.review_count, {
        item.product.asin: item.product.review_count for item in competitors
    }))
    rows.append(_bsr_metric(target.product, [item.product for item in competitors]))
    rows.append(_value_metric("listing_score", "Listing Score", target.analysis.overall_score, {
        item.product.asin: item.analysis.overall_score for item in competitors
    }))
    rows.append(_value_metric("title_score", "Title Score", target.analysis.sections.title.score, {
        item.product.asin: item.analysis.sections.title.score for item in competitors
    }))
    rows.append(_value_metric("bullet_score", "Bullet Score", target.analysis.sections.bullets.score, {
        item.product.asin: item.analysis.sections.bullets.score for item in competitors
    }))
    rows.append(_value_metric("description_score", "Description Score", target.analysis.sections.description.score, {
        item.product.asin: item.analysis.sections.description.score for item in competitors
    }))
    rows.append(_value_metric("image_score", "Image Score", target.analysis.sections.images.score, {
        item.product.asin: item.analysis.sections.images.score for item in competitors
    }))
    rows.append(_value_metric("completeness_score", "Completeness Score", target.analysis.sections.completeness.score, {
        item.product.asin: item.analysis.sections.completeness.score for item in competitors
    }))
    rows.append(_value_metric("social_proof_score", "Social Proof Score", target.analysis.sections.social_proof.score, {
        item.product.asin: item.analysis.sections.social_proof.score for item in competitors
    }))
    rows.append(_value_metric("title_length", "Title Length", _title_length(target.product), {
        item.product.asin: _title_length(item.product) for item in competitors
    }))
    rows.append(_value_metric("bullet_count", "Bullets", len(target.product.bullet_points), {
        item.product.asin: len(item.product.bullet_points) for item in competitors
    }))
    rows.append(_value_metric("image_count", "Images", len(target.product.images), {
        item.product.asin: len(item.product.images) for item in competitors
    }))
    rows.append(_value_metric("description_present", "Description Present", bool(target.product.description and target.product.description.strip()), {
        item.product.asin: bool(item.product.description and item.product.description.strip()) for item in competitors
    }))
    rows.append(_value_metric("availability", "Availability", target.product.availability, {
        item.product.asin: item.product.availability for item in competitors
    }))
    rows.append(_value_metric("brand", "Brand", target.product.brand, {
        item.product.asin: item.product.brand for item in competitors
    }))
    rows.append(_value_metric("category", "Category", target.product.category, {
        item.product.asin: item.product.category for item in competitors
    }))
    return rows


def _gaps(target: ComparedListing, competitors: list[ComparedListing]) -> list[CompetitiveGap]:
    gaps: list[CompetitiveGap] = []
    gaps.extend(_score_gaps("listing_score", "listing score", target.analysis.overall_score, competitors, lambda item: item.analysis.overall_score))
    gaps.extend(_score_gaps("title", "title score", target.analysis.sections.title.score, competitors, lambda item: item.analysis.sections.title.score))
    gaps.extend(_score_gaps("bullets", "bullet score", target.analysis.sections.bullets.score, competitors, lambda item: item.analysis.sections.bullets.score))
    gaps.extend(_score_gaps("description_score", "description score", target.analysis.sections.description.score, competitors, lambda item: item.analysis.sections.description.score))
    gaps.extend(_score_gaps("images_score", "image score", target.analysis.sections.images.score, competitors, lambda item: item.analysis.sections.images.score))
    gaps.extend(_score_gaps(
        "completeness",
        "completeness score",
        target.analysis.sections.completeness.score,
        competitors,
        lambda item: item.analysis.sections.completeness.score,
    ))
    gaps.extend(_score_gaps("social_proof", "social-proof score", target.analysis.sections.social_proof.score, competitors, lambda item: item.analysis.sections.social_proof.score))
    gaps.extend(_count_gaps("images", "images", len(target.product.images), competitors, lambda item: len(item.product.images), IMAGE_GAP_HIGH, 1))
    gaps.extend(_count_gaps("bullets", "bullets", len(target.product.bullet_points), competitors, lambda item: len(item.product.bullet_points), BULLET_GAP_HIGH, 1))
    gaps.extend(_review_gaps(target.product, [item.product for item in competitors]))
    gaps.extend(_rating_gaps(target.product, [item.product for item in competitors]))
    gaps.extend(_description_gaps(target.product, [item.product for item in competitors]))
    gaps.extend(_price_observation_gaps(target.product, [item.product for item in competitors]))
    return gaps


def _score_gaps(dimension: str, label: str, target_score: int, competitors: list[ComparedListing], getter) -> list[CompetitiveGap]:
    gaps: list[CompetitiveGap] = []
    for item in competitors:
        other = getter(item)
        if other <= target_score:
            continue
        diff = other - target_score
        severity = GapSeverity.HIGH if diff >= SCORE_GAP_HIGH else GapSeverity.MEDIUM if diff >= SCORE_GAP_MEDIUM else GapSeverity.LOW
        gaps.append(CompetitiveGap(
            dimension=dimension,
            target_value=target_score,
            competitor_reference=other,
            competitor_asin=item.product.asin,
            direction=GapDirection.BELOW,
            severity=severity,
            evidence=f"Target {label} is {target_score} while {item.product.asin} is {other}.",
        ))
    return gaps


def _count_gaps(dimension: str, label: str, target_count: int, competitors: list[ComparedListing], getter, high: int, medium: int) -> list[CompetitiveGap]:
    gaps: list[CompetitiveGap] = []
    for item in competitors:
        other = getter(item)
        if other <= target_count:
            continue
        diff = other - target_count
        severity = GapSeverity.HIGH if diff >= high else GapSeverity.MEDIUM if diff >= medium else GapSeverity.LOW
        gaps.append(CompetitiveGap(
            dimension=dimension,
            target_value=target_count,
            competitor_reference=other,
            competitor_asin=item.product.asin,
            direction=GapDirection.BELOW,
            severity=severity,
            evidence=f"Target has {target_count} {label} while {item.product.asin} has {other}.",
        ))
    return gaps


def _review_gaps(target: Product, competitors: list[Product]) -> list[CompetitiveGap]:
    if target.review_count is None:
        return []
    gaps: list[CompetitiveGap] = []
    for item in competitors:
        if item.review_count is None or item.review_count <= target.review_count:
            continue
        diff = item.review_count - target.review_count
        ratio = item.review_count / max(target.review_count, 1)
        if ratio >= REVIEW_RATIO_HIGH or diff >= REVIEW_DIFF_HIGH:
            severity = GapSeverity.HIGH
        elif ratio >= REVIEW_RATIO_MEDIUM or diff >= REVIEW_DIFF_MEDIUM:
            severity = GapSeverity.MEDIUM
        elif diff >= REVIEW_DIFF_LOW:
            severity = GapSeverity.LOW
        else:
            continue
        gaps.append(CompetitiveGap(
            dimension="review_count",
            target_value=target.review_count,
            competitor_reference=item.review_count,
            competitor_asin=item.asin,
            direction=GapDirection.BELOW,
            severity=severity,
            evidence=f"Target has {target.review_count} visible reviews while {item.asin} has {item.review_count}.",
        ))
    return gaps


def _rating_gaps(target: Product, competitors: list[Product]) -> list[CompetitiveGap]:
    if target.rating is None:
        return []
    gaps: list[CompetitiveGap] = []
    for item in competitors:
        if item.rating is None or item.rating <= target.rating:
            continue
        diff = round(item.rating - target.rating, 2)
        if diff >= RATING_GAP_HIGH:
            severity = GapSeverity.HIGH
        elif diff >= RATING_GAP_MEDIUM:
            severity = GapSeverity.MEDIUM
        elif diff >= 0.1:
            severity = GapSeverity.LOW
        else:
            continue
        gaps.append(CompetitiveGap(
            dimension="rating",
            target_value=target.rating,
            competitor_reference=item.rating,
            competitor_asin=item.asin,
            direction=GapDirection.BELOW,
            severity=severity,
            evidence=f"Target observed rating is {target.rating} while {item.asin} is {item.rating}.",
        ))
    return gaps


def _description_gaps(target: Product, competitors: list[Product]) -> list[CompetitiveGap]:
    target_has = bool(target.description and target.description.strip())
    if target_has:
        return []
    gaps: list[CompetitiveGap] = []
    for item in competitors:
        if item.description and item.description.strip():
            gaps.append(CompetitiveGap(
                dimension="description",
                target_value=None,
                competitor_reference="present",
                competitor_asin=item.asin,
                direction=GapDirection.MISSING,
                severity=GapSeverity.MEDIUM,
                evidence=f"Target listing has no description while {item.asin} does.",
            ))
    return gaps


def _price_observation_gaps(target: Product, competitors: list[Product]) -> list[CompetitiveGap]:
    deltas = _price_deltas(target, competitors)
    gaps: list[CompetitiveGap] = []
    for delta in deltas:
        if abs(delta.percentage_difference) < PRICE_NOTE_PCT:
            continue
        direction = GapDirection.ABOVE if delta.absolute_difference < 0 else GapDirection.BELOW
        gaps.append(CompetitiveGap(
            dimension="price",
            target_value=delta.target_amount,
            competitor_reference=delta.competitor_amount,
            competitor_asin=delta.competitor_asin,
            direction=direction,
            severity=GapSeverity.LOW,
            evidence=(
                f"Target observed price is {delta.currency} {delta.target_amount:.2f} while "
                f"{delta.competitor_asin} is {delta.currency} {delta.competitor_amount:.2f} "
                f"({delta.percentage_difference:.1f}%). This is not a recommendation to change price."
            ),
        ))
    return gaps


def _price_deltas(target: Product, competitors: list[Product]) -> list[PriceDelta]:
    if target.price is None:
        return []
    deltas: list[PriceDelta] = []
    for item in competitors:
        if item.price is None or item.price.currency != target.price.currency:
            continue
        absolute = item.price.amount - target.price.amount
        percent = (absolute / target.price.amount * 100) if target.price.amount else 0.0
        deltas.append(PriceDelta(
            competitor_asin=item.asin,
            target_amount=target.price.amount,
            competitor_amount=item.price.amount,
            currency=target.price.currency,
            absolute_difference=round(absolute, 2),
            percentage_difference=round(percent, 1),
        ))
    return deltas


def _bsr_metric(target: Product, competitors: list[Product]) -> ComparisonMetric:
    values = {item.asin: item.bsr.rank if item.bsr else None for item in competitors}
    target_rank = target.bsr.rank if target.bsr else None
    categories = {item.bsr.category for item in competitors if item.bsr}
    if target.bsr:
        categories.add(target.bsr.category)
    comparable = bool(target.bsr) and all(item.bsr and item.bsr.category == target.bsr.category for item in competitors if item.bsr)
    note = None
    if len(categories) > 1:
        note = "BSR ranks are shown only as facts. Categories differ, so ranks are not treated as comparable."
        comparable = False
    elif target.bsr is None:
        note = "Target BSR is not available."
        comparable = False
    return ComparisonMetric(
        key="bsr",
        label="BSR",
        target_value=target_rank,
        competitor_values=values,
        comparable=comparable,
        note=note,
    )


def _price_amount(product: Product) -> float | None:
    return product.price.amount if product.price else None


def _price_currency(product: Product) -> str | None:
    return product.price.currency if product.price else None


def _price_note(target: Product, competitors: list[ComparedListing]) -> str | None:
    currencies = {item.product.price.currency for item in competitors if item.product.price}
    if target.price:
        currencies.add(target.price.currency)
    if len(currencies) > 1:
        return "Prices use different currencies and are not compared as better or worse."
    return "Lower price is not treated as automatically better. Margin and conversion are unknown."


def _title_length(product: Product) -> int:
    return len(product.title.strip()) if product.title else 0


def _value_metric(key: str, label: str, target_value: object, competitor_values: dict[str, object], note: str | None = None) -> ComparisonMetric:
    comparable = target_value is not None and any(value is not None for value in competitor_values.values())
    return ComparisonMetric(
        key=key,
        label=label,
        target_value=target_value,
        competitor_values=competitor_values,
        comparable=comparable,
        note=note,
    )


def _numeric_metric(key: str, label: str, target_value: object, competitor_values: dict[str, object], note: str | None = None) -> ComparisonMetric:
    return _value_metric(key, label, target_value, competitor_values, note=note)


__all__ = ["compare_listings"]
