"""Compact Listing Intelligence V2 claims for EvidenceEnvelope.

Copies scores, findings, and recommendations from the deterministic analysis.
Does not recalculate scores or invent actions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.copilot.evidence import EvidenceClaim, claim
from app.models.listing_analysis_v2 import ListingAnalysisV2

ANALYSIS_ENGINE = "listing_analysis_v2"

SECTION_FIELDS: tuple[tuple[str, str], ...] = (
    ("title", "Title"),
    ("bullets", "Bullets"),
    ("description_a_plus", "Description / A+"),
    ("media_coverage", "Images"),
    ("content_structure", "Content structure"),
)

_PRIORITY_ORDER = {"high": 1, "medium": 2, "low": 3}
_WEAK_SEVERITIES = {"high", "medium"}
_MAX_ROWS = 8

CLAIM_KEYS = frozenset(
    {
        "listing_quality_score",
        "score_version",
        "analysis_engine",
        "section_scores",
        "findings",
        "weaknesses",
        "recommendations",
    }
)


def compact_section_scores(analysis: ListingAnalysisV2) -> dict[str, Any]:
    scores: dict[str, Any] = {}
    for field, label in SECTION_FIELDS:
        section = getattr(analysis.sections, field)
        scores[field] = {
            "label": label,
            "score": section.score,
            "max_score": section.max_score,
            "status": section.status.value,
        }
    return scores


def compact_findings(analysis: ListingAnalysisV2) -> list[dict[str, Any]]:
    return [
        {
            "code": item.code,
            "category": item.category,
            "severity": item.severity.value,
            "message": item.message,
        }
        for item in analysis.findings
    ]


def compact_weaknesses(analysis: ListingAnalysisV2) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in analysis.findings:
        if item.severity.value not in _WEAK_SEVERITIES:
            continue
        rows.append(
            {
                "area": item.category,
                "issue": item.message,
                "severity": item.severity.value,
                "code": item.code,
            }
        )
        if len(rows) >= _MAX_ROWS:
            break
    return rows


def compact_recommendations(analysis: ListingAnalysisV2) -> list[dict[str, Any]]:
    finding_messages = {item.code: item.message for item in analysis.findings}
    ranked = sorted(
        analysis.recommendations,
        key=lambda rec: _PRIORITY_ORDER.get(rec.priority.value, 9),
    )
    rows: list[dict[str, Any]] = []
    for index, rec in enumerate(ranked[:_MAX_ROWS], start=1):
        rows.append(
            {
                "priority": index,
                "priority_label": rec.priority.value,
                "action": rec.action,
                "reason": finding_messages.get(rec.finding_code) or rec.action,
                "code": rec.code,
                "area": rec.category,
            }
        )
    return rows


def listing_analysis_claims(
    analysis: ListingAnalysisV2,
    *,
    kind: str,
    source: str,
    as_of: datetime | None = None,
) -> list[EvidenceClaim]:
    """Evidence claims copied from ListingAnalysisV2. No raw Product or ORM objects."""

    return [
        claim(
            "listing_quality_score",
            analysis.listing_quality_score,
            kind=kind,  # type: ignore[arg-type]
            source=source,
            as_of=as_of,
            notes="Score from Listing Intelligence V2. Not recalculated.",
        ),
        claim("score_version", analysis.score_version, kind=kind, source=source, as_of=as_of),  # type: ignore[arg-type]
        claim("analysis_engine", ANALYSIS_ENGINE, kind=kind, source=source, as_of=as_of),  # type: ignore[arg-type]
        claim(
            "section_scores",
            compact_section_scores(analysis),
            kind=kind,  # type: ignore[arg-type]
            source=source,
            as_of=as_of,
        ),
        claim("findings", compact_findings(analysis), kind=kind, source=source, as_of=as_of),  # type: ignore[arg-type]
        claim("weaknesses", compact_weaknesses(analysis), kind=kind, source=source, as_of=as_of),  # type: ignore[arg-type]
        claim(
            "recommendations",
            compact_recommendations(analysis),
            kind=kind,  # type: ignore[arg-type]
            source=source,
            as_of=as_of,
        ),
    ]
