"""Custom Listing Intelligence V2 aggregate weights.

This module changes only how existing section scores are combined.
It does not change title, bullet, SEO, A+, media, or structure rules.
Market Signals and Data Coverage are never weighted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.models.listing_analysis_v2 import ListingAnalysisV2
from app.models.scoring_profile import ScoringWeights

STANDARD_PROFILE_ID = "standard-v2"
STANDARD_PROFILE_NAME = "Standard V2"

WEIGHT_KEYS = (
    "title",
    "bullets",
    "description_a_plus",
    "media",
    "content_structure",
)

RESERVED_PROFILE_NAMES = frozenset(
    {
        "standard v2",
        "standard-v2",
        "standard",
    }
)


@dataclass(frozen=True)
class SectionScores:
    title: int
    bullets: int
    description_a_plus: int
    media: int
    content_structure: int


def section_scores_from_analysis(analysis: ListingAnalysisV2) -> SectionScores:
    return SectionScores(
        title=analysis.sections.title.score,
        bullets=analysis.sections.bullets.score,
        description_a_plus=analysis.sections.description_a_plus.score,
        media=analysis.sections.media_coverage.score,
        content_structure=analysis.sections.content_structure.score,
    )


def calculate_weighted_listing_score(
    section_scores: SectionScores,
    weights: ScoringWeights,
) -> int:
    """Aggregate existing section scores with top-level weights.

    Rounding matches Listing Intelligence V2: round, then clamp 0–100.
    """
    total = (
        section_scores.title * (float(weights.title) / 100.0)
        + section_scores.bullets * (float(weights.bullets) / 100.0)
        + section_scores.description_a_plus * (float(weights.description_a_plus) / 100.0)
        + section_scores.media * (float(weights.media) / 100.0)
        + section_scores.content_structure * (float(weights.content_structure) / 100.0)
    )
    return int(round(min(100, max(0, total))))


def weights_total(weights: ScoringWeights) -> Decimal:
    return sum((_as_decimal(getattr(weights, key)) for key in WEIGHT_KEYS), Decimal("0"))


def validate_weights(weights: ScoringWeights) -> None:
    from app.core.exceptions import ScoringProfileValidationError

    values: list[Decimal] = []
    for key in WEIGHT_KEYS:
        number = _as_decimal(getattr(weights, key))
        if number < 0:
            raise ScoringProfileValidationError("Weights cannot be negative.")
        if number > 100:
            raise ScoringProfileValidationError("Each weight must be 100 or less.")
        values.append(number)
    total = sum(values, Decimal("0"))
    if total != Decimal("100"):
        raise ScoringProfileValidationError(f"Weights must total 100. Current total is {total}.")


def is_reserved_profile_name(name: str) -> bool:
    return name.strip().lower() in RESERVED_PROFILE_NAMES


def is_standard_profile_id(profile_id: str | None) -> bool:
    if profile_id is None:
        return False
    return profile_id.strip().lower() in {STANDARD_PROFILE_ID, "standard"}


def _as_decimal(value: object) -> Decimal:
    from app.core.exceptions import ScoringProfileValidationError

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ScoringProfileValidationError("Weights must be numeric.") from exc


STANDARD_V2_WEIGHTS = ScoringWeights(
    title=20,
    bullets=25,
    description_a_plus=20,
    media=20,
    content_structure=15,
)
