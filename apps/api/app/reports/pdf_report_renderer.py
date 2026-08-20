from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    CondPageBreak,
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.reports.fonts import register_report_fonts
from app.reports.pdf_widgets import (
    ACCENT,
    ATTENTION,
    BORDER,
    PRIMARY_DARK,
    SURFACE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING,
    HeroScore,
    NumberedCanvas,
    ScoreBar,
)
from app.reports.view_model import (
    PRIORITY_HEADINGS,
    PRIORITY_ORDER,
    ActionItem,
    ClientReportViewModel,
    CoverageGroupView,
    InsightModule,
    SectionScoreView,
)

PAGE_WIDTH = A4[0] - 36 * mm


class PdfReportRenderer:
    """Render a ClientReportViewModel to an A4 consulting-style PDF."""

    def render(self, report: ClientReportViewModel) -> bytes:
        register_report_fonts()
        styles = _styles()
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=20 * mm,
            bottomMargin=18 * mm,
            title=f"Listing Analysis Report · {report.asin}",
            author="Amazon Seller Intelligence",
            pageCompression=1,
        )
        story: list = []
        story.extend(self._cover(report, styles))
        story.append(PageBreak())
        story.extend(self._contents(report, styles))
        story.append(PageBreak())
        story.extend(self._executive(report, styles))
        story.append(PageBreak())
        story.extend(self._listing_quality(report, styles))
        story.extend(self._custom_scoring(report, styles))
        story.append(PageBreak())
        story.extend(self._market(report, styles))
        story.extend(self._coverage(report, styles))
        story.append(PageBreak())
        story.extend(self._findings(report, styles))
        story.extend(self._actions(report, styles))
        if report.has_ai:
            story.append(PageBreak())
            story.extend(self._ai(report, styles))
        if report.spec_covered or report.spec_missing or report.spec_avoid:
            story.extend(self._specifications(report, styles))
        if report.seller_plan:
            story.append(PageBreak())
            story.extend(self._seller_plan(report, styles))
        if report.has_suggested_copy:
            story.append(PageBreak())
            story.extend(self._copy(report, styles))
        if report.image:
            story.append(PageBreak())
            story.extend(self._image(report, styles))
        story.append(PageBreak())
        story.extend(self._metadata(report, styles))
        story.extend(self._disclaimer(report, styles))
        doc.build(
            story,
            canvasmaker=lambda *args, **kwargs: NumberedCanvas(*args, asin=report.asin, **kwargs),
        )
        return buffer.getvalue()

    def _cover(self, report: ClientReportViewModel, styles) -> list:
        cover = report.cover
        items = [
            Spacer(1, 22 * mm),
            Paragraph("AMAZON SELLER INTELLIGENCE", styles["cover_kicker"]),
            Spacer(1, 8 * mm),
            Paragraph("Listing Performance", styles["cover_title"]),
            Paragraph("&amp; Optimization Report", styles["cover_title"]),
            Spacer(1, 4 * mm),
            HRFlowable(width=28 * mm, thickness=2, color=ACCENT, spaceAfter=14, hAlign="LEFT"),
            Paragraph(escape(cover.brand), styles["cover_brand"]),
            Spacer(1, 2 * mm),
            Paragraph(escape(cover.display_title), styles["cover_product"]),
            Spacer(1, 12 * mm),
            _meta_block(
                [
                    ("ASIN", cover.asin),
                    ("Marketplace", cover.marketplace),
                    ("Analysis Date", cover.analysis_date),
                ],
                styles,
            ),
        ]
        if cover.image_bytes:
            try:
                image = Image(BytesIO(cover.image_bytes), width=58 * mm, height=58 * mm, kind="proportional")
                image.hAlign = "LEFT"
                items.extend([Spacer(1, 14 * mm), image])
            except Exception:
                items.append(Spacer(1, 18 * mm))
        else:
            items.append(Spacer(1, 22 * mm))
        items.extend(
            [
                Spacer(1, 28 * mm),
                Paragraph("Prepared using", styles["muted"]),
                Paragraph("Amazon Seller Intelligence", styles["cover_brand"]),
                Paragraph("Historical Analysis", styles["muted"]),
            ]
        )
        return items

    def _contents(self, report: ClientReportViewModel, styles) -> list:
        items = [_heading("Contents", styles)]
        for index, entry in enumerate(report.toc, start=1):
            items.append(Paragraph(f"{index:02d}    {escape(entry)}", styles["toc"]))
        return items

    def _executive(self, report: ClientReportViewModel, styles) -> list:
        items = [_heading("Executive Overview", styles)]
        items.append(HeroScore(report.overall_score, report.overall_status, PAGE_WIDTH))
        items.append(Spacer(1, 5 * mm))
        if report.custom_score is not None:
            items.append(
                Paragraph(
                    f"Standard score {report.standard_score} / 100    ·    "
                    f"Custom score {report.custom_score} / 100    ·    {escape(report.custom_profile_name or '')}",
                    styles["muted"],
                )
            )
        items.append(Spacer(1, 3 * mm))
        items.append(_section_score_cards(report.sections, styles))
        items.append(Spacer(1, 8 * mm))
        items.append(_heading("What to Fix First", styles, size="h2"))
        if report.fix_first:
            for item in report.fix_first:
                items.append(_fix_row(item.index, item.category, item.summary, item.priority, styles))
        else:
            items.append(Paragraph("No high-priority persisted actions were recorded.", styles["body"]))
        if report.ai_executive_paragraphs:
            items.append(Spacer(1, 6 * mm))
            items.append(CondPageBreak(28 * mm))
            items.extend(_callout("AI Executive Assessment", report.ai_executive_paragraphs, styles))
        return items

    def _listing_quality(self, report: ClientReportViewModel, styles) -> list:
        items = [_heading("Listing Quality", styles)]
        items.append(Paragraph("Product Details", styles["h2"]))
        items.append(Paragraph(escape(report.cover.full_title), styles["body"]))
        items.append(Paragraph(f"Brand: {escape(report.cover.brand)}", styles["muted"]))
        items.append(Spacer(1, 4 * mm))
        for section in report.sections:
            items.extend(_section_deep_dive(section, styles))
        return items

    def _custom_scoring(self, report: ClientReportViewModel, styles) -> list:
        if not report.has_custom:
            return []
        items = [_heading("Custom Scoring Profile", styles, size="h2")]
        items.append(
            Paragraph(
                f"{escape(report.custom_profile_name or '')}    ·    "
                f"Standard {report.standard_score} / 100    ·    Custom {report.custom_score} / 100",
                styles["body"],
            )
        )
        rows = [["Dimension", "Weight"]] + [[label, weight] for label, weight in report.custom_weights]
        items.append(_simple_table(rows, [120 * mm, 40 * mm], styles))
        items.append(
            Paragraph(
                "Custom weights change score aggregation only. The underlying section analysis remains unchanged.",
                styles["muted"],
            )
        )
        return items

    def _market(self, report: ClientReportViewModel, styles) -> list:
        items = [_heading("Market Signals", styles)]
        items.append(
            Paragraph(
                "Observed marketplace facts. These do not prove listing quality.",
                styles["muted"],
            )
        )
        items.append(_kpi_row(report.kpis_primary, styles))
        items.append(Spacer(1, 3 * mm))
        items.append(_kpi_row(report.kpis_secondary, styles))
        if report.bsr:
            items.append(Spacer(1, 5 * mm))
            items.append(Paragraph("Best Seller Rank", styles["h2"]))
            for item in report.bsr:
                items.append(
                    KeepTogether(
                        [
                            Paragraph(escape(item.rank), styles["bsr_rank"]),
                            Paragraph(escape(item.category), styles["muted"]),
                        ]
                    )
                )
        return items

    def _coverage(self, report: ClientReportViewModel, styles) -> list:
        items = [_heading("Data Coverage", styles)]
        items.append(Paragraph("Data Confidence", styles["h2"]))
        items.append(Paragraph(f"{report.coverage_overall}%", styles["coverage_hero"]))
        items.append(Paragraph(escape(report.coverage_band), styles["muted"]))
        items.append(Spacer(1, 3 * mm))
        items.append(_coverage_cards(report.coverage_groups, styles))
        items.append(Spacer(1, 4 * mm))
        items.append(Paragraph("Evidence matrix", styles["h2"]))
        matrix = [["Field", "Evidence state"]]
        for group in report.coverage_groups:
            for field in group.fields:
                matrix.append([field.label, field.state])
        items.append(_simple_table(matrix, [100 * mm, 60 * mm], styles))
        return items

    def _findings(self, report: ClientReportViewModel, styles) -> list:
        items = [_heading("What We Found", styles)]
        any_found = False
        for key in PRIORITY_ORDER:
            cards = report.findings.get(key) or []
            if not cards:
                continue
            any_found = True
            items.append(Paragraph(PRIORITY_HEADINGS[key].upper(), styles["priority_label"]))
            for card in cards:
                items.append(_finding_card(card.category, card.message, styles))
        if not any_found:
            items.append(Paragraph("No deterministic findings were recorded.", styles["body"]))
        return items

    def _actions(self, report: ClientReportViewModel, styles) -> list:
        items = [_heading("Recommended Action Plan", styles)]
        if not report.action_plan:
            items.append(Paragraph("No persisted recommended actions were recorded.", styles["body"]))
            return items
        for item in report.action_plan:
            items.extend(_roadmap_item(item, styles))
        return items

    def _ai(self, report: ClientReportViewModel, styles) -> list:
        items = [_heading("AI Content &amp; SEO Strategy", styles)]
        if report.ai_executive_paragraphs:
            items.extend(_callout("Executive Assessment", report.ai_executive_paragraphs, styles))
        if report.ai_priority_actions:
            items.append(Paragraph("Priority Actions", styles["h2"]))
            for item in report.ai_priority_actions:
                items.append(
                    Paragraph(
                        f"<b>{item.index:02d}</b>  {escape(item.category)} — {escape(item.summary)}  ({escape(item.priority)})",
                        styles["body"],
                    )
                )
        for module in report.ai_modules:
            items.extend(_insight_block(module, styles))
        if report.confidence_notes:
            items.append(Paragraph("Confidence Notes", styles["h2"]))
            for note in report.confidence_notes:
                items.append(Paragraph(f"• {escape(note)}", styles["bullet"]))
        return items

    def _specifications(self, report: ClientReportViewModel, styles) -> list:
        items = [_heading("Specification Coverage", styles, size="h2")]
        items.extend(_chip_group("Covered in listing", report.spec_covered, styles, kind="ok"))
        items.extend(_chip_group("Missing from customer copy", report.spec_missing, styles, kind="warn"))
        items.extend(_chip_group("Do not use as copy claims", report.spec_avoid, styles, kind="info"))
        return items

    def _seller_plan(self, report: ClientReportViewModel, styles) -> list:
        items = [_heading("Seller Action Plan", styles)]
        for item in report.seller_plan:
            items.extend(_roadmap_item(item, styles, show_detail=True))
        return items

    def _copy(self, report: ClientReportViewModel, styles) -> list:
        items = [_heading("Suggested Listing Copy", styles)]
        if report.suggested_title:
            items.append(Paragraph("Suggested Title", styles["h2"]))
            items.extend(_copy_box(report.suggested_title, styles))
            items.append(Paragraph(f"Suggested character count: {report.suggested_title_chars}", styles["muted"]))
        if report.suggested_bullets:
            items.append(Paragraph("Suggested Bullet Points", styles["h2"]))
            for index, bullet in enumerate(report.suggested_bullets, start=1):
                items.append(Paragraph(f"{index:02d}", styles["num"]))
                items.extend(_copy_box(bullet, styles))
                items.append(Spacer(1, 2 * mm))
        if report.suggested_description:
            items.append(Paragraph("Description Excerpt", styles["h2"]))
            items.extend(_copy_box(report.suggested_description, styles))
        return items

    def _image(self, report: ClientReportViewModel, styles) -> list:
        image = report.image
        if image is None:
            return []
        items = [_heading("Image &amp; Media Intelligence", styles)]
        items.append(
            Paragraph(
                "Qualitative visual intelligence only. This analysis does not produce a numeric image-quality score.",
                styles["muted"],
            )
        )
        items.extend(_callout("Executive Assessment", split_if_needed(image.executive), styles))
        for module in image.modules:
            items.extend(_insight_block(module, styles))
        if image.visual_strengths:
            items.extend(_chip_group("Visual strengths", image.visual_strengths, styles, kind="ok"))
        if image.improvements:
            items.append(Paragraph("Priority Improvements", styles["h2"]))
            for item in image.improvements:
                items.extend(_roadmap_item(item, styles, show_detail=True))
        if image.roles_observed or image.roles_missing:
            items.append(
                _simple_table(
                    [
                        ["Observed roles", ", ".join(image.roles_observed) or "None recorded"],
                        ["Not observed", ", ".join(image.roles_missing) or "None recorded"],
                    ],
                    [50 * mm, 110 * mm],
                    styles,
                    header=False,
                )
            )
        if image.redundancy:
            items.extend(_chip_group("Redundancy", image.redundancy, styles, kind="info"))
        if image.plan:
            items.append(Paragraph("Recommended Image Plan", styles["h2"]))
            for line in image.plan:
                items.append(Paragraph(escape(line), styles["bullet"]))
        return items

    def _metadata(self, report: ClientReportViewModel, styles) -> list:
        items = [_heading("Report Information", styles)]
        rows = [[key, value] for key, value in report.metadata_rows]
        items.append(_simple_table(rows, [55 * mm, 105 * mm], styles, header=False, muted=True))
        return items

    def _disclaimer(self, report: ClientReportViewModel, styles) -> list:
        items: list = [Spacer(1, 6 * mm)]
        items.extend(
            _callout(
                "Disclaimer",
                [
                    "This report is based on listing information available at the time of analysis. "
                    "Listing Quality Scores are internal analytical measures and are not Amazon-defined "
                    "performance grades. Market outcomes such as sales, conversion, ranking, traffic and "
                    "advertising performance require separate supporting data."
                ],
                styles,
                muted=True,
            )
        )
        return items


def split_if_needed(text: str) -> list[str]:
    from app.reports.formatting import split_assessment

    return split_assessment(text) or [text]


def _styles() -> dict[str, ParagraphStyle]:
    regular, bold, _ = register_report_fonts()
    base = getSampleStyleSheet()
    return {
        "cover_kicker": ParagraphStyle(
            "cover_kicker", parent=base["Normal"], fontName=regular, fontSize=9.5,
            textColor=TEXT_SECONDARY, spaceAfter=0,
        ),
        "cover_title": ParagraphStyle(
            "cover_title", parent=base["Normal"], fontName=bold, fontSize=28, leading=32,
            textColor=PRIMARY_DARK, spaceAfter=0,
        ),
        "cover_brand": ParagraphStyle(
            "cover_brand", parent=base["Normal"], fontName=bold, fontSize=11, textColor=ACCENT, spaceAfter=1,
        ),
        "cover_product": ParagraphStyle(
            "cover_product", parent=base["Normal"], fontName=bold, fontSize=16, leading=21, textColor=TEXT_PRIMARY,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"], fontName=bold, fontSize=18, leading=22,
            textColor=PRIMARY_DARK, spaceBefore=2, spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontName=bold, fontSize=12.5, leading=16,
            textColor=PRIMARY_DARK, spaceBefore=8, spaceAfter=4,
        ),
        "toc": ParagraphStyle(
            "toc", parent=base["Normal"], fontName=regular, fontSize=11, leading=18, textColor=TEXT_PRIMARY, spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName=regular, fontSize=10, leading=14.5,
            textColor=TEXT_PRIMARY, alignment=TA_JUSTIFY, spaceAfter=4,
        ),
        "muted": ParagraphStyle(
            "muted", parent=base["Normal"], fontName=regular, fontSize=8.5, leading=12, textColor=TEXT_SECONDARY, spaceAfter=3,
        ),
        "card_label": ParagraphStyle(
            "card_label", parent=base["Normal"], fontName=regular, fontSize=6.5, leading=8,
            textColor=TEXT_SECONDARY, alignment=TA_CENTER,
        ),
        "card_score": ParagraphStyle(
            "card_score", parent=base["Normal"], fontName=bold, fontSize=12, leading=14,
            textColor=PRIMARY_DARK, alignment=TA_CENTER,
        ),
        "card_status": ParagraphStyle(
            "card_status", parent=base["Normal"], fontName=bold, fontSize=7, leading=9,
            textColor=TEXT_PRIMARY, alignment=TA_CENTER,
        ),
        "kpi_label": ParagraphStyle(
            "kpi_label", parent=base["Normal"], fontName=regular, fontSize=7, textColor=TEXT_SECONDARY, alignment=TA_LEFT,
        ),
        "kpi_value": ParagraphStyle(
            "kpi_value", parent=base["Normal"], fontName=regular, fontSize=11, leading=14, textColor=PRIMARY_DARK,
        ),
        "bsr_rank": ParagraphStyle(
            "bsr_rank", parent=base["Normal"], fontName=bold, fontSize=16, leading=20,
            textColor=PRIMARY_DARK, spaceAfter=1,
        ),
        "coverage_hero": ParagraphStyle(
            "coverage_hero", parent=base["Normal"], fontName=bold, fontSize=22, leading=26,
            textColor=PRIMARY_DARK, spaceAfter=2,
        ),
        "priority_label": ParagraphStyle(
            "priority_label", parent=base["Normal"], fontName=bold, fontSize=9, textColor=ATTENTION, spaceBefore=6, spaceAfter=3,
        ),
        "num": ParagraphStyle(
            "num", parent=base["Normal"], fontName=bold, fontSize=11, textColor=ACCENT, spaceAfter=1,
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=base["Normal"], fontName=regular, fontSize=9.5, leading=13, textColor=TEXT_PRIMARY,
            leftIndent=8, spaceAfter=2,
        ),
        "box": ParagraphStyle(
            "box", parent=base["Normal"], fontName=regular, fontSize=10, leading=14, textColor=TEXT_PRIMARY,
        ),
        "cell": ParagraphStyle(
            "cell", parent=base["Normal"], fontName=regular, fontSize=8.5, leading=11, textColor=TEXT_PRIMARY,
        ),
        "cell_head": ParagraphStyle(
            "cell_head", parent=base["Normal"], fontName=bold, fontSize=8.5, leading=11, textColor=TEXT_PRIMARY,
        ),
        "cell_muted": ParagraphStyle(
            "cell_muted", parent=base["Normal"], fontName=regular, fontSize=8, leading=11, textColor=TEXT_SECONDARY,
        ),
    }


def _heading(title: str, styles, size: str = "h1"):
    return KeepTogether(
        [
            CondPageBreak(32 * mm),
            Paragraph(title, styles[size]),
            HRFlowable(width="100%", thickness=0.8, color=ACCENT if size == "h1" else BORDER, spaceAfter=8),
        ]
    )


def _meta_block(rows: list[tuple[str, str]], styles) -> Table:
    data = [
        [
            Paragraph(escape(label).upper(), styles["muted"]),
            Paragraph(escape(value), styles["body"]),
        ]
        for label, value in rows
    ]
    table = Table(data, colWidths=[40 * mm, 80 * mm])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, BORDER),
    ]))
    return table


def _section_score_cards(sections: list[SectionScoreView], styles) -> Table:
    cells = []
    for section in sections:
        inner = [
            Paragraph(escape(section.short_label.upper()), styles["card_label"]),
            Paragraph(str(section.score), styles["card_score"]),
            Paragraph(escape(section.status.upper()), styles["card_status"]),
        ]
        cell = Table([[inner[0]], [inner[1]], [inner[2]]], colWidths=[31 * mm])
        cell.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
            ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        cells.append(cell)
    table = Table([cells], colWidths=[PAGE_WIDTH / 5] * 5)
    table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 1.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def _fix_row(index: int, category: str, summary: str, priority: str, styles) -> KeepTogether:
    block = Table(
        [[
            Paragraph(f"{index:02d}", styles["num"]),
            Paragraph(
                f"<b>{escape(category.upper())}</b><br/>{escape(summary)}<br/>"
                f"<font color='#5C6370'>Priority: {escape(priority)}</font>",
                styles["body"],
            ),
        ]],
        colWidths=[12 * mm, PAGE_WIDTH - 12 * mm],
    )
    block.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, BORDER),
    ]))
    return KeepTogether([block])


def _callout(title: str, paragraphs: list[str], styles, muted: bool = False) -> list:
    body = [
        CondPageBreak(24 * mm),
        Paragraph(escape(title.upper()), styles["h2" if not muted else "muted"]),
        HRFlowable(width="100%", thickness=0.5, color=ACCENT if not muted else BORDER, spaceAfter=4),
    ]
    for part in paragraphs:
        body.append(Paragraph(escape(part), styles["body" if not muted else "muted"]))
    body.append(Spacer(1, 3 * mm))
    return body


def _section_deep_dive(section: SectionScoreView, styles) -> list:
    header = Table(
        [[
            Paragraph(escape(section.label), styles["h2"]),
            Paragraph(f"{section.score} / {section.max_score}", styles["body"]),
            Paragraph(escape(section.status.upper()), styles["card_status"]),
        ]],
        colWidths=[90 * mm, 35 * mm, 35 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    parts: list = [
        KeepTogether([
            CondPageBreak(30 * mm),
            header,
            ScoreBar(section.score, section.max_score, PAGE_WIDTH),
            Spacer(1, 2 * mm),
        ])
    ]
    if section.findings:
        for finding in section.findings:
            parts.append(Paragraph(f"• {escape(finding)}", styles["bullet"]))
    else:
        parts.append(Paragraph("No section findings were recorded.", styles["muted"]))
    parts.append(Spacer(1, 3 * mm))
    return parts


def _kpi_row(items, styles) -> Table:
    cells = []
    width = PAGE_WIDTH / max(len(items), 1)
    for item in items:
        inner = Table(
            [[Paragraph(escape(item.label.upper()), styles["kpi_label"])],
             [Paragraph(escape(item.value), styles["kpi_value"])]],
            colWidths=[width - 4 * mm],
        )
        inner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
            ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        cells.append(inner)
    table = Table([cells], colWidths=[width] * len(items))
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    return table


def _coverage_cards(groups: list[CoverageGroupView], styles) -> Table:
    cells = []
    width = PAGE_WIDTH / 5
    for group in groups:
        inner = Table(
            [[
                Paragraph(escape(group.title.upper()), styles["card_label"]),
            ], [
                Paragraph(f"{group.percentage}%", styles["card_score"]),
            ], [
                Paragraph(f"{group.available} / {group.expected} observed", styles["card_status"]),
            ]],
            colWidths=[width - 3 * mm],
        )
        inner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
            ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        cells.append(inner)
    table = Table([cells], colWidths=[width] * 5)
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return table


def _finding_card(category: str, message: str, styles) -> KeepTogether:
    inner = Table(
        [[Paragraph(escape(category), styles["h2"])], [Paragraph(escape(message), styles["body"])]],
        colWidths=[PAGE_WIDTH - 4 * mm],
    )
    inner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
        ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return KeepTogether([inner, Spacer(1, 2.5 * mm)])


def _roadmap_item(item: ActionItem, styles, show_detail: bool = False) -> list:
    inner = Table(
        [[
            Paragraph(f"{item.index:02d}", styles["num"]),
            Paragraph(
                f"<b>{escape(item.title)}</b>    "
                f"<font color='#8A3B32'>{escape(item.priority)}</font>",
                styles["body"],
            ),
        ]],
        colWidths=[14 * mm, PAGE_WIDTH - 14 * mm],
    )
    inner.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBEFORE", (1, 0), (1, 0), 1.2, ACCENT),
        ("LEFTPADDING", (1, 0), (1, 0), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    parts: list = [KeepTogether([CondPageBreak(24 * mm), inner])]
    if show_detail and item.detail:
        parts.append(Paragraph(escape(item.detail), styles["body"]))
        parts.append(Spacer(1, 3 * mm))
    else:
        parts.append(Spacer(1, 2 * mm))
    return parts


def _insight_block(module: InsightModule, styles) -> list:
    parts: list = [
        CondPageBreak(32 * mm),
        Paragraph(escape(module.title), styles["h2"]),
        Paragraph(escape(module.assessment), styles["body"]),
    ]
    if module.strengths:
        parts.extend(_side_box("Strengths", module.strengths, styles, ok=True))
    if module.opportunities:
        parts.extend(_side_box("Opportunities", module.opportunities, styles, ok=False))
    parts.append(Spacer(1, 3 * mm))
    return parts


def _side_box(title: str, lines: list[str], styles, ok: bool) -> list:
    mark = "•"
    parts: list = [
        Paragraph(escape(title.upper()), styles["muted"]),
        HRFlowable(width="100%", thickness=1.4, color=ACCENT if ok else WARNING, spaceAfter=3),
    ]
    for line in lines:
        parts.append(Paragraph(f"{mark} {escape(line)}", styles["bullet"]))
    parts.append(Spacer(1, 2 * mm))
    return parts


def _chip_group(title: str, values: list[str], styles, kind: str) -> list:
    heading = Paragraph(escape(title.upper()), styles["h2"])
    if not values:
        return [heading, Paragraph("None recorded.", styles["muted"])]
    prefix = "• "
    if kind == "warn":
        prefix = "! "
    parts: list = [heading]
    for item in values:
        parts.append(Paragraph(f"{prefix}{escape(item)}", styles["bullet"]))
    parts.append(Spacer(1, 3 * mm))
    return parts


def _copy_box(text: str, styles) -> list:
    return [
        HRFlowable(width="100%", thickness=0.8, color=PRIMARY_DARK, spaceBefore=1, spaceAfter=4),
        Paragraph(escape(text), styles["box"]),
        HRFlowable(width="100%", thickness=0.8, color=PRIMARY_DARK, spaceBefore=4, spaceAfter=6),
    ]


def _simple_table(rows: list[list[str]], widths: list[float], styles, header: bool = True, muted: bool = False) -> Table:
    styled = []
    for index, row in enumerate(rows):
        use_header = header and index == 0
        style = styles["cell_head"] if use_header else (styles["cell_muted"] if muted else styles["cell"])
        styled.append([Paragraph(escape(str(cell)), style) for cell in row])
    table = Table(styled, colWidths=widths, repeatRows=1 if header else 0)
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        commands.append(("BACKGROUND", (0, 0), (-1, 0), SURFACE))
    table.setStyle(TableStyle(commands))
    return table
