from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.models.listing_analysis_v2 import ListingAnalysisV2
from app.models.saved_analysis import SavedAnalysisDetail
from app.reports.client_analysis_report import (
    REPORT_TEMPLATE_VERSION,
    analysis_pdf_filename,
    source_display,
)
from app.reports.formatting import (
    coverage_band,
    display_title,
    evidence_label,
    format_date_long,
    format_date_short,
    format_int,
    format_price,
    format_rating,
    normalize_text,
    split_assessment,
)
from app.reports.labels import SECTION_ORDER, friendly_label, short_section_label

PRIORITY_ORDER = ("high", "medium", "low", "info")
PRIORITY_HEADINGS = {
    "high": "High Priority",
    "medium": "Medium Priority",
    "low": "Low Priority",
    "info": "Information",
}


@dataclass
class CoverView:
    brand: str
    display_title: str
    full_title: str
    asin: str
    marketplace: str
    analysis_date: str
    fetched_date: str
    source: str
    image_bytes: bytes | None


@dataclass
class SectionScoreView:
    key: str
    label: str
    short_label: str
    score: int
    max_score: int
    status: str
    findings: list[str]


@dataclass
class FixFirstItem:
    index: int
    category: str
    summary: str
    priority: str


@dataclass
class KpiItem:
    label: str
    value: str


@dataclass
class BsrItem:
    rank: str
    category: str


@dataclass
class CoverageFieldView:
    label: str
    state: str


@dataclass
class CoverageGroupView:
    title: str
    percentage: int
    available: int
    expected: int
    fields: list[CoverageFieldView]


@dataclass
class FindingCard:
    category: str
    message: str


@dataclass
class ActionItem:
    index: int
    title: str
    detail: str
    priority: str


@dataclass
class InsightModule:
    title: str
    assessment: str
    strengths: list[str]
    opportunities: list[str]


@dataclass
class ImageIntelView:
    executive: str
    modules: list[InsightModule]
    visual_strengths: list[str]
    improvements: list[ActionItem]
    roles_observed: list[str]
    roles_missing: list[str]
    redundancy: list[str]
    plan: list[str]


@dataclass
class ClientReportViewModel:
    report_id: UUID
    template_version: str
    filename: str
    asin: str
    cover: CoverView
    overall_score: int
    overall_status: str
    standard_score: int
    custom_score: int | None
    custom_profile_name: str | None
    custom_weights: list[tuple[str, str]]
    sections: list[SectionScoreView]
    fix_first: list[FixFirstItem]
    ai_priority_actions: list[FixFirstItem]
    ai_executive_paragraphs: list[str]
    kpis_primary: list[KpiItem]
    kpis_secondary: list[KpiItem]
    bsr: list[BsrItem]
    coverage_overall: int
    coverage_band: str
    coverage_groups: list[CoverageGroupView]
    findings: dict[str, list[FindingCard]]
    action_plan: list[ActionItem]
    ai_modules: list[InsightModule]
    spec_covered: list[str]
    spec_missing: list[str]
    spec_avoid: list[str]
    seller_plan: list[ActionItem]
    suggested_title: str | None
    suggested_title_chars: int | None
    suggested_bullets: list[str]
    suggested_description: str | None
    confidence_notes: list[str]
    image: ImageIntelView | None
    metadata_rows: list[tuple[str, str]]
    toc: list[str] = field(default_factory=list)

    @property
    def has_ai(self) -> bool:
        return bool(self.ai_executive_paragraphs or self.ai_modules)

    @property
    def has_custom(self) -> bool:
        return self.custom_score is not None

    @property
    def has_image(self) -> bool:
        return self.image is not None

    @property
    def has_suggested_copy(self) -> bool:
        return bool(self.suggested_title or self.suggested_bullets or self.suggested_description)


def build_client_report_view(
    detail: SavedAnalysisDetail,
    *,
    cover_image_bytes: bytes | None = None,
) -> ClientReportViewModel:
    product = detail.product
    analysis = detail.analysis
    brand, short_title = display_title(product)
    source = source_display(
        detail.meta.product_source.value
        if hasattr(detail.meta.product_source, "value")
        else (str(detail.meta.product_source) if detail.meta.product_source else None)
    )
    sections = [_section_view(analysis, key, label) for key, label in SECTION_ORDER]
    custom_score = None
    custom_name = None
    custom_weights: list[tuple[str, str]] = []
    if detail.custom_score:
        custom_score = detail.custom_score.custom_listing_quality_score
        custom_name = detail.custom_score.profile.profile_name
        weights = detail.custom_score.profile.weights
        custom_weights = [
            ("Title Optimization", f"{_pct(weights.title)}%"),
            ("Bullet Content & SEO Readiness", f"{_pct(weights.bullets)}%"),
            ("Description & A+ Content", f"{_pct(weights.description_a_plus)}%"),
            ("Media Coverage", f"{_pct(weights.media)}%"),
            ("Content Structure & Readability", f"{_pct(weights.content_structure)}%"),
        ]

    signals = analysis.market_signals
    kpis_primary = [
        KpiItem("Rating", format_rating(signals.rating)),
        KpiItem("Reviews", format_int(signals.review_count)),
        KpiItem("Price", format_price(signals.price, product.marketplace)),
    ]
    amazon = (
        "Not available"
        if signals.is_sold_by_amazon is None
        else ("Yes" if signals.is_sold_by_amazon else "No")
    )
    kpis_secondary = [
        KpiItem("Availability", normalize_text(signals.availability or signals.availability_type) or "Not available"),
        KpiItem("Recent sales", normalize_text(signals.recent_sales_text) or "Not available"),
        KpiItem("Sold by Amazon", amazon),
    ]
    bsr_items = [
        BsrItem(rank=f"#{item.rank}", category=normalize_text(item.category))
        for item in (signals.bsr_ranks or [])
    ]
    if not bsr_items and product.bsr:
        bsr_items = [BsrItem(rank=f"#{product.bsr.rank}", category=normalize_text(product.bsr.category))]

    coverage = analysis.data_coverage
    groups = [
        ("Core Listing", coverage.core_listing_content),
        ("Media", coverage.media),
        ("Enhanced Content", coverage.enhanced_content),
        ("Category Context", coverage.category_context),
        ("Market Signals", coverage.market_signals),
    ]
    coverage_groups = [
        CoverageGroupView(
            title=title,
            percentage=group.percentage,
            available=group.available,
            expected=group.expected,
            fields=[
                CoverageFieldView(label=friendly_label(field.name), state=evidence_label(field.evidence_state.value))
                for field in group.fields
            ],
        )
        for title, group in groups
    ]

    findings: dict[str, list[FindingCard]] = {key: [] for key in PRIORITY_ORDER}
    for item in analysis.findings:
        findings.setdefault(item.severity.value, []).append(
            FindingCard(category=friendly_label(item.category), message=normalize_text(item.message))
        )

    action_plan = [
        ActionItem(
            index=index,
            title=normalize_text(item.action),
            detail="",
            priority=item.priority.value.upper(),
        )
        for index, item in enumerate(analysis.recommendations, start=1)
    ]

    ai_paragraphs: list[str] = []
    ai_modules: list[InsightModule] = []
    spec_covered: list[str] = []
    spec_missing: list[str] = []
    spec_avoid: list[str] = []
    seller_plan: list[ActionItem] = []
    suggested_title = None
    suggested_bullets: list[str] = []
    suggested_description = None
    confidence_notes: list[str] = []
    if detail.ai_intelligence:
        ai = detail.ai_intelligence
        ai_paragraphs = [normalize_text(part) for part in split_assessment(ai.executive_assessment)]
        content = ai.content_analysis
        ai_modules = [
            _insight("Title Analysis", content.title.assessment, content.title.strengths, content.title.gaps),
            _insight(
                "Bullet Content & SEO Readiness",
                content.bullets.assessment,
                content.bullets.strengths,
                content.bullets.gaps + content.bullets.seo_readiness_notes,
            ),
            _insight(
                "Description Analysis",
                content.description.assessment,
                content.description.strengths,
                content.description.gaps,
            ),
            _insight(
                "A+ Content Analysis",
                content.a_plus.assessment,
                content.a_plus.strengths,
                content.a_plus.gaps,
            ),
            _insight(
                "Content Structure",
                content.structure.assessment,
                content.structure.redundancy_notes,
                content.structure.coverage_gaps,
            ),
        ]
        spec_covered = [normalize_text(item) for item in ai.specification_coverage.represented]
        spec_missing = [normalize_text(item) for item in ai.specification_coverage.missing_from_customer_copy]
        spec_avoid = [normalize_text(item) for item in ai.specification_coverage.not_recommended_for_copy]
        seller_plan = [
            ActionItem(
                index=item.step,
                title=normalize_text(item.action),
                detail=normalize_text(item.rationale),
                priority=item.priority.value.upper(),
            )
            for item in ai.seller_action_plan
        ]
        copy = ai.rewrite_suggestions
        suggested_title = normalize_text(copy.suggested_title) or None
        suggested_bullets = [normalize_text(item) for item in copy.suggested_bullets if item]
        suggested_description = normalize_text(copy.optional_description_excerpt) or None
        confidence_notes = [normalize_text(item) for item in ai.confidence_notes if item]

    image_view = None
    if detail.image_intelligence:
        image = detail.image_intelligence
        image_view = ImageIntelView(
            executive=normalize_text(image.executive_assessment),
            modules=[
                _insight(
                    "Main Image",
                    image.main_image_analysis.assessment,
                    image.main_image_analysis.strengths,
                    image.main_image_analysis.concerns,
                ),
                _insight(
                    "Gallery Strategy",
                    image.gallery_analysis.assessment,
                    [],
                    image.gallery_analysis.coverage_opportunities,
                ),
                _insight(
                    "A+ Visual Intelligence",
                    image.a_plus_visual_analysis.assessment,
                    image.a_plus_visual_analysis.strengths,
                    image.a_plus_visual_analysis.gaps,
                ),
                _insight(
                    "Brand Story",
                    image.brand_story_analysis.assessment,
                    image.brand_story_analysis.strengths,
                    image.brand_story_analysis.gaps,
                ),
            ],
            visual_strengths=[normalize_text(item) for item in image.visual_strengths],
            improvements=[
                ActionItem(
                    index=index,
                    title=normalize_text(item.issue),
                    detail=normalize_text(item.recommended_action),
                    priority=item.priority.value.upper(),
                )
                for index, item in enumerate(image.priority_improvements, start=1)
            ],
            roles_observed=[friendly_label(role.value) for role in image.media_role_coverage.observed],
            roles_missing=[friendly_label(role.value) for role in image.media_role_coverage.not_observed],
            redundancy=[normalize_text(item) for item in image.redundancy_analysis],
            plan=[
                f"{item.step}. {normalize_text(item.slot)} — {normalize_text(item.purpose)}"
                for item in image.recommended_image_plan
            ],
        )

    profile = custom_name or "Standard V2"
    meta = detail.meta
    view = ClientReportViewModel(
        report_id=detail.report_id,
        template_version=REPORT_TEMPLATE_VERSION,
        filename=analysis_pdf_filename(product.asin, meta.analyzed_at),
        asin=product.asin,
        cover=CoverView(
            brand=normalize_text(brand),
            display_title=normalize_text(short_title),
            full_title=normalize_text(product.title or "Untitled listing"),
            asin=product.asin,
            marketplace=product.marketplace or "Not available",
            analysis_date=format_date_short(meta.analyzed_at),
            fetched_date=format_date_short(meta.product_fetched_at),
            source=source,
            image_bytes=cover_image_bytes,
        ),
        overall_score=analysis.listing_quality_score,
        overall_status=friendly_label(analysis.status.value),
        standard_score=analysis.listing_quality_score,
        custom_score=custom_score,
        custom_profile_name=custom_name,
        custom_weights=custom_weights,
        sections=sections,
        fix_first=_fix_first(analysis, detail),
        ai_priority_actions=_ai_priority(detail),
        ai_executive_paragraphs=ai_paragraphs,
        kpis_primary=kpis_primary,
        kpis_secondary=kpis_secondary,
        bsr=bsr_items,
        coverage_overall=coverage.overall_percentage,
        coverage_band=coverage_band(coverage.overall_percentage),
        coverage_groups=coverage_groups,
        findings=findings,
        action_plan=action_plan,
        ai_modules=ai_modules,
        spec_covered=spec_covered,
        spec_missing=spec_missing,
        spec_avoid=spec_avoid,
        seller_plan=seller_plan,
        suggested_title=suggested_title,
        suggested_title_chars=len(suggested_title) if suggested_title else None,
        suggested_bullets=suggested_bullets,
        suggested_description=suggested_description,
        confidence_notes=confidence_notes,
        image=image_view,
        metadata_rows=[
            ("Report ID", str(detail.report_id)),
            ("ASIN", product.asin),
            ("Marketplace", product.marketplace or "Not available"),
            ("Product source", source),
            ("Analysis date", format_date_long(meta.analyzed_at)),
            ("Product fetched date", format_date_long(meta.product_fetched_at)),
            ("Score version", meta.listing_score_version or analysis.score_version),
            ("Scoring profile", profile),
            ("AI provider", meta.ai_provider or "Not generated"),
            ("AI model", meta.ai_model or "Not generated"),
            ("AI prompt version", meta.ai_prompt_version or "Not generated"),
            ("Image prompt version", meta.image_prompt_version or "Not generated"),
            ("PDF template version", REPORT_TEMPLATE_VERSION),
        ],
    )
    view.toc = _toc(view)
    return view


def _section_view(analysis: ListingAnalysisV2, key: str, label: str) -> SectionScoreView:
    section = getattr(analysis.sections, key)
    return SectionScoreView(
        key=key,
        label=label,
        short_label=short_section_label(key),
        score=section.score,
        max_score=section.max_score,
        status=friendly_label(section.status.value),
        findings=[normalize_text(item) for item in section.findings],
    )


def _insight(title: str, assessment: str, strengths: list[str], gaps: list[str]) -> InsightModule:
    return InsightModule(
        title=title,
        assessment=normalize_text(assessment),
        strengths=[normalize_text(item) for item in strengths if item],
        opportunities=[normalize_text(item) for item in gaps if item],
    )


def _fix_first(analysis: ListingAnalysisV2, detail: SavedAnalysisDetail) -> list[FixFirstItem]:
    items: list[FixFirstItem] = []
    ranked = sorted(analysis.recommendations, key=lambda rec: {"high": 0, "medium": 1, "low": 2}.get(rec.priority.value, 9))
    for rec in ranked[:5]:
        items.append(
            FixFirstItem(
                index=len(items) + 1,
                category=friendly_label(rec.category),
                summary=normalize_text(rec.action),
                priority=rec.priority.value.title(),
            )
        )
    if items:
        return items
    for finding in analysis.findings:
        if finding.severity.value not in {"high", "medium"}:
            continue
        items.append(
            FixFirstItem(
                index=len(items) + 1,
                category=friendly_label(finding.category),
                summary=normalize_text(finding.message),
                priority=finding.severity.value.title(),
            )
        )
        if len(items) >= 5:
            break
    if items:
        return items
    if detail.ai_intelligence:
        for action in detail.ai_intelligence.priority_actions[:5]:
            items.append(
                FixFirstItem(
                    index=len(items) + 1,
                    category=friendly_label(action.area),
                    summary=normalize_text(action.recommended_action or action.issue),
                    priority=action.priority.value.title(),
                )
            )
    return items


def _ai_priority(detail: SavedAnalysisDetail) -> list[FixFirstItem]:
    if not detail.ai_intelligence:
        return []
    items: list[FixFirstItem] = []
    for action in detail.ai_intelligence.priority_actions[:5]:
        items.append(
            FixFirstItem(
                index=len(items) + 1,
                category=friendly_label(action.area),
                summary=normalize_text(action.recommended_action or action.issue),
                priority=action.priority.value.title(),
            )
        )
    return items


def _toc(view: ClientReportViewModel) -> list[str]:
    entries = [
        "Executive Overview",
        "Listing Quality",
        "Market Signals & Data Coverage",
        "Findings & Opportunities",
    ]
    if view.has_ai:
        entries.append("AI Content & SEO Strategy")
    if view.seller_plan:
        entries.append("Seller Action Plan")
    if view.has_suggested_copy:
        entries.append("Suggested Listing Copy")
    if view.has_image:
        entries.append("Image & Media Intelligence")
    entries.append("Report Information")
    return entries


def _pct(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"
