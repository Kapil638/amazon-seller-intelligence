"""Citation validator and evidence-backed template fallback. Does not call tools."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from app.copilot.evidence import EvidenceEnvelope
from app.copilot.synthesis.schemas import (
    AllowedFact,
    EvidenceCitation,
    ProposedFinding,
    ProposedRecommendation,
    SynthesisProposal,
    SynthesizedResponse,
)

MAX_FACTS = 40
MAX_VALUE_CHARS = 400
MAX_FINDINGS = 6
MAX_RECOMMENDATIONS = 5

_CONFIDENCE = {"high", "medium", "low", "none"}
_TOOL_LABELS = {
    "get_saved_report": "Saved analysis",
    "list_saved_reports": "Saved analyses",
    "analyze_listing_v2": "Listing analysis",
    "get_product": "Product lookup",
    "get_profit_snapshot": "Profit snapshot",
    "analyze_profitability": "Profit calculation",
    "get_advertising_snapshot": "Advertising snapshot",
    "analyze_advertising_impact": "Advertising impact",
    # 12B.5A
    "prioritize_listing_health": "Listing health ranking",
    "investigate_non_buyable_listing": "Non-buyable listing investigation",
    "analyze_order_trends": "Order and sales trend analysis",
    "detect_cancellation_anomalies": "Cancellation analysis",
    "rank_listing_risk_by_order_exposure": "Listing risk by order exposure",
}

_SKILL_SUMMARY_TEMPLATES: dict[str, str] = {
    "listing_health_prioritizer": "Here is your Listing health ranking for this marketplace.",
    "non_buyable_listing_investigator": "Here is what ASI observed about this listing.",
    "order_and_sales_trend_analyst": "Here is your order and sales trend for this period.",
    "cancellation_operational_anomaly_detector": "Here is your cancellation analysis for this period.",
    "listing_risk_by_order_exposure": "Here are the Listings with the most verified order exposure tied to an open issue.",
}
# 12B.5A — the non_buyable_listing_investigator tool answers two shapes
# of evidence: a single named listing's detail (metrics has
# "is_buyable"), or — when the seller asks the general "why are my
# listings not buyable?" question with no SKU/ASIN named — a prioritized
# selection of not-buyable listings (metrics has "not_buyable_count"
# instead). The summary line must say which one this is.
_NON_BUYABLE_SELECTION_SUMMARY = "Here is a prioritized list of listings that are not currently buyable."

_CONTEXT_KEYS = (
    "last_asin",
    "last_report_id",
    "previous_intent",
    "evidence_refs",
    "recent_user_snippets",
)


def sanitize_compact_context(raw: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(raw or {})
    compact: dict[str, Any] = {}
    for key in _CONTEXT_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if key == "recent_user_snippets" and isinstance(value, list):
            compact[key] = [str(item)[:500] for item in value[:2]]
        elif key == "evidence_refs" and isinstance(value, list):
            compact[key] = value[:8]
        else:
            compact[key] = value
    return compact


def copy_evidence(envelopes: list[EvidenceEnvelope]) -> list[EvidenceEnvelope]:
    return [item.model_copy(deep=True) for item in envelopes]


def build_allowed_facts(envelopes: list[EvidenceEnvelope]) -> list[AllowedFact]:
    facts: list[AllowedFact] = []
    for item in envelopes:
        for claim in item.claims:
            if len(facts) >= MAX_FACTS:
                return facts
            facts.append(
                AllowedFact(
                    evidence_id=item.evidence_id,
                    tool_name=item.tool_name,
                    claim_key=claim.key,
                    value=_trim_value(claim.value),
                    kind=claim.kind,
                    source=claim.source,
                )
            )
    return facts


def validate_proposal(
    proposal: SynthesisProposal,
    *,
    facts: list[AllowedFact],
    intent: str,
    user_message: str,
    prompt_version: str | None,
    synthesis_model: str | None,
) -> SynthesizedResponse:
    index = _FactIndex(facts)
    findings: list[str] = []
    recommendations: list[str] = []
    citations: list[EvidenceCitation] = []
    unknowns = [str(item).strip() for item in proposal.unknowns if str(item).strip()]
    rewritten = False

    for item in proposal.findings:
        grounded, citation, changed = index.ground_finding(item)
        if grounded is None:
            if item.text.strip():
                unknowns.append("Dropped an unsupported finding.")
            rewritten = True
            continue
        findings.append(grounded)
        if citation is not None:
            _add_citation(citations, citation)
        rewritten = rewritten or changed
        if len(findings) >= MAX_FINDINGS:
            break

    for item in proposal.recommendations:
        grounded, citation, changed = index.ground_recommendation(item)
        if grounded is None:
            rewritten = True
            continue
        recommendations.append(grounded)
        if citation is not None:
            _add_citation(citations, citation)
        rewritten = rewritten or changed
        if len(recommendations) >= MAX_RECOMMENDATIONS:
            break

    summary = (proposal.summary or "").strip()
    if not findings or not summary or _has_ungrounded_language(summary, index):
        return template_response(
            facts,
            intent=intent,
            user_message=user_message,
            extras=unknowns,
        )

    confidence = proposal.confidence if proposal.confidence in _CONFIDENCE else "medium"
    source = "rewritten_citations" if rewritten else "synthesis_llm"
    response = SynthesizedResponse(
        summary=summary[:800],
        findings=findings,
        recommendations=recommendations,
        citations=citations,
        confidence=confidence,  # type: ignore[arg-type]
        unknowns=_unique(unknowns)[:8],
        source=source,  # type: ignore[arg-type]
        prompt_version=prompt_version,
        synthesis_model=synthesis_model,
        message="",
    )
    return response.model_copy(update={"message": format_seller_message(response)})


def template_response(
    facts: list[AllowedFact],
    *,
    intent: str,
    user_message: str,
    extras: list[str] | None = None,
) -> SynthesizedResponse:
    if intent == "out_of_scope":
        return _canned(
            summary=(
                "Competitor comparison, campaign PPC, and product-launch questions are not "
                "available in Copilot yet. Use Analyze for competitor discovery."
            ),
            confidence="none",
            extras=extras,
        )
    if intent == "clarify" and not facts:
        return _canned(
            summary="I need an ASIN or a saved analysis to continue. Paste an ASIN or open History.",
            confidence="none",
            extras=extras,
        )
    if not facts:
        return _canned(
            summary="I do not have analysis evidence for this question yet. Open History or confirm a new analysis.",
            confidence="none",
            extras=extras,
        )

    skill_response = _skill_template_response(facts, extras=extras)
    if skill_response is not None:
        return skill_response

    findings: list[str] = []
    recommendations: list[str] = []
    citations: list[EvidenceCitation] = []
    score_fact = _first(facts, "listing_quality_score")
    asin_fact = _first(facts, "asin")
    section_fact = _first(facts, "section_scores")
    weaknesses_fact = _first(facts, "weaknesses")
    findings_fact = _first(facts, "findings")
    recs_fact = _first(facts, "recommendations")
    reports_fact = _first(facts, "reports")
    total_fact = _first(facts, "total")
    profit_fact = _first(facts, "net_profit_before_ads")
    margin_fact = _first(facts, "margin_before_ads")
    roi_fact = _first(facts, "roi_on_cogs")
    acos_fact = _first(facts, "acos")
    tacos_fact = _first(facts, "tacos")
    after_ads_fact = _first(facts, "net_profit_after_ads")

    if profit_fact is not None:
        if profit_fact.kind == "unknown" or profit_fact.value is None:
            findings.append("Unit profit before ads is unknown.")
        else:
            findings.append(f"Unit profit before ads: {profit_fact.value}")
        _add_citation(citations, _citation(profit_fact))
    if margin_fact is not None and margin_fact.value is not None and margin_fact.kind != "unknown":
        findings.append(f"Margin before ads: {margin_fact.value}")
        _add_citation(citations, _citation(margin_fact))
    if roi_fact is not None and roi_fact.value is not None and roi_fact.kind != "unknown":
        findings.append(f"ROI on COGS: {roi_fact.value}")
        _add_citation(citations, _citation(roi_fact))
    if acos_fact is not None:
        if acos_fact.kind == "unknown" or acos_fact.value is None:
            findings.append("ACOS is unknown.")
        else:
            findings.append(f"ACOS: {acos_fact.value}")
        _add_citation(citations, _citation(acos_fact))
    if tacos_fact is not None:
        if tacos_fact.kind == "unknown" or tacos_fact.value is None:
            findings.append("TACOS is unknown.")
        else:
            findings.append(f"TACOS: {tacos_fact.value}")
        _add_citation(citations, _citation(tacos_fact))
    if after_ads_fact is not None:
        if after_ads_fact.kind == "unknown" or after_ads_fact.value is None:
            findings.append("Profit after ads is unknown.")
        else:
            findings.append(f"Profit after ads: {after_ads_fact.value}")
        _add_citation(citations, _citation(after_ads_fact))

    if score_fact is not None:
        findings.append(f"Listing quality score: {score_fact.value}")
        _add_citation(citations, _citation(score_fact))
    if section_fact is not None:
        for line in _section_score_lines(section_fact.value)[:3]:
            findings.append(line)
        _add_citation(citations, _citation(section_fact))
    if asin_fact is not None and asin_fact.value:
        if section_fact is None:
            findings.append(f"ASIN: {asin_fact.value}")
        _add_citation(citations, _citation(asin_fact))
    weakness_source = weaknesses_fact or findings_fact
    if weakness_source is not None:
        for row in _finding_rows(weakness_source.value)[:MAX_FINDINGS]:
            label = row.get("issue") or row.get("message") or row.get("code") or "Listing finding"
            findings.append(f"Weak area: {label}")
        _add_citation(citations, _citation(weakness_source))
    if recs_fact is not None and _finding_rows(recs_fact.value):
        for row in _finding_rows(recs_fact.value)[:MAX_RECOMMENDATIONS]:
            action = row.get("action")
            if action:
                recommendations.append(str(action))
        _add_citation(citations, _citation(recs_fact))
    elif findings_fact is not None:
        for row in _finding_rows(findings_fact.value)[:MAX_FINDINGS]:
            code = row.get("code")
            if code:
                recommendations.append(f"Fix {code.replace('_', ' ').lower()} first.")
    if reports_fact is not None and isinstance(reports_fact.value, list):
        for row in reports_fact.value[:5]:
            if not isinstance(row, dict):
                continue
            asin = row.get("asin") or ""
            score = row.get("listing_quality_score")
            findings.append(f"Saved analysis for {asin}: score {score}")
        _add_citation(citations, _citation(reports_fact))
        if total_fact is not None:
            _add_citation(citations, _citation(total_fact))

    findings = _unique(findings)[:MAX_FINDINGS]
    recommendations = _unique(recommendations)[:MAX_RECOMMENDATIONS]
    if not findings:
        findings = ["The loaded analysis did not include a score or finding list."]

    if score_fact is not None:
        summary = (
            f"Your listing analysis identified improvement opportunities. "
            f"The listing quality score is {score_fact.value}."
        )
    elif profit_fact is not None:
        summary = "Your profit snapshot is ready to explain. Copilot does not recalculate these numbers."
    elif acos_fact is not None or after_ads_fact is not None:
        summary = "Your advertising snapshot is ready to explain. Copilot does not recalculate ACOS or profit."
    elif total_fact is not None:
        summary = f"You have {total_fact.value} saved analyses related to this question."
    else:
        summary = "Your listing analysis identified improvement opportunities."

    confidence: str = "high" if score_fact is not None else "medium"
    response = SynthesizedResponse(
        summary=summary,
        findings=findings,
        recommendations=recommendations,
        citations=citations,
        confidence=confidence,  # type: ignore[arg-type]
        unknowns=_unique(extras or []),
        source="template_fallback",
        prompt_version=None,
        synthesis_model=None,
        message="",
    )
    _ = user_message
    return response.model_copy(update={"message": format_seller_message(response)})


def _skill_template_response(facts: list[AllowedFact], *, extras: list[str] | None) -> SynthesizedResponse | None:
    """12B.5A — deterministic (no-LLM) seller answer for any of the five
    Listings/Orders skills, built entirely from the one `skill_evidence`
    fact each skill's tool always emits (see `app.copilot.skills.
    contracts.skill_evidence_to_claims`). Returns `None` (never raises)
    when no such fact is present, so every non-skill intent's existing
    template path is completely unaffected.

    This is the path every SQLite/CI run actually exercises for these
    five skills (no LLM is attached there — see `planner/service.py`'s
    `_sqlite_test_database` gate, and `synthesis/service.py`'s identical
    one), so it must never merely echo raw JSON: every number here is
    labeled in plain language, "order value" is never called "revenue,"
    money is grouped by currency, and an anomaly is only ever named as
    such when the evidence's own `is_anomalous` metric says so.
    """
    fact = _first(facts, "skill_evidence")
    if fact is None or not isinstance(fact.value, dict):
        return None
    evidence = fact.value
    skill_id = evidence.get("skill_id")
    if skill_id not in _SKILL_SUMMARY_TEMPLATES:
        return None

    citation = _citation(fact)
    findings: list[str] = [_freshness_finding(evidence)]
    recommendations: list[str] = []
    metrics = evidence.get("metrics") or {}
    records = evidence.get("records") or []

    if skill_id == "listing_health_prioritizer":
        findings.append(
            f"{metrics.get('with_issues_count', 0)} of {metrics.get('total_listings', 0)} listings have an "
            f"open Amazon issue ({metrics.get('issue_severity_error_count', 0)} ERROR, "
            f"{metrics.get('issue_severity_warning_count', 0)} WARNING)."
        )
        for row in records[:3]:
            findings.append(
                f"{row.get('seller_sku')}: highest issue severity {row.get('highest_issue_severity') or 'none'}, "
                f"buyable={row.get('is_buyable')}, {row.get('recent_order_count', 0)} recent order(s)."
            )
        if records:
            recommendations.append(f"Start with {records[0].get('seller_sku')} — it ranks first.")
    elif skill_id == "non_buyable_listing_investigator" and "not_buyable_count" in metrics:
        # Selection mode: no specific SKU/ASIN was named, so this is a
        # prioritized list of candidates, not one listing's detail.
        findings.append(
            f"{metrics.get('not_buyable_count', 0)} listing(s) are currently not buyable in this marketplace."
        )
        for row in records[:5]:
            findings.append(
                f"{row.get('seller_sku')}: highest issue severity {row.get('highest_issue_severity') or 'none'}."
            )
        if records:
            recommendations.append(
                f"Ask about {records[0].get('seller_sku')} specifically for a full investigation."
            )
    elif skill_id == "non_buyable_listing_investigator":
        findings.append(
            f"is_buyable={metrics.get('is_buyable')}, is_active={metrics.get('is_active')}, "
            f"is_discoverable={metrics.get('is_discoverable')}."
        )
        findings.append(
            f"{metrics.get('issue_severity_error_count', 0)} ERROR and "
            f"{metrics.get('issue_severity_warning_count', 0)} WARNING issue(s) on record."
        )
        for row in records:
            if row.get("kind") in ("possible_explanation", "observed_fact") and row.get("note"):
                findings.append(row["note"])
        recommendations.append("Open this listing in Seller Listings to review Amazon's exact issue text.")
    elif skill_id == "order_and_sales_trend_analyst":
        change = metrics.get("order_count_percentage_change")
        sample_sufficient = metrics.get("sample_size_sufficient_for_trend", True)
        if change is None:
            change_text = "no orders in the comparison period (new activity)"
        elif not sample_sufficient:
            change_text = f"{change:+.1f}% vs the prior period (sample too small for a reliable trend)"
        else:
            change_text = f"{change:+.1f}% vs the prior period"
        findings.append(
            f"{metrics.get('order_count', 0)} orders and {metrics.get('unit_count', 0)} units this period "
            f"({change_text})."
        )
        for currency, amount in (metrics.get("order_value_by_currency") or {}).items():
            findings.append(f"Order value: {amount} {currency}.")
        if metrics.get("orders_without_items_count"):
            findings.append(f"{metrics['orders_without_items_count']} order(s) have no item rows on record.")
    elif skill_id == "cancellation_operational_anomaly_detector":
        findings.append(
            f"{metrics.get('cancelled_orders', 0)} of {metrics.get('total_orders', 0)} orders were cancelled "
            f"this period."
        )
        if metrics.get("is_anomalous"):
            findings.append(f"This is anomalous: {metrics.get('anomaly_reason')}.")
            recommendations.append("Review the cancelled orders for a common SKU or fulfillment pattern.")
        else:
            findings.append(f"Not labeled anomalous: {metrics.get('anomaly_reason')}.")
    elif skill_id == "listing_risk_by_order_exposure":
        findings.append(f"{metrics.get('at_risk_listing_count', 0)} listing(s) currently have an open issue.")
        for currency, amount in (metrics.get("exposed_order_value_by_currency") or {}).items():
            findings.append(f"Order value already observed for at-risk listings: {amount} {currency}.")
        for row in records[:3]:
            findings.append(
                f"{row.get('seller_sku')}: {row.get('highest_issue_severity')} issue, "
                f"{row.get('recent_order_count', 0)} recent order(s)."
            )

    limitations = [str(item) for item in (evidence.get("limitations") or [])]
    summary = (
        _NON_BUYABLE_SELECTION_SUMMARY
        if skill_id == "non_buyable_listing_investigator" and "not_buyable_count" in metrics
        else _SKILL_SUMMARY_TEMPLATES[skill_id]
    )

    response = SynthesizedResponse(
        summary=summary,
        findings=_unique(findings)[:MAX_FINDINGS],
        recommendations=_unique(recommendations)[:MAX_RECOMMENDATIONS],
        citations=[citation],
        confidence=_map_confidence(evidence.get("confidence")),
        unknowns=_unique((extras or []) + limitations)[:8],
        source="template_fallback",
        prompt_version=None,
        synthesis_model=None,
        message="",
    )
    return response.model_copy(update={"message": format_seller_message(response)})


def _freshness_finding(evidence: dict) -> str:
    parts: list[str] = []
    listings = evidence.get("listings_freshness")
    if isinstance(listings, dict):
        parts.append(f"Listings data: {listings.get('status')}")
        if listings.get("last_successful_synchronized_at"):
            parts.append(f"(through {listings['last_successful_synchronized_at']})")
    orders = evidence.get("orders_freshness")
    if isinstance(orders, dict):
        parts.append(f"Orders data: {orders.get('status')}")
        if orders.get("last_successful_synchronized_at"):
            parts.append(f"(through {orders['last_successful_synchronized_at']})")
    if evidence.get("has_newer_incomplete_run"):
        parts.append("A newer synchronization has not completed successfully yet.")
    return "Data freshness — " + " ".join(parts) if parts else "Data freshness is unknown."


def _map_confidence(value: object) -> str:
    if value == "insufficient_data":
        return "none"
    if value in _CONFIDENCE:
        return str(value)
    return "medium"


def format_seller_message(response: SynthesizedResponse) -> str:
    lines = ["## Summary", response.summary, "", "## Key Findings"]
    if response.findings:
        lines.extend(f"- {item}" for item in response.findings)
    else:
        lines.append("- No evidence-backed findings were available.")
    lines.extend(["", "## Recommended Actions"])
    if response.recommendations:
        lines.extend(f"- {item}" for item in response.recommendations)
    else:
        lines.append("- No evidence-backed actions were available.")
    lines.extend(["", "## Evidence"])
    if response.citations:
        seen: set[str] = set()
        for item in response.citations:
            label = f"{item.claim_key} · {item.label}"
            if label in seen:
                continue
            seen.add(label)
            lines.append(f"- {label}")
    else:
        lines.append("- No citations.")
    return "\n".join(lines)


class _FactIndex:
    def __init__(self, facts: list[AllowedFact]) -> None:
        self.facts = facts
        self.by_key: dict[str, list[AllowedFact]] = {}
        self.by_id_key: dict[tuple[str, str], AllowedFact] = {}
        self.finding_codes: dict[str, AllowedFact] = {}
        for fact in facts:
            self.by_key.setdefault(fact.claim_key, []).append(fact)
            self.by_id_key[(str(fact.evidence_id), fact.claim_key)] = fact
            if fact.claim_key == "findings":
                for row in _finding_rows(fact.value):
                    code = str(row.get("code") or "").strip()
                    if code:
                        self.finding_codes[code] = fact

    def resolve(self, claim_key: str | None, evidence_id: UUID | None) -> AllowedFact | None:
        key = (claim_key or "").strip()
        if evidence_id is not None and key:
            found = self.by_id_key.get((str(evidence_id), key))
            if found is not None:
                return found
        if key in self.finding_codes and key not in self.by_key:
            return self.finding_codes[key]
        matches = self.by_key.get(key) or []
        if evidence_id is not None:
            for item in matches:
                if item.evidence_id == evidence_id:
                    return item
            return None
        if len(matches) == 1:
            return matches[0]
        if matches:
            return matches[0]
        if key in self.finding_codes:
            return self.finding_codes[key]
        return None

    def ground_finding(self, item: ProposedFinding) -> tuple[str | None, EvidenceCitation | None, bool]:
        fact = self.resolve(item.claim_key, item.evidence_id)
        if fact is None:
            return None, None, True
        text = (item.text or "").strip()
        changed = False
        if not text or _has_ungrounded_language(text, self):
            text = _claim_as_finding(fact)
            changed = True
        if not text:
            return None, None, True
        return text, _citation(fact), changed

    def ground_recommendation(
        self, item: ProposedRecommendation
    ) -> tuple[str | None, EvidenceCitation | None, bool]:
        fact = self.resolve(item.claim_key, item.evidence_id)
        text = (item.text or "").strip()
        if fact is None or not text:
            return None, None, True
        if _has_ungrounded_language(text, self):
            return None, None, True
        return text, _citation(fact), False


def _has_ungrounded_language(text: str, index: _FactIndex) -> bool:
    lowered = (text or "").lower()
    allowed_keys = {key.lower() for key in index.by_key}
    if re.search(r"\bconversion\b", lowered) and not any("conversion" in key for key in allowed_keys):
        return True
    if any(token in lowered for token in ("ppc", "acos")) and not any(
        key in allowed_keys for key in ("ppc", "acos")
    ):
        return True
    if "profit margin" in lowered and "profit" not in allowed_keys:
        return True
    if "search volume" in lowered and "search_volume" not in allowed_keys:
        return True
    ranking_talk = any(
        token in lowered
        for token in ("amazon ranking", "organic rank", "bestseller rank", "amazon will penal", "amazon policy")
    )
    if ranking_talk and not any(key in {"bsr", "rank", "ranking"} or "rank" in key for key in allowed_keys):
        return True
    return False


def _claim_as_finding(fact: AllowedFact) -> str:
    if fact.claim_key == "listing_quality_score":
        return f"Listing quality score: {fact.value}"
    if fact.claim_key == "asin":
        return f"ASIN: {fact.value}"
    if fact.claim_key == "findings":
        rows = _finding_rows(fact.value)
        if not rows:
            return "The listing analysis recorded findings."
        first = rows[0]
        label = first.get("issue") or first.get("message") or first.get("code") or "listing finding"
        return f"Your listing analysis identified this weakness: {label}"
    if fact.claim_key == "weaknesses":
        rows = _finding_rows(fact.value)
        if not rows:
            return "The listing analysis recorded weaknesses."
        first = rows[0]
        label = first.get("issue") or first.get("code") or "listing weakness"
        return f"Your listing analysis identified this weakness: {label}"
    if fact.claim_key == "section_scores":
        lines = _section_score_lines(fact.value)
        if lines:
            return f"Lowest section score — {lines[0]}"
        return "Section scores are available from the listing analysis."
    if fact.claim_key == "recommendations":
        rows = _finding_rows(fact.value)
        if not rows:
            return "The listing analysis recorded recommended actions."
        action = rows[0].get("action") or "Improve the listing using saved analysis findings."
        return f"Your listing analysis recommended: {action}"
    if fact.claim_key == "total":
        return f"Saved analyses found: {fact.value}"
    value = fact.value
    if isinstance(value, (dict, list)):
        return f"{fact.claim_key} from { _tool_label(fact.tool_name) }"
    return f"{fact.claim_key}: {value}"


def _citation(fact: AllowedFact) -> EvidenceCitation:
    return EvidenceCitation(
        evidence_id=fact.evidence_id,
        claim_key=fact.claim_key,
        tool_name=fact.tool_name,
        label=_tool_label(fact.tool_name),
    )


def _tool_label(name: str) -> str:
    return _TOOL_LABELS.get(name, "Analysis evidence")


def _finding_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _section_score_lines(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    rows: list[tuple[int, str]] = []
    for item in value.values():
        if not isinstance(item, dict):
            continue
        score = item.get("score")
        max_score = item.get("max_score")
        label = item.get("label") or "Section"
        if score is None or max_score is None:
            continue
        rows.append((int(score), f"{label}: {score}/{max_score}"))
    rows.sort(key=lambda pair: pair[0])
    return [line for _score, line in rows]


def _first(facts: list[AllowedFact], key: str) -> AllowedFact | None:
    for item in facts:
        if item.claim_key == key:
            return item
    return None


def _trim_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_VALUE_CHARS]
    if isinstance(value, list):
        return [_trim_value(item) for item in value[:12]]
    if isinstance(value, dict):
        return {str(key): _trim_value(item) for key, item in list(value.items())[:20]}
    return value


def _add_citation(citations: list[EvidenceCitation], item: EvidenceCitation) -> None:
    key = (str(item.evidence_id), item.claim_key)
    if any((str(existing.evidence_id), existing.claim_key) == key for existing in citations):
        return
    citations.append(item)


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _canned(*, summary: str, confidence: str, extras: list[str] | None) -> SynthesizedResponse:
    response = SynthesizedResponse(
        summary=summary,
        findings=[],
        recommendations=[],
        citations=[],
        confidence=confidence,  # type: ignore[arg-type]
        unknowns=_unique(extras or []),
        source="template_fallback",
        prompt_version=None,
        synthesis_model=None,
        message="",
    )
    return response.model_copy(update={"message": format_seller_message(response)})
